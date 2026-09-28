# -*- coding: utf-8 -*-
"""远程备份通道（remotesync：push/list/pull/test）单测——runner 打桩不真出网。

跑法：python -m unittest discover -s tests -p "test_remotesync.py" -v
"""
from __future__ import annotations

import unittest
from unittest import mock

from base import BaseTest

from app.core import remotesync, settings as settings_mod


def _enable(url="https://dav.example.com/backup/"):
    settings_mod.save({"backup_remote_enabled": True, "backup_remote_url": url,
                       "backup_remote_user": "u", "backup_remote_pass": "p"})


class TestPushRemote(BaseTest):

    def test_disabled_skips(self):
        r = remotesync.push_remote("x.zip")
        self.assertTrue(r["skipped"])

    def test_missing_file(self):
        _enable()
        r = remotesync.push_remote("nope.zip")
        self.assertFalse(r["ok"])
        self.assertIn("不存在", r["error"])

    def test_push_uses_stream_upload_with_auth(self):
        _enable()
        f = self.data_dir / "b.zip"
        f.write_bytes(b"PK")
        with mock.patch.object(remotesync, "runner",
                               **{"run_process": mock.Mock(
                                   return_value={"ok": True, "stderr": ""})}) as mrun:
            r = remotesync.push_remote(str(f))
        self.assertTrue(r["ok"])
        argv = mrun.run_process.call_args.kwargs["argv"]
        self.assertIn("-T", argv)                       # 流式上传
        self.assertTrue(any(a == "u:p" for a in argv))  # Basic auth 走 --user 值
        self.assertTrue(any(a.endswith(f.name) for a in argv))


class TestListAndPull(BaseTest):

    def test_list_parses_propfind(self):
        _enable()
        body = '<D:response><D:href>/backup/codebee-backup-20260928-120000.zip</D:href></D:response>' \
               '<D:response><D:href>/backup/codebee-backup-20260927-090000.zip</D:href></D:response>'
        with mock.patch.object(remotesync, "runner",
                               **{"run_process": mock.Mock(
                                   return_value={"ok": True, "stdout": body, "stderr": ""})}):
            names, err = remotesync.list_remote()
        self.assertIsNone(err, err)
        self.assertEqual(names, ["codebee-backup-20260928-120000.zip",
                                 "codebee-backup-20260927-090000.zip"])

    def test_list_requires_enabled(self):
        settings_mod.save({})
        names, err = remotesync.list_remote()
        self.assertIsNone(names)
        self.assertIn("未启用", err)

    def test_pull_rejects_bad_name(self):
        _enable()
        p, err = remotesync.pull_remote("../evil.zip")
        self.assertIsNone(p)
        self.assertIn("非法", err)

    def test_pull_downloads_to_imports(self):
        _enable()
        from app.core import paths
        # runner 被打桩不会真下载：预置「下载结果」文件模拟 curl -o 已落盘
        dest = paths.DATA_DIR / "imports" / "codebee-backup-20260928-120000.zip"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"PK\x03\x04fake")
        run_mock = mock.Mock(return_value={"ok": True, "stderr": ""})
        with mock.patch.object(remotesync, "runner",
                               **{"run_process": run_mock}):
            p, err = remotesync.pull_remote("codebee-backup-20260928-120000.zip")
        self.assertIsNone(err, err)
        self.assertEqual(p, str(dest))
        run_mock.assert_called_once()

    def test_pull_failure_cleans_partial(self):
        _enable()
        with mock.patch.object(remotesync, "runner",
                               **{"run_process": mock.Mock(
                                   return_value={"ok": False,
                                                 "stderr": "curl: (28) timeout"})}):
            p, err = remotesync.pull_remote("codebee-backup-20260928-120000.zip")
        self.assertIsNone(p)
        self.assertIn("timeout", err)


if __name__ == "__main__":
    unittest.main()
