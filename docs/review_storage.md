# 评审数据结构化存储方案

## 目标与选型

使用 Python 标准库 `sqlite3` 保存结构化评审记录，数据库放在 CodeBee 数据目录，不与生产 Web 端口耦合。SQLite 适合按任务查询、聚合统计和后续路由特征提取；若部署环境不允许数据库文件，可用同一模型写 JSONL，接口保持一致（替代实现【需确认】）。

## 数据模型

```sql
CREATE TABLE review_records (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  run_id TEXT,
  author_cli TEXT NOT NULL,
  author_model_family TEXT,
  reviewer_cli TEXT NOT NULL,
  reviewer_model_family TEXT,
  scores_json TEXT NOT NULL,
  line_comments_json TEXT NOT NULL,
  passed INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  duration_ms INTEGER,
  schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_review_task ON review_records(task_id, created_at);
CREATE INDEX idx_review_author_cli ON review_records(author_cli);
CREATE INDEX idx_review_reviewer_cli ON review_records(reviewer_cli);
```

`scores_json` 是维度到数值的对象，例如 `{ "correctness": 8.5, "security": 9 }`；`line_comments_json` 是数组，每项包含 `path`、`line`、`severity`、`message`。禁止把 API key、完整提示词或未脱敏 CLI 原始日志写入评审记录。

## 写入与查询接口

推荐在 `app/core/review_storage.py`（当前模块尚未存在，落地位置【需确认】）提供以下标准库 API：

```python
from typing import List, Optional

def record_review(row: dict) -> str: ...
def list_reviews(task_id: str, *, limit: int = 100) -> List[dict]: ...
def cli_statistics(*, author_cli: Optional[str] = None,
                   reviewer_cli: Optional[str] = None) -> List[dict]: ...
```

写入使用参数化 SQL、单事务和 UTC ISO-8601 时间戳；`record_review` 对重复 `id` 幂等。读取时将 JSON 字段解码回字典/列表，并对损坏行返回可诊断错误而不是中断整个历史页。

## 示例查询

按任务查看所有评审：

```sql
SELECT * FROM review_records
WHERE task_id = ?
ORDER BY created_at ASC;
```

统计“Claude 作为作者的通过率”：

```sql
SELECT author_cli,
       COUNT(*) AS reviews,
       SUM(CASE WHEN passed = 1 THEN 1 ELSE 0 END) AS passed,
       CAST(SUM(CASE WHEN passed = 1 THEN 1 ELSE 0 END) AS REAL) / COUNT(*) AS pass_rate
FROM review_records
WHERE author_cli = ?
GROUP BY author_cli;
```

按模型族比较评审质量：

```sql
SELECT reviewer_model_family, AVG(json_extract(scores_json, '$.correctness')) AS avg_correctness
FROM review_records
GROUP BY reviewer_model_family;
```

`json_extract` 在 SQLite JSON1 扩展不可用时应由 Python 解码后聚合，不能把扩展当作运行时依赖。

## 生命周期与隐私

评审记录与 run 一起保留，删除/归档任务时按任务策略级联清理或匿名化；保留周期、备份位置和迁移策略尚未在现有代码中固定，标记【需确认】。任何导出接口都必须复用现有脱敏逻辑，并明确提示评审意见可能包含源代码片段。
