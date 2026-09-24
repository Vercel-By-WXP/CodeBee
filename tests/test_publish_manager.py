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


# ------------------------------------------------- 建书前置闸 + flow 加固（2026-09-24）

def test_create_preflight():
    good_fq = {"book_name": "戍边骑奴", "summary": "字" * 60, "category": "悬疑脑洞",
               "tags_theme": ["古代"], "tags_role": ["大佬"], "tags_plot": ["打脸"]}
    expect(manager._create_preflight("fanqie", good_fq) == "", "达标资料放行")
    e1 = manager._create_preflight("fanqie", dict(good_fq, summary="短简介"))
    expect("50-500" in e1, "番茄简介字数不符拦停：%s" % e1)
    e2 = manager._create_preflight("fanqie", dict(good_fq, summary="字" * 600))
    expect("50-500" in e2, "超长简介同样拦停：%s" % e2)
    e3 = manager._create_preflight("fanqie", dict(good_fq, book_name="（待补充）"))
    expect("书名" in e3, "占位书名拦停：%s" % e3)
    e4 = manager._create_preflight("fanqie", dict(good_fq, tags_role=[]))
    expect("标签" in e4, "空标签组拦停：%s" % e4)
    good_qm = {"book_name": "书", "summary": "简介", "category_main": "古代言情",
               "category_sub": "宫闱宅斗", "tags_style": ["悬疑"], "tags_role": ["兵王"],
               "tags_plot": ["鉴宝"], "tags_bg": ["古代"]}
    expect(manager._create_preflight("qimao", good_qm) == "", "七猫达标放行")
    e5 = manager._create_preflight("qimao", dict(good_qm, summary="（待补充）"))
    expect("简介" in e5, "七猫占位简介拦停：%s" % e5)
    e6 = manager._create_preflight("qimao", dict(good_qm, tags_bg=[]))
    expect("标签" in e6, "七猫空背景组拦停：%s" % e6)


class _FlowPage:
    """run_flow 离线假页：只实现被测步骤用到的面。"""

    def __init__(self, url="", call_results=None):
        self._url = url
        self._calls = 0
        self._call_results = list(call_results or [])

    def url(self):
        return self._url

    def call(self, js, *a, **kw):
        self._calls += 1
        if self._call_results:
            return self._call_results.pop(0)
        return {"ok": False, "err": "x"}

    def real_click_text(self, text, scope="", **kw):
        self._clicks = getattr(self, "_clicks", 0) + 1
        return {"ok": True}

    def wait_for(self, sel, timeout=8):
        pass

    def fill(self, sel, text):
        pass


def test_flow_url_any_brings_page_hint():
    from core.publish import flow
    pg = _FlowPage(url="https://fanqienovel.com/main/writer/create",
                   call_results=["作品简介至少50字"])
    try:
        flow.run_flow(pg, [{"do": "url_any", "any": ["book-info"]}], values={})
        raise AssertionError("url_any 失败应抛 FlowError")
    except flow.FlowError as e:
        expect("页面提示" in str(e) and "50字" in str(e), "页面报错要带回来：%s" % e)
    pg2 = _FlowPage(url="https://x/create", call_results=[""])
    try:
        flow.run_flow(pg2, [{"do": "url_any", "any": ["book-info"]}], values={})
        raise AssertionError("url_any 失败应抛 FlowError")
    except flow.FlowError as e:
        expect("表单校验未过" in str(e), "无页面提示时给人话猜测：%s" % e)


def test_flow_tags_group_retry_and_message():
    from core.publish import flow
    pg = _FlowPage()                       # 组名永远找不到=弹层没开
    try:
        flow.run_flow(pg, [{"do": "tags"}], values={"_tags": [["风格", "热血"]]})
        raise AssertionError("组名找不到应抛 FlowError")
    except flow.FlowError as e:
        expect("弹层未打开" in str(e), "报错点名弹层未开：%s" % e)
    expect(pg._calls == 4, "组名点击重试 4 次（等弹层开）：%d" % pg._calls)
    pg2 = _FlowPage(call_results=[{"ok": False}, {"ok": True}, {"ok": True}])
    n = flow.run_flow(pg2, [{"do": "tags"}], values={"_tags": [["风格", "热血"]]})
    expect(n == 1 and pg2._calls == 3, "第2次点中组名+1次标签点选：%d %d"
           % (n, pg2._calls))


def test_flow_submit_gate():
    from core.publish import flow
    pg = _FlowPage(url="https://fanqienovel.com/book-info/123")
    steps = [{"do": "fill", "sel": "input", "key": "t"},
             {"do": "submit", "text": "立即创建", "scope": "button"},
             {"do": "url_any", "any": ["book-info"]}]
    shots = []
    vals = {"t": "书名"}
    n = flow.run_flow(pg, steps, values=dict(vals), auto_submit=False,
                      shot=lambda name: shots.append(name))
    expect(n == 2 and getattr(pg, "_clicks", 0) == 0,
           "auto_submit=false 停在提交前不真点：%s %s" % (n, getattr(pg, "_clicks", 0)))
    expect(shots[-1] == "ready-manual-submit", "补人工确认截图：%s" % shots)
    n2 = flow.run_flow(pg, steps, values=dict(vals), auto_submit=True)
    expect(getattr(pg, "_clicks", 0) == 1 and n2 == 3,
           "auto_submit=true 直提交并走完校验：%s %s"
           % (getattr(pg, "_clicks", 0), n2))


# ------------------------------------------------- 发章 about:blank 三连败修复（2026-09-24）

def test_url_values_per_platform():
    """URL 占位值逐键取：平台缺 draft_url 不能连坐后面的 editor_url。

    番茄曾因按序连赋 + AttributeError 兜底拿不到 editor_url——{editor_url}
    残串进 navigate，页面停在 about:blank 误报「未登录或改版」。"""
    from core.publish import fanqie as fq, qimao as qm
    vq = manager._url_values(qm, {"book_id": "123", "title": "书"})
    expect(set(vq) == {"chapter_manage_url", "draft_url", "editor_url"},
           "七猫三键齐：%s" % sorted(vq))
    vf = manager._url_values(fq, {"book_id": "456", "title": "书"})
    expect("editor_url" in vf and "/publish/" in vf["editor_url"],
           "番茄 editor_url 必须在场：%s" % vf)
    expect("draft_url" not in vf, "番茄没有 draft_url，跳过不报错")
    expect("chapter-manage/456" in vf["chapter_manage_url"], "管理页 URL 带 id")


def test_flow_navigate_placeholder_guard():
    """navigate 占位符没被吃掉：点名缺的键，别放到 url_any 才误报。"""
    from core.publish import flow
    try:
        flow.run_flow(_FlowPage(), [{"do": "navigate", "url": "{editor_url}"}],
                      values={})
        raise AssertionError("占位符残留应抛 FlowError")
    except flow.FlowError as e:
        expect("editor_url" in str(e) and "占位符" in str(e),
               "报错点名缺的键：%s" % e)


class _ResolvePage:
    """resolve_book_id 假页：导航记录 + 点击恒中 + 可控最终 URL。"""

    def __init__(self, final_url=""):
        self._final = final_url
        self.navs = []

    def navigate(self, url, timeout=30):
        self.navs.append(url)

    def call(self, js, *a, **kw):
        return {"ok": True}

    def url(self):
        return self._final


def test_resolve_book_id_by_title():
    """登记缺 book_id：按书名在后台找回，提不到返回空串不拦死。"""
    pg = _ResolvePage("https://fanqienovel.com/main/writer/book-info/71430320")
    bid = manager._resolve_book_id("fanqie", pg,
                                   {"book_id": "", "title": "戍边骑奴"})
    expect(bid == "71430320", "从详情 URL 提取 id：%s" % bid)
    expect(pg.navs and "fanqienovel.com/main/writer/" in pg.navs[0],
           "先开后台首页：%s" % pg.navs)
    bid3 = manager._resolve_book_id(
        "fanqie", _ResolvePage("https://fanqienovel.com/main/writer/chapter-manage/99"),
        {"book_id": "", "title": "书"})
    expect(bid3 == "99", "章节管理页形态也能提：%s" % bid3)
    bid2 = manager._resolve_book_id("fanqie", _ResolvePage(""),
                                    {"book_id": "", "title": "书"})
    expect(bid2 == "", "提不到返回空串不拦死：%r" % bid2)
    pg4 = _ResolvePage("https://fanqienovel.com/main/writer/book-info/1")
    bid4 = manager._resolve_book_id("fanqie", pg4, {"book_id": "7", "title": "书"})
    expect(bid4 == "" and pg4.navs == [], "已有 id 短路，不动浏览器")
    expect(manager._resolve_book_id("qimao", _ResolvePage(),
                                    {"book_id": "", "title": "x"}) == "",
           "平台无钩子静默跳过")


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            check(name, fn)
    print("FAILS:", FAILS if FAILS else "none")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
