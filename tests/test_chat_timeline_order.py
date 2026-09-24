# -*- coding: utf-8 -*-
"""直连对话时间线排序（2026-09-24「错位」修复）单元测试。

背景：指挥信箱的消息在运行中途到达时还是未消费态，旧排序把全部未消费
消息放在运行中回复卡之前——21:31 开跑的卡排在 21:42/21:43 追问后面，
被读成「回复串到了别人的问题」，且步骤收尾时消息整组跳位。修复后：
回复卡锚在开跑位置，中途追话沉底等下一轮；排队卡（无 started_at）拿
run 创建时刻当锚点，触发排队的那条仍在卡前（开跑消费后位置不动）。

覆盖：
  1) 运行中：已消费消息 → 运行卡 → 中途追话（待送达沉底）；
  2) 排队中：以 run 创建时刻为锚，早于锚点的未消费消息在卡前，晚于的沉底；
  3) 终态：全步骤完成时顺序与旧行为一致（已消费→完成卡→未消费），且
     跨 run 继承消息去重不受影响。
"""
from __future__ import annotations

from base import BaseTest


class _TimelineCase(BaseTest):
    """公共脚手架：种任务 + 按用例捏 run 状态，调 /timeline 拿 items。"""

    def _timeline(self, make_runs):
        import sys as _sys
        from pathlib import Path as _Path
        from app.core import store
        task = store.create_task({"type": "direct", "title": "时间线排序",
                                  "goal": "写完整小说", "workdir": str(self.workdir)})
        self.assertEqual(task.get("engine"), "direct")   # 直连任务才走任务级合并
        runs = [store.create_run("orchestration", task["title"], task_id=task["id"])
                for _ in make_runs]
        # _new_id 尾缀随机：同秒创建的多个 run 按时间戳排序不可控——重排 _RUNS
        # 键为递增 id，保证「旧→新」就是创建顺序（时间线按 id 倒序取 run）
        with store.LOCK:
            for i, run in enumerate(runs):
                store._RUNS.pop(run["id"], None)
                run["id"] = "r-20260924-210000-%04d" % (i + 1)
                store._RUNS[run["id"]] = run
        for fn, run in zip(make_runs, runs):
            fn(run)
        app_root = str(_Path(__file__).resolve().parents[1] / "app")
        if app_root not in _sys.path:
            _sys.path.insert(0, app_root)
        import main as main_mod
        orig_store = main_mod.store
        main_mod.store = store     # main 侧 import 的是 core.store（另一份模块对象）
        try:
            handler = main_mod.Handler.__new__(main_mod.Handler)
            holder = {}
            handler._json = lambda code, obj: holder.update(data=obj) or obj
            handler._api_run_timeline(runs[0]["id"])
        finally:
            main_mod.store = orig_store
        return (holder.get("data") or {}).get("items") or []

    @staticmethod
    def _msg(mid, text, at, consumed=False):
        return {"id": mid, "text": text, "sender": "Chrome·电脑",
                "attachments": [], "created_at": at, "consumed": consumed}

    @staticmethod
    def _step(status, started_at, ended_at=None, output=""):
        return {"n": 1, "role": "chat", "agent": "builtin", "agent_label": "CodeBee",
                "note": "对话续轮", "model": "glm-5.3-flash", "status": status,
                "started_at": started_at, "ended_at": ended_at, "output": output,
                "summary": "", "log": "steps/01-chat-builtin.log", "followups": [],
                "thinking": "思考", "stream": "", "activity": [], "live": 0}


class TestRunningCardAnchorsBeforeSteeringMsgs(_TimelineCase):
    def runTest(self):
        """运行中：21:31 开跑的卡不得排在 21:42/21:43 中途追话之后。"""
        def make(run):
            run["status"] = "running"
            run["messages"] = [
                self._msg("000001", "给我完整的", "21:31:26", consumed=True),
                self._msg("000002", "咋样了啊，卡住了吗", "21:42:20"),
                self._msg("000003", "我要的是大纲", "21:43:25"),
            ]
            run["steps"] = [self._step("running", "21:31:26")]
        items = self._timeline([make])
        seq = [(it["kind"], it.get("id") or (it.get("status") if it["kind"] == "agent" else "-"))
               for it in items]
        self.assertEqual(seq, [
            ("user", "-"),         # 任务目标开场
            ("user", "000001"),    # 驱动本轮的已消费消息 → 卡前
            ("agent", "running"),  # 回复卡锚在开跑位置 21:31:26
            ("user", "000002"),    # 中途追话（晚于开跑）沉底等下一轮
            ("user", "000003"),
        ])
        # 待送达标记原样下发（前端胶囊文案依赖 consumed=false）
        self.assertFalse(items[3]["consumed"])
        self.assertFalse(items[4]["consumed"])


class TestQueuedCardAnchorsRunCreation(_TimelineCase):
    def runTest(self):
        """排队卡（无 started_at）：锚 run 创建时刻——触发排队的那条在卡前，
        卡排队期间到达的追话沉底；开跑消费后消息位置不动、时间线不跳变。"""
        def make(run):
            run["status"] = "queued"
            run["created_at"] = "2026-09-24 21:42:21"
            run["messages"] = [
                self._msg("000001", "触发排队的问题", "21:42:20"),
                self._msg("000002", "排队期间又追问", "21:43:25"),
            ]
            run["steps"] = [self._step("queued", None)]
        items = self._timeline([make])
        seq = [(it["kind"], it.get("id") or (it.get("status") if it["kind"] == "agent" else "-"))
               for it in items]
        self.assertEqual(seq, [
            ("user", "-"),
            ("user", "000001"),    # 早于锚点 21:42:21 → 卡前
            ("agent", "queued"),
            ("user", "000002"),    # 晚于锚点 → 沉底
        ])


class TestTerminalOrderAndDedup(_TimelineCase):
    def runTest(self):
        """终态：已消费→完成卡→未消费（与旧行为一致，收尾不跳位）；跨 run
        继承的同一条消息只在原位显示一次。"""
        def make_first(run):
            run["status"] = "done"
            run["messages"] = [
                self._msg("000001", "第一问", "21:31:26", consumed=True),
                self._msg("000002", "运行末尾追话", "21:59:00"),
            ]
            run["steps"] = [self._step("done", "21:31:26", "21:58:00", output="第一答")]
        def make_second(run):
            run["status"] = "queued"
            run["created_at"] = "2026-09-24 21:59:30"
            run["messages"] = [   # 续跑 run 继承副本（同 id/时间/文本）+ 新追问
                self._msg("000001", "第一问", "21:31:26", consumed=True),
                self._msg("000002", "运行末尾追话", "21:59:00", consumed=True),
            ]
            run["steps"] = [self._step("queued", None)]
        items = self._timeline([make_first, make_second])
        seq = [(it["kind"], it.get("id") or (it.get("status") if it["kind"] == "agent" else "-"))
               for it in items]
        self.assertEqual(seq, [
            ("user", "-"),
            ("user", "000001"),    # 首个 run 内的原位
            ("agent", "done"),     # 第一轮完成卡
            ("user", "000002"),    # 继承副本在第二个 run 里被去重，不重复出现
            ("agent", "queued"),   # 续跑排队卡沉底
        ])
        self.assertEqual(sum(1 for it in items if it.get("id") == "000001"), 1)
