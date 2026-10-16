# -*- coding: utf-8 -*-
"""附件提交跨盘回落回归（2026-10-16 大纲附件假成功实案）。

pending 在 %APPDATA%=C:，小说工作目录在 E:——os.replace 跨盘抛
WinError 17。旧实现回落 copy 写在 replace 之后永远执行不到：目录建了、
文件没落，异常再被提交接口吞掉，消息不带附件照发（模型没收到、UI 没
显示、三方全蒙在鼓里）。

锁定行为：
1. replace 抛 OSError（跨盘形态）→ 就地 copy+delete 兜底，文件必须落到
   _attachments/，pending 源与 sidecar 清干净，digest 照常记账；
2. 真正的文件系统失败（copy 也失败）→ 跳过该文件不炸整体（原有契约）。
"""
from __future__ import annotations

import base64
from pathlib import Path
from unittest import mock

from base import BaseTest


class TestAttachmentCrossDrive(BaseTest):
    def _pending(self, name, text):
        from app.core import attachments
        return attachments.save_pending(name, base64.b64encode(
            text.encode("utf-8")).decode("ascii"))

    def test_replace_failure_falls_back_to_copy(self):
        from app.core import attachments
        meta = self._pending("签约终版大纲.md", "# 大纲正文")
        with mock.patch.object(Path, "replace",
                               side_effect=OSError(17, "不同的磁盘设备")):
            items = attachments.commit_to_workdir(str(self.workdir), [meta["id"]])
        self.assertEqual(len(items), 1)
        fp = self.workdir / "_attachments" / "签约终版大纲.md"
        self.assertTrue(fp.is_file(), "跨盘回落必须把文件落到 _attachments/")
        self.assertEqual(fp.read_text(encoding="utf-8"), "# 大纲正文")
        self.assertTrue(items[0].get("digest"))
        # pending 源与 sidecar 清场
        from app.core import paths
        pend = paths.DATA_DIR / "pending"
        self.assertFalse((pend / (meta["id"] + ".json")).exists())

    def test_total_fs_failure_still_skips_quietly(self):
        from app.core import attachments
        meta = self._pending("b.md", "内容")
        with mock.patch.object(Path, "replace",
                               side_effect=OSError(17, "不同的磁盘设备")), \
             mock.patch.object(Path, "write_bytes",
                               side_effect=OSError(28, "磁盘空间不足")):
            items = attachments.commit_to_workdir(str(self.workdir), [meta["id"]])
        self.assertEqual(items, [])
