# -*- coding: utf-8 -*-
"""附件内容指纹与完整性对账测试（借鉴 WorkDSH 输入引用修订的落地守卫）。

覆盖：
  1. commit_to_workdir 落 digest（64 位 sha256，与文件内容一致）；
  2. items_from_paths（运行中追加）同样钉指纹；
  3. verify_task 四态：无漂移 []/内容被换 changed/文件被删 missing/
     老数据无 digest 不做推断；
  4. create_task 全链：任务上的附件自带指纹，改文件后续跑对账能点名。
"""
from __future__ import annotations

import base64
import hashlib

from base import BaseTest


class TestAttachmentDigest(BaseTest):
    def _pending(self, name, text):
        from app.core import attachments
        return attachments.save_pending(name, base64.b64encode(
            text.encode("utf-8")).decode("ascii"))

    def _commit(self, meta):
        from app.core import attachments
        return attachments.commit_to_workdir(str(self.workdir), [meta["id"]])

    def test_commit_records_digest(self):
        items = self._commit(self._pending("spec.txt", "hello 附件"))
        self.assertEqual(len(items), 1)
        d = items[0].get("digest") or ""
        self.assertEqual(len(d), 64)
        fp = self.workdir / "_attachments" / "spec.txt"
        self.assertEqual(d, hashlib.sha256(fp.read_bytes()).hexdigest())

    def test_items_from_paths_records_digest(self):
        from app.core import attachments
        rel = "_attachments/note.md"
        fp = self.workdir / rel
        fp.parent.mkdir(exist_ok=True)
        fp.write_text("正文", encoding="utf-8")
        items = attachments.items_from_paths([rel], str(self.workdir))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].get("digest"),
                         hashlib.sha256("正文".encode("utf-8")).hexdigest())

    def test_verify_task_states(self):
        from app.core import attachments
        items = self._commit(self._pending("data.txt", "v1"))
        task = {"attachments": items, "workdir": str(self.workdir)}
        # 无漂移
        self.assertEqual(attachments.verify_task(task), [])
        # 内容被换 → changed，pinned/current 分明
        (self.workdir / "_attachments" / "data.txt").write_text("v2", encoding="utf-8")
        drift = attachments.verify_task(task)
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]["status"], "changed")
        self.assertEqual(drift[0]["pinned"], items[0]["digest"])
        self.assertNotEqual(drift[0]["current"], drift[0]["pinned"])
        # 文件被删 → missing
        (self.workdir / "_attachments" / "data.txt").unlink()
        drift = attachments.verify_task(task)
        self.assertEqual(drift[0]["status"], "missing")
        self.assertEqual(drift[0]["current"], "")
        # 老数据无 digest → 不做推断，不告漂移
        legacy = {"attachments": [{"name": "data.txt",
                                   "path": "_attachments/data.txt",
                                   "mime": "text/plain", "size": 2}],
                  "workdir": str(self.workdir)}
        self.assertEqual(attachments.verify_task(legacy), [])

    def test_create_task_pins_digest_end_to_end(self):
        from app.core import store
        meta = self._pending("goal.md", "# 目标\n写一份报告")
        task = store.create_task({"type": "doc", "goal": "写报告",
                                  "workdir": str(self.workdir),
                                  "attachments": [meta["id"]]})
        items = task.get("attachments") or []
        self.assertEqual(len(items), 1)
        self.assertEqual(len(items[0].get("digest") or ""), 64)
        # 用户改了附件再续跑：对账点名（execute_run 的 warning 由管线接线，
        # 这里锁的是对账真源本身）
        (self.workdir / items[0]["path"]).write_text("# 目标（改版）", encoding="utf-8")
        from app.core import attachments
        drift = attachments.verify_task(store.get_task(task["id"]))
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]["status"], "changed")

    def test_verify_task_rejects_traversal_attachment(self):
        from app.core import attachments
        outside = self.tmp / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        task = {"workdir": str(self.workdir), "attachments": [{
            "name": "escape.txt", "path": "_attachments/../../outside.txt",
            "digest": hashlib.sha256(b"secret").hexdigest(),
        }]}
        drift = attachments.verify_task(task)
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]["status"], "unreadable")


if __name__ == "__main__":
    unittest.main()
