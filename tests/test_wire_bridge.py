"""wire 桥（Anthropic↔OpenAI 协议翻译）的守测。

背景：claude-code 只吃 Anthropic 面，而不少网关只开 OpenAI 面
（2026-11-05 实案：本机 Hy4 网关 `/v1/messages` 404）。此前 CodeBee 只有
「协议挑面」（wire_caps），没有「协议转换」，于是这类供应商对 claude
等于没有。`core.wire_bridge` 补了转换层，`modelhub._bridged_endpoint`
决定什么时候接管。

这里守三件事：
1. 请求体翻译正确（system 拍平、tool_result→role=tool、tool_use→tool_calls）；
2. 上游响应回译正确（finish_reason→stop_reason、usage 字段名）；
3. **接管范围**：只对「openai 上游 + 需要 anthropic」生效，其余一律不接管。
   第 3 条最重要——桥是增益，一旦越界接管就把本来能直连的链路也改道了。
"""
import json

from core import modelhub
from core import wire_bridge


def _cfg():
    return {"base": "http://127.0.0.1:1/v1", "key": "k", "model": ""}


# ---------------------------------------------------------------- 请求翻译
def test_system_is_flattened_to_first_message():
    body = {"system": [{"type": "text", "text": "你是编辑"},
                       {"type": "text", "text": "只输出正文"}],
            "messages": [{"role": "user", "content": "写一段"}],
            "max_tokens": 100}
    out = wire_bridge.to_openai(body, _cfg())
    assert out["messages"][0] == {"role": "system", "content": "你是编辑\n只输出正文"}
    assert out["messages"][1] == {"role": "user", "content": "写一段"}


def test_tool_result_becomes_role_tool():
    body = {"messages": [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "文件内容"},
        {"type": "text", "text": "继续"}]}]}
    msgs = wire_bridge.to_openai(body, _cfg())["messages"]
    assert msgs[0] == {"role": "tool", "tool_call_id": "t1", "content": "文件内容"}
    assert msgs[1] == {"role": "user", "content": "继续"}


def test_assistant_tool_use_becomes_tool_calls():
    body = {"messages": [{"role": "assistant", "content": [
        {"type": "text", "text": "我先看文件"},
        {"type": "tool_use", "id": "t2", "name": "Read",
         "input": {"path": "a.md"}}]}]}
    msg = wire_bridge.to_openai(body, _cfg())["messages"][0]
    assert msg["content"] == "我先看文件"
    assert msg["tool_calls"][0]["function"]["name"] == "Read"
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"path": "a.md"}


def test_tools_and_tool_choice_mapping():
    body = {"messages": [{"role": "user", "content": "x"}],
            "tools": [{"name": "write", "description": "写文件",
                       "input_schema": {"type": "object", "properties": {}}}],
            "tool_choice": {"type": "any"}}
    out = wire_bridge.to_openai(body, _cfg())
    assert out["tools"][0]["function"]["name"] == "write"
    assert out["tool_choice"] == "required"
    body["tool_choice"] = {"type": "tool", "name": "write"}
    assert wire_bridge.to_openai(body, _cfg())["tool_choice"] == {
        "type": "function", "function": {"name": "write"}}


# ---------------------------------------------------------------- 响应回译
def test_full_response_maps_stop_reason_and_usage():
    data = {"model": "hy4-preview",
            "choices": [{"finish_reason": "tool_calls",
                         "message": {"content": "看下",
                                     "tool_calls": [{"id": "c1", "function": {
                                         "name": "Read",
                                         "arguments": "{\"path\": \"a.md\"}"}}]}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 22}}
    msg = wire_bridge.from_openai_full(data, "hy4-preview")
    assert msg["stop_reason"] == "tool_use"
    assert msg["usage"] == {"input_tokens": 11, "output_tokens": 22}
    kinds = [c["type"] for c in msg["content"]]
    assert kinds == ["text", "tool_use"]
    assert msg["content"][1]["input"] == {"path": "a.md"}


# ---------------------------------------------------------------- 接管范围
def _prov(**kw):
    base = {"id": "prov-x", "name": "x", "protocol": "openai",
            "base_url": "http://127.0.0.1:8788/v1", "api_key": "k"}
    base.update(kw)
    return base


def test_bridge_takes_over_openai_provider_for_claude():
    ep = modelhub._entry_endpoint(_prov(), ("anthropic",))
    assert ep and ep[0] == "anthropic"
    assert "/v1" not in ep[1]            # 给的是桥地址，不是上游地址
    assert ep[1].startswith("http://127.0.0.1:")


def test_bridge_never_takes_over_when_openai_is_acceptable():
    ep = modelhub._entry_endpoint(_prov(), ("openai",))
    assert ep == ("openai", "http://127.0.0.1:8788/v1", "responses")


def test_bridge_skips_anthropic_provider_and_keyless():
    assert modelhub._entry_endpoint(
        _prov(protocol="anthropic", base_url="https://x/api/anthropic"),
        ("openai",)) is None
    assert modelhub._entry_endpoint(_prov(api_key=""), ("anthropic",)) is None
