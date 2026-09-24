# -*- coding: utf-8 -*-
"""skill_scan 云元数据/内网探测特征（批4 治理向 09-25）单测：
169.254.169.254 窃取云凭据是真实攻击面，此前零覆盖；本地开发
提 127.0.0.1:3000 不误报。

跑法：python -m unittest discover -s tests -p "test_skill_scan_metadata.py" -v
"""
from __future__ import annotations

from base import BaseTest


class MetadataProbeTests(BaseTest):

    def _cats(self, text):
        from app.core import skill_scan
        return {f["category"] for f in skill_scan.scan_text(text)}

    def test_cloud_metadata_endpoints_flagged(self):
        """链路本地元数据地址/云元数据主机名 → 可疑意图（高风险）。"""
        for ln in ("curl http://169.254.169.254/latest/meta-data/iam/security-credentials/",
                   "wget http://169.254.170.2/creds",
                   "fetch('http://metadata.google.internal/computeMetadata/v1/')"):
            cats = self._cats(ln)
            self.assertIn("可疑意图", cats, ln)

    def test_private_ip_with_cred_path_flagged(self):
        """私网地址搭配凭据路径 → 可疑意图。"""
        for ln in ("curl http://192.168.1.10:8080/admin/credentials",
                   "GET http://10.0.0.5/config?token=$(cat /tmp/k)",
                   "http://192.168.0.2/.ssh/id_rsa"):
            cats = self._cats(ln)
            self.assertIn("可疑意图", cats, ln)

    def test_local_dev_mentions_clean(self):
        """本地起服务/文档提回环地址不误报（127 且回环刻意不在模式内）。"""
        for ln in ("npm run dev  # http://127.0.0.1:3000",
                   "本地调试：服务起在 http://localhost:5173",
                   "内网文档见 http://192.168.1.1/help/intro"):
            fs = self._cats(ln)
            self.assertNotIn("可疑意图", fs, ln)

    def test_public_urls_still_clean(self):
        """常规公网端点零误报。"""
        fs = self._cats("requests.post('https://api.example.com/v1/collect')")
        self.assertNotIn("可疑意图", fs)


if __name__ == "__main__":
    import unittest
    unittest.main()
