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


def test_read_chapter_bare_heading():
    """裸「第X章 标题」首行（中文数字章稿常态）：标题行剥出正文、章号解析。
    认不出的旧世界=标题回退文件名 chapter-31 + 标题行留正文里，番茄编辑器
    序号/标题双输（2026-10-08 实案）。"""
    fp = _chapter("chapter-31.md",
                  "第三十一章 绕开的路线\n\n两点整，车还是拐进了院坝。\n\n" + "正文" * 60)
    no, title, body, err = manager.read_chapter(fp)
    expect(err == "" and no == 31, "章号解析：%s %d" % (err, no))
    expect(title == "第三十一章 绕开的路线", "标题=完整标题行：%s" % title)
    expect(body.startswith("两点整") and "第三十一章" not in body,
           "标题行不进正文：%s" % body[:40])
    fp2 = _chapter("c32.md", "第一百零五章 夜谈\n\n" + "正文" * 50)
    no2, t2, b2, _ = manager.read_chapter(fp2)
    expect(no2 == 105 and b2.startswith("正文") and "第一百零五章" not in b2,
           "百位中文数字：%d %s" % (no2, t2))


def test_split_chapter_name_and_fill_values():
    """番茄编辑器三件套拆键：chapter_no=阿拉伯串、chapter_name=正题、
    chapter_title=完整行（台账/七猫锚点口径不变）。"""
    expect(manager.split_chapter_name("第三十一章 绕开的路线") == "绕开的路线",
           "中文数字前缀拆掉：%s" % manager.split_chapter_name("第三十一章 绕开的路线"))
    expect(manager.split_chapter_name("第31章、风起") == "风起", "顿号分隔")
    expect(manager.split_chapter_name("第三十一章") == "第三十一章", "无正题原样")
    expect(manager.split_chapter_name("风起") == "风起", "无章号原样")
    expect(manager.split_chapter_name("chapter-31") == "chapter-31", "文件名原样")
    vals = manager.chapter_fill_values(31, "第三十一章 绕开的路线", "正文", "书")
    expect(vals["chapter_no"] == "31" and vals["chapter_name"] == "绕开的路线"
           and vals["chapter_title"] == "第三十一章 绕开的路线"
           and vals["chapter_body"] == "正文" and vals["book_name"] == "书",
           "fill values 三件套：%s" % vals)
    vals0 = manager.chapter_fill_values(0, "番外", "x", "书")
    expect(vals0["chapter_no"] == "", "章号 0 → 空串（fill 跳过）:%s" % vals0)


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

    def __init__(self, url="", call_results=None, click_results=None):
        self._url = url
        self._calls = 0
        self._call_results = list(call_results or [])
        self._click_results = list(click_results or [])

    def url(self):
        return self._url

    def call(self, js, *a, **kw):
        self._calls += 1
        if self._call_results:
            return self._call_results.pop(0)
        return {"ok": False, "err": "x"}

    def real_click_text(self, text, scope="", **kw):
        self._clicks = getattr(self, "_clicks", 0) + 1
        if self._click_results:
            return self._click_results.pop(0)
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
    expect(pg._calls == 5, "弹层探测 1 次+组名点击重试 4 次（等弹层开）：%d" % pg._calls)
    pg2 = _FlowPage(call_results=[{"ok": False}, {"ok": True}, {"ok": True}])
    n = flow.run_flow(pg2, [{"do": "tags"}], values={"_tags": [["风格", "热血"]]})
    expect(n == 1 and pg2._calls == 3, "探测 1 次+第2次点中组名+1次标签点选：%d %d"
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

class _DraftPage(_FlowPage):
    """upload_chapter_async(as_draft) 离线假页：真流程表走通存草稿链。

    主页 call 队列喂 js_click 的成功结果；verify 的 body 文本走新页签桩。"""

    def __init__(self):
        super().__init__(url="https://fanqienovel.com/main/writer/888/publish/",
                         call_results=[{"ok": True}])

    def navigate(self, url, timeout=45):
        self._url = url

    def screenshot(self, path):
        return ""

    def open_new_tab(self, url):
        return _DraftVerifyTab()

    def send(self, *a, **k):
        pass


class _DraftVerifyTab:
    """verify 步骤的新页签桩：章节管理页 body 文本含「第7章」。"""

    def call(self, js, *a, **kw):
        return "第7章 草稿测"

    def close_tab(self):
        pass


def test_upload_chapter_draft_flow_and_ledger():
    """as_draft=True（2026-10-09 全部发草稿）：质量闸拦直发不拦草稿；
    跑 upload_chapter_draft 流程表；成功后台账记 upload_chapter_draft；
    同章再存被幂等拦下。"""
    from core import store
    import time as _time
    t = store.create_task({"type": "serial_novel", "goal": "草稿单章测试",
                           "workdir": str(_TMP)})
    ledger.save_book(t["id"], "fanqie", {"book_id": "888", "title": "草稿书"})
    r = store.create_run("orchestration", "评审", task_id=t["id"])
    store.update_run(r["id"], status="done", verdict={"publishable": False},
                     ended_at="2026-10-09 00:00:00")
    fp = _chapter("第7章 草稿测.md", "# 第7章 草稿测\n\n" + "内容" * 300)
    # 对照：同评审结论下直发路径被质量闸拦
    ok, err = manager.upload_chapter_async(t["id"], "fanqie", fp)
    expect(not ok and "质量门禁" in err, "坏结论拦直发：%s/%s" % (ok, err))
    # 草稿路径放行：打桩浏览器会话与登录态，真流程表跑通
    orig_open, orig_guard = manager._open_page, manager._login_guard
    manager._open_page = lambda p: (None, _DraftPage())
    manager._login_guard = lambda p: (True, "")
    try:
        ok, err = manager.upload_chapter_async(t["id"], "fanqie", fp,
                                               as_draft=True)
        expect(ok, "草稿发起应成功：%s" % err)
        for _ in range(200):                # 等后台线程收尾（含流程内 sleep）
            if manager._st("fanqie").get("status") != "busy":
                break
            _time.sleep(0.1)
        expect(7 in ledger.drafted_chapters(t["id"], "fanqie"),
               "台账应记 upload_chapter_draft：%s"
               % [(x.get("action"), x.get("chapter_no"), x.get("ok"))
                  for x in ledger.recent(task_id=t["id"], limit=6)])
        expect(7 not in ledger.published_chapters(t["id"], "fanqie"),
               "存草稿不冒充已发布（已发口径干净）")
    finally:
        manager._open_page, manager._login_guard = orig_open, orig_guard
    # 幂等：已存草稿的章再存被拦（重复稿防线）
    ok2, err2 = manager.upload_chapter_async(t["id"], "fanqie", fp,
                                             as_draft=True)
    expect(not ok2 and "已存过草稿" in err2, "草稿幂等拦截：%s/%s" % (ok2, err2))


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


def test_flow_click_real_expect_checked():
    """radio 点击闭环（2026-09-28 建书弹层没开实案）：点击派发成功 ≠ 选中
    成功，expect_checked 步骤必须回头验 checked 态，验不中重点、耗尽报专用错。"""
    from core.publish import flow
    # 点击 ok + 选中态 ok → 一次过
    pg = _FlowPage(call_results=[{"ok": True}])
    n = flow.run_flow(pg, [{"do": "click_real", "text": "男频",
                            "scope": "label.arco-radio",
                            "expect_checked": True}], values={})
    expect(n == 1, "点击+选中双达标放行：n=%s" % n)
    # 点击 ok 但永远验不到选中态 → 专用报错（不是笼统的找不到）
    pg2 = _FlowPage()                    # call 走默认 {"ok":False}
    try:
        flow.run_flow(pg2, [{"do": "click_real", "text": "男频",
                             "scope": "label.arco-radio", "tries": 3,
                             "expect_checked": True}], values={})
        raise AssertionError("验不到选中态应抛 FlowError")
    except flow.FlowError as e:
        expect("选中态" in str(e), "报错点名选中态：%s" % e)
    expect(pg2._clicks == 3, "验不中要重点满 tries：%d" % pg2._clicks)
    # 点击本身被挡（elementFromPoint 守卫）→ 原样透出挡路死因
    blocked = {"ok": False,
               "err": "「男频」的落点被 DIV arco-mask 挡住（遮罩/浮层未退场）"}
    pg3 = _FlowPage(click_results=[dict(blocked), dict(blocked)])
    try:
        flow.run_flow(pg3, [{"do": "click_real", "text": "男频",
                             "scope": "label.arco-radio", "tries": 2,
                             "expect_checked": True}], values={})
        raise AssertionError("被挡应抛 FlowError")
    except flow.FlowError as e:
        expect("挡住" in str(e), "被挡死因透出：%s" % e)
    # 不带 expect_checked 的普通点击不受影响（不调 verify）
    pg4 = _FlowPage()
    n4 = flow.run_flow(pg4, [{"do": "click_real", "text": "确认",
                              "scope": "button", "tries": 2}], values={})
    expect(n4 == 1 and pg4._calls == 0, "无断言需求零额外求值：%s %s"
           % (n4, pg4._calls))


def test_fanqie_flow_closed_loop_contract():
    """番茄建书流程 JSON 契约（2026-09-28 弹层没开连败案）：radio 步骤必须
    带选中态断言；标签触发器点完必须 wait 弹层存在，不许退回盲 sleep。"""
    import json as _json
    from core.publish import flow as _flow
    fp = Path(_flow.__file__).with_name("flows-fanqie-calibrated.json")
    data = _json.loads(fp.read_text(encoding="utf-8"))
    cb = data["create_book"]
    radios = [s for s in cb if s.get("do") == "click_real"
              and "radio" in (s.get("scope") or "")]
    expect(len(radios) == 2 and all(s.get("expect_checked") for s in radios),
           "签约/读者两个 radio 步骤必须带 expect_checked：%s"
           % [(s.get("text"), s.get("expect_checked")) for s in radios])
    trig = [i for i, s in enumerate(cb) if s.get("text") == "请选择作品标签"]
    expect(len(trig) == 2, "阅读/内容两处标签触发器：%s" % trig)
    for i in trig:
        nxt = cb[i + 1]
        expect(nxt.get("do") == "wait" and nxt.get("sel") == ".category-modal",
               "触发器后必须 wait 弹层存在（sleep 已退役）：%s" % nxt)


def test_fanqie_upload_flow_contract():
    """番茄发章流程契约（2026-10-08 序号空案）：序号/标题/正文三件套必须
    各填各的键；verify 标记用「第{chapter_no}章」（用户手工改过标题时按名
    对版必假失败）。随包基线与 data 覆盖表（在场时）同规。"""
    import json as _json
    from core.publish import flow as _flow

    def contract(ups, tag):
        keys = [s.get("key") for s in ups if s.get("do") == "fill"]
        expect("chapter_no" in keys and "chapter_name" in keys
               and "chapter_title" not in keys,
               "%s 序号/正题分填、不再拿完整行填标题：%s" % (tag, keys))
        verifies = [s for s in ups if s.get("do") == "verify"]
        expect(len(verifies) == 1 and "第{chapter_no}章" in verifies[0]["any"]
               and "{chapter_name}" in verifies[0]["any"],
               "%s verify 双标记：%s" % (tag, verifies and verifies[0].get("any")))
        # manual 模式的命门：「下一步」必须是 submit 步骤（auto_submit=false
        # 在此步前停）。click_real 不会被 manual 闸拦——序号修好后 manual
        # 模式会一路点到「定时发布/发布」把人工确认模式架空（2026-10-08）。
        nxt = [s for s in ups if s.get("text") == "下一步"
               and s.get("do") in ("submit", "click_real")]
        expect(len(nxt) == 1 and nxt[0]["do"] == "submit",
               "%s 下一步必须走 submit 闸：%s" % (tag, nxt))
        # 批量自动发布·自动分卷（2026-10-08）：volume 步骤必须在填稿之前，
        # 且带全真机选择器（编辑器分卷弹窗/新建分卷/行内确认）。
        vi = next((k for k, s in enumerate(ups) if s.get("do") == "volume"), -1)
        first_fill = next((k for k, s in enumerate(ups)
                           if s.get("do") == "fill"), len(ups))
        expect(vi >= 0, "%s 缺 volume 步骤" % tag)
        expect(vi < first_fill, "%s volume 步骤必须在填稿前" % tag)
        vs = ups[vi]
        for k in ("open_sel", "modal_sel", "item_sel", "add_sel",
                  "input_sel", "confirm_sel"):
            expect(str(vs.get(k) or "").strip(), "%s volume 缺 %s" % (tag, k))
        expect(vs.get("key") == "volume_name", "%s volume key" % tag)

    base_fp = Path(_flow.__file__).with_name("flows-fanqie-calibrated.json")
    contract(_json.loads(base_fp.read_text(encoding="utf-8"))["upload_chapter"],
             "基线")
    contract(manager.load_flow("fanqie", "upload_chapter"), "解析后")


def test_volume_step_flow_logic():
    """volume 步骤全链（假页驱动）：当前卷命中直接过 / 卷已存在不重复建 /
    缺卷新建（输名→✓）/ 无目标跳过 / 无卷选择器 optional 跳过。
    平台不提供章节切卷（弹窗只能增删改卷，真机 code -4054 实案）——
    本步骤只「备好卷」，归属交给平台按卷自动归类。"""
    from core.publish import flow as _flow

    class VolPage:
        """分卷弹窗状态桩：头部卷名可读；open→列表；add→编辑态；confirm→落名。"""
        def __init__(self, vols, current, open_ok=True):
            self.vols = list(vols)          # 平台显示名
            self.current = current
            self.modal = False
            self.created = []
            self.open_ok = open_ok

        def call(self, fn, *a, **k):
            if "innerText||'').trim():''" in fn:    # 读头部当前卷
                return self.current if self.open_ok else ""
            if "v.click" in fn:
                if not self.open_ok:
                    return {"ok": False}
                self.modal = True
                return {"ok": True}
            if "width>0" in fn and "map" in fn:           # vol_items
                return list(self.vols) if self.modal else []
            if "e.click" in fn and "add" in fn:           # add_sel 点击
                return {"ok": True}
            if "HTMLInputElement" in fn:                  # 行内输名+confirm
                self.created.append(a[1])
                self.vols.append("第3卷：" + str(a[1]))
                self.current = "第3卷：" + str(a[1])      # 平台建卷后自动归入
                return {"ok": True}
            if "取消" in fn:                              # 收弹窗
                self.modal = False
                return {"ok": True}
            return {"ok": True}

    step = {"do": "volume", "key": "volume_name", "optional": True,
            "open_sel": ".vopen", "modal_sel": ".vmodal",
            "item_sel": ".vitem", "add_sel": ".vadd",
            "input_sel": "input", "confirm_sel": "i.ok", "settle": 0}

    def run(page, values):
        logs = []
        _flow.run_flow(page, [dict(step)], values=values, log=logs.append)
        return logs

    # 1) 当前卷即目标（去前缀比较）：不开弹窗直接过
    pg = VolPage(["第一卷：山门换锁", "第二卷：县里有旧账"], "第二卷：县里有旧账")
    run(pg, {"volume_name": "县里有旧账"})
    expect(not pg.modal, "当前卷命中不开弹窗")

    # 2) 目标卷已存在（当前在别的卷）：确认存在、不重复建
    pg2 = VolPage(["第一卷：山门换锁", "第二卷：县里有旧账"], "第一卷：山门换锁")
    vals2 = {"volume_name": "县里有旧账", "_volumes": []}
    run(pg2, vals2)
    expect(not pg2.created, "已存在的卷不重建：%s" % pg2.created)

    # 3) 缺卷：新建（输名→✓）→ 追加进共享缓存
    pg3 = VolPage(["第一卷：山门换锁", "第二卷：县里有旧账"], "第二卷：县里有旧账")
    vals3 = {"volume_name": "试着开一道口",
             "_volumes": ["第一卷：山门换锁", "第二卷：县里有旧账"]}
    run(pg3, vals3)
    expect(pg3.created == ["试着开一道口"], "新建输名：%s" % pg3.created)
    expect("第3卷：试着开一道口" in vals3["_volumes"], "缓存追加显示名：%s" % vals3["_volumes"])

    # 4) 无目标卷名：跳过
    pg4 = VolPage(["第一卷：山门换锁"], "第一卷：山门换锁")
    run(pg4, {"volume_name": ""})
    expect(not pg4.modal, "空卷名跳过")

    # 5) 编辑器没有卷选择器 + optional：跳过不报错
    pg5 = VolPage(["第一卷：山门换锁"], "第一卷：山门换锁", open_ok=False)
    run(pg5, {"volume_name": "山门换锁"})
    expect(not pg5.modal, "无选择器 optional 跳过")


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


def test_title_dup_precheck():
    """建书预检（2026-09-30 马甲案）：平台实时校验喊重名 → 提交前抛
    TitleDupError，不白点提交；dup_check 关闭（发章默认）不拦。"""
    from core.publish import flow
    pg0 = _FlowPage(url="https://x/writer", call_results=["书名已存在"])
    n = flow.run_flow(pg0, [{"do": "submit", "text": "创建"}], values={},
                      auto_submit=True)
    expect(n == 1, "未开 dup_check 不拦提交：%s" % n)
    pg = _FlowPage(url="https://x/writer",
                   call_results=["书名已存在，请重新输入"])
    try:
        flow.run_flow(pg, [{"do": "submit", "text": "创建"}], values={},
                      auto_submit=True, dup_check=True)
        raise AssertionError("页面喊重名应拦下提交")
    except flow.TitleDupError as e:
        expect("已存在" in e.hint, "异常带平台提示原文：%s" % e.hint)
        expect(getattr(pg, "_clicks", 0) == 0, "拦下时不点提交按钮")
    pg2 = _FlowPage(url="https://x/writer", call_results=[""])
    n2 = flow.run_flow(pg2, [{"do": "submit", "text": "创建"}], values={},
                       auto_submit=True, dup_check=True)
    expect(n2 == 1 and getattr(pg2, "_clicks", 0) == 1, "页面无重名照常提交")
    # 关键词表：核心重名词齐、宽泛词「重复」不收（防误伤其它字段校验）
    for w in ("已存在", "已被使用", "已被注册", "重名"):
        expect(w in flow.DUP_HINT_WORDS, "关键词缺 %s" % w)
    expect(flow.dup_hint(_FlowPage(call_results=[{"ok": False}])) == "",
           "非字符串返回当无提示")


def test_alt_titles_candidates():
    """撞名候选：无模型时静默降级机械变体；候选有界、不含原名、截 15 字。"""
    from core import modelhub
    orig = modelhub.resolve_orchestrator
    modelhub.resolve_orchestrator = lambda: None    # 测试环境不真调模型
    try:
        logs = []
        base = "我的马甲藏不住了"
        alts = manager._alt_titles("fanqie",
                                   {"book_name": base, "summary": "简介"},
                                   {"id": "t-dup"}, logs)
        expect(alts and len(alts) <= manager._ALT_COUNT + 2,
               "候选有界：%s" % alts)
        expect(all(a != base and len(a) <= 15 for a in alts),
               "候选非原名且截 15 字：%s" % alts)
        expect(manager._mech_titles(base) and
               any(m in alts for m in manager._mech_titles(base)),
               "机械变体兜底在场：%s" % alts)
        expect(manager._alt_titles("fanqie", {"book_name": ""},
                                   {"id": "t"}, logs) == [],
               "原名为空返回空队列")
    finally:
        modelhub.resolve_orchestrator = orig


def test_sync_renamed_book():
    """换名同步：bookmeta data、归档 Markdown、最新批次大纲统一到新名，
    台账另由 save_book 落——任何一环留旧名都会让下轮对账/重生成错位。"""
    from core import store
    wd = _TMP / "sync-dup-work"
    wd.mkdir(exist_ok=True)
    store._TASKS.pop("syncdup1", None)
    task = store.create_task({"type": "serial_novel", "title": "同步样例",
                              "goal": "测试目标", "context": "",
                              "workdir": str(wd)})
    tid = task["id"]                     # create_task 自动生成 id
    meta_entry = {"status": "done",
                  "data": {"book_name": "我的马甲藏不住了", "summary": "简介"},
                  "source": "测试", "at": "2026-09-30 00:00:00"}
    store.set_book_meta(tid, "fanqie", meta_entry)
    run = store.create_run("serial_novel", "同步样例", task_id=tid)
    store.update_run(run["id"], outline={
        "book_title": "我的马甲藏不住了",
        "chapters": [{"title": "第 1 章", "beats": "b", "hook": "", "highlight": ""}]})
    data = {"book_name": "我的马甲藏不住了"}
    logs = []
    manager._sync_renamed_book(tid, "fanqie", task, data, "马甲终藏不住", logs)
    expect(data["book_name"] == "马甲终藏不住", "data 就地改名")
    cur = store.get_task(tid)
    got = (((cur.get("book_meta") or {}).get("fanqie") or {}).get("data") or {})
    expect(got.get("book_name") == "马甲终藏不住", "bookmeta 已同步：%s" % got)
    md = (wd / "作品信息-番茄.md").read_text(encoding="utf-8")
    expect("马甲终藏不住" in md and "我的马甲藏不住了" not in md,
           "归档 Markdown 已重写为新名")
    outlines = [r.get("outline") for r in store.task_runs(tid)
                if isinstance(r.get("outline"), dict)]
    expect(outlines and outlines[0].get("book_title") == "马甲终藏不住",
           "最新批次大纲书名已同步：%s" % outlines)
    store._TASKS.pop(tid, None)          # 清场：不污染后续测试


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            check(name, fn)
    print("FAILS:", FAILS if FAILS else "none")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
