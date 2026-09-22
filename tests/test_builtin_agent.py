# -*- coding: utf-8 -*-
"""内置智能体（builtin_agent）测试：协议工具循环、路径守卫、直连引擎集成。

覆盖：
  1) resolve：无供应商 → None（安全回退 CLI）；编排者配置优先；
  2) openai 工具循环：tool_calls → 执行 → tool 结果回传 → 最终文本；
  3) anthropic 工具循环：tool_use blocks 同上；
  4) 路径守卫：../ 与盘符拒绝；合法路径读/写/列正常；
  5) 集成：种一个供应商 + 编排者配置后，_run_direct 走内置智能体
     （不经 CLI），步骤 output 干净、工作目录文件真实落盘。
传输层统一打桩在 builtin_agent._post_json（不发起真实网络）。
"""
from __future__ import annotations

import base64

from base import BaseTest


class TestBuiltinResolveEmpty(BaseTest):
    def runTest(self):
        from app.core import builtin_agent
        # 空数据目录：无供应商无编排者 → None（调用方回退 CLI/mock 路径）
        self.assertIsNone(builtin_agent.resolve())


class TestBuiltinExplicitConversationRoute(BaseTest):
    def runTest(self):
        """会话级厂商/模型只随任务保存，且失效时不得回退其他渠道。"""
        from unittest.mock import patch
        from app.core import builtin_agent, modelhub, pipeline, store

        modelhub._save({
            "providers": [{
                "id": "prov-chat", "name": "会话厂商", "protocol": "openai",
                "base_url": "http://gw.test/v1", "api_key": "sk-test",
                "enabled": True, "model": "chat-default",
                "models": [{"name": "chat-exact", "enabled": True, "priority": 1}],
            }],
            "bindings": {},
        })
        selected = builtin_agent.resolve("prov-chat", "chat-exact")
        self.assertEqual(selected["provider_id"], "prov-chat")
        self.assertEqual(selected["model"], "chat-exact")

        task = store.create_task({
            "type": "direct", "title": "指定会话渠道", "goal": "回答问题",
            "workdir": str(self.workdir), "direct_provider_id": "missing-provider",
            "thinking": "high",
        })
        self.assertEqual(task["direct_provider_id"], "missing-provider")
        self.assertEqual(task["thinking"], "high")
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents
        with patch.object(pipeline, "_run_step") as run_step:
            pipeline.execute_run(run["id"])
        failed = store.get_run(run["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("指定的对话厂商或模型不可用", failed["error"])
        run_step.assert_not_called()


class TestBuiltinOpenaiToolLoop(BaseTest):
    def runTest(self):
        from app.core import builtin_agent
        bi = {"prov": {"id": "p1", "name": "P1", "protocol": "openai",
                       "base_url": "http://gw.test/v1", "api_key": "sk-test",
                       "enabled": True, "model": "m1"},
              "model": "m1", "provider_id": "p1", "provider_name": "P1"}
        calls = []

        def fake_post(url, headers, body, allow_private, timeout):
            calls.append(body)
            if len(calls) == 1:
                self.assertTrue(body.get("tools"))   # 首轮带工具表
                return 200, {"choices": [{"message": {"content": "", "tool_calls": [
                    {"id": "c1", "type": "function",
                     "function": {"name": "write_file",
                                  "arguments": '{"path": "notes.md", "content": "内置写的"}'}},
                ]}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                "total_tokens": 15}}, ""
            return 200, {"choices": [{"message": {"content": "已写好 notes.md"}}],
                         "usage": {"prompt_tokens": 20, "completion_tokens": 8,
                                   "total_tokens": 28}}, ""

        orig = builtin_agent._post_json
        builtin_agent._post_json = fake_post
        try:
            res = builtin_agent.run(bi, "写个文件", str(self.workdir))
        finally:
            builtin_agent._post_json = orig
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["text"], "已写好 notes.md")
        self.assertEqual(res["iterations"], 2)
        self.assertEqual(res["usage"]["total"], 43)   # 两轮累计
        self.assertTrue((self.workdir / "notes.md").is_file())
        self.assertEqual((self.workdir / "notes.md").read_text(encoding="utf-8"), "内置写的")
        # 第二轮请求把工具结果以 role=tool 回传（openai 形状，内容=工具执行回执）
        tool_msgs = [m for m in calls[1]["messages"] if m.get("role") == "tool"]
        self.assertTrue(tool_msgs and "已写入 notes.md" in tool_msgs[0]["content"])


class TestBuiltinAnthropicToolLoop(BaseTest):
    def runTest(self):
        from app.core import builtin_agent
        bi = {"prov": {"id": "p2", "name": "P2", "protocol": "anthropic",
                       "base_url": "http://gw.test/v1", "api_key": "sk-test",
                       "enabled": True, "model": "m2"},
              "model": "m2", "provider_id": "p2", "provider_name": "P2"}
        calls = []

        def fake_post(url, headers, body, allow_private, timeout):
            calls.append(body)
            if len(calls) == 1:
                self.assertIn("tools", body)
                self.assertNotIn("tools", body.get("messages", [{}])[0])
                return 200, {"content": [
                    {"type": "text", "text": "我先看看目录"},
                    {"type": "tool_use", "id": "t1", "name": "list_files",
                     "input": {"path": ""}},
                ], "usage": {"input_tokens": 12, "output_tokens": 6}}, ""
            return 200, {"content": [{"type": "text", "text": "目录是空的"}],
                         "usage": {"input_tokens": 30, "output_tokens": 4}}, ""

        orig = builtin_agent._post_json
        builtin_agent._post_json = fake_post
        try:
            res = builtin_agent.run(bi, "看看目录", str(self.workdir))
        finally:
            builtin_agent._post_json = orig
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["text"], "目录是空的")
        # 第二轮请求把工具结果以 tool_result 回传（anthropic 形状）
        last = calls[1]["messages"][-1]
        self.assertEqual(last["role"], "user")
        self.assertEqual(last["content"][0]["type"], "tool_result")
        self.assertIn("空目录", last["content"][0]["content"])


class TestBuiltinReasoningWire(BaseTest):
    def runTest(self):
        """思考程度只写入支持该字段的 OpenAI wire。"""
        from app.core import builtin_agent

        def capture(proto):
            bodies = []
            bi = {"prov": {"id": "p", "name": "P", "protocol": proto,
                           "base_url": "http://gw.test/v1", "api_key": "sk-test",
                           "enabled": True, "model": "m"},
                  "model": "m", "provider_id": "p", "provider_name": "P",
                  "reasoning_effort": "high"}

            def fake_post(url, headers, body, allow_private, timeout):
                bodies.append(body)
                if proto == "anthropic":
                    return 200, {"content": [{"type": "text", "text": "ok"}]}, ""
                return 200, {"choices": [{"message": {"content": "ok"}}]}, ""

            orig = builtin_agent._post_json
            builtin_agent._post_json = fake_post
            try:
                res = builtin_agent.run(bi, "回答", str(self.workdir))
            finally:
                builtin_agent._post_json = orig
            self.assertTrue(res["ok"], res.get("error"))
            return bodies[0]

        self.assertEqual(capture("openai").get("reasoning_effort"), "high")
        self.assertNotIn("reasoning_effort", capture("anthropic"))


class TestBuiltinPathGuard(BaseTest):
    def runTest(self):
        from app.core import builtin_agent
        # 越界/非法路径一律拒绝
        for bad in ("../evil.txt", "a/../../evil.txt", "C:/evil.txt", "..", ""):
            out = builtin_agent._tool_write_file(
                str(self.workdir), {"path": bad, "content": "x"})
            self.assertIn("非法", out + "越界", out)
        self.assertFalse((self.workdir.parent / "evil.txt").exists())
        # 合法路径：写 → 读 → 列 全链路
        out = builtin_agent._tool_write_file(
            str(self.workdir), {"path": "sub/ok.md", "content": "你好"})
        self.assertIn("已写入", out)
        self.assertEqual((self.workdir / "sub" / "ok.md").read_text(encoding="utf-8"), "你好")
        self.assertIn("你好", builtin_agent._tool_read_file(
            str(self.workdir), {"path": "sub/ok.md"}))
        listing = builtin_agent._tool_list_files(str(self.workdir), {"path": ""})
        self.assertIn("sub/ok.md", listing)


class TestBuiltinDirectPipeline(BaseTest):
    def runTest(self):
        """种供应商 + 编排者配置后，direct 任务自动走内置智能体（不经 CLI）。"""
        from app.core import builtin_agent, modelhub, pipeline, store
        modelhub._save({
            "providers": [{"id": "prov-bi", "name": "测试网关", "protocol": "openai",
                           "base_url": "http://gw.test/v1", "api_key": "sk-test",
                           "enabled": True, "model": "bi-model"}],
            "bindings": {},
            "orchestrator": {"provider_id": "prov-bi", "model": "bi-model",
                             "enabled": True},
        })
        bi = builtin_agent.resolve()
        self.assertIsNotNone(bi)
        self.assertEqual(bi["model"], "bi-model")

        calls = []

        def fake_post(url, headers, body, allow_private, timeout):
            calls.append(body)
            return 200, {"choices": [{"message": {"content": "1+1=2"}}],
                         "usage": {"prompt_tokens": 5, "completion_tokens": 2,
                                   "total_tokens": 7}}, ""

        orig = builtin_agent._post_json
        builtin_agent._post_json = fake_post
        try:
            task = store.create_task({
                "type": "direct", "title": "内置直连", "goal": "1+1=?",
                "workdir": str(self.workdir),
            })
            run = store.create_run("orchestration", task["title"], task_id=task["id"])
            pipeline._agents = self.mock_agents
            pipeline.execute_run(run["id"])
        finally:
            builtin_agent._post_json = orig

        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        self.assertEqual(run["verdict"]["impl"], "builtin")
        self.assertIn("CodeBee", run["route"]["implementer"])
        step = run["steps"][0]
        self.assertEqual(step["agent"], "builtin")
        self.assertEqual(step["role"], "direct")
        self.assertEqual(step["output"], "1+1=2")       # 气泡正文=干净回答
        self.assertEqual(step["tokens"], 7)
        self.assertTrue(calls)                            # 走了直连 API（打桩证实）
        self.assertIn("1+1=?", calls[0]["messages"][-1]["content"])


class TestBuiltinReadBinaryAndEnc(BaseTest):
    def runTest(self):
        from app.core import builtin_agent
        # 假 PNG（魔数+字节汤）：拒绝读取并给出格式提示，而不是喷乱码喂给模型
        (self.workdir / "pic.png").write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"\x9a\xfc\x00\x8d" * 100)
        out = builtin_agent._tool_read_file(str(self.workdir), {"path": "pic.png"})
        self.assertIn("图片文件", out)                        # 图片类走引导文案
        self.assertIn("PNG", out)
        self.assertIn("无法按文本读取", out)
        self.assertNotIn("\ufffd", out)
        # GBK 落盘的文本（CLI 子代理章稿纪律）：读出中文而非 U+FFFD
        (self.workdir / "gbk.md").write_bytes("你好，章稿".encode("gbk"))
        self.assertIn("你好，章稿", builtin_agent._tool_read_file(
            str(self.workdir), {"path": "gbk.md"}))
        # UTF-8 截断落在多字节字符中间：头尾保留读出（TokenJuice 借鉴）——
        # 旧纯截头改为头 44k+尾 16k+中段省略标注，剥残缺多字节后无乱码
        (self.workdir / "long.md").write_text("蜂" * 40000, encoding="utf-8")
        got = builtin_agent._tool_read_file(str(self.workdir), {"path": "long.md"})
        self.assertTrue(got.startswith("…（文件超 64KB，已头尾保留读取）"))
        self.assertIn("中段省略", got)
        head_part = got.split("\n", 1)[1]
        self.assertTrue(head_part.startswith("蜂"))            # 头段剥残缺后整字起
        self.assertNotIn("\ufffd", got)                        # 全文无乱码
        # 空文件给明确提示（而非空串让模型误以为读取失败）
        (self.workdir / "empty.txt").write_bytes(b"")
        self.assertIn("空文件", builtin_agent._tool_read_file(
            str(self.workdir), {"path": "empty.txt"}))


class TestBuiltinImageWire(BaseTest):
    """传图链路：_prep_images 预处理、三协议 wire 展开、image_in 能力闸门。"""

    PNG_1PX = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
        "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

    def _bi(self, proto, image_in=True):
        return {"prov": {"id": "p9", "name": "P9", "protocol": proto,
                         "base_url": "http://gw.test/v1", "api_key": "sk-test",
                         "enabled": True, "model": "mv",
                         "models": [{"name": "mv", "enabled": True,
                                     "image_in": image_in}]},
                "model": "mv", "provider_id": "p9", "provider_name": "P9"}

    def test_prep_images_scale_and_reject(self):
        from app.core import builtin_agent
        # 1x1 PNG 正常通过；>5MB 假 PNG（魔数+垃圾，解码失败退原始字节）被剔除
        p1 = self.workdir / "tiny.png"
        p1.write_bytes(self.PNG_1PX)
        big = self.workdir / "big.png"
        big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (6 * 1024 * 1024))
        out = builtin_agent._prep_images([str(p1), str(big)])
        self.assertEqual(len(out), 1)
        mime, b64 = out[0]
        self.assertEqual(mime, "image/png")
        # Pillow 会重编码：比对 PNG 魔数与像素尺寸，不比对字节流
        decoded = base64.b64decode(b64)
        self.assertTrue(decoded.startswith(b"\x89PNG\r\n\x1a\n"))
        # 非图片路径：读取失败跳过不抛
        self.assertEqual(builtin_agent._prep_images([str(self.workdir / "nope.png")]), [])

    def test_openai_image_wire(self):
        from app.core import builtin_agent
        png = self.workdir / "att.png"
        png.write_bytes(self.PNG_1PX)
        bodies = []

        def fake_post(url, headers, body, allow_private, timeout):
            bodies.append(body)
            return 200, {"choices": [{"message": {"content": "看到了图片"}}],
                         "usage": {"prompt_tokens": 5, "completion_tokens": 2,
                                   "total_tokens": 7}}, ""

        orig = builtin_agent._post_json
        builtin_agent._post_json = fake_post
        try:
            res = builtin_agent.run(self._bi("openai"), "看这张图", str(self.workdir),
                                    images=[str(png)])
        finally:
            builtin_agent._post_json = orig
        self.assertTrue(res["ok"], res.get("error"))
        # openai wire：messages[0] 是 system，首条 user 在 messages[1]
        content = bodies[0]["messages"][1]["content"]
        self.assertIsInstance(content, list)                 # 多模态 content 数组
        self.assertEqual(content[0]["type"], "text")
        self.assertIn("看这张图", content[0]["text"])
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"]
                        .startswith("data:image/png;base64,"))

    def test_anthropic_image_wire(self):
        from app.core import builtin_agent
        png = self.workdir / "att.png"
        png.write_bytes(self.PNG_1PX)
        bodies = []

        def fake_post(url, headers, body, allow_private, timeout):
            bodies.append(body)
            return 200, {"content": [{"type": "text", "text": "图上是一只蜂"}],
                         "usage": {"input_tokens": 5, "output_tokens": 2}}, ""

        orig = builtin_agent._post_json
        builtin_agent._post_json = fake_post
        try:
            res = builtin_agent.run(self._bi("anthropic"), "看这张图", str(self.workdir),
                                    images=[str(png)])
        finally:
            builtin_agent._post_json = orig
        self.assertTrue(res["ok"], res.get("error"))
        blocks = bodies[0]["messages"][0]["content"]
        self.assertEqual(blocks[0]["type"], "text")
        img = blocks[1]
        self.assertEqual(img["type"], "image")
        self.assertEqual(img["source"]["media_type"], "image/png")
        # Pillow 重编码字节流会变：验证是合法 PNG 即可
        self.assertTrue(base64.b64decode(img["source"]["data"])
                        .startswith(b"\x89PNG\r\n\x1a\n"))

    def test_google_image_wire(self):
        from app.core import builtin_agent
        png = self.workdir / "att.png"
        png.write_bytes(self.PNG_1PX)
        bodies = []

        def fake_post(url, headers, body, allow_private, timeout):
            bodies.append(body)
            return 200, {"candidates": [{"content": {"parts": [{"text": "好的"}]}}],
                         "usageMetadata": {"totalTokenCount": 3}}, ""

        orig = builtin_agent._post_json
        builtin_agent._post_json = fake_post
        try:
            res = builtin_agent.run(self._bi("google"), "看这张图", str(self.workdir),
                                    images=[str(png)])
        finally:
            builtin_agent._post_json = orig
        self.assertTrue(res["ok"], res.get("error"))
        parts = bodies[0]["contents"][0]["parts"]
        self.assertEqual(parts[0]["text"], "看这张图")
        self.assertEqual(parts[1]["inline_data"]["mime_type"], "image/png")

    def test_gate_no_image_cap(self):
        """模型未声明 image_in：不塞图，prompt 前置降级提示。"""
        from app.core import builtin_agent
        png = self.workdir / "att.png"
        png.write_bytes(self.PNG_1PX)
        bodies = []

        def fake_post(url, headers, body, allow_private, timeout):
            bodies.append(body)
            return 200, {"choices": [{"message": {"content": "我不支持图片"}}],
                         "usage": {"prompt_tokens": 5, "completion_tokens": 2,
                                   "total_tokens": 7}}, ""

        orig = builtin_agent._post_json
        builtin_agent._post_json = fake_post
        try:
            res = builtin_agent.run(self._bi("openai", image_in=False), "看这张图",
                                    str(self.workdir), images=[str(png)])
        finally:
            builtin_agent._post_json = orig
        self.assertTrue(res["ok"], res.get("error"))
        content = bodies[0]["messages"][1]["content"]
        self.assertIsInstance(content, str)                  # 纯文本，无图片块
        self.assertNotIn("data:image", content)
        self.assertIn("未开启图片输入", content)              # 前置降级提示


class TestBuiltinGoogleParse(BaseTest):
    def runTest(self):
        from app.core import builtin_agent
        text, calls, usage = builtin_agent._parse_reply("google", {
            "candidates": [{"content": {"parts": [{"text": "回答"}]}}],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2,
                              "totalTokenCount": 5},
        })
        self.assertEqual(text, "回答")
        self.assertEqual(calls, [])
        self.assertEqual(usage["total"], 5)


class TestBuiltinRunCommand(BaseTest):
    """run_command 全信任执行：退出码/输出回灌/超时杀树/取消杀树/头尾截断。"""

    def _run(self, args, cancel_event=None):
        import os
        from app.core import builtin_agent
        return builtin_agent._tool_run_command(str(self.workdir), args,
                                               cancel_event=cancel_event)

    def test_basics(self):
        import os
        import time as _t
        # 工具表注册进两种 wire
        from app.core import builtin_agent
        self.assertIn("run_command",
                      [t["function"]["name"] for t in builtin_agent._openai_tools()])
        self.assertIn("run_command",
                      [t["name"] for t in builtin_agent._anthropic_tools()])
        # 空命令拒绝
        self.assertIn("命令为空", self._run({"command": "  "}))
        # echo + 退出码 0
        out = self._run({"command": "cmd /c echo bee_ok" if os.name == "nt"
                         else "echo bee_ok"})
        self.assertIn("退出码: 0", out)
        self.assertIn("bee_ok", out)
        # 非零退出码照实回传
        out = self._run({"command": "cmd /c exit 3" if os.name == "nt" else "exit 3"})
        self.assertIn("退出码: 3", out)
        # 工作目录即命令 cwd
        out = self._run({"command": "cd" if os.name == "nt" else "pwd"})
        self.assertIn(os.path.abspath(str(self.workdir)).lower(),
                      out.replace('"', "").lower())
        # 超时杀树：timeout_sec 钳最小 5s；杀树+管道收尾有 10s 级上限，
        # 绝不该跑满命令本身（120s）。阈值放宽到 40s 容忍慢盘/AV 扫描。
        t0 = _t.time()
        out = self._run({"command": "ping -n 120 127.0.0.1" if os.name == "nt"
                         else "sleep 120", "timeout_sec": 3})
        self.assertIn("超时", out)
        self.assertLess(_t.time() - t0, 40)

    def test_cancel_kills_tree(self):
        import os
        import threading
        ev = threading.Event()
        timer = threading.Timer(0.8, ev.set)
        timer.start()
        try:
            out = self._run({"command": "ping -n 120 127.0.0.1"
                             if os.name == "nt" else "sleep 120"}, cancel_event=ev)
        finally:
            timer.cancel()
        self.assertIn("已取消", out)
        self.assertIn("退出码", out)   # 照常返回结构化结果，工具循环能继续

    def test_output_head_tail_cap(self):
        import os
        big = ("cmd /c for /l %i in (1,1,3000) do @echo "
               "0123456789012345678901234567890123456789" if os.name == "nt"
               else "yes 0123456789012345678901234567890123456789 | head -c 120000")
        out = self._run({"command": big})
        self.assertIn("中段省略", out)
        self.assertLess(len(out), 64 * 1024)   # 头 24K + 尾 8K + 标注，远小于原 120K


class TestDirectAgentCli(BaseTest):
    """直接对话手动指定 CLI（2026-09-23）：direct_agent 压过模型绑定与自动推荐。"""

    def test_mock_cli_executes_direct_task(self):
        """direct_agent=mock-a → 走 CLI 步骤路径（非 builtin），路由标注手动指定。"""
        from app.core import pipeline, store
        task = store.create_task({
            "type": "direct", "title": "点名 CLI", "goal": "讲个笑话",
            "workdir": str(self.workdir), "direct_agent": "mock-a",
        })
        self.assertEqual(task.get("direct_agent"), "mock-a")
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents
        pipeline.execute_run(run["id"])
        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        self.assertEqual(run["verdict"]["impl"], "mock-a")
        self.assertIn("手动指定 CLI", run["route"]["implementer"])
        step = run["steps"][0]
        self.assertEqual(step["agent"], "mock-a")

    def test_unavailable_cli_fails_clearly(self):
        """direct_agent 指向未安装 CLI → 运行即刻失败并说明原因（不静默换人）。"""
        from app.core import pipeline, store
        task = store.create_task({
            "type": "direct", "title": "点名幽灵", "goal": "你好",
            "workdir": str(self.workdir), "direct_agent": "ghost-cli-xyz",
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents
        pipeline.execute_run(run["id"])
        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "failed")
        self.assertIn("ghost-cli-xyz", run.get("error") or "")
        self.assertIn("不可用", run.get("error") or "")

    def test_params_set_and_clear_direct_agent(self):
        """params 接口可改/可清 direct_agent（空闲态）；清空回内置智能体路径。"""
        from app.core import store
        task = store.create_task({
            "type": "direct", "title": "参数改CLI", "goal": "你好",
            "workdir": str(self.workdir),
        })
        self.assertNotIn("direct_agent", task)
        ok, err = store.update_task_params(task["id"], {"direct_agent": "mock-b"})
        self.assertTrue(ok, err)
        self.assertEqual(store.get_task(task["id"]).get("direct_agent"), "mock-b")
        ok, err = store.update_task_params(task["id"], {"direct_agent": ""})
        self.assertTrue(ok, err)
        self.assertEqual(store.get_task(task["id"]).get("direct_agent"), "")
