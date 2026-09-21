# -*- coding: utf-8 -*-
"""runner / store / mocks 单元测试（全部用 mock，不碰真实 CLI）。"""
from __future__ import annotations

import json

from base import BaseTest


class TestExtractJson(BaseTest):
    def runTest(self):
        from app.core import runner
        self.assertEqual(runner.extract_json('{"a":1}'), {"a": 1})
        self.assertEqual(runner.extract_json('前言 ```json\n{"a": 2}\n``` 后记'),
                         {"a": 2})
        self.assertEqual(runner.extract_json('评语 {"scores": {"x": 1.5}} 结尾'),
                         {"scores": {"x": 1.5}})
        self.assertIsNone(runner.extract_json("没有任何 JSON"))
        self.assertIsNone(runner.extract_json('{"popped'))  # 坏 JSON 不炸


class TestParsers(BaseTest):
    def runTest(self):
        from app.core import runner
        codex_out = "\n".join([
            '{"type":"thread.started","thread_id":"t1"}',
            '{"type":"turn.started"}',
            '{"type":"item.completed","item":{"id":"i0","type":"agent_message","text":"你好"}}',
            '{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":5}}',
        ])
        text, usage, sid = runner._parse_codex_jsonl(codex_out)
        self.assertEqual(text, "你好")
        self.assertEqual(sid, "t1")  # §07 T1.1：thread_id 供会话复用
        self.assertEqual(usage["total"], 105)
        self.assertEqual(usage["input"], 100)
        self.assertEqual(usage["output"], 5)

        claude_out = json.dumps({
            "type": "result", "is_error": False, "result": "OK",
            "total_cost_usd": 0.01,
            "session_id": "sess-abc",
            "usage": {"input_tokens": 10, "output_tokens": 2},
        })
        p = runner._parse_claude_json(claude_out)
        self.assertEqual(p["text"], "OK")
        self.assertEqual(p["tokens"], 12)
        self.assertEqual(p["usage"]["total"], 12)
        self.assertEqual(p["sid"], "sess-abc")  # §07 T1.1
        self.assertIsNone(runner._parse_claude_json("not json"))


class TestResolveCommand(BaseTest):
    def runTest(self):
        from app.core import runner
        parts = runner.resolve_command("cmd")
        self.assertEqual(len(parts), 1)  # 直接可执行文件原样返回
        self.assertEqual(runner.resolve_command(r"C:\x\tool.cmd")[0:2], ["cmd", "/c"])
        self.assertEqual(runner.resolve_command(r"C:\x\tool.exe"), [r"C:\x\tool.exe"])


class TestStoreValidation(BaseTest):
    def runTest(self):
        from app.core import store
        wd = str(self.workdir)
        with self.assertRaises(ValueError):
            store.create_task({"type": "bad", "title": "t", "goal": "g", "workdir": wd})
        with self.assertRaises(ValueError):
            store.create_task({"type": "code", "title": "", "goal": "", "workdir": wd})
        # 标题可省略：自动取目标首行
        t0 = store.create_task({"type": "code", "title": "", "goal": "标题自动派生\n第二行",
                                "workdir": wd})
        self.assertEqual(t0["title"], "标题自动派生")
        with self.assertRaises(ValueError):
            store.create_task({"type": "code", "title": "t", "goal": "", "workdir": wd})
        with self.assertRaises(ValueError):
            store.create_task({"type": "code", "title": "t", "goal": "g", "workdir": "relative/path"})
        with self.assertRaises(ValueError):
            store.create_task({"type": "code", "title": "t", "goal": "g",
                               "workdir": str(self.tmp / "nope")})
        # 稿件名消毒：带路径分隔符会被拍平
        t = store.create_task({"type": "novel", "title": "t", "goal": "g", "workdir": wd,
                               "manuscript": r"..\..\evil.md", "rounds": 2, "threshold": 7.0})
        self.assertNotIn("..", t["manuscript"])
        self.assertNotIn("/", t["manuscript"])
        self.assertNotIn("\\", t["manuscript"])


class TestStepLogTraversal(BaseTest):
    def runTest(self):
        from app.core import store
        run = store.create_run("mgmt", "t")
        secret = self.tmp / "secret.txt"
        secret.write_text("TOPSECRET", encoding="utf-8")
        # 目录穿越尝试必须拿不到内容
        self.assertEqual(store.read_step_log(run["id"], "../secret.txt"), "")


class TestTailDecoded(BaseTest):
    def runTest(self):
        from app.core import runner
        # 切点落在 UTF-8 多字节字符中间时，必须对齐到下一个完整字符，
        # 否则残缺字节走 GBK 回退被解成乱码（详情页日志出现 ◆ 的根因）
        mid = "不要在回复里复述或解释正文。".encode("utf-8")
        raw = b"a" * 1000 + mid + b"b" * 3959  # 切点=len-4000 落在「不」字第 2 字节
        out = runner.tail_decoded(raw, 4000)
        self.assertFalse("\ufffd" in out or "◆" in out, repr(out[:20]))
        self.assertTrue(out.startswith("要在回复里复述或解释正文。"), repr(out[:20]))
        self.assertTrue(out.endswith("b" * 10))
        # 短于 tail 不截断
        self.assertEqual(runner.tail_decoded("中文".encode("utf-8"), 4000), "中文")
        self.assertEqual(runner.tail_decoded(b"", 4000), "")


class TestDeleteRun(BaseTest):
    def runTest(self):
        from app.core import paths, store
        run = store.create_run("mgmt", "t")
        (paths.RUNS_DIR / run["id"] / "steps").mkdir(parents=True, exist_ok=True)
        (paths.RUNS_DIR / run["id"] / "steps" / "01-x.log").write_text("log", encoding="utf-8")
        store.update_run(run["id"], status="done")  # 初始为 queued，先置为已结束
        # 正常删除：内存与磁盘同时清掉
        ok, err = store.delete_run(run["id"])
        self.assertTrue(ok, err)
        self.assertIsNone(store.get_run(run["id"]))
        self.assertFalse((paths.RUNS_DIR / run["id"]).exists())
        # 重复删除 / 不存在
        ok, err = store.delete_run(run["id"])
        self.assertFalse(ok)
        self.assertIn("不存在", err)
        # 非法 ID（防目录穿越）
        for bad in ("../evil", "a/b", "a\\b", "..", ""):
            ok, err = store.delete_run(bad)
            self.assertFalse(ok, bad)
            self.assertIn("非法", err)
        # 运行中/排队中不允许删
        r2 = store.create_run("mgmt", "t2")
        for st in ("queued", "running"):
            store.update_run(r2["id"], status=st)
            ok, err = store.delete_run(r2["id"])
            self.assertFalse(ok)
            self.assertIn("取消", err)
        self.assertIsNotNone(store.get_run(r2["id"]))
        # 已取消/失败/完成可删
        store.update_run(r2["id"], status="cancelled")
        ok, err = store.delete_run(r2["id"])
        self.assertTrue(ok, err)
        self.assertIsNone(store.get_run(r2["id"]))


class TestMocksDeterministic(BaseTest):
    def runTest(self):
        from app.core import mocks
        task = {"title": "T", "goal": "G"}
        dims = ["情节", "人物"]
        c1a = mocks.critique("mock-a", 1, dims, 7.0)
        c1b = mocks.critique("mock-a", 1, dims, 7.0)
        self.assertEqual(c1a, c1b)  # 同参数结果必须一致（可重放）
        r1 = mocks.critique("mock-a", 1, dims, 7.0)
        r2 = mocks.critique("mock-a", 2, dims, 7.0)
        self.assertFalse(r1["scores"]["情节"] >= 7.0)   # 第 1 轮低于阈值
        self.assertTrue(r2["scores"]["情节"] >= 7.0)    # 第 2 轮达标


class TestTaskArchiveDelete(BaseTest):
    def runTest(self):
        from app.core import store
        wd = str(self.workdir)
        t1 = store.create_task({"type": "code", "goal": "任务一", "workdir": wd})
        t2 = store.create_task({"type": "novel", "goal": "任务二", "workdir": wd})
        r1 = store.create_run("orchestration", t1["title"], task_id=t1["id"])
        r2 = store.create_run("orchestration", t2["title"], task_id=t2["id"])
        store.update_run(r1["id"], status="done")   # 新建运行默认 queued，不可删
        store.update_run(r2["id"], status="done")

        # 归档：默认列表隐藏、归档列表可见、可恢复
        ok, err = store.archive_task(t1["id"], True)
        self.assertTrue(ok, err)
        self.assertEqual([t["id"] for t in store.list_tasks(archived=False)], [t2["id"]])
        self.assertEqual([t["id"] for t in store.list_tasks(archived=True)], [t1["id"]])
        ok, _ = store.archive_task(t1["id"], False)
        self.assertTrue(ok)
        self.assertEqual(len(store.list_tasks(archived=False)), 2)

        # 删除：任务 + 关联运行（内存与磁盘目录）一并清除
        ok, err = store.delete_task(t2["id"])
        self.assertTrue(ok, err)
        self.assertIsNone(store.get_task(t2["id"]))
        self.assertIsNone(store.get_run(r2["id"]))
        # 删除接口先 rename 脱离可见路径，递归清理在后台进行，不阻塞请求。
        self.assertFalse((self._paths.RUNS_DIR / r2["id"]).exists())
        self.assertEqual([r["id"] for r in store.list_runs(10)], [r1["id"]])

        # 防护：运行中的任务不能删除；归档不设限（只是隐藏，运行照常继续）；非法 ID 拒绝
        store.update_run(r1["id"], status="running")
        ok, err = store.delete_task(t1["id"])
        self.assertFalse(ok)
        self.assertIn("取消", err)
        ok, _ = store.archive_task(t1["id"], True)
        self.assertTrue(ok)
        self.assertEqual([t["id"] for t in store.list_tasks(archived=True)], [t1["id"]])
        ok, _ = store.delete_task("../escape")
        self.assertFalse(ok)


class TestTaskRetry(BaseTest):
    def runTest(self):
        from app.core import store
        wd = str(self.workdir)
        t = store.create_task({"type": "code", "goal": "重试我", "workdir": wd})
        r1 = store.create_run("orchestration", t["title"], task_id=t["id"])
        store.update_run(r1["id"], status="running")
        ok, err, _ = store.retry_task(t["id"])
        self.assertFalse(ok)                      # 运行中不能重试
        store.update_run(r1["id"], status="failed")
        # 终态回填：run 失败时任务状态同步为 failed（归档不再误报"运行中"）
        self.assertEqual(store.get_task(t["id"])["status"], "failed")
        ok, err, r2 = store.retry_task(t["id"])
        self.assertTrue(ok, err)
        self.assertEqual(r2["task_id"], t["id"])
        self.assertEqual(store.get_task(t["id"])["status"], "queued")
        ok, _ = store.archive_task(t["id"], True)
        self.assertTrue(ok)                       # 运行中也可归档：只是隐藏，运行照常继续
        store.update_run(r2["id"], status="done")
        self.assertEqual(store.get_task(t["id"])["status"], "done")
        # load_all 回填历史遗留：run 终态而任务停在 queued
        t3 = store.create_task({"type": "code", "goal": "遗留", "workdir": wd})
        r3 = store.create_run("orchestration", t3["title"], task_id=t3["id"])
        store.update_run(r3["id"], status="failed")
        store._TASKS[t3["id"]]["status"] = "queued"
        store.load_all()
        self.assertEqual(store.get_task(t3["id"])["status"], "failed")
        # 非法 ID
        ok, _, _ = store.retry_task("../escape")
        self.assertFalse(ok)
        # 重启恢复：磁盘上停留在 queued/running 的运行判为中断（failed），任务状态随之回填
        t4 = store.create_task({"type": "code", "goal": "断点", "workdir": wd})
        r4 = store.create_run("orchestration", t4["title"], task_id=t4["id"])
        store._RUNS[r4["id"]]["status"] = "running"
        store._save_json(self._paths.RUNS_DIR / r4["id"] / "run.json", store._RUNS[r4["id"]])
        store._RUNS.clear()
        store._TASKS.clear()
        store.load_all()
        self.assertEqual(store.get_run(r4["id"])["status"], "failed")
        self.assertEqual(store.get_task(t4["id"])["status"], "failed")
        # 卡在 queued 却从未有运行（创建中断）→ 标记失败，可重试
        t5 = store.create_task({"type": "code", "goal": "创建中断", "workdir": wd})
        store._TASKS[t5["id"]]["status"] = "queued"
        store.load_all()
        self.assertEqual(store.get_task(t5["id"])["status"], "failed")
        ok, err, r5 = store.retry_task(t5["id"])
        self.assertTrue(ok, err)


class TestRunBatchDelete(BaseTest):
    def runTest(self):
        from app.core import paths, store

        def mk(title, status):
            r = store.create_run("mgmt", title)
            (paths.RUNS_DIR / r["id"] / "steps").mkdir(parents=True, exist_ok=True)
            (paths.RUNS_DIR / r["id"] / "steps" / "01-x.log").write_text("log", encoding="utf-8")
            store.update_run(r["id"], status=status)
            return r

        done1, done2 = mk("a", "done"), mk("b", "failed")
        running, queued = mk("c", "running"), mk("d", "queued")

        # 批量删除：终态删除，运行中/排队中与不存在的 ID 计入跳过
        n, skipped, err = store.delete_runs(
            [done1["id"], done2["id"], running["id"], queued["id"], "r-nope-1"])
        self.assertEqual(n, 2)
        self.assertEqual(skipped, 3)
        self.assertEqual(err, "")
        self.assertIsNone(store.get_run(done1["id"]))
        self.assertFalse((paths.RUNS_DIR / done1["id"]).exists())   # 磁盘目录一并清除
        self.assertIsNone(store.get_run(done2["id"]))
        self.assertIsNotNone(store.get_run(running["id"]))
        self.assertIsNotNone(store.get_run(queued["id"]))

        # 非法 ID 只计数不中断，同批合法记录照常删除
        r3 = mk("e", "cancelled")
        n, skipped, err = store.delete_runs([r3["id"], "../evil", "a/b"])
        self.assertEqual(n, 1)
        self.assertEqual(skipped, 0)
        self.assertIn("非法", err)

        # ids 非数组
        n, skipped, err = store.delete_runs("oops")
        self.assertEqual((n, skipped), (0, 0))
        self.assertIn("数组", err)

        # 一键清除：终态全清，活跃记录保留
        r4 = mk("f", "done")
        n, skipped = store.clear_runs()
        self.assertEqual(n, 1)        # 此时仅剩 r4 可清
        self.assertEqual(skipped, 2)  # running + queued
        self.assertIsNone(store.get_run(r4["id"]))
        self.assertIsNotNone(store.get_run(running["id"]))
        self.assertIsNotNone(store.get_run(queued["id"]))


class TestNpmPkgName(BaseTest):
    def runTest(self):
        from app.core import manager

        # 常规：包名紧跟 -g
        self.assertEqual(manager._npm_pkg_name("npm install -g @openai/codex"),
                         "@openai/codex")
        # 带版本号：去掉 @latest
        self.assertEqual(
            manager._npm_pkg_name("npm install -g @qwen-code/qwen-code@latest"),
            "@qwen-code/qwen-code")
        # 包名前有 flag（Pi 的官方写法）：不能把 --ignore-scripts 当成包名
        self.assertEqual(
            manager._npm_pkg_name(
                "npm install -g --ignore-scripts @earendil-works/pi-coding-agent"),
            "@earendil-works/pi-coding-agent")
        self.assertEqual(
            manager._npm_pkg_name(
                "npm install -g --ignore-scripts @earendil-works/pi-coding-agent@latest"),
            "@earendil-works/pi-coding-agent")
        # 非 npm 命令不误判
        self.assertIsNone(manager._npm_pkg_name("winget install -e --id xAI.GrokBuild"))
        self.assertIsNone(manager._npm_pkg_name("mimo upgrade"))
        self.assertIsNone(manager._npm_pkg_name(None))


class TestCatalogResumePatch(BaseTest):
    def runTest(self):
        from app.core import catalog, paths
        # 模拟旧版 data/catalog.json：mimo 没有 resume_argv_template
        entries = [dict(e) for e in catalog.DEFAULT_CATALOG]
        for e in entries:
            if e["id"] == "mimo-code":
                (e.get("orch") or {}).pop("resume_argv_template", None)
        paths.CATALOG_FILE.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        catalog._CACHE["entries"] = None
        loaded = catalog.load(force=True)
        mimo = next(e for e in loaded if e["id"] == "mimo-code")
        self.assertEqual(mimo["orch"]["resume_argv_template"], ["run", "-s", "{session}"])
        # 幂等：再 load 一次不会叠加
        loaded2 = catalog.load(force=True)
        mimo2 = next(e for e in loaded2 if e["id"] == "mimo-code")
        self.assertEqual(mimo2["orch"]["resume_argv_template"], ["run", "-s", "{session}"])
        catalog._CACHE["entries"] = None


class TestUninstallDerive(BaseTest):
    """卸载命令由安装命令推导：不单独维护一份，避免两边不同步。"""

    def runTest(self):
        from app.core import catalog
        cases = [
            ("npm install -g @openai/codex",
             "npm uninstall -g @openai/codex"),
            ("npm install -g --ignore-scripts @earendil-works/pi-coding-agent",
             "npm uninstall -g @earendil-works/pi-coding-agent"),
            ("npm install -g opencode-ai@latest",
             "npm uninstall -g opencode-ai"),
            ("winget install -e --id Anthropic.ClaudeCode",
             "winget uninstall -e --id Anthropic.ClaudeCode"),
            ("py -3.13 -m pip install -U aider-chat",
             "py -3.13 -m pip uninstall -y aider-chat"),
            # brew 渠道（2026-09-19 Mac 适配：AI 修复白名单放行 brew install）
            ("brew install node",
             "brew uninstall node"),
            ("brew install --cask firefox",
             "brew uninstall firefox"),
        ]
        for install, want in cases:
            self.assertEqual(catalog.derive_uninstall(install), want, install)
        # 认不出的渠道返回 None（由显式 uninstall 字段兜底）
        self.assertIsNone(catalog.derive_uninstall("scoop install foo"))
        self.assertIsNone(catalog.derive_uninstall(""))
        self.assertIsNone(catalog.derive_uninstall(None))
        # 显式配置优先于推导
        self.assertEqual(
            catalog.uninstall_command({"id": "x", "install": "npm install -g a",
                                       "uninstall": "  custom uninstall  "}),
            "custom uninstall")
        self.assertEqual(
            catalog.uninstall_command({"id": "x", "install": "npm install -g a"}),
            "npm uninstall -g a")
        # 每个内置条目都能推出卸载命令（装了就能卸）
        for e in catalog.DEFAULT_CATALOG:
            self.assertTrue(catalog.uninstall_command(e),
                            "内置条目缺少可推导的卸载命令: %s" % e["id"])


class TestMgmtUninstall(BaseTest):
    """卸载走与管理操作同一套：run_mgmt_command 取推导命令并执行。"""

    def runTest(self):
        from app.core import manager, runner
        seen = {}
        orig = runner.run_process

        def fake(shell_cmd=None, **kw):
            seen["cmd"] = shell_cmd
            return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "",
                    "duration": 0.0, "cancelled": False, "timed_out": False}
        runner.run_process = fake
        # 2026-09-18 起 mgmt 成功会后台复检更新徽章（check_update 线程），隔离掉：
        # 否则复检线程用 argv 调 run_process，会把下面捕获的 shell_cmd 覆盖成 None
        orig_check = manager.check_update
        manager.check_update = lambda e, force=False: None
        try:
            entry = {"id": "probe", "name": "Probe",
                     "install": "npm install -g @scope/probe"}
            res = manager.run_mgmt_command(entry, "uninstall")
            self.assertTrue(res["ok"])
            self.assertEqual(seen["cmd"], "npm uninstall -g @scope/probe")
            self.assertEqual(res["command"], "npm uninstall -g @scope/probe")
            # brew 渠道已可推导（2026-09-19 Mac 适配），反例换真正认不出的渠道
            mac = manager.run_mgmt_command({"id": "m", "install": "brew install node"},
                                           "uninstall")
            self.assertTrue(mac["ok"])
            self.assertEqual(seen["cmd"], "brew uninstall node")
            # 推不出命令时明确报错，不执行空命令
            bad = manager.run_mgmt_command({"id": "y", "install": "scoop install z"},
                                           "uninstall")
            self.assertFalse(bad["ok"])
            self.assertIn("uninstall", bad["error"])
        finally:
            runner.run_process = orig
            manager.check_update = orig_check


class TestArtifactGate(BaseTest):
    def runTest(self):
        """成品口径闸：任务一步都没跑出来过 → 工作目录里的并行改动不算成品。"""
        from app.core import store
        wd = self.workdir / "gate-repo"
        wd.mkdir()
        task = store.create_task({"type": "code", "title": "口径闸", "goal": "g",
                                  "workdir": str(wd)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        # 模拟并行改动：工作目录里有任务创建之后新写的文件
        (wd / "someone-elses.md").write_text("并行活动的产物", encoding="utf-8")
        store.update_task_status(task["id"], "failed")

        # 0 步 → files 被闸掉
        d = store.task_side(task["id"])
        self.assertEqual(d["files"], [])
        self.assertEqual(store.task_step_count(task["id"]), 0)

        # 跑出过步骤（断点续跑/正常完成）→ 文件照常列为成品
        store.update_run(run["id"], steps=[{"n": 1, "status": "done", "role": "起草",
                                            "summary": "s", "duration_s": 1}])
        d2 = store.task_side(task["id"])
        self.assertTrue(any(f["name"] == "someone-elses.md" for f in d2["files"]))
        self.assertEqual(store.task_step_count(task["id"]), 1)


if __name__ == "__main__":
    import unittest as _u
    _u.main()
