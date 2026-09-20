# -*- coding: utf-8 -*-
"""封面图生成（covergen）单测。

跑法：python -m unittest discover -s tests -p "test_covergen.py" -v
"""
from __future__ import annotations

import os

from base import BaseTest


class CoverSafeUrlTests(BaseTest):

    def test_rejects_non_https(self):
        from app.core import covergen
        with self.assertRaises(ValueError):
            covergen._safe_image_url("http://cdn.example.com/a.png")

    def test_rejects_private_and_loopback(self):
        from app.core import covergen
        for u in ("https://127.0.0.1/a.png", "https://192.168.1.2/a.png",
                  "https://10.0.0.3/a.png", "https://169.254.169.254/latest/meta-data"):
            with self.assertRaises(ValueError):
                covergen._safe_image_url(u)

    def test_accepts_public_https(self):
        from unittest import mock
        from app.core import covergen
        # DNS 解析结果打桩：公网地址放行
        infos = [(None, None, None, "", ("93.184.216.34", 443))]
        with mock.patch.object(covergen.socket, "getaddrinfo", return_value=infos):
            self.assertEqual(covergen._safe_image_url("https://cdn.example.com/a.png"),
                             "https://cdn.example.com/a.png")


class CoverGenFlowTests(BaseTest):

    def _task(self, wd):
        return {"id": "t-cover", "title": "测试书", "goal": "写个故事", "workdir": wd}

    def _cover_path(self, run_id):
        return os.path.join(str(self.tmp), "data", "runs", run_id, "cover.png")

    def test_start_no_run(self):
        from app.core import covergen, store
        task = store.create_task({"type": "direct", "title": "t", "goal": "g",
                                  "workdir": str(self.workdir)})
        ok, err = covergen.start(task["id"])
        self.assertFalse(ok)
        self.assertIn("运行记录", err)

    def test_start_running_idempotent(self):
        from app.core import covergen, store
        task = store.create_task({"type": "direct", "title": "t", "goal": "g",
                                  "workdir": str(self.workdir)})
        run = store.create_run("orchestration", "t", task_id=task["id"])
        self.assertTrue(covergen.start(task["id"], run["id"])[0])
        ok, err = covergen.start(task["id"], run["id"])   # running 幂等
        self.assertTrue(ok)
        self.assertEqual((store.get_task(task["id"]).get("cover_gen") or {}).get("status"),
                         "running")

    def test_make_cover_url_flow(self):
        from unittest import mock
        from app.core import covergen
        run_id = "r-cover"
        wd = os.path.join(str(self.tmp), "bookwd")
        os.makedirs(wd, exist_ok=True)
        curl_calls = []

        def fake_call(base, key, model, prompt, size, allow_private):
            return {"url": "https://cdn.example.com/cover.png"}, ""

        def fake_curl_to(url, out_path):
            curl_calls.append((url, out_path))
            with open(out_path, "wb") as f:
                f.write(b"\x89PNGfake")

        orch = {"id": "p", "base_url": "https://api.x/v4", "protocol": "openai",
                "allow_private": False, "api_key": "k-test"}
        with mock.patch.object(covergen, "_call_images", side_effect=fake_call), \
             mock.patch.object(covergen, "_curl_to", side_effect=fake_curl_to), \
             mock.patch.object(covergen, "_safe_image_url", side_effect=lambda u: u), \
             mock.patch("app.core.modelhub.resolve_orchestrator", return_value=(orch, "m")):
            entry = covergen.make_cover(run_id, self._task(wd))
        self.assertEqual(entry["status"], "done")
        self.assertEqual(entry["model"], "cogview-3-flash")
        self.assertEqual(entry["provider"], "p")   # 出图供应商随 entry 记录，卡片可见
        self.assertEqual(entry["size"], "768x1344")
        self.assertEqual(len(curl_calls), 1)
        self.assertTrue(curl_calls[0][0].startswith("https://"))
        cover = self._cover_path(run_id)
        self.assertTrue(os.path.isfile(cover))   # 落 paths.RUNS_DIR 受管路径

    def test_make_cover_all_models_fail(self):
        from unittest import mock
        from app.core import covergen
        wd = os.path.join(str(self.tmp), "bookwd2")
        os.makedirs(wd, exist_ok=True)
        orch = {"id": "p", "base_url": "https://api.x/v4", "protocol": "openai",
                "api_key": "k-test"}
        with mock.patch.object(covergen, "_call_images",
                               return_value=(None, "nope")), \
             mock.patch("app.core.modelhub.resolve_orchestrator", return_value=(orch, "m")):
            entry = covergen.make_cover("r-cover", self._task(wd))
        self.assertEqual(entry["status"], "failed")
        self.assertEqual(entry["error"], "nope")


class CoverCandidateTests(BaseTest):
    """端点/模型候选：编排者 anthropic 面不再挡路，openai 面自动跟上。"""

    def test_openai_face(self):
        from app.core import covergen
        self.assertEqual(
            covergen._openai_face({"protocol": "openai", "base_url": "https://a/v4/"}),
            "https://a/v4")
        # 显式 anthropic 但适配实测出 openai 面：用实测面地址
        self.assertEqual(
            covergen._openai_face({"protocol": "anthropic",
                                   "base_url": "https://a/api/anthropic",
                                   "wire_caps": {"openai": {"base": "https://a/v4"}}}),
            "https://a/v4")
        # 没实测过不猜（两协议面路径不同，猜必错）
        self.assertEqual(
            covergen._openai_face({"protocol": "anthropic",
                                   "base_url": "https://a/api/anthropic"}), "")

    def test_candidates_orch_first_then_openai_peers(self):
        from unittest import mock
        from app.core import covergen, modelhub
        orch = {"id": "orch", "name": "编排", "protocol": "anthropic",
                "base_url": "https://a/api/anthropic", "api_key": "k1",
                "wire_caps": {"openai": {"base": "https://a/v4"}}}
        peer = {"id": "p2", "name": "备胎", "protocol": "openai",
                "base_url": "https://b/v1", "api_key": "k2"}
        off = {"id": "p3", "name": "停用", "protocol": "openai",
               "base_url": "https://c", "api_key": "k3", "enabled": False}
        noface = {"id": "p4", "name": "无面", "protocol": "anthropic",
                  "base_url": "https://d/api/anthropic", "api_key": "k4"}
        with mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=(orch, "m")), \
             mock.patch.object(modelhub, "providers",
                               return_value=[peer, off, orch, noface]):
            cands = covergen._candidates()
        # 编排者置顶（用实测 openai 面），显式 openai 的备选跟上；停用/无面不收
        self.assertEqual([(c["label"], tuple(c["bases"])) for c in cands],
                         [("编排", ("https://a/v4",)), ("备胎", ("https://b/v1",))])

    def test_image_bases_canon_first_for_bigmodel(self):
        from app.core import covergen
        # wire_caps 面是 chat 代理路径（anthropic 面）：规范图像面在前省一轮假 200
        p = {"protocol": "anthropic", "base_url": "https://open.bigmodel.cn/api/anthropic",
             "api_key": "k", "wire_caps": {"openai": {"base": "https://open.bigmodel.cn/api/anthropic"}}}
        self.assertEqual(covergen._image_bases(p),
                         ["https://open.bigmodel.cn/api/paas/v4",
                          "https://open.bigmodel.cn/api/anthropic"])
        # face 本身就是规范面：去重只剩一条
        p2 = {"protocol": "openai", "base_url": "https://open.bigmodel.cn/api/paas/v4",
              "api_key": "k"}
        self.assertEqual(covergen._image_bases(p2),
                         ["https://open.bigmodel.cn/api/paas/v4"])
        # 无 face 但主机命中规范表（没探过 wire_caps 的智谱户也能出图）
        p3 = {"protocol": "anthropic", "base_url": "https://open.bigmodel.cn/api/anthropic",
              "api_key": "k"}
        self.assertEqual(covergen._image_bases(p3),
                         ["https://open.bigmodel.cn/api/paas/v4"])

    def test_call_images_rejects_success_false_envelope(self):
        from unittest import mock
        from app.core import covergen
        # 智谱 anthropic 面：HTTP 200 + {"success":false,"msg":"404 NOT_FOUND"}
        with mock.patch.object(covergen, "_post_images",
                               return_value=(200, {"code": 500, "msg": "404 NOT_FOUND",
                                                   "success": False}, "")):
            item, err = covergen._call_images("https://x", "k", "cogview-3-flash",
                                              "p", "1024x1024", False)
        self.assertIsNone(item)
        self.assertEqual(err, "404 NOT_FOUND")

    def test_call_images_default_no_watermark_and_env_toggle(self):
        from unittest import mock
        from app.core import covergen
        seen = {}

        def fake_post(url, key, body, allow_private, timeout):
            seen["body"] = body
            return 200, {"data": [{"url": "https://x/a.png"}]}, ""

        with mock.patch.object(covergen, "_post_images", side_effect=fake_post):
            covergen._call_images("https://x", "k", "cogview-3-flash", "p",
                                  "1024x1024", False)
            self.assertIs(seen["body"].get("watermark"), False)
            covergen._call_images("https://x", "k", "cogview-4", "p",
                                  "1024x1024", False)
            self.assertIs(seen["body"].get("watermark"), False)
        with mock.patch.dict(os.environ, {"CODEBEE_IMAGE_WATERMARK": "1"}):
            with mock.patch.object(covergen, "_post_images", side_effect=fake_post):
                covergen._call_images("https://x", "k", "cogview-3-flash", "p",
                                      "1024x1024", False)
        self.assertNotIn("watermark", seen["body"])

    def test_post_images_reads_httperror_body(self):
        from unittest import mock
        import io
        import urllib.error
        from app.core import covergen

        class FakeResp(io.BytesIO):
            code = 503

            def __init__(self, body):
                super().__init__(body.encode("utf-8"))

        err = urllib.error.HTTPError("https://x/v1/images/generations", 503, "Service Unavailable",
                                     {}, FakeResp('{"error":{"code":"model_not_found","message":'
                                                  '"No available channel for model cogview-3-flash"}}'))
        opener = mock.MagicMock()
        opener.open.side_effect = err
        from app.core import modelhub
        with mock.patch.object(modelhub, "_validate_host",
                               return_value=("x", "")), \
             mock.patch.object(modelhub, "_opener", return_value=opener):
            status, data, msg = covergen._post_images("https://x/v1/images/generations",
                                                      "k", {}, False, 10)
        self.assertEqual(status, 503)
        self.assertIn("No available channel", msg)   # 真因直达封面卡，不再是光秃 503

    def test_image_models_keyword_scan(self):
        from app.core import covergen
        old = os.environ.pop("CODEBEE_IMAGE_MODEL", None)
        try:
            prov = {"models": [{"name": "glm-4.6"}, {"name": "Cogview-4"},
                               {"name": "cogview-3-flash"}]}
            ms = covergen._image_models(prov)
            self.assertEqual(ms[0], "Cogview-4")          # 清单序优先于默认候选
            self.assertNotIn("glm-4.6", ms)               # 文本模型不混入
            self.assertEqual(ms.count("cogview-3-flash"), 1)   # 默认候选去重补位
        finally:
            if old is not None:
                os.environ["CODEBEE_IMAGE_MODEL"] = old

    def test_make_cover_no_candidates_guidance(self):
        from unittest import mock
        from app.core import covergen, modelhub
        with mock.patch.object(modelhub, "resolve_orchestrator", return_value=None), \
             mock.patch.object(modelhub, "providers", return_value=[]):
            entry = covergen.make_cover("r-x", {"title": "t", "goal": "g"})
        self.assertEqual(entry["status"], "failed")
        self.assertIn("openai", entry["error"])
        self.assertIn("适配测试", entry["error"])   # 失败文案要给出路，不是死胡同


if __name__ == "__main__":
    unittest.main()
