# -*- coding: utf-8 -*-
"""automation（定时任务）单元测试：启动器一律打成 stub，绝不真调 LLM/CLI。"""
from __future__ import annotations

import json
from datetime import datetime

from base import BaseTest


def _dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


class AutomationCase(BaseTest):
    """公共脚手架：automation._FILE 重定向到本用例临时目录，启动器换成 stub。"""

    def setUp(self):
        super().setUp()
        from app.core import automation
        self.aut = automation
        self.aut._FILE = self.data_dir / "automation.json"
        with self.aut._LOCK:
            self.aut._TASKS.clear()
            self.aut._LOADED = False
        self.calls = []
        self._orig_launch = self.aut._launch_run
        self.launch_error = None

        def fake_launch(t):
            if self.launch_error is not None:
                raise self.launch_error
            self.calls.append({"name": t.get("name"), "prompt": t.get("prompt"),
                               "workdir": t.get("workdir")})
            return "r-fake-%d" % len(self.calls)
        self._fake_launch = fake_launch
        self.aut._launch_run = fake_launch
        self.addCleanup(self._restore)

    def _restore(self):
        self.aut._launch_run = self._orig_launch
        with self.aut._LOCK:
            self.aut._TASKS.clear()
            self.aut._LOADED = False

    def make(self, **kw):
        payload = {"name": "每日巡检", "prompt": "做一次巡检", "kind": "daily",
                   "time": "09:00", "workdir": str(self.workdir)}
        payload.update(kw)
        return self.aut.create(payload)

    def force_due(self, tid, past="2020-01-01 00:00:00"):
        """把任务 next_run 拨到过去，模拟「已到期」。"""
        with self.aut._LOCK:
            self.aut._TASKS[tid]["next_run"] = past


class TestCreatePersistReload(AutomationCase):
    def runTest(self):
        t = self.make()
        listed = self.aut.list_tasks()
        self.assertEqual([x["id"] for x in listed], [t["id"]])
        self.assertEqual(listed[0]["prompt"], "做一次巡检")
        # next_run 已算好：未来的 09:00 整
        nr = _dt(t["next_run"])
        self.assertGreater(nr, datetime.now())
        self.assertEqual(nr.strftime("%H:%M:%S"), "09:00:00")

        # 落盘：tmp+replace 后文件里有这条任务
        data = json.loads(self.aut._FILE.read_text(encoding="utf-8"))
        self.assertEqual([x["id"] for x in data["tasks"]], [t["id"]])

        # 重载：清空内存后从磁盘读回，字段一致
        with self.aut._LOCK:
            self.aut._TASKS.clear()
            self.aut._LOADED = False
        n = self.aut.load()
        self.assertEqual(n, 1)
        got = self.aut.get_task(t["id"])
        for k in ("id", "name", "prompt", "kind", "time", "enabled",
                  "created_at", "next_run", "run_count"):
            self.assertEqual(got[k], t[k], k)


class TestNextRunKinds(AutomationCase):
    """四种 kind 的 next_run 计算（固定 now，含跨天/跨周边界）。"""

    def runTest(self):
        c = self.aut.compute_next_run
        # daily：今天还没到 → 今天；正好到点/已过 → 明天（跨天）
        daily = {"kind": "daily", "time": "09:00"}
        self.assertEqual(c(daily, _dt("2026-09-15 08:00:00")), "2026-09-15 09:00:00")
        self.assertEqual(c(daily, _dt("2026-09-15 09:00:00")), "2026-09-16 09:00:00")
        self.assertEqual(c(daily, _dt("2026-09-15 23:59:59")), "2026-09-16 09:00:00")

        # weekly：2026-09-14 是周一（weekday=0）
        monday = _dt("2026-09-14 00:00:00")
        self.assertEqual(monday.weekday(), 0)
        weekly = {"kind": "weekly", "weekday": 0, "time": "09:00"}
        # 周一还没到点 → 本周一；正好到点/已过 → 下周一（跨周）
        self.assertEqual(c(weekly, _dt("2026-09-14 08:00:00")), "2026-09-14 09:00:00")
        self.assertEqual(c(weekly, _dt("2026-09-14 09:00:00")), "2026-09-21 09:00:00")
        # 周六才看 → 下周一
        self.assertEqual(c(weekly, _dt("2026-09-19 12:00:00")), "2026-09-21 09:00:00")
        # 目标周五（weekday=4）
        friday = {"kind": "weekly", "weekday": 4, "time": "18:00"}
        self.assertEqual(c(friday, _dt("2026-09-14 10:00:00")), "2026-09-18 18:00:00")

        # interval：从创建时间起算，整步推进到第一个未来时刻（跨天）
        iv = {"kind": "interval", "interval_hours": 6,
              "created_at": "2026-09-15 08:00:00", "last_run": ""}
        self.assertEqual(c(iv, _dt("2026-09-15 08:00:00")), "2026-09-15 14:00:00")
        self.assertEqual(c(iv, _dt("2026-09-15 21:00:00")), "2026-09-16 02:00:00")
        # 有 last_run 时以 last_run 为锚
        iv2 = dict(iv, last_run="2026-09-15 20:00:00")
        self.assertEqual(c(iv2, _dt("2026-09-15 21:00:00")), "2026-09-16 02:00:00")

        # once：即 run_at 本身（归一成 %Y-%m-%d %H:%M:%S）
        once = {"kind": "once", "run_at": "2026-10-01 09:30"}
        self.assertEqual(c(once, _dt("2026-09-15 10:00:00")), "2026-10-01 09:30:00")
        # 算不出的情况一律空串（tick 跳过，不会误触发）
        self.assertEqual(c({"kind": "daily", "time": "bad"}, _dt("2026-09-15 10:00:00")), "")
        self.assertEqual(c({"kind": "weird"}, _dt("2026-09-15 10:00:00")), "")


class TestValidation(AutomationCase):
    def runTest(self):
        wd = str(self.workdir)
        bad = [
            {"kind": "hourly", "time": "09:00"},                 # 非法 kind
            {"kind": "daily"},                                   # 缺 time
            {"kind": "daily", "time": "9点"},                    # time 格式
            {"kind": "daily", "time": "24:00"},                  # time 越界
            {"kind": "weekly", "time": "09:00", "weekday": 7},   # weekday 越界
            {"kind": "interval", "interval_hours": 0},           # 间隔下限
            {"kind": "interval", "interval_hours": 9999},        # 间隔上限
            {"kind": "once", "run_at": "2020-01-01 08:00:00"},   # 过去时间不补跑
            {"kind": "once", "run_at": "不是时间"},               # run_at 格式
            {"kind": "daily", "time": "09:00", "prompt": ""},    # 空 prompt
            {"kind": "daily", "time": "09:00", "name": "  "},    # 空 name
            {"kind": "daily", "time": "09:00", "flow": "nope"},  # 未知流程
            {"kind": "daily", "time": "09:00", "workdir": "relative/path"},
            {"kind": "daily", "time": "09:00", "workdir": str(self.tmp / "nope")},
        ]
        for kw in bad:
            payload = {"name": "x", "prompt": "p", "workdir": wd}
            payload.update(kw)
            with self.assertRaises(ValueError, msg=repr(kw)):
                self.aut.create(payload)
        self.assertEqual(self.aut.list_tasks(), [])
        # update 同样把关：改坏字段被拒且原任务毫发无损
        t = self.make()
        with self.assertRaises(ValueError):
            self.aut.update(t["id"], {"time": "25:00"})
        self.assertEqual(self.aut.get_task(t["id"])["time"], "09:00")
        self.assertIsNone(self.aut.update("auto-nope", {"name": "y"}))


class TestDueTrigger(AutomationCase):
    def runTest(self):
        t = self.make()
        self.force_due(t["id"])
        self.aut._tick()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["prompt"], "做一次巡检")
        self.assertEqual(self.calls[0]["workdir"], str(self.workdir))
        cur = self.aut.get_task(t["id"])
        self.assertEqual(cur["run_count"], 1)
        self.assertEqual(cur["last_status"], "started")
        self.assertTrue(cur["last_run"])
        # 触发后 next_run 推进到未来，立刻再 tick 不会重复触发
        self.assertGreater(_dt(cur["next_run"]), datetime.now())
        self.aut._tick()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(cur["run_count"], 1)


class TestOnceFiresThenDisables(AutomationCase):
    def runTest(self):
        t = self.make(kind="once", run_at="2099-01-01 08:00:00")
        self.force_due(t["id"], past="2026-01-01 08:00:00")   # 模拟时间已走过到期点
        self.aut._tick()
        self.assertEqual(len(self.calls), 1)
        cur = self.aut.get_task(t["id"])
        self.assertFalse(cur["enabled"])       # once 触发完自动停用
        self.assertEqual(cur["next_run"], "")
        self.assertEqual(cur["run_count"], 1)
        self.aut._tick()                        # 已停用：不会再触发
        self.assertEqual(len(self.calls), 1)


class TestLaunchFailureKeepsAlive(AutomationCase):
    def runTest(self):
        t = self.make()
        self.launch_error = ValueError("工作目录不存在")
        self.force_due(t["id"])
        self.aut._tick()                        # 拉起失败：异常被吞，调度继续
        cur = self.aut.get_task(t["id"])
        self.assertEqual(cur["last_status"], "error")
        self.assertEqual(cur["run_count"], 1)
        self.assertGreater(_dt(cur["next_run"]), datetime.now())
        self.assertEqual(self.calls, [])
        # 修好后下个周期恢复正常（调度线程没死）
        self.launch_error = None
        self.force_due(t["id"])
        self.aut._tick()
        cur = self.aut.get_task(t["id"])
        self.assertEqual(cur["last_status"], "started")
        self.assertEqual(len(self.calls), 1)


class TestDisabledNoTrigger(AutomationCase):
    def runTest(self):
        t = self.make()
        self.assertIs(self.aut.set_enabled(t["id"], False)["enabled"], False)
        self.assertEqual(self.aut.get_task(t["id"])["next_run"], "")
        self.force_due(t["id"])
        self.aut._tick()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.aut.get_task(t["id"])["run_count"], 0)
        # 重新启用：next_run 重算到未来
        cur = self.aut.set_enabled(t["id"], True)
        self.assertTrue(cur["enabled"])
        self.assertGreater(_dt(cur["next_run"]), datetime.now())


class TestRunNow(AutomationCase):
    def runTest(self):
        t = self.make()
        before = self.aut.get_task(t["id"])["next_run"]
        cur, run_id = self.aut.run_now(t["id"])
        self.assertEqual(run_id, "r-fake-1")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(cur["run_count"], 1)
        self.assertEqual(cur["last_status"], "started")
        self.assertTrue(cur["last_run"])
        self.assertEqual(cur["next_run"], before)   # run_now 不影响计划
        self.assertGreater(_dt(before), datetime.now())
        # 不存在的任务
        self.assertEqual(self.aut.run_now("auto-nope"), (None, None))
        # 拉起失败：记 error，不影响计划
        self.launch_error = RuntimeError("boom")
        cur, run_id = self.aut.run_now(t["id"])
        self.assertEqual(run_id, "")
        self.assertEqual(cur["last_status"], "error")
        self.assertEqual(cur["next_run"], before)


class TestDelete(AutomationCase):
    def runTest(self):
        t = self.make()
        self.assertTrue(self.aut.delete(t["id"]))
        self.assertIsNone(self.aut.get_task(t["id"]))
        self.assertFalse(self.aut.delete(t["id"]))          # 重复删除
        self.assertFalse(self.aut.delete("auto-nope"))      # 不存在
        # 已落盘删除：重载后仍不在
        with self.aut._LOCK:
            self.aut._TASKS.clear()
            self.aut._LOADED = False
        self.aut.load()
        self.assertIsNone(self.aut.get_task(t["id"]))
        self.assertEqual(self.aut.list_tasks(), [])


class TestUpdateRecompute(AutomationCase):
    def runTest(self):
        t = self.make()
        up = self.aut.update(t["id"], {"time": "23:30", "name": "夜巡"})
        self.assertEqual(up["name"], "夜巡")
        self.assertTrue(up["next_run"].endswith("23:30:00"))
        # 改 kind 需要配套字段：weekly 缺 weekday 被拒；带齐后 next_run 落在目标日
        with self.assertRaises(ValueError):
            self.aut.update(t["id"], {"kind": "weekly"})
        up2 = self.aut.update(t["id"], {"kind": "weekly", "weekday": 0, "time": "08:15"})
        self.assertEqual(up2["kind"], "weekly")
        self.assertEqual(up2["weekday"], 0)
        self.assertTrue(up2["next_run"].endswith("08:15:00"))
        nr2 = _dt(up2["next_run"])
        self.assertEqual(nr2.weekday(), 0)   # 落在周一
        self.assertGreater(nr2, datetime.now())
        # interval 改法
        up3 = self.aut.update(t["id"], {"kind": "interval", "interval_hours": 3})
        self.assertEqual(up3["kind"], "interval")
        self.assertGreater(_dt(up3["next_run"]), datetime.now())


class TestRestartRecovery(AutomationCase):
    def runTest(self):
        # once 已到期但服务一直没开：重启对账必须停用且不补跑
        once = self.make(kind="once", name="一次性", run_at="2099-01-01 08:00:00")
        daily = self.make(name="每日", time="23:50")
        with self.aut._LOCK:
            self.aut._TASKS[once["id"]]["next_run"] = "2026-01-01 08:00:00"
            self.aut._TASKS[daily["id"]]["next_run"] = "2020-01-01 23:50:00"
            self.aut._save_locked()
        # 模拟重启：清内存 → load → 恢复对账
        with self.aut._LOCK:
            self.aut._TASKS.clear()
            self.aut._LOADED = False
        self.aut.load()
        n = self.aut._recover_after_restart()
        self.assertEqual(n, 2)
        self.aut._tick()   # 对账后没有任何到期任务（尤其不会补跑 once）
        self.assertEqual(self.calls, [])
        o = self.aut.get_task(once["id"])
        self.assertFalse(o["enabled"])
        self.assertEqual(o["next_run"], "")
        self.assertEqual(o["last_status"], "missed")
        d = self.aut.get_task(daily["id"])
        self.assertTrue(d["enabled"])
        self.assertTrue(d["next_run"].endswith("23:50:00"))
        self.assertGreater(_dt(d["next_run"]), datetime.now())


class TestRunPrefs(AutomationCase):
    """运行偏好（编排模式/思考程度/对话模型）：与 Composer 同字段，创建/编辑可改。"""

    def runTest(self):
        # 默认值：不传就是 Composer 的默认档，历史任务加载后同样落这两档
        t0 = self.make(name="默认档")
        self.assertEqual(t0["mode"], "auto")
        self.assertEqual(t0["thinking"], "standard")
        self.assertEqual(t0["direct_provider_id"], "")
        self.assertEqual(t0["direct_model"], "")

        # 显式选择：原样落盘（不是只在内存里）
        t = self.make(name="专家档", mode="expert", thinking="high")
        self.assertEqual(t["mode"], "expert")
        self.assertEqual(t["thinking"], "high")
        with self.aut._LOCK:
            self.aut._TASKS.clear()
            self.aut._LOADED = False
        self.aut.load()
        self.assertEqual(self.aut.get_task(t["id"])["mode"], "expert")
        self.assertEqual(self.aut.get_task(t["id"])["thinking"], "high")

        # 脏值收敛到默认，不抛异常：偏好不是配置，不该把保存卡死
        t1 = self.make(name="脏值", mode="bogus", thinking="deep")
        self.assertEqual(t1["mode"], "auto")
        self.assertEqual(t1["thinking"], "standard")

        # 非 direct 流程：模型字段一律清空（换流程后残留绑定不得静默生效）
        t2 = self.make(name="非直连", flow="doc",
                       direct_provider_id="prov-x", direct_model="model-y")
        self.assertEqual(t2["direct_provider_id"], "")
        self.assertEqual(t2["direct_model"], "")

        # direct 流程：保留；只有模型没有厂商 → 明确报错（不许猜厂商）
        t3 = self.make(name="直连", flow="direct",
                       direct_provider_id="prov-x", direct_model="model-y")
        self.assertEqual(t3["direct_provider_id"], "prov-x")
        self.assertEqual(t3["direct_model"], "model-y")
        with self.assertRaises(ValueError):
            self.make(name="悬空模型", flow="direct", direct_model="model-y")

        # 编辑：偏好可改；把流程从 direct 换成 review 时残留模型被清掉
        up = self.aut.update(t3["id"], {"mode": "fast", "thinking": "low"})
        self.assertEqual(up["mode"], "fast")
        self.assertEqual(up["thinking"], "low")
        up2 = self.aut.update(t3["id"], {"flow": "doc"})
        self.assertEqual(up2["direct_provider_id"], "")
        self.assertEqual(up2["direct_model"], "")
        # 未触及偏好的编辑不动它们
        up3 = self.aut.update(t3["id"], {"name": "改个名"})
        self.assertEqual(up3["mode"], "fast")
        self.assertEqual(up3["thinking"], "low")


class TestRunPrefsThroughLaunchChain(AutomationCase):
    """偏好要真的走到运行里：走真实 _launch_run，只 stub jobs.enqueue。"""

    def runTest(self):
        from app.core import jobs as jobs_mod
        from app.core import store as store_mod
        enqueued = []
        orig_enqueue = jobs_mod.enqueue
        jobs_mod.enqueue = lambda job: enqueued.append(job)
        self.aut._launch_run = self._orig_launch   # 换回真实启动链
        try:
            t = self.make(name="专家巡检", kind="daily", time="09:00",
                          mode="expert", thinking="high")
            self.aut.run_now(t["id"])
            task = store_mod.get_task(enqueued[0]["task_id"])
            self.assertEqual(task["mode"], "expert")
            self.assertEqual(task["thinking"], "high")

            # 直连流程：厂商/模型随 payload 透传进任务（与手动建任务等价）
            t2 = self.make(name="直连巡检", kind="daily", time="09:00", flow="direct",
                           direct_provider_id="prov-x", direct_model="model-y")
            self.aut.run_now(t2["id"])
            task2 = store_mod.get_task(enqueued[1]["task_id"])
            self.assertEqual(task2["type"], "direct")
            self.assertEqual(task2["direct_provider_id"], "prov-x")
            self.assertEqual(task2["direct_model"], "model-y")

            # 非直连流程：不带 direct_* 进 payload（create_task 里那两个键只认 direct）
            t3 = self.make(name="文档巡检", kind="daily", time="09:00", flow="doc")
            self.aut.run_now(t3["id"])
            task3 = store_mod.get_task(enqueued[2]["task_id"])
            self.assertEqual(task3["type"], "doc")
            self.assertNotIn("direct_provider_id", task3)
        finally:
            jobs_mod.enqueue = orig_enqueue
            self.aut._launch_run = self._fake_launch


class TestTemplates(AutomationCase):
    def runTest(self):
        ts = self.aut.templates()
        self.assertEqual(len(ts), 4)
        ids = set()
        for tp in ts:
            for k in ("id", "name", "desc", "prompt", "suggested_kind"):
                self.assertTrue(tp.get(k), "%s 缺 %s" % (tp.get("id"), k))
            self.assertIn(tp["suggested_kind"], self.aut.KINDS)
            self.assertGreater(len(tp["prompt"]), 100)   # 真材实料的提示词，不是占位符
            ids.add(tp["id"])
        self.assertEqual(len(ids), 4)
        # templates 返回副本：改返回值不影响内置模板
        ts[0]["prompt"] = "tampered"
        self.assertNotEqual(self.aut.templates()[0]["prompt"], "tampered")


class TestRealLaunchChain(AutomationCase):
    """_launch_run 与 store 的真实对接：只 stub jobs.enqueue，不碰 LLM/CLI。"""

    def runTest(self):
        from app.core import jobs as jobs_mod
        enqueued = []
        orig_enqueue = jobs_mod.enqueue

        def fake_enqueue(job):
            enqueued.append(job)
        jobs_mod.enqueue = fake_enqueue
        self.aut._launch_run = self._orig_launch   # 换回真实启动链
        try:
            t = self.make(name="每日巡检", kind="daily", time="09:00")  # flow 缺省
            self.force_due(t["id"])
            self.aut._tick()

            self.assertEqual(len(enqueued), 1)
            job = enqueued[0]
            self.assertEqual(job["kind"], "orchestration")
            from app.core import store as store_mod
            task = store_mod.get_task(job["task_id"])
            self.assertIsNotNone(task)
            self.assertEqual(task["type"], "doc")          # flow 缺省兜底
            self.assertEqual(task["goal"], "做一次巡检")
            self.assertEqual(task["workdir"], str(self.workdir))
            self.assertTrue(task["title"].startswith("每日巡检 "))  # 标题 = 名称 + 日期时间
            self.assertEqual(task["status"], "queued")
            run = store_mod.get_run(job["run_id"])
            self.assertIsNotNone(run)
            self.assertEqual(run["task_id"], task["id"])

            # 显式 flow + 空 workdir：跟随默认保存路径（settings 兜底，自动创建）
            t2 = self.make(name="周报", flow="code", workdir="")
            cur, _rid = self.aut.run_now(t2["id"])
            self.assertEqual(cur["last_status"], "started")
            self.assertEqual(len(enqueued), 2)
            task2 = store_mod.get_task(enqueued[1]["task_id"])
            self.assertEqual(task2["type"], "code")
            self.assertTrue(task2["workdir"])               # 非空：默认路径已落
        finally:
            jobs_mod.enqueue = orig_enqueue
            self.aut._launch_run = self._fake_launch


class TestRealLaunchFailureClosesRecords(AutomationCase):
    """真实定时启动链在入队失败时不能留下 queued 孤儿记录。"""

    def runTest(self):
        from app.core import jobs as jobs_mod
        from app.core import store as store_mod

        orig_enqueue = jobs_mod.enqueue
        self.aut._launch_run = self._orig_launch

        def fail_enqueue(_job):
            raise RuntimeError("worker queue unavailable")

        jobs_mod.enqueue = fail_enqueue
        try:
            task = self.make(name="入队失败", kind="daily", time="09:00")
            with self.assertRaises(RuntimeError):
                self.aut._launch_run(task)
        finally:
            jobs_mod.enqueue = orig_enqueue
            self.aut._launch_run = self._fake_launch

        created = store_mod.list_tasks(20)
        self.assertTrue(created)
        persisted_task = next(t for t in created if t["title"].startswith("入队失败 "))
        self.assertEqual(persisted_task["status"], "failed")
        run = store_mod.latest_run_by_task()[persisted_task["id"]]
        self.assertEqual(run["status"], "failed")
        self.assertNotIn("worker queue unavailable", run.get("error", ""))


if __name__ == "__main__":
    import unittest as _u
    _u.main()
