import json
import os
import tempfile
import unittest
from pathlib import Path

from app.core import paths, remote


class RemoteSecurityTests(unittest.TestCase):
    def test_same_origin_normalizes_default_ports_and_rejects_cross_site(self):
        self.assertTrue(remote.same_origin("http://127.0.0.1", "127.0.0.1:80"))
        self.assertTrue(remote.same_origin("https://codebee.example:443",
                                          "codebee.example:443"))
        self.assertFalse(remote.same_origin("https://evil.example", "127.0.0.1:8765"))
        self.assertFalse(remote.same_origin("null", "127.0.0.1:8765"))
        self.assertFalse(remote.same_origin("javascript:alert(1)", "127.0.0.1:8765"))
        self.assertTrue(remote.same_origin("", "127.0.0.1:8765"))

    def test_host_allowlist_blocks_dns_rebinding_hosts(self):
        old_bind_host = remote._BIND_HOST
        old_public_url = remote.PUBLIC_URL
        try:
            remote.set_bind_host("127.0.0.1")
            remote.PUBLIC_URL = ""
            for local_host in ("localhost", "localhost:8765", "127.0.0.1:9999",
                               "[::1]:8765"):
                self.assertTrue(remote.allowed_request_host(local_host), local_host)
            for hostile_host in ("attacker.example", "127.0.0.1.attacker.example",
                                 "attacker.example@127.0.0.1", "127.0.0.1/evil",
                                 "", "not a host"):
                self.assertFalse(remote.allowed_request_host(hostile_host), hostile_host)
        finally:
            remote.set_bind_host(old_bind_host)
            remote.PUBLIC_URL = old_public_url

    def test_host_allowlist_accepts_explicit_bind_and_configured_proxy_host(self):
        old_bind_host = remote._BIND_HOST
        old_public_url = remote.PUBLIC_URL
        old_trust_proxy = remote._TRUST_PROXY
        old_lan_ip = remote.lan_ip
        old_tailscale_ip = remote.tailscale_ip
        try:
            remote.set_bind_host("0.0.0.0")
            remote.PUBLIC_URL = "https://codebee.example:443"
            remote.lan_ip = lambda: "192.168.1.24"
            remote.tailscale_ip = lambda: "100.64.0.10"
            for host in ("codebee.example", "192.168.1.24:8765", "100.64.0.10"):
                self.assertTrue(remote.allowed_request_host(host), host)
            self.assertFalse(remote.allowed_request_host("unconfigured.example"))
            remote.set_bind_host("::")
            remote.PUBLIC_URL = ""
            self.assertTrue(remote.allowed_request_host("192.168.1.24"))
            remote.set_bind_host("127.0.0.1")
            remote._TRUST_PROXY = True
            self.assertTrue(remote.allowed_request_host(
                "proxy-only.example", "198.51.100.8"))
            self.assertFalse(remote.allowed_request_host("proxy-only.example"))
        finally:
            remote.set_bind_host(old_bind_host)
            remote.PUBLIC_URL = old_public_url
            remote._TRUST_PROXY = old_trust_proxy
            remote.lan_ip = old_lan_ip
            remote.tailscale_ip = old_tailscale_ip

    def test_new_token_is_high_entropy_and_written_atomically(self):
        old_data_dir = paths.DATA_DIR
        old_token = remote._TOKEN
        try:
            with tempfile.TemporaryDirectory() as tmp:
                paths.DATA_DIR = Path(tmp)
                remote._TOKEN = ""
                value = remote.token()
                self.assertGreaterEqual(len(value), 40)
                persisted = json.loads((Path(tmp) / "remote.json").read_text(encoding="utf-8"))
                self.assertEqual(persisted["token"], value)
                self.assertTrue((Path(tmp) / "remote.json").is_file())
                if os.name != "nt":
                    self.assertEqual((Path(tmp) / "remote.json").stat().st_mode & 0o777, 0o600)
        finally:
            paths.DATA_DIR = old_data_dir
            remote._TOKEN = old_token


if __name__ == "__main__":
    unittest.main()
