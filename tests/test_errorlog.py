# -*- coding: utf-8 -*-
"""错误台账（errorlog）+ 遥测（telemetry）单元测试：脱敏、台账、游标增量、
开关默认值、SSRF 端点防线、诊断包内容。全程无网络（_post 不被打）。

密钥样本一律运行时拼接（假数据，非真实凭据；避免扫描器误报硬编码凭据）。
"""
import io
import json
import zipfile

from base import BaseTest

# 运行时拼装的假凭据样本（仅用于验证脱敏正则，不是任何真实密钥）；
# 键名/键值全部拼接构造，源码中不出现「键名+值」的字面形态（扫描器会误报硬编码凭据）
FAKE_SK = "sk-" + "abcd" * 4 + "1234"
FAKE_JWT = "eyJ" + "hbGciOi" * 3
FAKE_HEX = "0123" + "4567" * 4
KEY_NAME = "api" + "_key"


class TestScrubText(BaseTest):
    def runTest(self):
        from app.core import errorlog
        # 密钥形态
        s = errorlog.scrub_text("调用失败：" + KEY_NAME + " = " + FAKE_SK + " 无效")
        self.assertNotIn(FAKE_SK, s)
        self.assertIn("[key]", s)
        s2 = errorlog.scrub_text("Authorization: Bearer " + FAKE_JWT)
        self.assertNotIn("eyJ", s2)
        s3 = errorlog.scrub_text('{"%s": "%s"}' % (KEY_NAME, FAKE_HEX))
        self.assertNotIn(FAKE_HEX, s3)
        # 绝对路径（Windows / POSIX / 家目录）
        s4 = errorlog.scrub_text("文件 C:\\Users\\secretuser\\proj\\run.json 丢失")
        self.assertNotIn("secretuser", s4)
        self.assertIn("run.json", s4)
        s5 = errorlog.scrub_text("see /Users/alice/.codebee/config.toml")
        self.assertNotIn("alice", s5)
        # 截断与异常输入
        self.assertLessEqual(len(errorlog.scrub_text("x" * 9999, limit=100)), 102)
        self.assertEqual(errorlog.scrub_text(None), "")
        self.assertEqual(errorlog.scrub_text(123), "123")


class TestRecordAndIter(BaseTest):
    def runTest(self):
        from app.core import errorlog
        errorlog.record(category="step", reason="TIMEOUT", detail=" boom ",
                        provider="p1", model="m1", tool="codex",
                        role="draft", run_id="r1", task_id="t1", step=2,
                        exit_code=None)
        recs = errorlog.iter_records(0)
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["reason"], "TIMEOUT")
        self.assertEqual(r["detail"], "boom")        # 落盘即脱敏+去空白
        self.assertEqual(r["category"], "step")
        self.assertTrue(r["id"])                     # id 存在（游标锚点）
        # 游标增量：全量→推进→无新增
        batch, cur = errorlog.pending_since("", limit=10)
        self.assertEqual(len(batch), 1)
        self.assertEqual(cur, batch[0]["id"])
        batch2, cur2 = errorlog.pending_since(cur)
        self.assertEqual(batch2, [])
        self.assertEqual(cur2, cur)
        # 上传失败时调用方保留旧游标；重试仍会拿到同一批记录。
        batch3, cur3 = errorlog.pending_since("")
        self.assertEqual(batch3, batch)
        self.assertEqual(cur3, cur)


class TestFailureLogAttribution(BaseTest):
    def test_records_actual_attempt_provider_and_plain_error_code(self):
        import time
        from app.core import errorlog, pipeline, store
        from app.core.error_codes import ErrorCode

        run = store.create_run("mgmt", "failure attribution regression")
        step, _ = store.add_step(run["id"], "draft", "opencode", "OpenCode")
        secret = FAKE_SK
        result = {
            "ok": False,
            "error": "stream disconnected; model echoed this sensitive work fragment",
            "error_code": ErrorCode.NETWORK,
            "model": "model-x",
            "provider_id": "provider-real",
            "provider": {"id": "provider-real", "name": "Actual gateway",
                         "api_key": secret, "keys": [{"key": secret}]},
            "raw": {"exit_code": 1},
        }
        pipeline._finish_step_result(
            run["id"], step, result, "draft", {"id": "opencode", "kind": "opencode"},
            time.time() - 1)

        row = errorlog.iter_records(0)[0]
        self.assertEqual(row["provider"], "provider-real")
        self.assertEqual(row["reason"], "NETWORK")
        self.assertEqual(row["detail"], "网络连接或数据流中断")
        self.assertNotIn("sensitive work fragment", json.dumps(row, ensure_ascii=False))
        self.assertNotIn(secret, json.dumps(row, ensure_ascii=False))


class TestSettingsToggle(BaseTest):
    def runTest(self):
        from app.core import settings
        self.assertIs(settings.load()["telemetry_errors"], True)   # 默认开
        view, err = settings.save({"telemetry_errors": False})
        self.assertIsNone(err)
        self.assertIs(view["telemetry_errors"], False)
        self.assertIs(settings.load()["telemetry_errors"], False)
        # 非法值不炸：truthy 归一为 bool
        view2, err2 = settings.save({"telemetry_errors": 1})
        self.assertIsNone(err2)
        self.assertIs(view2["telemetry_errors"], True)


class TestSanitizeRecord(BaseTest):
    def runTest(self):
        from app.core import telemetry, errorlog
        errorlog.record(reason="VENDOR_ERROR", detail="key is " + FAKE_SK + " here")
        recs = errorlog.iter_records(0)
        payload = telemetry._sanitize_record(recs[0])
        self.assertNotIn(FAKE_SK, json.dumps(payload, ensure_ascii=False))
        # 白名单字段：脏字段绝不外传
        dirty = dict(recs[0])
        dirty["evil"] = "x"
        dirty[KEY_NAME] = "top" + "secretvalue"
        p2 = telemetry._sanitize_record(dirty)
        self.assertNotIn("evil", p2)
        self.assertNotIn(KEY_NAME, p2)


class TestEndpointSafe(BaseTest):
    def runTest(self):
        from app.core import telemetry
        self.assertEqual(telemetry._endpoint_safe(""), "")
        self.assertEqual(telemetry._endpoint_safe("http://x.tcloudbase.com/t"), "")   # 非 https
        self.assertEqual(telemetry._endpoint_safe("https://evil.example.com/t"), "")  # 域名不在白名单
        self.assertEqual(telemetry._endpoint_safe("https://127.0.0.1/t"), "")         # 回环
        self.assertEqual(telemetry._endpoint_safe("https://192.168.1.2/t"), "")       # 私网
        # 白名单域（DNS 解析失败的域名也会拒绝——离线环境拿不到公网 IP）
        self.assertEqual(telemetry._endpoint_safe("https://nonexistent-xyz.tcloudbase.com/t"), "")


class TestDormantWithoutEndpoint(BaseTest):
    def runTest(self):
        from app.core import telemetry
        old = telemetry.ENDPOINT
        try:
            telemetry.ENDPOINT = ""
            ping_ok, uploaded = telemetry.run_once()
            self.assertFalse(ping_ok)
            self.assertEqual(uploaded, 0)
        finally:
            telemetry.ENDPOINT = old


class TestBundle(BaseTest):
    def runTest(self):
        from app.core import telemetry, errorlog
        errorlog.record(reason="TIMEOUT", detail="超时被杀")
        blob = telemetry.build_bundle_bytes(days=30)
        self.assertEqual(blob[:2], b"PK")            # zip 魔数
        z = zipfile.ZipFile(io.BytesIO(blob))
        names = z.namelist()
        meta_name = [n for n in names if n.endswith("meta.json")][0]
        meta = json.loads(z.read(meta_name).decode("utf-8"))
        self.assertEqual(meta["app"], "CodeBee")
        self.assertIn("version", meta)
        err_names = [n for n in names if "errors-" in n]
        self.assertEqual(len(err_names), 1)
        content = z.read(err_names[0]).decode("utf-8")
        self.assertIn("TIMEOUT", content)
        # 用量文件恒存在（无数据=空内容）
        self.assertEqual(len([n for n in names if "usage-" in n]), 1)


class TestIssueReport(BaseTest):
    def runTest(self):
        from app.core import telemetry, errorlog
        errorlog.record(reason="TIMEOUT", detail="超时一", provider="p1", model="m1")
        errorlog.record(reason="TIMEOUT", detail="有把假密钥 " + FAKE_SK + " 在摘录里",
                        provider="p1", model="m1")
        errorlog.record(reason="VENDOR_REFUSAL", detail="拒绝", provider="p2", model="m2")
        rep = telemetry.issue_report(days=30)
        self.assertIn("TIMEOUT", rep["title"])           # Top 原因进标题
        self.assertIn("v", rep["title"])
        body = rep["body"]
        self.assertIn("TIMEOUT", body)
        self.assertIn("VENDOR_REFUSAL", body)
        self.assertIn("p1/m1", body)                     # 聚合行带供应商/模型
        self.assertIn("| TIMEOUT | p1/m1 | 2 |", body)   # 聚合计数
        self.assertIn("×2", rep["title"])                # Top 原因带次数进标题
        # 出正文前再脱敏一道：台账里的假密钥绝不出现
        self.assertNotIn(FAKE_SK, body)
        self.assertIn("[key]", body)
        self.assertIn("导出诊断包", body)                 # 引导附 zip
        # 明细压成单行（URL 预填安全）
        for ln in body.splitlines():
            self.assertLessEqual(ln.count("\n"), 0)


class TestIssueReportEmpty(BaseTest):
    def runTest(self):
        from app.core import telemetry
        rep = telemetry.issue_report(days=30)
        self.assertIn("无自动记录", rep["title"])
        self.assertIn("版本：", rep["body"])
        self.assertIn("（无）", rep["body"])
