# -*- coding: utf-8 -*-
"""整机备份导出/导入（core/backup.py）：打包内容、路径重映射、合并/替换、忙时拒导。"""
from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests.base import BaseTest


class BackupTest(BaseTest):
    def setUp(self):
        super().setUp()
        from app.core import backup, settings
        self.backup = backup
        self.settings = settings
        # 默认保存路径指到 tmp/ws，任务工作目录建在其下（app 管辖区，随包走）
        self.ws_root = self.tmp / "ws"
        self.ws_root.mkdir()
        self.settings.save({"default_workdir": str(self.ws_root)})
        self.out_dir = self.tmp / "out"
        self.out_dir.mkdir()

    def _make_task_with_run(self, chapter_text="第一章 蜜蜂出发。"):
        from app.core import store
        wd = self.ws_root / "novel1"
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "第01章.md").write_text(chapter_text, encoding="utf-8")
        task = store.create_task({"type": "doc", "title": "演示", "goal": "写个开篇",
                                  "workdir": str(wd)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        steps = Path(paths_runs_dir()) / run["id"] / "steps"
        steps.mkdir(parents=True, exist_ok=True)
        (steps / "01-draft.log").write_text("x" * 2048, encoding="utf-8")
        return task, run

    # ------------------------------------------------------------ 导出

    def test_export_pack_layout_and_manifest(self):
        task, run = self._make_task_with_run()
        info = self.backup.export_data(target_dir=str(self.out_dir))
        zpath = Path(info["path"])
        self.assertTrue(zpath.is_file())
        self.assertEqual(info["tasks"], 1)
        self.assertEqual(info["runs"], 1)
        self.assertEqual(info["workspace_files"], 1)
        self.assertEqual(info["external_workdirs"], [])
        with zipfile.ZipFile(str(zpath)) as zf:
            names = zf.namelist()
            m = json.loads(zf.read("manifest.json").decode("utf-8"))
        self.assertEqual(m["app"], "codebee")
        self.assertIn("data/tasks/%s.json" % task["id"], names)
        self.assertIn("data/runs/%s/run.json" % run["id"], names)
        self.assertIn("data/runs/%s/steps/01-draft.log" % run["id"], names)
        self.assertTrue(any(n.startswith("workspace/novel1/第01章.md") for n in names),
                        names)
        # 排除项：服务日志/锁/缓存目录绝不进包
        self.assertFalse([n for n in names if n.endswith(".log") and not n.startswith("data/runs")])
        self.assertNotIn("data/pet.lock", names)

    def test_export_without_logs_and_external_dirs_listed(self):
        from app.core import store
        self._make_task_with_run()
        # 外部目录：不在默认保存路径下也不在数据目录里（用户自己的仓库）
        ext = self.tmp / "ext-repo"
        ext.mkdir()
        store.create_task({"type": "doc", "goal": "外部任务", "workdir": str(ext)})
        info = self.backup.export_data(target_dir=str(self.out_dir), include_logs=False)
        self.assertEqual(info["external_workdirs"], [str(ext)])
        with zipfile.ZipFile(info["path"]) as zf:
            names = zf.namelist()
        self.assertFalse([n for n in names if "/steps/" in n])   # 不带日志开关生效
        self.assertTrue([n for n in names if n.startswith("workspace/novel1/")])


class BackupImportTest(BaseTest):
    """导入侧：把备份落到「新机器」（第二套临时数据目录）上验证。"""

    def setUp(self):
        super().setUp()
        from app.core import backup, settings
        self.backup = backup
        self.settings = settings
        self.ws_root = self.tmp / "ws"
        self.ws_root.mkdir()
        self.settings.save({"default_workdir": str(self.ws_root)})
        self.out_dir = self.tmp / "out"
        self.out_dir.mkdir()

    def _export(self):
        from app.core import store
        wd = self.ws_root / "novel1"
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "第01章.md").write_text("第一章 蜜蜂出发。", encoding="utf-8")
        task = store.create_task({"type": "doc", "title": "演示", "goal": "写个开篇",
                                  "workdir": str(wd)})
        info = self.backup.export_data(target_dir=str(self.out_dir))
        return task, info["path"]

    def _rebind_new_machine(self, name):
        """把全局路径/模块绑定整体切到「新机器」的数据目录。返回新数据目录。"""
        from app.core import paths, settings, store
        new_data = Path(self.tmp) / name
        for attr, val in (("DATA_DIR", new_data),
                          ("TASKS_DIR", new_data / "tasks"),
                          ("RUNS_DIR", new_data / "runs"),
                          ("USAGE_DIR", new_data / "usage"),
                          ("ERRORS_DIR", new_data / "errors"),
                          ("CATALOG_FILE", new_data / "catalog.json"),
                          ("ENABLED_FILE", new_data / "orchestration.json")):
            setattr(paths, attr, val)
        paths.ensure_dirs()
        settings._FILE = new_data / "settings.json"
        store._TASKS.clear()
        store._RUNS.clear()
        return new_data

    def test_import_merge_with_remap(self):
        old_task, zpath = self._export()
        # ---- 切到新机器：数据目录与默认保存路径都换了
        new_data = self._rebind_new_machine("machine-b")
        new_ws = self.tmp / "machine-b-ws"
        new_ws.mkdir()
        self.settings._FILE = new_data / "settings.json"
        self.settings.save({"default_workdir": str(new_ws)})

        pv = self.backup.inspect_backup(zpath)
        self.assertTrue(pv["remap_needed"])
        self.assertEqual(pv["tasks"], 1)
        self.assertEqual(pv["busy_runs"], [])

        r = self.backup.apply_import(zpath, mode="merge", remap=True)
        self.assertEqual(r["mode"], "merge")
        self.assertTrue(r["restart_required"])
        # 任务记录路径已重映射到新机器工作区
        from app.core import store
        t = store._TASKS[old_task["id"]]
        self.assertEqual(t["workdir"], str(new_ws / "novel1"))
        # 工作目录文件已落到新根
        self.assertTrue((new_ws / "novel1" / "第01章.md").is_file())
        # 导入 settings.json 的默认路径也被重映射到新根
        raw = json.loads((new_data / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(raw.get("default_workdir"), str(new_ws))
        # 反悔备份已生成
        self.assertTrue((new_data / "imports").is_dir())
        self.assertTrue(list((new_data / "imports").glob("pre-import-*.zip")))

    def test_import_replace_drops_local_extras(self):
        from app.core import store
        _, zpath = self._export()
        new_data = self._rebind_new_machine("machine-b")
        new_ws = self.tmp / "machine-b-ws"
        new_ws.mkdir()
        self.settings._FILE = new_data / "settings.json"
        self.settings.save({"default_workdir": str(new_ws)})
        # 本机多出一个备份包里没有的任务（replace 后应消失）
        extra_wd = new_ws / "extra"
        extra_wd.mkdir()
        store.create_task({"type": "doc", "goal": "本机独有", "workdir": str(extra_wd)})
        # 新机器内存里只有刚建的本机独有任务（备份包里的任务只在 zip 里）
        self.assertEqual(len(store.list_tasks(limit=100)), 1)

        r = self.backup.apply_import(zpath, mode="replace", remap=True)
        self.assertEqual(r["mode"], "replace")
        tasks = store.list_tasks(limit=100)
        self.assertEqual(len(tasks), 1)
        # 本机独有任务已被清掉：剩下的是备份包里的 novel1
        self.assertEqual(tasks[0]["workdir"], str(new_ws / "novel1"))

    def test_import_refuses_while_run_active(self):
        from app.core import store
        _, zpath = self._export()
        self._rebind_new_machine("machine-b")
        run = store.create_run("orchestration", "在跑")
        store.update_run(run["id"], status="running")
        with self.assertRaises(ValueError):
            self.backup.apply_import(zpath, mode="merge")
        pv = self.backup.inspect_backup(zpath)
        self.assertEqual(len(pv["busy_runs"]), 1)

    def test_remap_value_prefix_edges(self):
        f = self.backup._remap_value
        pairs = [("E:\\GoOut\\ws", "/new/ws"), ("E:\\GoOut", "/new")]
        # 长前缀优先、兄弟目录不误伤、纯前缀整串命中；尾巴分隔符原样保留
        self.assertEqual(f("E:\\GoOut\\ws\\book\\a.md", pairs),
                         ("/new/ws" + "\\book\\a.md", True))
        self.assertEqual(f("E:\\GoOut2\\x", pairs), ("E:\\GoOut2\\x", False))
        self.assertEqual(f("E:\\GoOut", pairs), ("/new", True))
        self.assertEqual(f("普通文本", pairs), ("普通文本", False))

    def test_zip_slip_entries_rejected_atomically(self):
        """恶意备份包：.. 逃逸/反斜杠逃逸/盘符绝对路径一律整体拒绝，盘上零落笔。"""
        import zipfile as zfmod
        evil = self.out_dir / "evil.zip"
        entries = {
            "manifest.json": json.dumps({
                "app": "codebee", "schema": 1, "exported_at": "2026-01-01 00:00:00",
                "data_dir": "E:\\old", "default_workdir": "E:\\old\\ws",
                "counts": {}, "external_workdirs": [],
            }).encode("utf-8"),
            "data/../../evil.txt": b"x",
            "data/tasks/..\\..\\evil2.txt": b"x",
            "workspace/../../evil3.txt": b"x",
            "data/C:/Windows/evil4.txt": b"x",
        }
        with zfmod.ZipFile(str(evil), "w") as z:
            for name, body in entries.items():
                z.writestr(name, body)

        new_data = self._rebind_new_machine("machine-z")
        new_ws = self.tmp / "machine-z-ws"
        new_ws.mkdir()
        self.settings._FILE = new_data / "settings.json"
        self.settings.save({"default_workdir": str(new_ws)})

        pv = self.backup.inspect_backup(evil)      # 预览不落盘、能列出条目
        self.assertEqual(pv["data_files"], 3)      # 3 条 data/* + 1 条 workspace/*
        with self.assertRaises(ValueError):
            self.backup.apply_import(evil, mode="merge")
        # 盘上零落笔：数据目录、工作区、临时目录上层都不该出现 evil*
        for probe in (new_data, new_ws, self.tmp, self.tmp.parent):
            self.assertEqual([p.name for p in probe.glob("evil*")], [],
                             str(probe))
        # 单元级：_safe_join 对每种逃逸形态都要拒绝
        from app.core import backup as B
        for name in ("../evil", "a/../../evil", "a\\..\\..\\evil",
                     "C:/evil", "C:\\evil"):
            with self.assertRaises(ValueError):
                B._safe_join(new_data, name)
        # UNC/绝对形态被切段驯化：必须落在 base 之内而非盘外
        p = B._safe_join(new_data, "\\\\srv\\share\\x")
        self.assertTrue(str(p).lower().startswith(str(new_data.resolve()).lower()))


def paths_runs_dir():
    from app.core import paths
    return paths.RUNS_DIR


if __name__ == "__main__":
    unittest.main()
