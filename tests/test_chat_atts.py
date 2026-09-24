# -*- coding: utf-8 -*-
"""对话附件「点击查看」的服务端基础：附件路径归一（norm_rel）+
待提交附件读取（read_pending，GET /api/attachments/<id> 的底层）。"""
from __future__ import annotations

import base64

from base import BaseTest


class TestChatAttBasics(BaseTest):
    def runTest(self):
        from app.core import attachments

        # ---- norm_rel：统一成 _attachments/ 起头的相对路径 ----
        self.assertEqual(attachments.norm_rel({"path": "_attachments/a.png"}),
                         "_attachments/a.png")
        self.assertEqual(attachments.norm_rel({"name": "a.png", "path": "_attachments/a.png"}),
                         "_attachments/a.png")     # path 优先于 name
        self.assertEqual(attachments.norm_rel({"name": "a.png"}), "_attachments/a.png")
        self.assertEqual(attachments.norm_rel("_attachments/b/c.txt"),
                         "_attachments/b/c.txt")   # 已是相对路径：原样保留
        # 老数据：绝对路径（含反斜杠）→ 剥前缀钉回 _attachments/
        self.assertEqual(attachments.norm_rel("E:\\工作\\demo\\_attachments\\旧图.png"),
                         "_attachments/旧图.png")
        self.assertEqual(attachments.norm_rel("C:/work/_attachments/old.txt"),
                         "_attachments/old.txt")
        # 裸文件名 → 也按落在 _attachments/ 处理
        self.assertEqual(attachments.norm_rel("readme.md"), "_attachments/readme.md")
        # 空值兜底
        self.assertEqual(attachments.norm_rel(""), "")
        self.assertEqual(attachments.norm_rel(None), "")
        self.assertEqual(attachments.norm_rel({}), "")
        self.assertEqual(attachments.norm_rel({"path": "  "}), "")

        # ---- read_pending：待提交区原文 ----
        body = "待提交预览内容abc\n"
        meta = attachments.save_pending("pending.txt", base64.b64encode(body.encode("utf-8")).decode())
        data, mime, name = attachments.read_pending(meta["id"])
        self.assertEqual(data.decode("utf-8"), body)
        self.assertEqual(name, "pending.txt")
        self.assertTrue(mime.startswith("text/"))

        # 找不到 / 非法 id 一律空三元组（路由层据此 404）
        self.assertEqual(attachments.read_pending("0123456789abcdef"), (b"", "", ""))  # 不存在
        self.assertEqual(attachments.read_pending("../evil"), (b"", "", ""))           # 非 16 位 hex
        self.assertEqual(attachments.read_pending(""), (b"", "", ""))
        self.assertEqual(attachments.read_pending(None), (b"", "", ""))
