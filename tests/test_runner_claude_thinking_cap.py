"""claude 分支必须给思考预算兜个上限（2026-11-05 实案）。

claude-code 认不出绑定的第三方模型时（``[claude-code:unrecognized_model]``，
实案是 ``glm-5.3-flash`` 打在 Bigmodel 的 anthropic 面上），不会套任何模型
画像，thinking 预算无人约束：两本新书的 ``draft-c1`` 各自烧到 3.4~3.6 万
思考 token、二十多分钟，正文一个字都没有——而同一 prompt 在大纲步骤
（无 thinking）只用了 4 分钟。

这不是「慢」，是「不收敛」。守四件事：
1. 默认必须带上 ``MAX_THINKING_TOKENS``；
2. 绑定里显式配了的值原样保留（它没有 ANTHROPIC_ 前缀，过滤时最容易被丢掉）；
3. env 可覆盖；
4. env 置 ``off`` 表示不干预（老行为可退回）。
"""

import json
import pathlib

import pytest

from core import runner


def _claude_env(agent_env):
    agent = {"command": "claude", "env": dict(agent_env)}
    argv, _stdin, _prompt, _tmp = runner._build_call(
        agent, "claude", None, False, "glm-5.3-flash", "hi")
    assert "--settings" in argv, "claude 分支必须落一次性 --settings"
    path = pathlib.Path(argv[argv.index("--settings") + 1])
    return json.loads(path.read_text(encoding="utf-8"))["env"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("TUTTI_CLAUDE_MAX_THINKING_TOKENS", raising=False)


def test_default_cap_present():
    env = _claude_env({"ANTHROPIC_BASE_URL": "https://example.invalid"})
    assert env.get("MAX_THINKING_TOKENS") == str(runner._CLAUDE_DEFAULT_MAX_THINKING)
    assert int(env["MAX_THINKING_TOKENS"]) > 0


def test_explicit_binding_value_wins():
    env = _claude_env({"ANTHROPIC_BASE_URL": "https://example.invalid",
                       "MAX_THINKING_TOKENS": "512"})
    assert env["MAX_THINKING_TOKENS"] == "512"


def test_env_override(monkeypatch):
    monkeypatch.setenv("TUTTI_CLAUDE_MAX_THINKING_TOKENS", "2048")
    env = _claude_env({"ANTHROPIC_BASE_URL": "https://example.invalid"})
    assert env["MAX_THINKING_TOKENS"] == "2048"


def test_env_off_disables_cap(monkeypatch):
    monkeypatch.setenv("TUTTI_CLAUDE_MAX_THINKING_TOKENS", "off")
    env = _claude_env({"ANTHROPIC_BASE_URL": "https://example.invalid"})
    assert "MAX_THINKING_TOKENS" not in env


def test_bad_env_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("TUTTI_CLAUDE_MAX_THINKING_TOKENS", "not-a-number")
    env = _claude_env({"ANTHROPIC_BASE_URL": "https://example.invalid"})
    assert env["MAX_THINKING_TOKENS"] == str(runner._CLAUDE_DEFAULT_MAX_THINKING)
