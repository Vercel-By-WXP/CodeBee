# -*- coding: utf-8 -*-
"""内存重建 modelhub.py：HEAD + 仅本次「空流退回非流式」改动。
stdin=HEAD 内容，stdout=重建后内容。替换唯一命中，失败即非零退出。"""
import sys

b = sys.stdin.buffer.read().decode("utf-8")

old = '''        status, text, usage_d, err = _post_sse_http(
            url, headers, sbody, bool(prov.get("allow_private")), timeout, proto, on_delta)
        if status == 0 or err:
            return {"ok": False, "text": "", "tokens": 0, "usage": None, "error": err}
        if not usage_d.get("total"):
            usage_d["total"] = (usage_d.get("input", 0) + usage_d.get("output", 0)
                                + usage_d.get("cached", 0))
        return {"ok": True, "text": (text or "").strip(), "tokens": usage_d.get("total") or 0,
                "usage": usage_d, "error": ""}
'''
new = '''        status, text, usage_d, err = _post_sse_http(
            url, headers, sbody, bool(prov.get("allow_private")), timeout, proto, on_delta)
        if status == 0 or err:
            return {"ok": False, "text": "", "tokens": 0, "usage": None, "error": err}
        if not (text or "").strip():
            # 网关对 stream 请求回了 200 但没吐任何 SSE 事件（空流/普通 JSON 体，
            # 实测 vsllm 大请求会这样）：绝不能当成功返回空文本，掉到下方非流式重发
            pass
        else:
            if not usage_d.get("total"):
                usage_d["total"] = (usage_d.get("input", 0) + usage_d.get("output", 0)
                                    + usage_d.get("cached", 0))
            return {"ok": True, "text": (text or "").strip(), "tokens": usage_d.get("total") or 0,
                    "usage": usage_d, "error": ""}
'''

n = b.count(old)
assert n == 1, "count %d != 1" % n
b = b.replace(old, new)
sys.stdout.buffer.write(b.encode("utf-8"))
