"""端到端流程：init → sync → triage → context → verify → record → learn → report。

全程使用临时目录 + local 缺陷源，不碰禅道、不碰真实仓库。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zfix.cli import main

INBOX = [
    {
        "id": "101",
        "title": "订单详情页保存报 NullPointerException",
        "status": "active",
        "severity": "2",
        "pri": "1",
        "steps": "打开订单详情（该订单无收货地址），点保存。\n"
        'Traceback (most recent call last):\n  File "app/service.py", line 42, in save\n'
        "AttributeError: 'NoneType' object has no attribute 'get'\n",
    },
    {
        "id": "102",
        "title": "订单详情页保存报 NullPointerException",
        "status": "active",
        "severity": "2",
        "pri": "1",
        "steps": "打开订单详情（该订单无收货地址），点保存。\n"
        'Traceback (most recent call last):\n  File "app/service.py", line 43, in save\n'
        "AttributeError: 'NoneType' object has no attribute 'get'\n",
    },
    {"id": "103", "title": "列表页样式错位", "status": "active", "steps": "不好看"},
    {"id": "104", "title": "已修复的问题", "status": "closed", "steps": "x" * 80},
]

CONFIG = """[project]
name = "demo"
repo = "."

[source]
kind = "local"
local_dir = ".zfix/inbox"
status = "active"

[stack]
lang = "python"
test = ""

[guard]
block = false
"""


@pytest.fixture()
def project(tmp_path, monkeypatch):
    # 隔离 ~/.zfix，避免污染真实知识库
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "app" / "service.py").write_text(
        "def save(order):\n"
        "    address = order.get('address')\n"
        "    return address.get('city')\n",
        encoding="utf-8",
    )
    (root / ".zfix" / "inbox").mkdir(parents=True)
    (root / ".zfix" / "inbox" / "bugs.json").write_text(
        json.dumps(INBOX, ensure_ascii=False), encoding="utf-8"
    )
    cfg_path = root / ".zfix" / "config.toml"
    cfg_path.write_text(CONFIG, encoding="utf-8")
    return root


def run(root, *args, expect=0):
    code = main(["--root", str(root)] + list(args)) if "--json" not in args else main(
        [a for a in ["--root", str(root)] + list(args)]
    )
    return code


def test_full_flow(project, capsys):
    root = project

    assert run(root, "doctor") in (0, 1)
    out = capsys.readouterr().out
    assert "自检" in out

    assert run(root, "sync") == 0
    assert "拉取 4 条" in capsys.readouterr().out

    assert run(root, "list") == 0
    listing = capsys.readouterr().out
    assert "101" in listing

    assert run(root, "triage") == 0
    triage_out = capsys.readouterr().out
    assert "分诊完成" in triage_out
    assert "重复 1" in triage_out or "skip" in triage_out

    # 上下文包
    assert run(root, "context", "101") == 0
    ctx_out = capsys.readouterr().out
    assert "上下文包已生成" in ctx_out
    ctx_dir = root / ".zfix" / "context"
    files = list(ctx_dir.glob("*.md"))
    assert files
    content = files[0].read_text(encoding="utf-8")
    for section in ("## 1. 基本信息", "## 3. 候选代码位置", "验收命令", "完成标准"):
        assert section in content
    assert "app/service.py" in content

    # 归档
    assert run(
        root, "record", "101",
        "--root-cause", "null_ref",
        "--reason", "order 缺少 address 时未判空直接取 city",
        "--files", "app/service.py",
    ) == 0
    rec_out = capsys.readouterr().out
    assert "已归档" in rec_out

    # 学习（只出现一次，不应产出模式）
    assert run(root, "learn", "--min", "1") == 0
    learn_out = capsys.readouterr().out
    assert "候选模式" in learn_out

    # 报表
    assert run(root, "report") == 0
    report = capsys.readouterr().out
    assert "修复档案" in report
    assert "验收通过率" in report


def test_record_requires_reason(project, capsys):
    run(project, "sync")
    with pytest.raises(SystemExit):
        run(project, "record", "101", "--root-cause", "null_ref", "--reason", "  ")


def test_record_rejects_unknown_cause(project):
    run(project, "sync")
    with pytest.raises(SystemExit):
        main(["--root", str(project), "record", "101", "--reason", "x", "--root-cause", "nope"])


def test_guard_reads_diff_file(project, capsys, tmp_path):
    diff = tmp_path / "x.diff"
    diff.write_text(
        "--- a/app/x.py\n+++ b/app/x.py\n@@ -0,0 +1,1 @@\n+resp = requests.get(url)\n",
        encoding="utf-8",
    )
    code = main(["--root", str(project), "guard", "--diff-file", str(diff)])
    assert code == 0  # block=false → 只提示
    assert "命中" in capsys.readouterr().out


def test_guard_block_returns_nonzero(project, capsys, tmp_path):
    cfg = project / ".zfix" / "config.toml"
    cfg.write_text(CONFIG.replace("block = false", "block = true"), encoding="utf-8")
    diff = tmp_path / "x.diff"
    diff.write_text(
        "--- a/app/x.py\n+++ b/app/x.py\n@@ -0,0 +1,1 @@\n+resp = requests.get(url)\n",
        encoding="utf-8",
    )
    assert main(["--root", str(project), "guard", "--diff-file", str(diff)]) == 1


def test_preflight_writes_agents_md(project, capsys):
    assert main(["--root", str(project), "preflight", "--platforms", "claude,codex"]) == 0
    agents = project / "AGENTS.md"
    assert agents.is_file()
    text = agents.read_text(encoding="utf-8")
    assert "zfix:rules:begin" in text
    assert "已知错误模式" in text
    claude = project / "CLAUDE.md"
    assert claude.is_file()


def test_verify_skips_missing_tool(project, capsys):
    cfg = project / ".zfix" / "config.toml"
    cfg.write_text(CONFIG.replace('test = ""', 'test = "zfix_definitely_missing_tool --run"'), encoding="utf-8")
    run(project, "sync")
    code = main(["--root", str(project), "verify", "101"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "SKIP" in out


def test_next_picks_bug_and_writes_context(project, capsys):
    run(project, "sync")
    run(project, "triage")
    capsys.readouterr()
    assert main(["--root", str(project), "next"]) == 0
    out = capsys.readouterr().out
    assert "下一件事" in out
    assert list((project / ".zfix" / "context").glob("*.md"))
