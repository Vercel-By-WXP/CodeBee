# -*- coding: utf-8 -*-
"""已发章数平台校准单测（不碰真浏览器）。

台账只记得 CodeBee 自己发成功的章，用户手工补交/平台驳回/重复记录都会让
「已发 N」漂移（2026-09-28 实案：番茄平台 8 章台账只显示 7）。校准链路：
章节管理页数行（count_rows_js）→ 状态分桶（bucket_chapter_rows）→
写回 books.json（update_book）→ 前端显示平台实况。

跑法：python tests/test_publish_sync.py
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="pubsync-test-")).resolve()
os.environ["TUTTI_DATA"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from core.publish import ledger, manager  # noqa: E402

FAILS = []


def check(name, fn):
    try:
        fn()
        print("  ok   %s" % name)
    except Exception as e:
        FAILS.append(name)
        print("  FAIL %s: %r" % (name, e))


def expect(cond, msg=""):
    if not cond:
        raise AssertionError(msg or "expect failed")


# 2026-09-28 实机校准抓到的真实行文本形态（番茄 Arco 表 / 七猫 Element 表）
FANQIE_ROWS = [
    "第8章 军册上的名字 2323 0 已发布 2026-09-26 00:00",
    "第7章 霍字旗倒下的地方 1943 0 已发布 2026-09-26 00:00",
    "第1章 七十三 2902 0 已发布 2026-09-24 21:10",
]
QIMAO_ROWS = [
    "8 第8章 军册上的名字 2323 免费章节 正文 2026-09-25 00:17:46 待审核",
    "1 第1章 七十三 2902 免费章节 正文 2026-09-24 22:47:23 待审核",
]
MIXED_ROWS = FANQIE_ROWS + ["第9章 草稿 800 0 未通过 2026-09-27 00:00"]


def test_bucket_rows():
    st = manager.bucket_chapter_rows(FANQIE_ROWS)
    expect(st == {"total": 3, "published": 3, "review": 0, "rejected": 0},
           "番茄行全为已发布：%s" % st)
    st = manager.bucket_chapter_rows(QIMAO_ROWS)
    expect(st == {"total": 2, "published": 0, "review": 2, "rejected": 0},
           "七猫未签约书全为待审核：%s" % st)
    st = manager.bucket_chapter_rows(MIXED_ROWS)
    expect(st["total"] == 4 and st["published"] == 3 and st["rejected"] == 1,
           "驳回行进 rejected 桶：%s" % st)
    st = manager.bucket_chapter_rows(["章节名称 字数 错别字 审核状态 发布时间 操作"])
    expect(st["total"] == 0, "表头行不计：%s" % st)
    expect(manager.bucket_chapter_rows([])["total"] == 0, "空行清单")


def test_update_book_merge():
    ledger.save_book("t-sync", "fanqie", {"book_id": "b1", "title": "书S"})
    ledger.update_book("t-sync", "fanqie", remote_total=8, remote_published=8,
                       remote_review=0, remote_synced_at="2026-09-28 12:00:00")
    b = ledger.book_for("t-sync", "fanqie")
    expect(b["book_id"] == "b1" and b["title"] == "书S", "登记键不被合并覆盖")
    expect(b["remote_total"] == 8 and b["remote_synced_at"], "校准字段写入")
    # 发章链找回 book_id 会再走一次 save_book（整条覆写语义）——不能抹掉校准
    ledger.save_book("t-sync", "fanqie", {"book_id": "b2", "title": "书S"})
    b = ledger.book_for("t-sync", "fanqie")
    expect(b["book_id"] == "b2" and b.get("remote_total") == 8,
           "save_book 合并语义保留 remote_*")
    ledger.update_book("t-none", "fanqie", remote_total=1)
    expect(ledger.book_for("t-none", "fanqie") is None,
           "没登记过的书不凭空造条目")


def test_published_local_distinct():
    # 重试会留下多条 ok 记录（UI 旧口径数记录会虚高），台账口径按章号去重
    ledger.record("fanqie", "upload_chapter", task_id="t-dist", chapter_no=1, ok=True)
    ledger.record("fanqie", "upload_chapter", task_id="t-dist", chapter_no=1, ok=True)
    ledger.record("fanqie", "upload_chapter", task_id="t-dist", chapter_no=2, ok=True)
    ledger.record("fanqie", "upload_chapter", task_id="t-dist", chapter_no=3, ok=False)
    expect(manager.published_local("t-dist", "fanqie") == 2,
           "去重章号数=2（3 条记录、1 条失败）")
    expect(manager.published_local("t-dist", "qimao") == 0, "跨平台隔离")


def test_sync_preflight():
    # 未知平台
    ok, err = manager._sync_preflight("t-x", "weixin", manual=False)
    expect(not ok and "未知平台" in err, err)
    # 未登记作品
    ok, err = manager._sync_preflight("t-noreg", "fanqie", manual=False)
    expect(not ok and "登记" in err, err)
    # 平台忙
    ledger.save_book("t-busy", "fanqie", {"book_id": "b", "title": "书"})
    manager._set("fanqie", status="busy")
    ok, err = manager._sync_preflight("t-busy", "fanqie", manual=False)
    expect(not ok and "操作正在进行中" in err, err)
    manager._set("fanqie", status="connected")
    # 自动触发（manual=False）要求浏览器活着：现在没浏览器 → 拒绝且不弹窗
    ok, err = manager._sync_preflight("t-busy", "fanqie", manual=False)
    expect(not ok and "浏览器未连接" in err, err)
    # 手动模式不做浏览器预检（允许 attach-or-launch）
    ok, err = manager._sync_preflight("t-busy", "fanqie", manual=True)
    expect(ok, "手动校准放行：%s" % err)
    # 预检失败时 async 入口不起线程、直接回报原因
    ok, err = manager.sync_published_async("t-noreg", "fanqie", manual=False)
    expect(not ok and "登记" in err, "async 入口透传预检失败")


def test_books_snapshot_carry_remote():
    """history 接口的 books 字段即 books.json 原文——remote_* 随之到前端。"""
    ledger.update_book("t-sync", "fanqie", remote_error="浏览器未连接，点「连接平台」后再校准",
                       remote_synced_at="2026-09-28 12:01:00")
    books = ledger.load_books().get("t-sync") or {}
    expect("remote_error" in (books.get("fanqie") or {}),
           "失败原因落盘供前端提示")


def test_sync_glue_with_stub_page():
    """sync_published 全链：attach → navigate → 数行 → 分桶 → 写回登记。"""
    import types
    from core.publish import browser as browser_mod

    class FakePage:
        def __init__(self, rows, url="https://fanqienovel.com/main/writer/chapter-manage/777"):
            self._rows, self._url = rows, url
        def navigate(self, url, timeout=30):
            pass
        def url(self):
            return self._url
        def call(self, js):
            return self._rows

    class FakeBrowser:
        port = 59999
        def first_page(self, create=True):
            return FakePage.rows_page
        @staticmethod
        def attach(port):
            if not port:
                raise browser_mod.BrowserError("dead")
            return FakeBrowser()

    manager._set("fanqie", status="connected", port=59999)
    ledger.save_book("t-glue", "fanqie", {"book_id": "777", "title": "校准书"})

    # —— 平台 8 章（7 已发布 + 1 待审核）→ remote_* 落盘
    FakePage.rows_page = FakePage(
        ["第%d章 x %d 0 已发布 09-28" % (i, 900) for i in range(1, 8)]
        + ["8 第8章 y 800 免费章节 正文 t 待审核"])
    orig_attach = browser_mod.Browser.attach
    browser_mod.Browser.attach = FakeBrowser.attach
    try:
        ok, err = manager.sync_published("t-glue", "fanqie")
    finally:
        browser_mod.Browser.attach = orig_attach
    expect(ok, err)
    b = ledger.book_for("t-glue", "fanqie")
    expect(b["remote_total"] == 8 and b["remote_published"] == 7
           and b["remote_review"] == 1, "分桶写回：%s" % b)

    # —— 识别不到行（改版/未登录）不覆写 remote_*，宁可显示旧数
    ledger.update_book("t-glue", "fanqie", remote_total=8)
    FakePage.rows_page = FakePage([], url="https://fanqienovel.com/main/writer/chapter-manage/777")
    browser_mod.Browser.attach = FakeBrowser.attach
    try:
        ok, err = manager.sync_published("t-glue", "fanqie")
    finally:
        browser_mod.Browser.attach = orig_attach
    expect(not ok and "未识别" in err, err)
    expect(ledger.book_for("t-glue", "fanqie")["remote_total"] == 8,
           "失败不覆写旧校准值")

    # —— 登录态失效（跳登录页）如实报因
    FakePage.rows_page = FakePage(["第1章 x 1 0 已发布 t"],
                                  url="https://ssofanqie.com/login?next=x")
    browser_mod.Browser.attach = FakeBrowser.attach
    try:
        ok, err = manager.sync_published("t-glue", "fanqie")
    finally:
        browser_mod.Browser.attach = orig_attach
    expect(not ok and "登录" in err, err)


def test_reregister_clears_stale_remote():
    """换绑另一本书：旧书的校准数必须作废，防张冠李戴。"""
    from core import store
    tid = store.create_task({"name": "重登记", "type": "novel",
                             "goal": "校准测试"})["id"]
    ledger.save_book(tid, "qimao", {"book_id": "a1", "title": "旧书"})
    ledger.update_book(tid, "qimao", remote_total=8, remote_published=8,
                       remote_synced_at="2026-09-28 10:00:00")
    manager.register_book(tid, "qimao", "新书", book_id="b2")
    b = ledger.book_for(tid, "qimao")
    expect(b["book_id"] == "b2" and "remote_total" not in b
           and "remote_synced_at" not in b, "换绑书清掉旧校准：%s" % b)
    # 同一本书重复登记（纠错口）不抖掉校准
    ledger.update_book(tid, "qimao", remote_total=5, remote_synced_at="t")
    manager.register_book(tid, "qimao", "新书", book_id="b2")
    b = ledger.book_for(tid, "qimao")
    expect(b.get("remote_total") == 5, "同书重登记保留校准：%s" % b)


def test_create_book_empty_id_reconciles_and_skips_entry():
    """建书未取得 book_id：先按书名对账，找回即落账；仍空则不落台账
    （空条目曾把界面骗成「已建书」、校准盲跑——2026-09-30 假登记案）。"""
    import time as _time
    from core import store

    class FakePage:
        def url(self):
            return "https://fanqienovel.com/main/writer/create"  # 无 book-info

    class FakeBrowser:
        def first_page(self, create=True):
            return FakePage()

    patches = {
        "_open_page": lambda plat: (FakeBrowser(), FakePage()),
        "_create_preflight": lambda plat, data: "",
        "_quality_release_guard": lambda *a, **k: {"allowed": True},
        "_login_guard": lambda plat: (True, ""),
    }
    saved = {k: getattr(manager, k) for k in patches}
    saved_flow = manager.flow.run_flow
    manager.flow.run_flow = lambda *a, **k: None
    for k, v in patches.items():
        setattr(manager, k, v)
    try:
        # —— 场景A：id 落空但对账找回 → 正常落账翻成功
        ta = store.create_task({"name": "建书对账A", "type": "novel",
                                "goal": "x"})["id"]
        store.set_book_meta(ta, "fanqie", {"status": "done", "data": {
            "book_name": "对账找回书", "summary": "x", "category": "都市脑洞",
            "tags_theme": ["都市异能"], "tags_role": ["扮猪吃虎"],
            "tags_plot": ["打脸"]}})
        manager._resolve_book_id = lambda plat, page, book: "888"
        manager.create_book_async(ta, "fanqie")
        _wait_idle()
        b = ledger.book_for(ta, "fanqie")
        expect(b and b["book_id"] == "888" and b.get("source") == "create",
               "对账找回的 id 落账：%s" % b)

        # —— 场景B：id 落空且对账也找不回 → 不落台账，审计记「疑似未建成」
        tb = store.create_task({"name": "建书对账B", "type": "novel",
                                "goal": "x"})["id"]
        store.set_book_meta(tb, "fanqie", {"status": "done", "data": {
            "book_name": "没建成的书", "summary": "x", "category": "都市脑洞",
            "tags_theme": ["都市异能"], "tags_role": ["扮猪吃虎"],
            "tags_plot": ["打脸"]}})
        manager._resolve_book_id = lambda plat, page, book: ""
        manager.create_book_async(tb, "fanqie")
        _wait_idle()
        expect(ledger.book_for(tb, "fanqie") is None,
               "对账找不回不落台账（防假「已建书」）")
        recs = [r for r in ledger.recent(task_id=tb, platform="fanqie", limit=10)
                if r.get("action") == "create_book"]
        expect(recs and not recs[0].get("ok")
               and "疑似未建成" in str(recs[0].get("error") or ""),
               "审计如实记「疑似未建成」：%s" % (recs[:1]))
    finally:
        manager.flow.run_flow = saved_flow
        for k, v in saved.items():
            setattr(manager, k, v)


def _wait_idle(plat="fanqie", timeout=15.0):
    """等 create_book_async 的守护线程收尾（busy 翻走）。"""
    import time as _time
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        if manager._st(plat).get("status") != "busy":
            return
        _time.sleep(0.2)
    raise AssertionError("建书线程 %ss 未收尾" % timeout)


def test_sync_empty_id_resolves_first():
    """校准遇 book_id 空登记：先对账找回再校准；找不回明确报「可能未建成」，
    不再拿空 id 盲跑作品列表页报误导人的「可能改版」。"""
    from core.publish import browser as browser_mod

    class FakePage:
        def __init__(self, rows, url=""):
            self._rows, self._url = rows, url
            self.navigated = []
        def navigate(self, url, timeout=30):
            self.navigated.append(url)
        def url(self):
            return self._url
        def call(self, js, *a):
            return self._rows

    class FakeBrowser:
        def first_page(self, create=True):
            return FakeBrowser.page
        @staticmethod
        def attach(port):
            return FakeBrowser()

    pages = {}
    def make_page():
        pages["cur"] = FakePage(
            ["第1章 x 900 0 已发布 09-30"],
            url="https://fanqienovel.com/main/writer/chapter-manage/666")
        return pages["cur"]

    saved = (manager._resolve_book_id, browser_mod.Browser.attach)
    try:
        manager._set("fanqie", status="connected", port=59998)
        browser_mod.Browser.attach = lambda port: FakeBrowser()

        # —— 找回：补账 book_id 且继续校准写回 remote_*
        ledger.save_book("t-synde", "fanqie", {"book_id": "", "title": "空id书A"})
        manager._resolve_book_id = lambda plat, page, book: "666"
        FakeBrowser.page = make_page()
        ok, err = manager.sync_published("t-synde", "fanqie")
        expect(ok, err)
        b = ledger.book_for("t-synde", "fanqie")
        expect(b["book_id"] == "666" and b.get("remote_total") == 1,
               "对账找回并完成校准：%s" % b)

        # —— 找不回：明确报「可能尚未建成」，不盲跑章节管理 URL
        ledger.save_book("t-synmiss", "fanqie", {"book_id": "", "title": "空id书B"})
        manager._resolve_book_id = lambda plat, page, book: ""
        FakeBrowser.page = make_page()
        ok, err = manager.sync_published("t-synmiss", "fanqie")
        expect(not ok and "未找到" in err and "尚未建成" in err, err)
        expect(ledger.book_for("t-synmiss", "fanqie")["book_id"] == "",
               "找不回不补账")
        expect(pages["cur"].navigated == [], "找不回不导航盲跑：%s"
               % pages["cur"].navigated)
    finally:
        manager._resolve_book_id, browser_mod.Browser.attach = saved


if __name__ == "__main__":
    print("== test_publish_sync")
    check("bucket_rows", test_bucket_rows)
    check("update_book_merge", test_update_book_merge)
    check("published_local_distinct", test_published_local_distinct)
    check("sync_preflight", test_sync_preflight)
    check("books_snapshot_carry_remote", test_books_snapshot_carry_remote)
    check("sync_glue_with_stub_page", test_sync_glue_with_stub_page)
    check("reregister_clears_stale_remote", test_reregister_clears_stale_remote)
    check("create_book_empty_id_reconciles_and_skips_entry",
          test_create_book_empty_id_reconciles_and_skips_entry)
    check("sync_empty_id_resolves_first", test_sync_empty_id_resolves_first)
    print("== %d fail" % len(FAILS))
    sys.exit(1 if FAILS else 0)
