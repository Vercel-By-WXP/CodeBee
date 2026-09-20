# -*- coding: utf-8 -*-
"""发布台账与管理状态机单测（不碰真浏览器；CDP 驱动见 test_publish_browser.py）。

跑法：python tests/test_publish_manager.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="pub-test-")).resolve()
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


def _chapter(name, text):
    """在受控临时目录内落一个章稿文件，返回绝对路径。"""
    fp = (_TMP / name).resolve()
    fp.relative_to(_TMP)                 # 守卫：不越出测试数据目录
    fp.write_text(text, encoding="utf-8")
    return str(fp)


def test_ledger_roundtrip():
    ledger.record("fanqie", "create_book", task_id="t1", title="书A", ok=True)
    ledger.record("fanqie", "upload_chapter", task_id="t1", chapter_no=1, title="第1章", ok=True)
    ledger.record("fanqie", "upload_chapter", task_id="t1", chapter_no=2, title="第2章", ok=True)
    ledger.record("fanqie", "upload_chapter", task_id="t1", chapter_no=2, title="第2章", ok=False, error="超时")
    ledger.record("fanqie", "upload_chapter", task_id="t1", chapter_no=3, title="第3章", ok=False, error="未登录")
    expect(ledger.published_chapters("t1", "fanqie") == {1, 2},
           "成功过的章入集合（先成功后失败仍在）；纯失败章不入：%s"
           % ledger.published_chapters("t1", "fanqie"))
    expect(len(ledger.recent(task_id="t1")) == 5, "recent 应含失败记录（审计）")
    expect(ledger.published_chapters("t1", "qimao") == set(), "跨平台隔离")


def test_books_registry():
    ledger.save_book("t1", "fanqie", {"book_id": "", "title": "书A"})
    b = ledger.book_for("t1", "fanqie")
    expect(b and b["title"] == "书A", "登记可读回")
    expect(ledger.book_for("t1", "qimao") is None, "未登记平台为空")
    ledger.save_book("t2", "qimao", {"book_id": "123", "title": "书B"})
    expect(len(ledger.load_books()) == 2, "多任务共存")


def test_chapter_no():
    cases = {"第12章": 12, "第十二章": 12, "第二十三章": 23, "第一百零五章": 105,
             "第一千零一章": 1001, "第9900章": 9900, "番外": 0}
    for k, v in cases.items():
        expect(ledger.parse_chapter_no(k) == v, "%s != %d" % (k, v))


def test_read_chapter():
    fp = _chapter("第3章 风起.md", "# 第3章 风起\n\n正文" + "内容" * 100)
    no, title, body, err = manager.read_chapter(fp)
    expect(err == "" and no == 3 and title == "第3章 风起", "标准章稿：%s %d %s" % (err, no, title))
    expect(len(body) > 100, "正文非空")
    fp2 = _chapter("第五章.md", "正文内容" * 60)
    no2, t2, _, e2 = manager.read_chapter(fp2)
    expect(e2 == "" and no2 == 5 and t2 == "第五章", "文件名兜底：%s %d %s" % (e2, no2, t2))
    fp3 = _chapter("短.md", "# 第1章\n短")
    _, _, b3, _ = manager.read_chapter(fp3)
    expect(len(b3) < 100, "短正文可读出（长度闸门在 upload 侧）")
    expect(manager.read_chapter(str(_TMP / "nope.md"))[3] != "", "缺文件报错")


def test_flow_override():
    steps = manager.load_flow("fanqie", "check_login")
    expect(steps and steps[0]["do"] == "navigate", "内置流程可读")
    ov = {"check_login": [{"do": "navigate", "url": "https://example.com/"}]}
    fp = (_TMP / "publish" / "flows-fanqie.json").resolve()
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.relative_to(_TMP)
    fp.write_text(json.dumps(ov), encoding="utf-8")
    steps2 = manager.load_flow("fanqie", "check_login")
    expect(steps2[0]["url"] == "https://example.com/", "覆盖生效")
    # 未覆盖的 action 走三层回落：data 只覆盖 check_login，create_book
    # 应落 calibrated 模板（真机校准基线）而非 py 内置推测
    cb = manager.load_flow("fanqie", "create_book")
    expect(cb and cb[0].get("do") == "navigate", "未覆盖 action 仍可读")
    expect(not any(s.get("url") == "https://example.com/" for s in cb
                   if s.get("do") == "navigate"),
           "data 覆盖不串 action")


def test_tag_steps_insert():
    steps = [{"do": "navigate", "url": "x"}, {"do": "shot", "name": "s"}, {"do": "submit", "sel": "b"}]
    out = manager._with_tag_steps(steps, [("tags_theme", ["都市", "重生"])], {})
    idx_shot = next(i for i, s in enumerate(out) if s.get("do") == "shot")
    texts = [s.get("text") for s in out if s.get("do") == "click_text"]
    expect(texts == ["都市", "重生"], "标签步骤生成：%s" % texts)
    expect(all(s.get("do") != "submit" or i > idx_shot for i, s in enumerate(out)),
           "标签点在 submit 之前")


def test_recover_orphans():
    manager._set("fanqie", status="waiting_login")
    manager._set("qimao", status="connected")
    n = manager.recover_orphans()
    expect(n == 1, "只收尸 waiting_login/busy：%d" % n)
    expect(manager.view()["platforms"]["fanqie"]["status"] == "error", "改判 error")
    expect(manager.view()["platforms"]["qimao"]["status"] == "connected", "connected 不动")


def test_register_existing_book():
    """登记已有作品：手工建书/流程没走通后的补账口，三道闸 + 覆盖更新。"""
    from core import paths
    paths.TASKS_DIR.mkdir(parents=True, exist_ok=True)
    tid = "t-reg"
    (paths.TASKS_DIR / (tid + ".json")).write_text(
        json.dumps({"id": tid, "title": "登记回归", "status": "done"}), encoding="utf-8")
    expect(manager.register_book(tid, "nope", "书X")[0] is False, "未知平台拒绝")
    expect(manager.register_book("no-such", "qimao", "书X")[0] is False, "任务不存在拒绝")
    expect(manager.register_book(tid, "qimao", "   ")[0] is False, "空书名拒绝（发章按书名找书）")
    ok, err = manager.register_book(tid, "qimao", "同事手建的书")
    expect(ok and err == "", "登记成功：%s" % err)
    b = ledger.book_for(tid, "qimao")
    expect(bool(b) and b["title"] == "同事手建的书" and b["book_id"] == "",
           "台账读回（book_id 选填）：%s" % b)
    manager.register_book(tid, "qimao", "同事手建的书", book_id="12345")
    b2 = ledger.book_for(tid, "qimao")
    expect(b2["book_id"] == "12345", "重复登记覆盖更新（纠错口）")


def test_login_timeout_final_recheck():
    """等扫码超时的终审：profile 登录态还在（用户只是关了窗口）→ 翻
    connected 不冤判；复核也过不了才维持超时错误。"""
    manager._set("qimao", status="waiting_login", error="")
    orig = (manager._open_page, manager._check_login)
    manager._open_page = lambda p: (None, None)
    manager._check_login = lambda p, pg: (True, "https://zuozhe.qimao.com/")
    try:
        expect(manager._finalize_login_wait("qimao") is True, "复核通过返回 True")
        v = manager.view()["platforms"]["qimao"]
        expect(v["status"] == "connected" and v["error"] == "", "终审翻 connected：%s" % v)
    finally:
        manager._open_page, manager._check_login = orig

    manager._set("qimao", status="waiting_login", error="")

    def _boom(p):
        raise Exception("浏览器不可用")

    manager._open_page = _boom
    try:
        expect(manager._finalize_login_wait("qimao") is False, "复核失败返回 False")
        v = manager.view()["platforms"]["qimao"]
        expect(v["status"] == "error" and "超时" in v["error"], "维持超时错误文案：%s" % v)
    finally:
        manager._open_page, manager._check_login = orig


def test_upgrade_selfheal_stale_timeout():
    """旧版遗留的「等待登录超时」假错误，升级后启动自愈：attach 到活实例
    且登录态在 → 翻 connected；attach 不到维持原错误（绝不 launch 弹窗）。"""
    from core.publish.browser import BrowserError

    class _Dead:
        @staticmethod
        def attach(port):
            raise BrowserError("端口 %s 上没有活着的浏览器" % port)

    manager._set("qimao", status="error", port=4999,
                 error="等待登录超时（15 分钟），请重新点连接")
    orig = (manager.Browser, manager._check_login)
    manager.Browser = _Dead
    try:
        n = manager.recover_orphans()
        expect(n == 0, "error 态不计入重启收尸计数：%d" % n)
        manager._recheck_stale_errors(["qimao"])      # 同步调，免线程竞态
        v = manager.view()["platforms"]["qimao"]
        expect(v["status"] == "error" and "等待登录超时" in v["error"],
               "attach 不到维持原错误：%s" % v)
    finally:
        manager.Browser, manager._check_login = orig
        manager._browsers.pop("qimao", None)

    class _FakePage:
        pass

    class _FakeB:
        def first_page(self, create=True):
            return _FakePage()

    class _Alive:
        @staticmethod
        def attach(port):
            return _FakeB()

    manager._set("qimao", status="error", port=4999,
                 error="等待登录超时（15 分钟），请重新点连接")
    manager.Browser = _Alive
    manager._check_login = lambda p, pg: (True, "https://zuozhe.qimao.com/")
    try:
        manager._recheck_stale_errors(["qimao"])
        v = manager.view()["platforms"]["qimao"]
        expect(v["status"] == "connected" and v["error"] == "",
               "假超时自愈翻 connected：%s" % v)
    finally:
        manager.Browser, manager._check_login = orig
        manager._browsers.pop("qimao", None)


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            check(name, fn)
    print("FAILS:", FAILS if FAILS else "none")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
