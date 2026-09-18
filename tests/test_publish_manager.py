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
    expect(manager.load_flow("fanqie", "create_book")[0]["url"] == "{home}",
           "未覆盖的 action 仍走内置")


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


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            check(name, fn)
    print("FAILS:", FAILS if FAILS else "none")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
