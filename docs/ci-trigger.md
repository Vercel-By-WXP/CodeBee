# 外部 CI 触发 CodeBee 任务（GitHub Actions 示例）

CodeBee 的编排链可以由外部系统触发：`POST /api/hooks/run` 用自己的令牌
鉴权（不需要设备控制权），建任务并真实起跑——PR 修复、CI 失败排查、
定时巡检都可以接进这条链。任务分支隔离（检出 `codebee/<id>` 分支、
改动可裁决合并/丢弃）保证自动化改动不污染你的工作区。

## 前置条件

1. CodeBee 启动时控制台会显示访问令牌；钩子令牌在 设置 → 编排设置
   → `hooks_token`（存 data/settings.json），请求头 `X-Hooks-Token` 携带。
2. 服务需可被 GitHub Actions runner 访问（内网自建 runner 或穿透隧道；
   请勿把无 TLS 的端口直接暴露公网）。

## 示例：CI 挂了自动派 CodeBee 排查修复

`.github/workflows/codebee-fix.yml`：

```yaml
name: CodeBee auto-fix
on:
  workflow_run:
    workflows: ["CI"]          # 你的测试工作流名
    types: [completed]
jobs:
  dispatch:
    if: ${{ github.event.workflow_run.conclusion == 'failure' }}
    runs-on: [self-hosted, intranet]   # 能访问 CodeBee 服务的 runner
    steps:
      - name: Dispatch CodeBee task
        run: |
          curl -sS -X POST "$CODEBEE_URL/api/hooks/run" \
            -H "Content-Type: application/json" \
            -H "X-Hooks-Token: $CODEBEE_TOKEN" \
            -d '{
              "type": "code",
              "title": "CI 修复 ${{ github.event.workflow_run.name }}",
              "goal": "CI 在 ${{ github.event.workflow_run.html_url }} 失败了。请查看失败日志，定位并修复问题，确保本地验证命令通过。改动保持最小，不要顺手重构。",
              "workdir": "${{ vars.CODEBEE_REPO_PATH }}"
            }'
        env:
          CODEBEE_URL: ${{ secrets.CODEBEE_URL }}
          CODEBEE_TOKEN: ${{ secrets.CODEBEE_TOKEN }}
```

## 请求/响应

- 请求体与界面建任务同构：`type`（任务类型 id，如 `code`）、`title`、
  `goal`、`workdir`（必填，任务分支从这里检出）。
- 成功返回 `{"ok": true, ...}`（含任务/运行标识）；鉴权失败 401，
  参数缺失 400。
- 修复完成后任务进入「待裁决」：改动在 `codebee/<task_id>` 分支上，
  到运行详情页人工合并或丢弃——自动化只干活，落不落盘由你拍板。
  配了推送通道的话，收尾摘要（含「⚠ 任务分支待裁决」）会自动推到
  你的手机。

## 其他触发面

- 定时巡检类任务走 CodeBee 自身的「自动化」页（cron 式调度，复用同一条
  运行链），不必经 GitHub。
- 评测台定时回归（`bench_auto_enabled`）也是同一 tick 驱动，可独立开关。
