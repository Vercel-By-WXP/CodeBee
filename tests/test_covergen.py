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
        orch = {"id": "p", "base_url": "https://api.x/v4", "protocol": "openai"}
        with mock.patch.object(covergen, "_call_images",
                               return_value=(None, "nope")), \
             mock.patch("app.core.modelhub.resolve_orchestrator", return_value=(orch, "m")):
            entry = covergen.make_cover("r-cover", self._task(wd))
        self.assertEqual(entry["status"], "failed")


if __name__ == "__main__":
    unittest.main()
