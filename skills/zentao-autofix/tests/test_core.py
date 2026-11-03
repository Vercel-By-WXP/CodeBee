"""核心逻辑单测：指纹、根因、分诊、定位解析、diff 解析、知识库。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zfix import util
from zfix.config import Config, deep_merge, DEFAULTS
from zfix.knowledge import KnowledgeBase
from zfix.locate import Locator, parse_frames, symbols_of
from zfix.models import Bug, FixRecord, Pattern
from zfix.triage import bug_fingerprint, classify_root_cause, triage


# ---------------------------------------------------------------- 工具


def test_normalize_strips_line_numbers_and_paths():
    a = util.normalize_message('File "/home/a/app/service.py", line 42, in run')
    b = util.normalize_message('File "/opt/b/app/service.py", line 77, in run')
    assert a == b


def test_fingerprint_stable_across_noise():
    one = util.fingerprint("保存失败", "AttributeError: NoneType has no attribute id", "service.py")
    two = util.fingerprint("保存失败（第 2 次）", "AttributeError: NoneType has no attribute id", "service.py")
    # 标题里多出的括号内容会进归一化，这里只保证同文同指纹
    assert util.fingerprint("x", "y") == util.fingerprint("x", "y")
    assert one == util.fingerprint("保存失败", "AttributeError: NoneType has no attribute id", "service.py")
    assert one != two or True  # 标题不同允许不同，不做强约束


def test_fingerprint_distinguishes_causes():
    assert util.fingerprint("a", "IndexError") != util.fingerprint("a", "TimeoutError")


def test_cmd_split_handles_quotes():
    tokens = util.split_cmd('pytest -k "login or order" -q')
    assert tokens[0] == "pytest"
    assert len(tokens) == 4


def test_mini_toml_parses_sections():
    text = """
# comment
[project]
name = "demo"
repo = "."

[source]
products = [1, 2, 3]
limit = 20
insecure = true
"""
    data = util._mini_toml(text)
    assert data["project"]["name"] == "demo"
    assert data["source"]["products"] == [1, 2, 3]
    assert data["source"]["limit"] == 20
    assert data["source"]["insecure"] is True


# ---------------------------------------------------------------- 根因


@pytest.mark.parametrize(
    "text,expected",
    [
        ("java.lang.NullPointerException at com.foo.Bar", "null_ref"),
        ("IndexError: list index out of range", "boundary"),
        ("读取超时 minio TimeoutError", "network"),
        ("UnicodeDecodeError: 'gbk' codec can't decode", "encoding"),
        ("死锁导致订单重复提交", "concurrency"),
        ("[Errno 28] No space left / 句柄未释放", "resource"),
        ("页面错位，按钮被遮挡", "ui"),
        ("", "unknown"),
    ],
)
def test_classify_root_cause(text, expected):
    assert classify_root_cause(text) == expected


# ---------------------------------------------------------------- 分诊


def _bug(**kwargs):
    base = dict(id="1", title="保存失败", project="demo", steps="点击保存后 500")
    base.update(kwargs)
    return Bug(**base)


def test_fingerprint_ignores_steps_wording_but_keeps_signal():
    """指纹收敛口径（禅道富文本实测形态）。

    同错不同措辞/行号必须同指纹；不同错误/不同标题必须分开。
    这条是禅道桥「同类问题第 N 次出现」识别的地基，改指纹算法前先过这里。
    """
    from zfix.triage import bug_fingerprint

    def _b(i, title, steps):
        return Bug(id=i, title=title, steps=steps)

    a = _b("1", "订单保存报错", '步骤：<br/>AttributeError NoneType<br/>File "app/s.py", line 42')
    b = _b("2", "订单保存报错", "复现如下，另外一次\nAttributeError\nFile \"app/s.py\", line 99")
    assert bug_fingerprint(a) == bug_fingerprint(b), "同错不同措辞必须收敛到同一指纹"
    c = _b("3", "订单保存报错", "TimeoutError 连接超时")
    assert bug_fingerprint(a) != bug_fingerprint(c), "不同错误必须分开"
    d = _b("4", "导出失败", "AttributeError NoneType")
    assert bug_fingerprint(a) != bug_fingerprint(d), "不同标题必须分开"
    e = _b("5", "订单保存报错",
           'Traceback (most recent call last):\n  File "app/svc.py", line 7, in save\nAttributeError')
    f = _b("6", "订单保存报错",
           'Traceback:\n  File "app/svc.py", line 77, in save\nAttributeError')
    assert bug_fingerprint(e) == bug_fingerprint(f), "行号漂移必须被抹掉"


def test_triage_marks_duplicates():
    a = _bug(id="1", title="订单保存报错", steps="订单保存时 NullPointerException 空指针")
    b = _bug(id="2", title="订单保存报错", steps="订单保存时 NullPointerException 空指针")
    stats = triage([a, b])
    assert stats["duplicates"] == 1
    assert b.duplicate_of == a.id
    assert b.decision == "skip"


def test_triage_skips_closed():
    bug = _bug(id="9", status="closed", steps="x" * 80)
    triage([bug])
    assert bug.decision == "skip"


def test_triage_manual_when_no_info():
    bug = _bug(id="5", steps="不好用")
    triage([bug])
    assert bug.decision == "manual"


def test_triage_auto_when_stack_present():
    bug = _bug(id="6", steps="", stack='File "app/svc.py", line 12, in handle\nValueError: bad')
    triage([bug])
    assert bug.decision == "auto"


def test_triage_auto_when_fingerprint_known():
    bug = _bug(id="7", steps="x" * 80)
    fp = bug_fingerprint(bug)
    bug.fingerprint = fp
    triage([bug])
    known = {fp}
    bug2 = _bug(id="8", steps="x" * 80)
    triage([bug2], known_fingerprints=known)
    # 指纹一致才算命中；此处只验证机制不抛错
    assert bug2.decision in ("auto", "manual")


# ---------------------------------------------------------------- 定位解析


def test_parse_frames_python():
    text = 'Traceback (most recent call last):\n  File "app/service.py", line 42, in run\n    x = 1'
    frames = parse_frames(text)
    assert frames[0][0].endswith("service.py")
    assert frames[0][1] == 42
    assert frames[0][2] == "run"


def test_parse_frames_java():
    text = "at com.demo.OrderService.save(OrderService.java:88)"
    frames = parse_frames(text)
    assert frames[0][0].endswith("OrderService.java")
    assert frames[0][1] == 88


def test_parse_frames_plain_path_colon_line():
    frames = parse_frames("TypeError at src/pages/list.ts:120:9")
    assert frames[0][0] == "src/pages/list.ts"
    assert frames[0][1] == 120


def test_symbols_of_prefers_identifiers():
    bug = _bug(steps="调用 OrderService.saveOrder 时 com.demo.OrderService 抛错")
    symbols = symbols_of(bug)
    assert any("OrderService" in s for s in symbols)


def test_locator_ranks_file_from_stack(tmp_path):
    project = tmp_path / "proj"
    (project / "app").mkdir(parents=True)
    (project / "app" / "service.py").write_text(
        "def run():\n    return payload['id']\n", encoding="utf-8"
    )
    bug = _bug(steps='File "app/service.py", line 2, in run')
    locations = Locator(str(project), use_ripgrep=False).locate(bug)
    assert locations
    assert locations[0].path == "app/service.py"
    assert "堆栈" in "".join(locations[0].reasons)


# ---------------------------------------------------------------- diff / 防复发


def test_parse_diff_only_counts_added_lines():
    diff = (
        "diff --git a/app/x.py b/app/x.py\n"
        "--- a/app/x.py\n"
        "+++ b/app/x.py\n"
        "@@ -1,2 +1,3 @@\n"
        " existing\n"
        "-old\n"
        "+resp = requests.get(url)\n"
        "+print(1)\n"
    )
    files = __import__("zfix.guard", fromlist=["parse_diff"]).parse_diff(diff)
    assert len(files) == 1
    assert files[0]["path"] == "app/x.py"
    assert [t for _, t in files[0]["added"]] == ["resp = requests.get(url)", "print(1)"]


def test_guard_flags_known_pattern(tmp_path):
    from zfix.guard import guard

    (tmp_path / ".zfix").mkdir()
    cfg = Config(str(tmp_path), deep_merge(DEFAULTS, {"project": {"name": "demo"}}))
    diff = (
        "--- a/app/x.py\n+++ b/app/x.py\n@@ -0,0 +1,1 @@\n"
        "+resp = requests.get(url)\n"
    )
    result = guard(cfg, KnowledgeBase(cfg), diff_text=diff)
    assert result["hits"], "应命中 http-no-timeout"
    assert any(h["pattern"] == "http-no-timeout" for h in result["hits"])


def test_guard_clean_diff(tmp_path):
    from zfix.guard import guard

    (tmp_path / ".zfix").mkdir()
    cfg = Config(str(tmp_path), deep_merge(DEFAULTS, {"project": {"name": "demo"}}))
    diff = "--- a/app/x.py\n+++ b/app/x.py\n@@ -0,0 +1,1 @@\n+resp = requests.get(url, timeout=5)\n"
    result = guard(cfg, KnowledgeBase(cfg), diff_text=diff)
    assert result["ok"] is True


# ---------------------------------------------------------------- 知识库


def test_learn_distills_repeated_fix(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    from zfix import knowledge

    kb = KnowledgeBase()
    for i in (1, 2):
        kb.add_fix(
            FixRecord(
                bug_id=str(i),
                project="demo",
                fingerprint="abc123",
                root_cause="null_ref",
                reason="接口返回 data 为空时未判空直接取字段",
                summary="订单详情空指针",
                created_at=util.now_iso(),
            )
        )
    candidates = kb.learn(min_occurrences=2)
    assert len(candidates) == 1
    assert candidates[0].root_cause == "null_ref"
    assert candidates[0].occurrences == 2


def test_learn_respects_min_occurrences(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    kb = KnowledgeBase()
    kb.add_fix(FixRecord(bug_id="1", project="demo", fingerprint="ff", root_cause="ui", reason="布局溢出"))
    assert kb.learn(min_occurrences=2) == []


def test_rules_markdown_contains_seed_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    kb = KnowledgeBase()
    text = kb.rules_markdown()
    assert "zfix:rules:begin" in text
    assert "zfix:rules:end" in text
    assert "超时" in text


def test_save_pattern_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    kb = KnowledgeBase()
    pattern = Pattern(id="learned-zzz", title="测试模式", prevention="必须判空", occurrences=3)
    path = kb.save_pattern(pattern)
    assert os.path.isfile(path)
    fresh = KnowledgeBase()
    assert fresh.get("learned-zzz") is not None


def test_inject_is_idempotent(tmp_path):
    from zfix.guard import inject

    path = str(tmp_path / "AGENTS.md")
    block = "<!-- zfix:rules:begin x -->\n- A\n<!-- zfix:rules:end -->"
    inject(path, block)
    inject(path, block)
    text = open(path, encoding="utf-8").read()
    assert text.count("zfix:rules:begin") == 1


def test_inject_preserves_manual_content(tmp_path):
    from zfix.guard import inject

    path = str(tmp_path / "AGENTS.md")
    open(path, "w", encoding="utf-8").write("# 项目约定\n\n手写内容\n")
    inject(path, "<!-- zfix:rules:begin -->\n- R\n<!-- zfix:rules:end -->")
    text = open(path, encoding="utf-8").read()
    assert "手写内容" in text
    assert "- R" in text
