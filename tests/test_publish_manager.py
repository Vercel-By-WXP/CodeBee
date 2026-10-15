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
    ledger.record("fanqie", "upload_chapter_scheduled", task_id="t1", chapter_no=9,
                  title="第9章", ok=True)
    expect(ledger.published_chapters("t1", "fanqie") == {1, 2},
           "成功过的章入集合（先成功后失败仍在）；纯失败章不入：%s"
           % ledger.published_chapters("t1", "fanqie"))
    expect(len(ledger.recent(task_id="t1")) == 6, "recent 应含失败与排期记录（审计）")
    expect(ledger.published_chapters("t1", "qimao") == set(), "跨平台隔离")
    expect(ledger.scheduled_chapters("t1", "fanqie") == {9},
           "排期回执单独记录")
    expect(9 not in ledger.published_chapters("t1", "fanqie"),
           "排期中的章节不能提前算已发布")


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


def test_qimao_calibrated_draft_and_book_contract():
    """七猫作品与发草稿必须走已校准的真实动作链。

    草稿动作要有保存反馈和草稿箱核验；作品动作要有明确的确认创建提交
    步骤，避免只填表后 manager 误记成功。
    """
    import json as _json
    from core.publish import flow as _flow, qimao as _qm

    fp = Path(_flow.__file__).with_name("flows-qimao-calibrated.json")
    data = _json.loads(fp.read_text(encoding="utf-8"))
    # Runtime data has higher precedence than the packaged calibrated template.
    # Assert the exact flow manager will execute so an old ignored override
    # cannot silently remove the save receipt or draft action.
    runtime_draft = manager.load_flow("qimao", "upload_chapter_draft")
    runtime_publish = manager.load_flow("qimao", "upload_chapter")
    runtime_create = manager.load_flow("qimao", "create_book")
    expect(any(s.get("do") == "expect_text" for s in runtime_draft),
           "运行时七猫草稿表必须校验保存反馈")
    expect(any(s.get("do") == "verify" and "{draft_url}" in str(s.get("url"))
               for s in runtime_draft), "运行时七猫草稿表必须核验草稿箱")
    expect(any(s.get("do") == "click_real" and s.get("text") == "立即发布"
               for s in runtime_publish),
           "运行时七猫发布表必须点真实发布按钮，不能以存草稿代替")
    expect(any(s.get("do") == "verify_chapter_status" for s in runtime_publish),
           "运行时七猫发布表必须核验章节管理页真实状态")
    status_check = next(s for s in runtime_publish
                        if s.get("do") == "verify_chapter_status")
    expect(status_check.get("new_tab") is True,
           "七猫发布状态必须用新页签核验，避免编辑器导航取消提交")
    expect(any(s.get("do") == "submit" and s.get("text") == "确认创建"
               for s in runtime_create), "运行时七猫创建表必须执行确认创建")
    draft = data.get("upload_chapter_draft")
    expect(isinstance(draft, list), "七猫存草稿必须是步骤表")
    save = next((s for s in draft if s.get("do") == "click_real"
                 and s.get("text") == "存草稿"), None)
    expect(save and int(save.get("tries") or 0) >= 1,
           "七猫存草稿必须真实点击保存按钮")
    feedback = next((s for s in draft if s.get("do") == "expect_text"), None)
    expect(feedback and any("保存" in str(x) or "草稿" in str(x)
                            for x in (feedback.get("any") or [])),
           "七猫存草稿必须校验保存成功反馈")
    verify = next((s for s in draft if s.get("do") == "verify"), None)
    expect(verify and "{draft_url}" in str(verify.get("url")),
           "七猫存草稿必须核验草稿箱")

    create = data.get("create_book")
    submit = next((s for s in (create or []) if s.get("do") == "submit"), None)
    expect(submit and submit.get("text") == "确认创建",
           "七猫作品流程必须有确认创建提交")
    expect(_qm.CONFIG.get("book_manage") and "qimao.com" in _qm.CONFIG["home"],
           "七猫作品入口配置完整")

    # 旧版 data 覆盖文件会被本机优先加载；契约守卫必须把它淘汰，不能让
    # 「只填表/只点一下」的过期流程重新造成假成功。
    override = (_TMP / "publish" / "flows-qimao.json").resolve()
    override.write_text(json.dumps({
        "create_book": [{"do": "fill", "sel": "input", "key": "title"}],
        "upload_chapter_draft": [{"do": "fill", "sel": "textarea",
                                   "key": "chapter_body"}],
        "upload_chapter": [{"do": "submit", "text": "发布"}],
    }), encoding="utf-8")
    expect(any(s.get("do") == "submit" and s.get("text") == "确认创建"
               for s in manager.load_flow("qimao", "create_book")),
           "旧七猫建书覆盖必须回退校准流程")
    expect(any(s.get("do") == "click_real" and s.get("text") == "存草稿"
               for s in manager.load_flow("qimao", "upload_chapter_draft")),
           "旧七猫草稿覆盖必须回退校准流程")
    expect(any(s.get("do") == "click_real" and s.get("text") == "立即发布"
               for s in manager.load_flow("qimao", "upload_chapter")),
           "旧七猫发布覆盖必须回退到真实立即发布流程")
    expect(any(s.get("do") == "verify_chapter_status"
               for s in manager.load_flow("qimao", "upload_chapter")),
           "旧七猫发布覆盖不能移除真实章节状态核验")

    # 离线跑一遍草稿的关键尾链：即使 auto_submit=False，也必须点击存草稿，
    # 且没有反馈时应失败，防止批量把未保存章节记成成功。
    pg = _FlowPage(url="https://zuozhe.qimao.com/front/book-upload?id=1",
                   call_results=["保存成功", "第1章 七猫草稿"])
    pg.navigate = lambda url, timeout=30: setattr(pg, "_url", url)
    _flow.run_flow(pg, [save, feedback, verify],
                   values={"chapter_title": "第1章 七猫草稿",
                           "draft_url": "https://zuozhe.qimao.com/front/book-manage/draft?id=1"},
                   auto_submit=False)
    expect(getattr(pg, "_clicks", 0) == 1,
           "七猫草稿尾链必须实际点击存草稿")

    # 发布成功必须由章节管理页对应行证明；上传成功 toast
    # 单独出现都不够。一个真实发布状态通过，草稿状态和缺少目标行都失败。
    live_page = _FlowPage(call_results=[["第1章 七猫测试 待审核"]])
    live_page.navigate = lambda url, timeout=30: setattr(live_page, "_url", url)
    _flow.run_flow(live_page, [
        {"do": "verify_chapter_status", "url": "https://qimao.test/manage",
         "timeout": 1, "settle": 0, "row_sel": ".el-table__row",
         "live_status": ["待审核", "审核中", "已发布"], "draft_status": ["草稿"]},
    ], values={"chapter_title": "第1章 七猫测试", "chapter_no": 1})

    draft_page = _FlowPage(call_results=[["第1章 七猫测试 草稿"]])
    draft_page.navigate = lambda url, timeout=30: setattr(draft_page, "_url", url)
    try:
        _flow.run_flow(draft_page, [{"do": "verify_chapter_status",
            "url": "https://qimao.test/manage", "timeout": 1, "settle": 0,
            "row_sel": ".el-table__row", "live_status": ["待审核", "审核中", "已发布"],
            "draft_status": ["草稿"]}], values={"chapter_title": "第1章 七猫测试",
                                                       "chapter_no": 1})
        raise AssertionError("章节管理页仍为草稿时不能算发布成功")
    except _flow.FlowError as exc:
        expect("仍是草稿" in str(exc), "草稿状态失败信息要明确：%s" % exc)

    # 七猫当前校准证据没有定时发布入口；不要把普通草稿发布链冒充排期。
    expect("upload_chapter_scheduled" not in data,
           "七猫没有实测定时入口时不得生成猜测流程")


def test_qimao_chapter_title_selector_supports_editor_fields():
    """The Qimao editor title moved from input to textarea/contenteditable."""
    import json as _json
    from core.publish import flow as _flow, qimao as _qm

    selector_texts = []
    for flow in (_qm.FLOWS["upload_chapter"],
                 _qm.FLOWS["upload_chapter_draft"]):
        selector_texts.extend(
            step.get("sel", "") for step in flow
            if step.get("do") == "fill" and step.get("key") == "chapter_title")
    calibrated_path = Path(_qm.__file__).with_name("flows-qimao-calibrated.json")
    calibrated = _json.loads(calibrated_path.read_text(encoding="utf-8"))
    for name in ("upload_chapter", "upload_chapter_draft"):
        selector_texts.extend(
            step.get("sel", "") for step in calibrated[name]
            if step.get("do") == "fill" and step.get("key") == "chapter_title")

    expect(len(selector_texts) == 4,
           "内置及校准的发布/草稿流程都应有标题填充选择器：%s" % selector_texts)
    for selector in selector_texts:
        expect("textarea[placeholder" in selector and "请输入章节名称" in selector,
               "标题选择器须命中七猫真实章节标题 textarea：%s" % selector)

    # optional cannot disguise a failed publish: run_flow must fail before manager
    # can record the queued chapter as success when the confirm action is absent.
    missing_confirm = _FlowPage(click_results=[{"ok": False, "err": "not found"}])
    try:
        _flow.run_flow(missing_confirm, [{"do": "click_real", "text": "立即发布",
                                          "tries": 1}], auto_submit=True)
        raise AssertionError("找不到七猫立即发布按钮时必须失败")
    except _flow.FlowError:
        pass


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


def test_flow_js_click_accepts_preopened_modal():
    """真实鼠标已打开弹窗时，JS 兜底应视为完成而不是反向报错。"""
    from core.publish import flow
    pg = _FlowPage(call_results=[{"ok": False, "err": "modal-open"}])
    n = flow.run_flow(pg, [{"do": "js_click", "text": "立即发布",
                            "scope": "button", "tries": 1,
                            "skip_if_modal": True}], values={})
    expect(n == 1 and pg._calls == 1,
           "已打开确认弹窗应跳过兜底点击并继续流程：%s %s"
           % (n, pg._calls))


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


def test_flow_submit_always_flag():
    """always=true 的提交步（草稿流「存草稿」）不吃 auto_submit 人工闸：
    manual 语义下也真点并继续走后续步骤（2026-10-10 44 章假成功修复）。"""
    from core.publish import flow
    pg = _FlowPage()
    steps = [{"do": "fill", "sel": "input", "key": "t"},
             {"do": "submit", "text": "存草稿", "scope": "button",
              "always": True},
             {"do": "sleep", "s": 0.1}]
    n = flow.run_flow(pg, steps, values={"t": "第1章"},
                      auto_submit=False)
    expect(getattr(pg, "_clicks", 0) == 1 and n == 3,
           "always 提交步在 manual 语义下真点并走完：%s %s"
           % (getattr(pg, "_clicks", 0), n))


def test_flow_expect_text_hit_and_timeout():
    """expect_text（草稿保存凭据）：命中任一标记即过且记录；超时抛错点名标记。"""
    from core.publish import flow
    pg = _FlowPage(call_results=["操作成功：草稿已保存"])
    n = flow.run_flow(pg, [{"do": "expect_text",
                            "any": ["保存成功", "草稿已保存"],
                            "timeout": 3}])
    expect(n == 1, "命中标记通过：%d" % n)
    pg2 = _FlowPage(call_results=["发布失败：序号重复"])
    try:
        flow.run_flow(pg2, [{"do": "expect_text",
                             "any": ["保存成功", "草稿已保存"],
                             "timeout": 0.5}])
        raise AssertionError("超时应抛 FlowError")
    except flow.FlowError as e:
        expect("保存成功" in str(e) and "未见保存成功反馈" in str(e),
               "超时报错带标记清单：%s" % e)


def test_draft_single_chapter_mutex():
    """另一批自动发布在跑 → 单章草稿也拒起（浏览器互斥，2026-10-10）。"""
    from core.publish import auto as pub_auto
    from core import store
    t = store.create_task({"type": "serial_novel", "goal": "草稿互斥",
                           "workdir": str(_TMP)})
    ledger.save_book(t["id"], "fanqie", {"book_id": "888", "title": "草稿书"})
    fp = _chapter("第9章 互斥测.md", "# 第9章 互斥测\n\n" + "内容" * 300)
    pub_auto._running["t-other-mutex"] = {"status": "running",
                                          "platform": "fanqie"}
    try:
        ok, err = manager.upload_chapter_async(t["id"], "fanqie", fp,
                                               as_draft=True)
        expect(not ok and "互踩" in err and "t-other-mutex" in err,
               "单章草稿被另一批拒起：%s/%s" % (ok, err))
    finally:
        pub_auto._running.pop("t-other-mutex", None)
    store._TASKS.pop(t["id"], None)


# ------------------------------------------------- 发章 about:blank 三连败修复（2026-09-24）

class _DraftPage(_FlowPage):
    """upload_chapter_async(as_draft) 离线假页：真流程表走通存草稿链。

    call 队列喂 expect_text 的页面文本（含保存反馈标记「保存成功」）；
    verify 的 body 文本走新页签桩。"""

    def __init__(self):
        super().__init__(url="https://fanqienovel.com/main/writer/888/publish/",
                         call_results=[{"ok": True}, "保存成功 草稿已落草稿箱"])

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
    ledger.save_book(t["id"], "fanqie",
                     {"book_id": "7682979513168301118", "title": "草稿书"})
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


def test_quality_guard_ignores_newer_qa_run_for_article():
    """普通文章发布读取质量 verdict，不被更新的答疑 run 覆盖。"""
    from core import store
    from unittest.mock import patch
    task = {"id": "article-quality", "type": "article", "engine": "review",
            "workdir": str(_TMP)}
    review = {"id": "r-1", "status": "done", "verdict": {
        "engine": "review", "publishable": True,
        "scores": {"结构": 8.0, "表达": 8.0}, "major_issues": []}}
    for qa_fields in ({"op": "qa", "verdict": {"publishable": False}},
                      {"verdict": {"qa": True, "publishable": False}},
                      {"op": "qa", "verdict": {"qa": True, "answer": "已回答"}}):
        qa = dict({"id": "r-2", "status": "done"}, **qa_fields)
        with patch.object(store, "task_runs", return_value=[qa, review]):
            decision = manager._quality_release_guard(task, "fanqie")
        expect(decision.get("allowed"), "答疑 run 不应覆盖质量 verdict：%s" % decision)
        expect(decision.get("review_run_id") == "r-1", "必须取真正评审 run")


def test_quality_guard_latest_failed_review_still_blocks_article():
    """新版评审失败必须覆盖旧通过结果，即使随后完成了一轮答疑。"""
    from core import store
    from unittest.mock import patch
    task = {"id": "article-quality", "type": "article", "engine": "review"}
    for latest_value in (False, None):
        runs = [{"id": "r-3", "status": "done", "verdict": {"qa": True}},
                {"id": "r-2", "status": "done", "verdict": {
                    "publishable": latest_value, "scores": {"结构": 6}}},
                {"id": "r-1", "status": "done", "verdict": {
                    "publishable": True, "scores": {"结构": 8}}}]
        with patch.object(store, "task_runs", return_value=runs):
            decision = manager._quality_release_guard(task, "fanqie")
        expect(not decision.get("allowed"), "不能回退到旧通过结果：%s" % decision)
        expect(decision.get("review_run_id") == "r-2", "失败评审仍是最新证据")


def test_quality_guard_uses_actual_serial_workflow():
    """单篇小说和自定义连载按实际流水线使用各自的质量证据。"""
    from core import store
    from unittest.mock import patch
    run = {"id": "r-1", "status": "done", "verdict": {
        "publishable": True, "scores": {"情节": 8}}}
    with patch.object(store, "task_runs", return_value=[run]):
        single = manager._quality_release_guard(
            {"id": "single", "type": "novel", "engine": "review"}, "fanqie")
        serial = manager._quality_release_guard(
            {"id": "serial", "type": "custom", "engine": "review",
             "serial": {"chapters": 2}}, "fanqie")
        legacy_serial = manager._quality_release_guard(
            {"id": "legacy", "type": "serial_novel", "engine": "review"}, "fanqie")
    expect(single.get("allowed"), "单篇小说不要求连载证据：%s" % single)
    expect(not serial.get("allowed"), "自定义连载不能少全局证据：%s" % serial)
    expect(not legacy_serial.get("allowed"), "旧连载任务仍要求全局证据")


def test_quality_guard_missing_review_still_blocks_article():
    from core import store
    from unittest.mock import patch
    with patch.object(store, "task_runs", return_value=[
            {"id": "r-2", "status": "done", "verdict": {"qa": True}},
            {"id": "r-1", "status": "running", "verdict": {"publishable": True}}]):
        decision = manager._quality_release_guard(
            {"id": "article", "type": "article", "engine": "review"}, "fanqie")
    expect(not decision.get("allowed"), "没有已完成质量评审不能发布")


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
        publishers = [s for s in ups if s.get("do") == "fanqie_publish"]
        verifies = [s for s in ups if s.get("do") == "verify_chapter_status"]
        expect(len(publishers) == 1,
               "%s 必须且只能有一个番茄发布确认链：%s" % (tag, publishers))
        expect(len(verifies) == 1 and verifies[0].get("new_tab") is True
               and "待发布" in (verifies[0].get("live_status") or []),
               "%s 必须在新页签核验本章真实状态：%s" % (tag, verifies))
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
        if tag == "基线":
            draft = _json.loads(base_fp.read_text(encoding="utf-8"))["upload_chapter_draft"]
            expect(any(s.get("do") == "expect_text" for s in draft),
                   "番茄存草稿流程必须校验保存成功反馈")
            save = next((s for s in draft
                         if s.get("do") == "submit" and s.get("text") == "存草稿"), None)
            expect(save and save.get("always") is True,
                   "番茄存草稿提交步必须绕过人工闸真正点击")
            scheduled = _json.loads(base_fp.read_text(encoding="utf-8"))["upload_chapter_scheduled"]
            expect(any(s.get("do") == "js_click" and s.get("text") == "下一步"
                        for s in scheduled),
                   "番茄定时发布必须包含下一步 JS 兜底点击")
            scheduled_publish = [s for s in scheduled
                                 if s.get("do") == "fanqie_publish"]
            scheduled_verify = [s for s in scheduled
                                if s.get("do") == "verify_chapter_status"]
            expect(len(scheduled_publish) == 1
                   and scheduled_publish[0].get("force_schedule") is True,
                   "番茄定时发布必须强制定时：%s" % scheduled_publish)
            expect(len(scheduled_verify) == 1
                   and scheduled_verify[0].get("new_tab") is True,
                   "番茄定时发布必须核验章节管理页：%s" % scheduled_verify)

    base_fp = Path(_flow.__file__).with_name("flows-fanqie-calibrated.json")
    contract(_json.loads(base_fp.read_text(encoding="utf-8"))["upload_chapter"],
             "基线")
    contract(manager.load_flow("fanqie", "upload_chapter"), "解析后")


def test_fanqie_active_dialog_prefers_blocking_prompt():
    """An overlay prompt must win regardless of DOM order."""
    from core.publish.flow import _fanqie_active_dialog

    prompt = "发布提示\n检测到你还有错别字未修改，是否确定提交？"
    risk_prompt = "是否进行内容风险检测？\n开启后将消耗次数"
    settings = "发布设置\n定时发布\n确认发布"
    expect(_fanqie_active_dialog([prompt, settings]) == prompt,
           "提示框在 DOM 前面时仍应优先处理")
    expect(_fanqie_active_dialog([settings, prompt]) == prompt,
           "提示框在 DOM 后面时仍应优先处理")
    expect(_fanqie_active_dialog([settings, risk_prompt]) == risk_prompt,
           "可选风险检测框必须优先于发布设置")
    expect(_fanqie_active_dialog([settings]) == settings,
           "没有上层提示框时应继续处理发布设置")


def test_fanqie_publish_feedback_detects_new_quota_toast():
    """Quota feedback rendered in the page body must trigger a retry."""
    from core.publish.flow import _has_new_feedback

    before = {"dialog": "发布设置", "feedback": [], "body": "编辑器"}
    after = {"dialog": "发布设置", "feedback": [],
             "body": "编辑器\n提交字数超出每日上限"}
    expect(_has_new_feedback(before, after, ["每日上限"]),
           "正文里的额度提示应触发日期顺延")
    expect(not _has_new_feedback(after, after, ["每日上限"]),
           "相同旧提示不能误判为本次新反馈")

    from core.publish.flow import _fanqie_submission_limit_changed
    before = {"dialog": "发布设置", "body": "正文"}
    after = {"dialog": "发布设置", "body": "正文\n提交字数超出每日上限"}
    expect(_fanqie_submission_limit_changed(before, after),
           "投稿字数额度反馈应终止日期顺延")
    expect(not _fanqie_submission_limit_changed(after, after),
           "历史残留的投稿额度提示不能触发本次终止")


def test_fanqie_schedule_conflict_keeps_same_day_and_advances_thirty_minutes():
    from datetime import date, time
    from core.publish.flow import _next_fanqie_schedule_slot

    day, slot = _next_fanqie_schedule_slot(date(2026, 10, 15), time(0, 0))
    expect(day == date(2026, 10, 15) and slot == time(0, 30),
           "同时间冲突应当天顺延半小时")
    day, slot = _next_fanqie_schedule_slot(date(2026, 10, 15), time(23, 30))
    expect(day == date(2026, 10, 16) and slot == time(0, 0),
           "只有顺延越过午夜时才应进入下一天")


def test_fanqie_stale_override_falls_back_to_calibrated_flow():
    """旧用户覆盖缺少当前确认链时，不能盖掉随包校准流程。"""
    fp = (_TMP / "publish" / "flows-fanqie.json").resolve()
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps({
        "upload_chapter": [{"do": "verify", "url": "https://old.invalid"}],
        "upload_chapter_scheduled": [
            {"do": "fanqie_publish"},
            {"do": "verify_chapter_status"},
        ],
    }), encoding="utf-8")
    for action in ("upload_chapter", "upload_chapter_scheduled"):
        steps = manager.load_flow("fanqie", action)
        expect(sum(s.get("do") == "fanqie_publish" for s in steps) == 1,
               "旧覆盖必须回退到新发布链：%s" % action)
        expect(sum(s.get("do") == "verify_chapter_status" for s in steps) == 1,
               "旧覆盖必须回退到真实状态核验：%s" % action)


def test_fanqie_verify_uses_semantic_title_and_pagination():
    """章号定位跨页，标题比较使用正题，且接受待发布状态。"""
    from core.publish import flow as _flow
    pg = _FlowPage(call_results=[
        ["第108章 上一章 已发布"],
        {"done": False},
        ["第109章 山里的新账 待发布"],
    ])
    pg.navigate = lambda url, timeout=30: setattr(pg, "_url", url)
    _flow.run_flow(pg, [{
        "do": "verify_chapter_status",
        "url": "https://fanqie.test/manage",
        "timeout": 1,
        "settle": 0,
        "page_settle": 0,
        "row_sel": "tr",
        "pager_next_js": "()=>({done:false})",
        "live_status": ["待发布", "审核中", "已发布"],
    }], values={
        "chapter_no": "109",
        "chapter_name": "山里的新账",
        "chapter_title": "第一百零九章 山里的新账",
    })
    expect(pg._calls == 3, "应翻到第二页后命中目标章：%s" % pg._calls)


def test_fanqie_rejects_short_book_id():
    from core import store
    task = store.create_task({"type": "serial_novel", "goal": "番茄 ID 校验"})
    ok, err = manager.register_book(task["id"], "fanqie", "测试书", "768297951")
    expect(not ok and "完整长 ID" in err, "短番茄 ID 必须拒绝：%s/%s" % (ok, err))
    ok2, err2 = manager.register_book(
        task["id"], "fanqie", "测试书", "7682979513168301118")
    expect(ok2 and not err2, "完整番茄 ID 应通过：%s/%s" % (ok2, err2))


def test_fanqie_publish_feedback_must_be_new():
    """整页残留的旧限额文案不能把本次成功提交误判成拒绝。"""
    from core.publish import flow as _flow
    stale = {"dialog": "", "feedback": [], "body": "昨日已达每日上限"}
    expect(not _flow._has_new_feedback(stale, stale, ["每日上限"]),
           "整页旧文案不能算本次新反馈")
    before = {"dialog": "发布设置", "feedback": ["旧提示"]}
    after = {"dialog": "发布设置", "feedback": ["旧提示", "已达每日上限"]}
    expect(_flow._has_new_feedback(before, after, ["每日上限"]),
           "本次新增限额 toast 必须被识别")


def test_volume_step_flow_logic():
    """volume 步骤：当前卷命中直接过 / 已有目标卷必须点选并确认 /
    缺卷新建后点选并确认 / 无目标跳过 / 无卷选择器 optional 跳过。"""
    from core.publish import flow as _flow

    class VolPage:
        """分卷弹窗状态桩：选中条目并确认后，头部切到目标卷。"""
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
                return {"ok": True}
            if "mm.querySelectorAll(s)" in fn:             # 弹窗内点选目标卷
                _modal, _sel, title = a
                if title not in self.vols:
                    return {"ok": False, "err": "noitem"}
                self.selected = title
                return {"ok": True}
            if "querySelectorAll('button')" in fn:         # 弹窗确定
                self.current = getattr(self, "selected", self.current)
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

    # 2) 目标卷已存在（当前在别的卷）：必须点选、确认，头部切到目标卷
    pg2 = VolPage(["第一卷：山门换锁", "第二卷：县里有旧账"], "第一卷：山门换锁")
    vals2 = {"volume_name": "县里有旧账", "_volumes": []}
    run(pg2, vals2)
    expect(not pg2.created, "已存在的卷不重建：%s" % pg2.created)
    expect(pg2.current == "第二卷：县里有旧账", "已有目标卷选中并生效：%s" % pg2.current)
    expect(not pg2.modal, "选择后确认关闭弹窗")

    # 3) 缺卷：新建后必须点选并确认，且追加进共享缓存
    pg3 = VolPage(["第一卷：山门换锁", "第二卷：县里有旧账"], "第二卷：县里有旧账")
    vals3 = {"volume_name": "试着开一道口",
             "_volumes": ["第一卷：山门换锁", "第二卷：县里有旧账"]}
    run(pg3, vals3)
    expect(pg3.created == ["试着开一道口"], "新建输名：%s" % pg3.created)
    expect("第3卷：试着开一道口" in vals3["_volumes"], "缓存追加显示名：%s" % vals3["_volumes"])
    expect(pg3.current == "第3卷：试着开一道口", "新建目标卷选中并生效：%s" % pg3.current)

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


def test_manual_confirm_continues_batch():
    """人工点确认后，服务端应立即启动剩余章节并返回续跑结果。"""
    from core import store
    from core.publish import auto
    task = store.create_task({"type": "direct", "goal": "确认续跑"})
    ledger.save_book(task["id"], "fanqie", {"book_id": "bk-confirm", "title": "确认续跑"})
    ledger.record("fanqie", "upload_chapter_pending", task_id=task["id"],
                  chapter_no=1, book_id="bk-confirm", title="第1章",
                  ok=False, operation_status="pending")
    calls = []
    original = auto.publish_pending_async
    auto.publish_pending_async = lambda tid, plat, auto_submit=False: (
        calls.append((tid, plat, auto_submit)) or (True, ""))
    try:
        ok, err = manager.confirm_manual_chapter(task["id"], "fanqie", 1)
        expect(ok and not err, "确认后续跑不应报错：%s" % err)
        expect(calls == [(task["id"], "fanqie", True)],
               "确认后必须自动提交下一章：%s" % calls)
        expect(1 in ledger.published_chapters(task["id"], "fanqie"),
               "人工确认章必须进入已发布台账")
    finally:
        auto.publish_pending_async = original


def test_manual_confirm_without_pending_record():
    """Auto-submit failure followed by browser manual submit must unblock the chapter."""
    from core import store
    wd = _TMP / "manual-confirm-no-pending"
    wd.mkdir(exist_ok=True)
    task = store.create_task({"type": "serial_novel", "goal": "人工确认兜底",
                              "workdir": str(wd)})
    ledger.save_book(task["id"], "fanqie",
                     {"book_id": "bk-manual", "title": "人工确认兜底"})
    (wd / "chapter-7.md").write_text("# 第7章 手工已发\n\n" + "正文" * 100,
                                      encoding="utf-8")
    ledger.record("fanqie", "upload_chapter", task_id=task["id"],
                  chapter_no=7, title="第7章 手工已发", ok=False,
                  error="点击提交被挡住")
    ok, err = manager.confirm_manual_chapter(task["id"], "fanqie", 7)
    expect(ok and "手工发布" in err, "无 pending 记录也应能确认：%s" % err)
    expect(7 in ledger.published_chapters(task["id"], "fanqie"),
           "手工确认章必须进入已发布集合")


def test_manual_confirm_uses_failed_chapter_book_id():
    """A continuation task can recover a confirmed chapter without a copied binding."""
    from core import store
    wd = _TMP / "manual-confirm-lost-binding"
    wd.mkdir(exist_ok=True)
    task = store.create_task({"type": "serial_novel", "goal": "续写确认",
                              "workdir": str(wd)})
    (wd / "chapter-8.md").write_text("# 第8章 已发\n\n" + "正文" * 100,
                                     encoding="utf-8")
    ledger.record("fanqie", "upload_chapter", task_id=task["id"],
                  chapter_no=8, book_id="bk-original", ok=False,
                  error="点击提交被挡住")
    ok, err = manager.confirm_manual_chapter(task["id"], "fanqie", 8)
    expect(ok and "手工发布" in err, "丢失绑定时应允许核对后确认：%s" % err)
    row = next(r for r in ledger.recent(task_id=task["id"],
                                       platform="fanqie", limit=10)
               if r.get("chapter_no") == 8 and r.get("ok"))
    expect(row.get("book_id") == "bk-original",
           "确认记录必须沿用失败行的作品 ID：%s" % row)


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
