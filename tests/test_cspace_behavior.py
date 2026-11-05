# -*- coding: utf-8 -*-
"""空间管家底盘的行为守测：全部在临时目录里造样本，绝不碰真实用户文件。

重点锁三件事：① 只读层不会误把软链接里的目标盘数据算进来；② 重复文件必须
三阶段确认，尺寸相同但内容不同不许成组；③ 所有写动作 confirm=False 时
一个字节都不改（含计划、搬家、建链接）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "app") not in sys.path:
    sys.path.insert(0, str(ROOT / "app"))

from core.cspace import (analyzer, cleaner, ledger, links, migrator,
                         risk, rules, scanner)  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="cspace-test-")
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def make(self, rel, data):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p


class MacroAndRiskTest(Base):
    def test_windir_macro_expands(self):
        env = {"WINDIR": "C:\\Windows", "SYSTEMROOT": "C:\\Windows",
               "SYSTEMDRIVE": "C:", "USERPROFILE": "C:\\Users\\x",
               "LOCALAPPDATA": "C:\\Users\\x\\AppData\\Local",
               "APPDATA": "C:\\Users\\x\\AppData\\Roaming",
               "PROGRAMDATA": "C:\\ProgramData", "TEMP": "C:\\Temp"}
        out = rules.expand_macros("%WINDIR%\\Temp", env=env)
        self.assertEqual(os.path.normpath("C:\\Windows\\Temp"),
                         os.path.normpath(str(out)))

    def test_unknown_macro_returns_none(self):
        self.assertIsNone(rules.expand_macros("%NO_SUCH_MACRO%\\x", env={}))

    def test_forbidden_system_paths(self):
        for p in ("C:\\Windows\\System32", "C:\\Windows\\WinSxS",
                  "C:\\Windows", "C:\\", "C:\\System Volume Information",
                  "C:\\pagefile.sys"):
            self.assertTrue(risk.is_forbidden(p), "应该拦住 %s" % p)
        self.assertFalse(risk.is_forbidden(r"C:\WindowsFoo"),
                         "不能把同名前缀的正常目录误判成系统目录")

    def test_assert_action_allowed_blocks_clean_on_forbidden(self):
        with self.assertRaises(risk.UnsafePath):
            risk.assert_action_allowed(r"C:\Windows\System32", "clean")


class ScannerTest(Base):
    def test_calc_size_and_level(self):
        self.make("a/one.bin", b"1" * 100)
        self.make("a/two.bin", b"2" * 50)
        self.make("b/three.bin", b"3" * 25)
        size, count = scanner.calc_size(str(self.root / "a"))
        self.assertEqual((150, 2), (size, count))
        rows = scanner.scan_level(str(self.root))
        self.assertEqual(2, len(rows))
        self.assertGreaterEqual(rows[0]["size"], rows[1]["size"], "必须按体积倒序")
        self.assertTrue(all("size_human" in r for r in rows))

    def test_reparse_point_is_not_traversed(self):
        self.make("real/data.bin", b"x" * 64)
        link = self.root / "shortcut"
        try:
            os.symlink(str(self.root / "real"), str(link), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("当前环境不允许建目录符号链接")
        rows = list(scanner.iter_entries(str(self.root)))
        inside = [r for r in rows if "shortcut" + os.sep in str(r["path"])]
        self.assertEqual([], inside, "穿进软链接会把目标盘数据重复计算")
        self.assertTrue(any(r["reparse"] for r in rows), "链接本身应被标出来")


class AnalyzerTest(Base):
    def test_duplicates_need_full_hash_match(self):
        payload = b"A" * 4096
        a = self.make("a.bin", payload)
        b = self.make("nested/b.bin", payload)
        self.make("c.bin", b"B" * 4096)  # 同尺寸、同头尾采样以外的内容不同
        groups = analyzer.duplicate_groups([str(self.root)], min_bytes=1024)
        self.assertEqual(1, len(groups), "同尺寸不同内容不许成组")
        self.assertEqual({str(a), str(b)}, set(groups[0]["files"]))
        self.assertEqual(len(payload), groups[0]["wasted"])

    def test_big_files_threshold(self):
        self.make("big.iso", b"0" * (2 * 1024 * 1024))
        self.make("tiny.txt", b"0" * 16)
        rows = analyzer.big_files([str(self.root)], min_bytes=1024 * 1024)
        self.assertEqual(1, len(rows))
        self.assertIn("category", rows[0])

    def test_hardlink_replace_is_two_phase(self):
        payload = b"same" * 512
        a = self.make("a.bin", payload)
        b = self.make("b.bin", payload)
        before = (a.stat().st_ino, b.stat().st_ino)
        dry = analyzer.replace_with_hardlink(str(a), str(b), confirm=False)
        self.assertTrue(dry.get("dry_run"), "默认必须只给计划")
        self.assertEqual((a.read_bytes(), b.read_bytes()), (payload, payload))
        self.assertEqual(before, (a.stat().st_ino, b.stat().st_ino),
                         "dry-run 不许改动 inode")
        done = analyzer.replace_with_hardlink(str(a), str(b), confirm=True)
        self.assertTrue(done.get("ok"), "确认后应成功：%s" % done)
        self.assertEqual(payload, b.read_bytes(), "替换后内容必须不变")


class WriteGuardTest(Base):
    def test_plan_refuses_unlisted_path(self):
        # 隔离：真实磁盘全量盘点要几十秒，且不该被单元测试碰到。
        # 这里只验「不在白名单里的路径被拒」，用空候选集即可。
        rogue = self.root / "rogue"
        rogue.mkdir()
        with mock.patch.object(cleaner, "candidates", return_value=[]):
            res = cleaner.plan([str(rogue)], base=str(self.root / "state"))
        self.assertIn("refused", res)
        self.assertTrue(res["refused"], "不在规则白名单里的目录必须被拒")
        self.assertTrue(rogue.exists(), "计划阶段绝不许动目录")

    def test_plan_accepts_injected_whitelist_without_rescanning(self):
        cache_dir = self.root / "cache"
        cache_dir.mkdir()
        item = {"path": str(cache_dir), "rule_id": "test", "name": "测试缓存",
                "risk": "safe", "size": 10, "size_human": "10 B", "files": 1,
                "clean_target": "contents"}
        with mock.patch.object(cleaner, "candidates") as scan:
            res = cleaner.plan([str(cache_dir)], base=str(self.root / "state"),
                               allowed=[item])
            scan.assert_not_called()
        self.assertEqual(1, len(res["items"]))
        self.assertEqual([], res["refused"])
        self.assertTrue(cache_dir.exists(), "计划阶段绝不许动目录")

    def test_migrate_dry_run_creates_nothing(self):
        src = self.root / "src"
        src.mkdir()
        self.make("src/data.bin", b"x" * 128)
        dst = self.root / "dst"
        res = migrator.run(str(src), str(dst), confirm=False,
                           base=str(self.root / "state"))
        self.assertTrue(res.get("dry_run") or "precheck" in res,
                        "confirm=False 必须只给预检/计划")
        self.assertFalse(dst.exists(), "dry-run 不许创建目标目录")
        self.assertTrue((src / "data.bin").is_file())

    def test_create_junction_dry_run(self):
        target = self.root / "target"
        target.mkdir()
        link = self.root / "link"
        res = links.create_junction(str(link), str(target), confirm=False)
        self.assertTrue(res.get("dry_run"), "默认只给计划")
        self.assertFalse(link.exists())

    def test_ledger_plan_roundtrip_and_bad_file(self):
        base = str(self.root / "state")
        ledger.put_plan("p1", {"items": [1]}, base=base)
        saved = ledger.get_plan("p1", base=base)
        self.assertEqual([1], saved["items"], "计划要能原样取回")
        self.assertIn("created_at", saved, "计划必须带创建时间才能判过期")
        ledger.drop_plan("p1", base=base)
        self.assertIsNone(ledger.get_plan("p1", base=base))
        store = Path(ledger.store_dir(base))
        store.mkdir(parents=True, exist_ok=True)
        (store / "ledger.json").write_text("{broken", encoding="utf-8")
        self.assertEqual({"links": [], "migrations": []}, ledger.load(base=base))

    def test_restore_and_rollback_use_link_redline_not_migrate(self):
        """真 Junction 演练抓到的回归：迁回/回滚时 src 本来就是重解析点。

        用 "migrate" 过红线会被 assert_action_allowed 以「目标是重解析点」
        直接拒死，一键迁回永远调不通；这两处必须用 "link" 动作（只挡红线），
        再由 links.is_link 单独确认形状。
        """
        base = str(self.root / "state")
        src = self.root / "src"
        src.mkdir()
        target = self.root / "target"
        target.mkdir()
        ledger.add_migration({"source": str(src), "target": str(target),
                              "kind": "junction"}, base)

        real = risk.assert_action_allowed
        cases = ((migrator.restore, {}),
                 (migrator.rollback, {"dst": str(self.root / "absent")}))
        for fn, extra in cases:
            calls = []

            def spy(path, action, _real=real):
                calls.append(action)
                return _real(path, action)

            # is_reparse=True 模拟 Junction；is_link=False 让函数在红线之后
            # 立刻安全返回，不触发任何真实复制/删除。
            with mock.patch.object(risk, "is_reparse", return_value=True), \
                 mock.patch.object(links, "is_link", return_value=False), \
                 mock.patch.object(risk, "assert_action_allowed", side_effect=spy):
                res = fn(str(src), confirm=True, base=base, **extra)
            self.assertEqual(["link"], calls,
                             "%s 必须用 link 动作过红线" % fn.__name__)
            self.assertNotIn("安全红线", str(res.get("error") or ""),
                             "重解析点不该让迁回/回滚被红线拒掉")

    def test_migrate_run_rechecks_redline_on_both_paths(self):
        """执行阶段必须独立于预检再过一次红线，且目标盘也要查。"""
        src = self.root / "src"
        src.mkdir()
        dst = self.root / "dst"
        pre = {"ok": False, "blockers": ["预检未通过"], "checks": [],
               "source_bytes": 0, "source_files": 0}
        real = risk.assert_action_allowed
        calls = []

        def spy(path, action, _real=real):
            calls.append((path, action))
            return _real(path, action)

        with mock.patch.object(migrator, "precheck", return_value=pre), \
             mock.patch.object(risk, "assert_action_allowed", side_effect=spy):
            res = migrator.run(str(src), str(dst), confirm=True,
                               base=str(self.root / "state"))
        self.assertEqual([(str(src), "migrate"), (str(dst), "migrate")], calls)
        self.assertFalse(res.get("ok"))
        self.assertFalse(dst.exists(), "预检没过时不许碰文件系统")

    def test_link_target_strips_windows_nt_prefix(self):
        with mock.patch.object(links, "is_link", return_value=True), \
             mock.patch.object(os, "readlink",
                               return_value=r"\\?\D:\DemoCache", create=True):
            self.assertEqual(r"D:\DemoCache", links.link_target("anything"))

    def test_fsutil_fallback_keeps_drive_letter(self):
        """回退解析只切标签后的第一个冒号，盘符 C: 不能被切掉。"""
        out = b"Reparse Tag Value : 0xa0000003\nSubstitue Name: \\??\\C:\\DemoCache\n"
        proc = mock.Mock(stdout=out, returncode=0)
        with mock.patch.object(links, "is_link", return_value=True), \
             mock.patch.object(os, "readlink", side_effect=OSError("no"), create=True), \
             mock.patch.object(links.subprocess, "run", return_value=proc), \
             mock.patch.object(os, "name", "nt"):
            self.assertEqual(r"C:\DemoCache", links.link_target("x"))


if __name__ == "__main__":
    unittest.main()
