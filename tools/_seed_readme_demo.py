# -*- coding: utf-8 -*-
"""README 演示数据造数：TUTTI_DATA=<临时目录> 下跑，产出上相的任务/运行/步骤/
供应商/绑定/经验。只碰临时目录，绝不动真实 data/。"""
from __future__ import annotations
import json
import os
import sys
import time

assert os.environ.get("TUTTI_DATA"), "必须设置 TUTTI_DATA 指向临时目录"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from core import paths, store, skills  # noqa: E402
from pathlib import Path  # noqa: E402

paths.ensure_dirs()
WD = str(paths.DATA_DIR.parent / "workspace")
Path(WD).mkdir(parents=True, exist_ok=True)

# ---- 供应商与绑定（绑定页/模型页上相）----
models = {
    "providers": [
        {"id": "p-zai", "name": "智谱 Z.ai", "protocol": "openai",
         "base_url": "https://api.z.ai/api/paas/v4", "api_key": "demo-key",
         "enabled": True, "wire_api": "responses",
         "models": [
             {"name": "glm-4.7", "enabled": True, "image_in": True},
             {"name": "glm-4.7-air", "enabled": True},
             {"name": "glm-4.6", "enabled": True},
         ]},
        {"id": "p-ds", "name": "DeepSeek", "protocol": "openai",
         "base_url": "https://api.deepseek.com/v1", "api_key": "demo-key",
         "enabled": True, "wire_api": "chat",
         "models": [
             {"name": "deepseek-v4", "enabled": True},
             {"name": "deepseek-v4-flash", "enabled": True},
         ]},
    ],
    "bindings": {
        "codex-cli": {"models": ["glm-4.7", "deepseek-v4"],
                      "chain": [{"provider_id": "p-zai", "model": "glm-4.7"},
                                {"provider_id": "p-ds", "model": "deepseek-v4"}]},
        "claude-code": {"models": ["deepseek-v4"],
                        "chain": [{"provider_id": "p-ds", "model": "deepseek-v4"}]},
        "qwencode": {"models": ["glm-4.7-air"],
                     "chain": [{"provider_id": "p-zai", "model": "glm-4.7-air"}]},
    },
}
paths.CATALOG_FILE.parent.mkdir(parents=True, exist_ok=True)
(paths.DATA_DIR / "models.json").write_text(
    json.dumps(models, ensure_ascii=False, indent=2), encoding="utf-8")

# ---- 任务与运行 ----
now = time.strftime("%Y-%m-%d %H:%M:%S")


def step_done(run_id, role, agent, label, summary, log_text, tokens, cost, dur):
    st, log_abs = store.add_step(run_id, role, agent, label)
    if log_abs:
        log_abs.write_text(log_text, encoding="utf-8")
    store.finish_step(run_id, st["n"], "done", summary=summary, exit_code=0,
                      tokens=tokens, cost_usd=cost, duration_s=dur,
                      model="glm-4.7" if "zai" in agent else "deepseek-v4")


def step_running(run_id, role, agent, label, note=""):
    st, log_abs = store.add_step(run_id, role, agent, label, note=note)
    if log_abs:
        log_abs.write_text("（正在执行中…已输出 1,842 字）\n", encoding="utf-8")
    return st["n"]


# A) 连载任务：运行中（蜂巢有忙碌蜜蜂、步骤时间线丰富）
tA = store.create_task({"type": "serial_novel", "title": "连载《深海蜜途》",
                        "goal": "海洋题材连载小说，目标 20 万字，每章 6000 字，"
                                "评审不合格自动打回重写",
                        "workdir": WD,
                        "context": "文风：克制、有画面感；双主角：潜水教练与海洋生物学家。"})
rA = store.create_run("orchestration", "连载《深海蜜途》·续写", task_id=tA["id"])
store.update_run(rA["id"], status="running", started_at=now)
step_done(rA["id"], "plan", "codex", "章节规划",
          "已生成第 3-4 章大纲：风暴夜救场与灯塔下的基因样本；评审要点 3 条",
          "「第 3 章」风暴夜，潜水艇失联……\n「第 4 章」灯塔样本揭示发光生物群落…\n",
          1834, 0.0210, 41.2)
step_done(rA["id"], "write", "codex", "第 3 章草稿",
          "第 3 章《风暴夜》完稿 6,214 字，已落盘 chapters/03.md",
          "风暴在午夜登陆。林潮生把最后一根缆绳系上桩头时…（6,214 字）\n",
          9120, 0.1043, 212.8)
step_done(rA["id"], "review", "claude", "跨厂商评审（DeepSeek 评 GLM 稿）",
          "评审通过（8.5/10）：节奏张力足；建议收束第 3 章结尾悬念，已给 2 处行级批注",
          "总评：8.5/10\n1. 开场风暴描写有画面感，节奏控制得当…\n"
          "2. 结尾悬念收束略突兀，建议…（行级批注 ×2）\n",
          2415, 0.0131, 58.6)
step_done(rA["id"], "revise", "codex", "按评审修订",
          "采纳 2 条批注，第 3 章终稿 6,341 字",
          "修订完成：结尾改写 214 字…\n", 1302, 0.0155, 47.9)
step_done(rA["id"], "write", "codex", "第 4 章草稿",
          "第 4 章《灯塔样本》完稿 6,187 字，已落盘 chapters/04.md",
          "灯塔的光束扫过礁石，样本瓶里的发光生物……（6,187 字）\n",
          8860, 0.0996, 201.3)
step_done(rA["id"], "review", "claude", "跨厂商评审（DeepSeek 评 GLM 稿）",
          "评审通过（8.5/10）：双线汇合自然；建议压缩第 4 章中段对话",
          "总评：8.5/10\n", 2308, 0.0125, 55.0)
store.update_run(rA["id"], status="done", started_at=now, ended_at=now,
                 verdict={"ok": True, "score": 8.5},
                 summary="第 3-4 章完成并通过跨厂商评审（8.5/10），已合并全本草稿。",
                 cost_usd=0.2560, tokens=24959)
store.update_task_status(tA["id"], "done")

# B) 代码任务：已完成（有报告/裁决）
tB = store.create_task({"type": "code", "title": "重构导出模块",
                        "goal": "把 exporter.py 拆成流式写出器 + 格式适配器，补齐单元测试",
                        "workdir": WD})
rB = store.create_run("orchestration", "重构导出模块", task_id=tB["id"])
step_done(rB["id"], "plan", "codex", "方案拆解",
          "3 个子任务：抽出 StreamSink、实现 FormatAdapter、迁移调用点",
          "拆解：1) StreamSink 接口…\n", 962, 0.0098, 24.0)
step_done(rB["id"], "implement", "codex", "实现与迁移",
          "新增 sink.py / formats.py，exporter.py 缩减 62%，12 个单测全绿",
          "$ pytest -q\n............ 12 passed\n", 5240, 0.0611, 183.4)
step_done(rB["id"], "review", "claude", "跨厂商评审",
          "评审通过（9/10）：接口边界清晰；1 条非阻塞建议（适配器注册改装饰器）",
          "总评：9/10\n", 1104, 0.0062, 31.5)
store.update_run(rB["id"], status="done", started_at=now, ended_at=now,
                 verdict={"ok": True, "score": 9.0},
                 summary="重构完成：导出模块拆为流式写出器 + 格式适配器，12 个单测全绿。",
                 cost_usd=0.0771, tokens=7306)
store.finish_task(tB["id"]) if hasattr(store, "finish_task") else \
    store.update_task_status(tB["id"], "done") if hasattr(store, "update_task_status") else None

# C) 排队任务
tC = store.create_task({"type": "translation", "title": "文档中译英",
                        "goal": "README 与部署手册全文翻译，保持术语表一致", "workdir": WD})

# ---- 经验库 ----
lessons = [
    ("连载", "章稿超时先查盘", "起草步骤超时≠失败：先看 chapters/ 是否已落盘，落盘即送评审，不要盲目重跑。", "dim", "timeout"),
    ("编码", "评审必须跨厂商", "同族自评有同款盲区；评审者强制换模型族，无跨厂商时如实备注。", "quality", "review"),
    ("环境", "网关限流先退避", "Not Allowed/UnknownError 多为分钟级限流：退避重试同作者保风格一致，别急着换将。", "dim", "throttle"),
]
for scope, title, content, dim, cat in lessons:
    skills.upsert_lesson(scope, title, content, source="演示数据", dim=dim)

print("seeded:",
      "tasks=", len(store.list_tasks(50)),
      "runs=", len(store.list_runs(50)),
      "lessons=", len(skills.list_lessons()))
