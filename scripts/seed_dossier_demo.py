"""成稿台演示数据播种（只写独立演示目录，绝不碰生产）。

用法：
    TUTTI_DATA=<demo 目录> PYTHONPATH=app python scripts/seed_dossier_demo.py

它会建两份「文档类」任务并落稿：
  * 一份标书：故意缺 6 项硬性要件（投标保证金/法人证明/报价表/财务/纳税社保/
    无违法记录），用来演示「缺项高亮 + 废标风险」；
  * 一份普通文档：只写了背景与范围，用来演示「建议补充」。
不调模型、不发外部请求。
"""
import json
import os
import sys
import time
from pathlib import Path

DATA = os.environ.get("TUTTI_DATA")
if not DATA:
    sys.exit("必须先设置 TUTTI_DATA 指向一个独立的演示目录")
if "cb-dossier-demo" not in Path(DATA).name:
    sys.exit("拒绝播种：TUTTI_DATA=%s 不像演示目录" % DATA)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from core import paths, store  # noqa: E402

BID = """# 机房搬迁投标文件

## 一、投标函
致：某某招标代理有限公司

我方已仔细阅读招标文件全部内容，愿意按照招标文件的各项要求参加投标。

## 二、公司资格
本公司持有营业执照（统一社会信用代码 9133XXXXXXXXXXXXXX），
具备电子与智能化工程专业承包二级资质证书。

## 三、技术方案
本次搬迁分三阶段实施：现状梳理与备份、分批停机迁移、回切验证。
核心设备采用双机热备，迁移窗口安排在业务低峰期。

## 四、工期承诺
合同签订后 60 日历天内完成全部搬迁与验收。
"""

DOC = """# 机房巡检作业说明

## 一、背景
为规范机房日常巡检，减少因环境异常导致的设备故障。

## 二、适用范围
适用于本公司自有及托管机房的日常巡检工作。
"""


def _write(workdir, name, text):
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / name).write_text(text, encoding="utf-8")


def _task(kind, title, goal, workdir, status="done"):
    task = store.create_task({
        "type": kind,
        "title": title,
        "goal": goal,
        "workdir": str(workdir),
    })
    tid = task["id"]
    tpath = paths.TASKS_DIR / ("%s.json" % tid)
    data = json.loads(tpath.read_text(encoding="utf-8"))
    data["status"] = status
    tpath.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    run = store.create_run("orchestration", title, task_id=tid)
    now = time.time()
    store.update_run(run["id"], status=status, progress=100,
                     summary="已产出，等待交稿前核对。",
                     started_at=time.strftime("%Y-%m-%d %H:%M:%S",
                                              time.localtime(now - 1800)),
                     ended_at=time.strftime("%Y-%m-%d %H:%M:%S",
                                            time.localtime(now)))
    return tid


def main():
    base = Path(DATA)
    bid_wd = base / "dossier-01-bid"
    doc_wd = base / "dossier-02-doc"
    _write(bid_wd, "bid.md", BID)
    _write(doc_wd, "document.md", DOC)

    bid_tid = _task("bid_doc", "机房搬迁投标文件",
                    "给甲方写一份机房搬迁投标文件，含资格、技术方案与工期。",
                    bid_wd)
    doc_tid = _task("doc", "机房巡检作业说明",
                    "写一份机房巡检作业说明，给运维同事看。", doc_wd)

    print("已播种 2 份文档：")
    print("  标书：", bid_tid, "（缺 6 项硬性要件）")
    print("  文档：", doc_tid, "（只写了背景与范围）")
    print("数据目录：", DATA)


if __name__ == "__main__":
    main()
