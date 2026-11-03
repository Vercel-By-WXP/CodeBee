"""技能包自身的一致性检查：Agent Skills 规范 + 文档里提到的命令必须真实存在。

这类「文档与实现脱节」的 bug 最难发现：用户照着 SKILL.md 敲命令，报 command not found。
"""

import io
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def read(name):
    with io.open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


def test_skill_frontmatter_is_valid():
    text = read("SKILL.md")
    assert text.startswith("---"), "SKILL.md 必须以 YAML frontmatter 开头"
    fm = text.split("---")[1]
    name = re.search(r"^name:\s*(\S+)\s*$", fm, re.M)
    desc = re.search(r"^description:\s*(.+?)(?=\n[a-z_]+:|\Z)", fm, re.M | re.S)
    assert name, "frontmatter 缺 name"
    assert desc, "frontmatter 缺 description"
    assert re.fullmatch(r"[a-z0-9-]{1,64}", name.group(1)), "name 必须是小写字母/数字/连字符，≤64 字符"
    assert len(desc.group(1).strip()) <= 1024, "description 不能超过 1024 字符"


def test_skill_commands_all_exist():
    """SKILL.md 命令表里出现的每个子命令，CLI 必须真的支持。"""
    from zfix.cli import build_parser

    parser = build_parser()
    actions = [a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction"]
    assert actions, "解析器没有子命令"
    known = set(actions[0].choices)
    text = read("SKILL.md")
    table = re.findall(r"^\|\s*`([a-z]+)", text, re.M)
    assert table, "SKILL.md 里没找到命令表"
    missing = [cmd for cmd in table if cmd not in known]
    assert not missing, f"SKILL.md 写了不存在的命令：{missing}"


def test_every_subcommand_has_help():
    from zfix.cli import main

    for cmd in ("init", "doctor", "sync", "list", "triage", "locate", "branch",
                "verify", "record", "learn", "guard", "preflight", "report", "next", "show"):
        with pytest.raises(SystemExit) as exc:
            main([cmd, "--help"])
        assert exc.value.code == 0


def test_readme_documents_all_toplevel_commands():
    from zfix.cli import build_parser

    parser = build_parser()
    choices = [a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction"][0].choices
    readme = read("README.md")
    missing = [cmd for cmd in choices if f"`{cmd}" not in readme]
    assert not missing, f"README 没写到这些命令：{missing}"


def test_seed_patterns_are_wellformed():
    import json

    data = json.loads(read(os.path.join("knowledge", "patterns", "seed.json")))
    assert isinstance(data, list) and len(data) >= 10
    from zfix.models import ROOT_CAUSES

    for row in data:
        assert row["id"] and row["title"], row
        assert row["root_cause"] in ROOT_CAUSES, f"{row['id']} 的根因不在词表里"
        assert row["prevention"].strip(), f"{row['id']} 缺预防规则"
        for regex in row.get("detect_regex") or []:
            re.compile(regex)  # 编译不过直接炸，避免坏正则静默失效


def test_seed_pattern_regex_does_not_match_clean_code():
    """防止「所有代码都报警」的垃圾规则：至少保证正常写法不被误判。"""
    import json

    data = {row["id"]: row for row in json.loads(read(os.path.join("knowledge", "patterns", "seed.json")))}
    clean_samples = [
        ("http-no-timeout", "resp = requests.get(url, timeout=(3, 10))"),
        ("http-no-timeout", "resp = requests.post(url, json=body, timeout=5)"),
        ("swallow-exception", "except ValueError as exc:\n    logger.warning('bad', exc_info=True)"),
        ("py-none-chain", "value = data.get('a')\nif value is None:\n    return"),
        ("first-item-assumption", "first = items[0] if items else None"),
    ]
    for pattern_id, code in clean_samples:
        pattern = data[pattern_id]
        for regex in pattern["detect_regex"]:
            compiled = re.compile(regex)
            assert not any(compiled.search(line) for line in code.splitlines()), (
                f"{pattern_id} 误报干净代码：{code!r} / {regex}"
            )


def test_recipes_are_wellformed():
    import json

    folder = os.path.join(HERE, "recipes")
    files = [f for f in os.listdir(folder) if f.endswith(".json")]
    assert len(files) >= 4
    for name in files:
        data = json.loads(read(os.path.join("recipes", name)))
        assert data["id"], name
        assert "detect" in data and "commands" in data, name
