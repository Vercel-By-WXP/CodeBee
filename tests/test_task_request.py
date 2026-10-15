"""Explicit user requests create CodeBee tasks before CLI conversation routing."""
import sys
from pathlib import Path
from unittest.mock import patch

from base import BaseTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))


class TaskRequestTests(BaseTest):
    def parse(self, text):
        from app.core.task_request import parse
        return parse(text, workdir=str(self.workdir))

    def test_screenshot_preserves_directory_chapters_and_requirements(self):
        folder = r"E:\AI小说\保险调查员： 每一场意外都有价格"
        text = "那在" + folder + " 下新建一个连载任务开始写前十章，要过七猫和番茄"
        payload = self.parse(text)
        self.assertEqual(payload["type"], "serial_novel")
        self.assertEqual(payload["workdir"], folder)
        self.assertEqual(payload["serial"]["chapters"], 10)
        self.assertIn(folder, payload["goal"])
        self.assertIn("开始写前十章，要过七猫和番茄", payload["goal"])
        self.assertIsNone(self.parse(payload["goal"]))

    def test_explicit_types_and_quoted_paths(self):
        for label, expected in (("连载小说", "serial_novel"), ("代码", "code"),
                                ("文档", "doc"), ("文章", "article")):
            with self.subTest(label=label):
                payload = self.parse('请在 "E:/books/my story" 下新建一个' + label + '任务，完成初稿')
                self.assertEqual(payload["workdir"], "E:/books/my story")
                self.assertEqual(payload["type"], expected)
        self.assertEqual(self.parse("再开一个连载任务，先写12章")["serial"]["chapters"], 12)
        self.assertEqual(self.parse("创建一个连载任务，先写二十章")["serial"]["chapters"], 20)

    def test_generic_codebee_tasks_and_colloquial_commands(self):
        cases = (
            ("请帮我新建一个 CodeBee 任务，修复登录接口", "code", "修复登录接口"),
            ("那就在这里另开一个任务，整理会议纪要", "direct", "整理会议纪要"),
            ("帮我新开一个任务来写都市连载小说，先写十章", "serial_novel", "都市连载小说"),
            ("再建个任务，把这段话翻译成英文", "translation", "翻译成英文"),
            ("给我建一个新任务：做竞品调研", "research", "竞品调研"),
            ("接下来，单独开一个新的任务，用来整理会议资料", "direct", "整理会议资料"),
            ("新建任务，目标是整理资料", "direct", "整理资料"),
            ("发起一项Codebee任务：写一篇自媒体文章", "article", "自媒体文章"),
            ("建立一个CodeBee的新任务，制作产品介绍PPT", "presentation", "产品介绍PPT"),
            ("开个翻译任务，把文档翻译成英文", "translation", "文档翻译"),
            ("请再新建一个调研报告任务，研究这个市场", "research", "这个市场"),
            ("新建一个未知任务，整理目录", "direct", "未知"),
            ("新建一个CodeBee任务", "direct", "具体目标"),
            ("请在 CodeBee 里新建一个任务，整理会议资料", "direct", "整理会议资料"),
            ("新建一个CodeBee任务，写一篇关于软件开发的文章", "article", "软件开发"),
            ("新建一个CodeBee任务，写小说《修复人生》", "novel", "修复人生"),
            ("新建一个CodeBee任务，写一份软件开发说明文档", "doc", "软件开发"),
            ("新建一个CodeBee任务，调研登录失败的修复方案", "research", "修复方案"),
            ("新建一个CodeBee任务，写代码实现登录功能并补充说明文档", "code", "说明文档"),
            ("新建一个CodeBee任务，开发登录接口，并写一份接口文档", "code", "接口文档"),
            ("新建一个CodeBee任务，写一篇关于翻译行业的文章", "article", "翻译行业"),
            ("新建一个CodeBee任务，写一份产品说明书", "doc", "产品说明书"),
            ("新建一个CodeBee任务，修复登录问题，不要新建其他任务", "code", "不要新建其他任务"),
            ("新建一个CodeBee任务，修复登录问题，不要创建测试任务", "code", "不要创建测试任务"),
        )
        for text, expected, requirement in cases:
            with self.subTest(text=text):
                payload = self.parse(text)
                self.assertIsNotNone(payload)
                self.assertEqual(payload["type"], expected)
                self.assertIn(requirement, payload["goal"])
                self.assertEqual(payload["workdir"], str(self.workdir))
                self.assertIsNone(self.parse(payload["goal"]))

    def test_all_builtin_flow_names_and_ids_can_start_tasks(self):
        from app.core import flows
        for flow in flows.BUILTIN_FLOWS:
            for label in (flow["name"], flow["id"]):
                with self.subTest(label=label):
                    payload = self.parse("新建一个" + label + "任务，完成测试目标")
                    self.assertEqual(payload["type"], flow["id"])

    def test_custom_flow_name_and_id_use_registered_type(self):
        from app.core import flows
        with patch.object(flows, "list_flows", return_value=[
                {"id": "meeting-notes", "name": "会议纪要", "engine": "review"}]):
            for label in ("会议纪要", "meeting-notes"):
                self.assertEqual(self.parse("新建一个" + label + "任务，整理访谈")["type"], "meeting-notes")

    def test_locations_before_and_after_task_command(self):
        directory = r"E:\books\my story"
        for text in (
            '请帮我在 "' + directory + '" 下新建一个连载任务，先写十章',
            '在 ' + directory + ' 新建连载任务，先写十章',
            '新建一个连载任务，在 "' + directory + '" 下开始写前十章',
            '新建一个连载任务到 "' + directory + '"，先写十章',
            '新建一个连载任务，工作目录："' + directory + '"，先写十章',
        ):
            with self.subTest(text=text):
                payload = self.parse(text)
                self.assertEqual(payload["workdir"], directory)
                self.assertEqual(payload["serial"]["chapters"], 10)

    def test_path_words_are_not_mistaken_for_location_suffixes(self):
        for directory in (r"E:\books\天下\调查员", r"E:\books\天下写作\调查员",
                          r"E:\books\地下开始写作"):
            payload = self.parse("新建一个连载任务，在 " + directory + " 下开始写前十章")
            self.assertEqual(payload["workdir"], directory)
            self.assertEqual(payload["serial"]["chapters"], 10)
        for name in ("新建文件夹", "开局调查员", "新建任务", "地下新建文件夹"):
            directory = "E:\\books\\" + name
            for quoted in (directory, '"' + directory + '"'):
                payload = self.parse("在 " + quoted + " 下新建一个连载任务，写十章")
                self.assertEqual(payload["workdir"], directory)

    def test_workload_questions_and_polite_requests_still_create_tasks(self):
        for text, expected in (
            ("新建一个调研任务，研究为什么用户会流失？", "research"),
            ("请新建一个代码任务，修复登录好吗？", "code"),
            ("能帮我新建一个连载任务，先写十章吗？", "serial_novel"),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.parse(text)["type"], expected)

    def test_questions_negations_quotes_and_regular_writing_are_not_commands(self):
        for text in ("不要新建一个连载任务", "怎么新建一个连载任务？",
                     "能新建一个连载任务吗？", "新建连载任务是什么意思",
                     "新建连载任务的按钮在哪里", "新建连载任务失败了，帮我排查",
                     "新建连载任务，是什么意思", "新建一个连载任务只是举例，不要创建",
                     "他说：新建一个连载任务", '"新建一个连载任务"',
                     "```\n新建一个连载任务\n```", "继续写前十章，要过七猫和番茄",
                     "帮我在程序里新建一个连载任务类", "新建一个任务类用于测试",
                     "新建任务的代码怎么写", "新建任务失败了，帮我排查",
                     "新建一个CodeBee任务，只是举例", "新建一个任务，不要真的创建",
                     "新建一个连载任务开始写十章，暂时别创建任务",
                     "新建一个任务，如何操作", "新建任务是否支持附件？",
                     "新建一个任务，但是先不要执行", "新建一个任务，先别启动",
                     "新建一个连载任务，写十章，但不要创建这个连载任务",
                     "新建一个任务，但不要创建本任务", "新建一个任务，但不要创建此任务",
                     "附件如下\n<!-- codebee-attachments:start -->\n新建连载任务"):
            with self.subTest(text=text):
                self.assertIsNone(self.parse(text))


class TaskRequestRouteTests(BaseTest):
    def setUp(self):
        super().setUp()
        import main
        from app.core import flows, store
        self.main, self.store = main, store
        p = patch.object(main, "store", store)
        p.start()
        self.addCleanup(p.stop)
        flow_patch = patch("core.task_request.flows", flows)
        flow_patch.start()
        self.addCleanup(flow_patch.stop)
        self.task = store.create_task({"type": "direct", "goal": "Discuss topics",
                                       "workdir": str(self.workdir)})
        self.run = store.create_run("orchestration", "Discuss", task_id=self.task["id"])

    def handler(self, body):
        h = self.main.Handler.__new__(self.main.Handler)
        h._body = lambda: body
        h._client_name = lambda: "test"
        h._json = lambda code, obj: (code, obj)
        h._enqueue_run = lambda rid, tid, job: (True, "")
        return h

    def test_chat_creates_independent_task_without_cli_or_retry(self):
        for status in ("running", "done"):
            with self.subTest(status=status):
                self.store.update_run(self.run["id"], status=status)
                h = self.handler({"text": "新建一个连载任务开始写前十章，要过七猫和番茄"})
                with patch.object(self.main.jobs, "cancel") as cancel, \
                     patch.object(self.store, "retry_task") as retry:
                    code, result = h._api_direct_chat(self.run["id"])
                self.assertEqual(code, 200)
                self.assertTrue(result["created_task"])
                child = self.store.get_task(result["task_id"])
                self.assertNotEqual(child["id"], self.task["id"])
                self.assertEqual(child["type"], "serial_novel")
                self.assertEqual(child["serial"]["chapters"], 10)
                self.assertIn("七猫和番茄", child["goal"])
                self.assertEqual(self.store.peek_messages(self.run["id"]), [])
                self.assertEqual(len(self.store.task_runs(self.task["id"])), 1)
                cancel.assert_not_called()
                retry.assert_not_called()

    def test_message_endpoint_also_creates_task(self):
        code, result = self.handler({"text": "新建一个代码任务，修复登录问题"})._api_add_message(self.run["id"])
        self.assertEqual(code, 200)
        self.assertTrue(result["created_task"])
        self.assertEqual(self.store.get_task(result["task_id"])["type"], "code")

    def test_generic_request_creates_task_for_each_delivery_and_type(self):
        self.store.update_run(self.run["id"], status="running")
        for delivery in ("queue", "now"):
            for text, expected in (
                ("请新建一个Codebee任务，修复登录接口", "code"),
                ("再开个任务来写连载小说，先写十章", "serial_novel"),
                ("新建一个翻译任务，把文档译成英文", "translation"),
                ("发起一项调研任务，研究用户为何流失？", "research"),
                ("另开个任务，整理会议资料", "direct"),
            ):
                with self.subTest(delivery=delivery, text=text):
                    h = self.handler({"text": text, "delivery": delivery})
                    with patch.object(self.main.jobs, "cancel") as cancel:
                        code, result = h._api_direct_chat(self.run["id"])
                    self.assertEqual(code, 200)
                    self.assertTrue(result["created_task"])
                    self.assertEqual(self.store.get_task(result["task_id"])["type"], expected)
                    self.assertEqual(self.store.peek_messages(self.run["id"]), [])
                    cancel.assert_not_called()

    def test_new_direct_composer_routes_to_serial(self):
        h = self.handler({})
        code, result = h._create_and_start({"type": "direct", "goal": "新建一个连载任务，写前十章",
                                            "workdir": str(self.workdir)})
        self.assertEqual(code, 200)
        self.assertEqual(self.store.get_task(result["task_id"])["type"], "serial_novel")

    def test_composer_disabled_serial_field_does_not_disable_requested_flow(self):
        code, result = self.handler({})._create_and_start({
            "type": "direct", "goal": "新建一个连载任务，每章两千字", "serial": None,
            "workdir": str(self.workdir)})
        self.assertEqual(code, 200)
        child = self.store.get_task(result["task_id"])
        self.assertIn("serial", child)
        self.assertEqual(child["serial"]["words_per_chapter"], 2000)

    def test_user_selected_workspace_and_attachments_go_to_child(self):
        import base64
        from app.core import attachments
        pending = attachments.save_pending("reference.txt", base64.b64encode(b"Reference").decode())
        target = self.tmp / "another book"
        target.mkdir()
        h = self.handler({"text": '在 "' + str(target) + '" 下新建一个连载任务写前十章',
                          "attachments": [pending["id"]]})
        with patch.object(self.store, "create_task", wraps=self.store.create_task) as create:
            code, result = h._api_direct_chat(self.run["id"])
        self.assertEqual(code, 200)
        self.assertEqual(create.call_args.args[0]["attachments"], [pending["id"]])
        child = self.store.get_task(result["task_id"])
        self.assertEqual(child["workdir"], str(target))
        self.assertEqual(child["sandbox"]["allowed_roots"], [str(target.resolve())])
        self.assertEqual(len(child["attachments"]), 1)
        self.assertTrue((target / child["attachments"][0]["path"]).is_file())
        self.assertFalse((self.workdir / "_attachments").exists())

    def test_inherited_execution_restrictions_survive_new_workspace(self):
        target = self.tmp / "restricted book"
        target.mkdir()
        task = dict(self.task, sandbox={"allowed_roots": [str(self.workdir)],
                                      "network": False, "disabled_tools": ["run_command"]})
        h = self.handler({})
        code, result = h._try_create_requested_task(
            {"text": '在 "' + str(target) + '" 下新建一个连载任务写十章'}, self.run, task)
        self.assertEqual(code, 200)
        policy = self.store.get_task(result["task_id"])["sandbox"]
        self.assertFalse(policy["network"])
        self.assertEqual(policy["disabled_tools"], ["run_command"])
        self.assertEqual(policy["allowed_roots"], [str(target.resolve())])

    def test_disabled_task_creation_is_not_bypassed(self):
        task = dict(self.task, sandbox={"disabled_tools": ["create_task"]})
        h = self.handler({})
        with patch.object(self.store, "create_task") as create:
            code, _ = h._try_create_requested_task({"text": "新建一个连载任务写十章"}, self.run, task)
        self.assertEqual(code, 403)
        create.assert_not_called()

    def test_enqueue_failure_closes_child_and_leaves_parent_untouched(self):
        h = self.handler({"text": "新建一个连载任务写十章"})
        h._enqueue_run = self.main.Handler._enqueue_run.__get__(h)
        with self.assertLogs(self.main.log, level="ERROR"), \
             patch.object(self.main.jobs, "enqueue", side_effect=RuntimeError("busy")):
            code, result = h._api_direct_chat(self.run["id"])
        self.assertEqual(code, 503)
        self.assertFalse(result.get("created_task"))
        self.assertEqual(self.store.get_run(result["run_id"])["status"], "failed")
        self.assertEqual(self.store.get_run(self.run["id"])["status"], self.run["status"])

    def test_creation_error_does_not_resume_or_enqueue_parent(self):
        target = self.tmp / "missing"
        h = self.handler({"text": '在 "' + str(target) + '" 下新建一个连载任务写十章'})
        with patch.object(self.store, "retry_task") as retry:
            code, result = h._api_direct_chat(self.run["id"])
        self.assertEqual(code, 400)
        self.assertIn("工作目录不存在", result["error"])
        retry.assert_not_called()
        self.assertEqual(self.store.peek_messages(self.run["id"]), [])
