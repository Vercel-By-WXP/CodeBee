# -*- coding: utf-8 -*-
"""自动发布单测：ledger 护栏统计 / auto.guards 护栏 / auto.pending 待发枚举 /
publish_pending_async 顺序发布（manager 全打桩，不碰浏览器）。

不碰真实 data/（TUTTI_DATA 抢设教训见 test_bookmeta.py：全量套跑时会被
别的测试模块抢设，落盘一律走本模块 TUTTI_DATA 指到的临时目录）。"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("TUTTI_DATA", tempfile.mkdtemp(prefix="tutti-pa-"))
sys.path.insert(0, str(ROOT / "app"))

from core import settings, store  # noqa: E402
from core import paths  # noqa: E402
from core.publish import auto, ledger  # noqa: E402

WD = Path(tempfile.mkdtemp(prefix="tutti-pa-wd-"))
TODAY = time.strftime("%Y-%m-%d")
THIS_MONTH = TODAY[:7].replace("-", "")


def _seed(records):
    """手写台账行（不用 ledger.record：要精确控制 day/顺序/跨天）。"""
    paths.PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
    fp = paths.PUBLISH_DIR / ("publish-%s.jsonl" % THIS_MONTH)
    with open(fp, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _rec(action, ok=True, ch=0, task="t1", plat="fanqie", day=TODAY):
    return {"ts": "2026-09-18 00:00:00", "day": day, "platform": plat,
            "action": action, "task_id": task, "chapter_no": ch,
            "book_id": "", "title": "", "ok": ok, "error": "", "shot": ""}


def _reset_ledger():
    """台账/护栏配置按月文件+settings 落盘持久——逐例清空防串扰
    （连败口径跨任务，前一个用例的失败会拦住后一个用例）。"""
    for fp in paths.PUBLISH_DIR.glob("publish-*.jsonl"):
        try:
            fp.unlink()
        except OSError:
            pass
    settings.save({"publish_daily_cap": 10, "publish_fail_streak": 3})


class TestLedgerGuards(unittest.TestCase):
    def setUp(self):
        _reset_ledger()

    def test_today_count_only_today_ok_same_book(self):
        _seed([
            _rec("upload_chapter", ok=True, ch=1),
            _rec("upload_chapter", ok=True, ch=2),
            _rec("upload_chapter", ok=False, ch=3),          # 失败不计
            _rec("upload_chapter", ok=True, ch=9, day="2026-01-01"),  # 跨月旧账
            _rec("upload_chapter", ok=True, ch=4, task="t2"),          # 别的任务
            _rec("upload_chapter", ok=True, ch=5, plat="qimao"),       # 别的平台
            _rec("connect", ok=True),                                  # 非发章动作
        ])
        self.assertEqual(ledger.today_count("t1", "fanqie"), 2)

    def test_consecutive_failures_stops_at_success(self):
        _seed([                                   # 时间序旧→新
            _rec("upload_chapter", ok=False),
            _rec("upload_chapter", ok=True),
            _rec("upload_chapter", ok=False, task="t9"),
            _rec("create_book", ok=False),
        ])
        self.assertEqual(ledger.consecutive_failures("fanqie"), 2)
        self.assertEqual(ledger.consecutive_failures("qimao"), 0)


class TestGuards(unittest.TestCase):
    def setUp(self):
        _reset_ledger()
        settings.save({"publish_daily_cap": 2, "publish_fail_streak": 3})

    def test_daily_cap_blocks(self):
        _seed([_rec("upload_chapter", ch=1), _rec("upload_chapter", ch=2)])
        ok, why = auto.guards("t1", "fanqie")
        self.assertFalse(ok)
        self.assertIn("上限", why)

    def test_fail_streak_blocks(self):
        _seed([_rec("upload_chapter", ok=False) for _ in range(3)])
        ok, why = auto.guards("t1", "fanqie")
        self.assertFalse(ok)
        self.assertIn("连续", why)

    def test_clear_passes(self):
        _seed([_rec("upload_chapter", ch=1)])
        ok, why = auto.guards("t1", "fanqie")
        self.assertTrue(ok)
        self.assertEqual(why, "")


class TestPending(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.task = store.create_task({"type": "direct", "goal": "写一本测试书",
                                      "workdir": str(WD)})
        store.create_run("orchestration", "跑一跑", task_id=cls.task["id"])
        (WD / "第1章 风起.md").write_text("# 第1章 风起\n正文", encoding="utf-8")
        (WD / "第2章 暗涌.md").write_text("# 第2章 暗涌\n正文", encoding="utf-8")
        (WD / "manuscript").mkdir(exist_ok=True)
        (WD / "manuscript" / "第3章 破局.md").write_text("正文", encoding="utf-8")
        (WD / "大纲.md").write_text("不是章节", encoding="utf-8")
        (WD / "第1章 重复.md").write_text("同章号另一文件", encoding="utf-8")

    def test_pending_sorted_and_filtered(self):
        _reset_ledger()
        pend, err = auto.pending(self.task["id"], "fanqie")
        self.assertEqual(err, "")
        self.assertEqual([p["chapter_no"] for p in pend], [1, 2, 3])
        # 子目录章节用相对路径；同章号去重只留一个
        self.assertTrue(any(p["file"].startswith("manuscript/") for p in pend))
        self.assertEqual(sum(1 for p in pend if p["chapter_no"] == 1), 1)

    def test_pending_excludes_published(self):
        _reset_ledger()
        _seed([_rec("upload_chapter", ch=1, task=self.task["id"])])
        pend, _ = auto.pending(self.task["id"], "fanqie")
        self.assertEqual([p["chapter_no"] for p in pend], [2, 3])

    def test_pending_bad_task(self):
        pend, err = auto.pending("t-nope", "fanqie")
        self.assertEqual(pend, [])
        self.assertNotEqual(err, "")


class _FakeUpload:
    """manager.upload_chapter_async 桩：按文件名解析章号记台账。

    fail_at={章号: 原因} 控制某章失败；slow_s 模拟长动作测单飞与等待。"""

    def __init__(self, fail_at=None, slow_s=0.0):
        self.calls = []
        self.fail_at = fail_at or {}
        self.slow_s = slow_s

    def __call__(self, task_id, plat, fp, auto_submit=False):
        self.calls.append(fp)
        if self.slow_s:
            time.sleep(self.slow_s)
        n = ledger.parse_chapter_no(Path(fp).name)
        if n in self.fail_at:
            ledger.record(plat, "upload_chapter", task_id=task_id, chapter_no=n,
                          ok=False, error=self.fail_at[n])
            return True, ""
        ledger.record(plat, "upload_chapter", task_id=task_id, chapter_no=n, ok=True)
        return True, ""


class TestPublishPending(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 独立工作目录：与他类共享目录时，任务创建与文件 mtime 只差秒级
        # 粒度，别的类的章节文件会漏进本任务的成品口径（同章号去重吃错文件）
        cls.wd = Path(tempfile.mkdtemp(prefix="tutti-pa-wd2-"))
        cls.task = store.create_task({"type": "direct", "goal": "自动发布测试",
                                      "workdir": str(cls.wd)})
        store.create_run("orchestration", "跑", task_id=cls.task["id"])
        for i in (1, 2, 3):
            (cls.wd / ("第%d章.md" % i)).write_text("# 第%d章\n正文" % i,
                                                    encoding="utf-8")
        ledger.save_book(cls.task["id"], "fanqie",
                         {"book_id": "", "title": "测试书"})

    def setUp(self):
        _reset_ledger()
        from core.publish import manager
        self.manager = manager
        auto.PACE_S = 0
        auto.IDLE_POLL_S = 0.05

    def _wait_status(self, task_id, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = auto._running.get(task_id) or {}
            if st.get("status") in ("done", "error", "manual_pause"):
                return dict(st)
            time.sleep(0.05)
        return dict(auto._running.get(task_id) or {})

    def _calibrate(self, plat="fanqie"):
        """造 flows-<plat>.json 校准文件（auto_submit 直发的闸）。"""
        import json as _json
        paths.PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
        fp = paths.PUBLISH_DIR / ("flows-%s.json" % plat)
        fp.write_text(_json.dumps({"upload_chapter": []}), encoding="utf-8")
        self.addCleanup(lambda: fp.unlink(missing_ok=True))

    def test_auto_submit_requires_calibration(self):
        # 未校准（无 flows-fanqie.json）不许无人值守直发
        ok, err = auto.publish_pending_async(self.task["id"], "fanqie",
                                             auto_submit=True)
        self.assertFalse(ok)
        self.assertIn("校准", err)
        self._calibrate()
        orig = self.manager.upload_chapter_async
        self.manager.upload_chapter_async = _FakeUpload()
        try:
            ok, err = auto.publish_pending_async(self.task["id"], "fanqie",
                                                 auto_submit=True)
            self.assertTrue(ok, err)
            st = self._wait_status(self.task["id"])
            self.assertEqual(st.get("status"), "done")
            self.assertEqual(st.get("done"), 3)
        finally:
            self.manager.upload_chapter_async = orig

    def test_manual_mode_fills_one_then_pauses(self):
        # 人工确认模式：填好一章停在 manual_pause，等用户浏览器提交后再发起
        # （连发第二章会导航离开未提交的编辑器，把上一章内容丢掉）
        fake = _FakeUpload()
        orig = self.manager.upload_chapter_async
        self.manager.upload_chapter_async = fake
        try:
            ok, err = auto.publish_pending_async(self.task["id"], "fanqie")
            self.assertTrue(ok, err)
            st = self._wait_status(self.task["id"])
            self.assertEqual(st.get("status"), "manual_pause")
            self.assertEqual(st.get("done"), 1)
            self.assertEqual(len(fake.calls), 1)
            self.assertIn("提交", st.get("message") or "")
            # 再发起一轮 → 发下一章（幂等台账已记第 1 章）
            ok, _ = auto.publish_pending_async(self.task["id"], "fanqie")
            self.assertTrue(ok)
            st = self._wait_status(self.task["id"])
            self.assertEqual(st.get("status"), "manual_pause")
            self.assertEqual(st.get("done"), 1)
            self.assertEqual(len(fake.calls), 2)
        finally:
            self.manager.upload_chapter_async = orig

    def test_walks_all_chapters_in_order(self):
        self._calibrate()                 # 直发模式的前置：流程已校准
        fake = _FakeUpload()
        orig = self.manager.upload_chapter_async
        self.manager.upload_chapter_async = fake
        try:
            ok, err = auto.publish_pending_async(self.task["id"], "fanqie",
                                                 auto_submit=True)
            self.assertTrue(ok, err)
            st = self._wait_status(self.task["id"])
            self.assertEqual(st.get("status"), "done")
            self.assertEqual(st.get("done"), 3)
            self.assertEqual(len(fake.calls), 3)
            # 按章号语义断言顺序（文件名尾缀在不同夹具间不稳定）
            nos = [ledger.parse_chapter_no(Path(c).name) for c in fake.calls]
            self.assertEqual(nos, [1, 2, 3])
            for c in fake.calls:
                self.assertTrue(Path(c).is_file())
        finally:
            self.manager.upload_chapter_async = orig

    def test_stops_on_chapter_failure(self):
        # 失败即停属直发模式的语义（人工模式一章一停，走不到第二章）
        self._calibrate()
        fake = _FakeUpload(fail_at={2: "平台报错"})
        orig = self.manager.upload_chapter_async
        self.manager.upload_chapter_async = fake
        try:
            ok, _ = auto.publish_pending_async(self.task["id"], "fanqie",
                                               auto_submit=True)
            self.assertTrue(ok)
            st = self._wait_status(self.task["id"])
            self.assertEqual(st.get("status"), "error")
            self.assertIn("第 2 章", st.get("error") or "")
            self.assertEqual(st.get("done"), 1)      # 第 1 章已发，后续未发
            self.assertEqual(len(fake.calls), 2)
        finally:
            self.manager.upload_chapter_async = orig

    def test_single_flight_while_running(self):
        fake = _FakeUpload(slow_s=0.4)
        orig = self.manager.upload_chapter_async
        self.manager.upload_chapter_async = fake
        try:
            ok, _ = auto.publish_pending_async(self.task["id"], "fanqie")
            self.assertTrue(ok)
            ok2, err2 = auto.publish_pending_async(self.task["id"], "fanqie")
            self.assertFalse(ok2)
            self.assertIn("进行中", err2)
            st = self._wait_status(self.task["id"], timeout=15)
            # 人工模式一章一停：首轮以 manual_pause 收场（单飞闸已验）
            self.assertEqual(st.get("status"), "manual_pause")
            self.assertEqual(st.get("done"), 1)
        finally:
            self.manager.upload_chapter_async = orig

    def test_guard_blocks_before_start(self):
        settings.save({"publish_daily_cap": 1})
        _seed([_rec("upload_chapter", ch=9, task=self.task["id"])])
        try:
            ok, err = auto.publish_pending_async(self.task["id"], "fanqie")
            self.assertFalse(ok)
            self.assertIn("上限", err)
        finally:
            settings.save({"publish_daily_cap": 10})

    def test_requires_book_binding(self):
        # 真任务但没建过书（假任务 id 会先撞「任务不存在」，测不到建书闸）
        t = store.create_task({"type": "direct", "goal": "未建书任务",
                               "workdir": str(self.wd)})
        ok, err = auto.publish_pending_async(t["id"], "fanqie")
        self.assertFalse(ok)
        self.assertIn("建书", err)

    def test_no_pending_rejects(self):
        t = store.create_task({"type": "direct", "goal": "没有章节的任务",
                               "workdir": str(WD)})
        ledger.save_book(t["id"], "fanqie", {"title": "空书"})
        ok, err = auto.publish_pending_async(t["id"], "fanqie")
        self.assertFalse(ok)
        self.assertIn("待发", err)


class TestAutoPublishDue(unittest.TestCase):
    """P2.5 定时联动：norm 校验 / due_tasks 条件 / fire_due 触发幂等。"""

    def setUp(self):
        _reset_ledger()
        auto._AP_FIRED = None            # 清「今日已触发」缓存
        try:
            (paths.PUBLISH_DIR / "auto_publish.json").unlink()
        except OSError:
            pass
        self.task = store.create_task({"type": "direct", "goal": "定时发布任务",
                                       "workdir": str(WD)})
        ledger.save_book(self.task["id"], "fanqie", {"title": "定时书"})
        self.calls = []
        self._orig = auto.publish_pending_async
        auto.publish_pending_async = \
            lambda tid, plat, auto_submit=False: (self.calls.append(tid) or (True, ""))

    def tearDown(self):
        auto.publish_pending_async = self._orig
        auto._AP_FIRED = None

    def test_norm_validates(self):
        ap, err = auto.norm_auto_publish({"platform": "fanqie", "time": "9:05"})
        self.assertTrue(ap and ap["time"] == "09:05" and ap["enabled"] is False)
        self.assertEqual(auto.norm_auto_publish({"platform": "xx", "time": "09:00"})[1],
                         "platform 必须是 fanqie 或 qimao")
        self.assertIn("超出", auto.norm_auto_publish({"platform": "fanqie", "time": "25:00"})[1])

    def test_due_requires_time_passed_book_and_not_fired(self):
        store.set_auto_publish(self.task["id"],
                               {"enabled": True, "platform": "fanqie", "time": "00:01"})
        late = time.strptime(TODAY + " 23:59", "%Y-%m-%d %H:%M")
        self.assertEqual(len(auto.due_tasks(now=late)), 1)
        # 未到点不触发
        early = time.strptime(TODAY + " 00:00", "%Y-%m-%d %H:%M")
        self.assertEqual(auto.due_tasks(now=early), [])
        # 今日已触发过 → 不再 due
        auto._mark_fired(self.task["id"], TODAY)
        self.assertEqual(auto.due_tasks(now=late), [])
        # enabled=false 不 due
        auto._AP_FIRED = None
        store.set_auto_publish(self.task["id"],
                               {"enabled": False, "platform": "fanqie", "time": "00:01"})
        self.assertEqual(auto.due_tasks(now=late), [])

    def test_due_skips_unbound_platform(self):
        # 没建过书的平台不触发（触发也只会被拒，还烧掉当日额度）
        store.set_auto_publish(self.task["id"],
                               {"enabled": True, "platform": "qimao", "time": "00:01"})
        late = time.strptime(TODAY + " 23:59", "%Y-%m-%d %H:%M")
        self.assertEqual(auto.due_tasks(now=late), [])

    def test_fire_due_once_then_idempotent(self):
        store.set_auto_publish(self.task["id"],
                               {"enabled": True, "platform": "fanqie", "time": "00:01"})
        late = time.strptime(TODAY + " 23:59", "%Y-%m-%d %H:%M")
        self.assertEqual(auto.fire_due(now=late), 1)
        self.assertEqual(self.calls, [self.task["id"]])
        # 同日第二次 tick：已 fired，不再触发（连败护栏场景不被反复撞）
        self.assertEqual(auto.fire_due(now=late), 0)
        self.assertEqual(self.calls, [self.task["id"]])

    def test_fire_due_records_failure_not_refire(self):
        auto.publish_pending_async = lambda tid, plat, auto_submit=False: (False, "护栏拦截")
        store.set_auto_publish(self.task["id"],
                               {"enabled": True, "platform": "fanqie", "time": "00:01"})
        late = time.strptime(TODAY + " 23:59", "%Y-%m-%d %H:%M")
        self.assertEqual(auto.fire_due(now=late), 0)     # 触发失败不计成功
        self.assertEqual(auto.fire_due(now=late), 0)     # 且当日不再重试
        recs = [r for r in ledger.recent(task_id=self.task["id"])
                if r.get("action") == "auto_fire"]
        self.assertEqual(len(recs), 1)
        self.assertFalse(recs[0]["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
