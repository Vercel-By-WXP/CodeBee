"""Security and real Worker/Codebee integration checks (isolated data and ports).

CODEBEE_RELAY_TEST_URL=http://127.0.0.1:18791 enables the integration case.
Run the Worker locally with `npm run dev` in cloud/phone-relay first.
"""
from __future__ import annotations

import base64
import concurrent.futures
import gzip
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.core import phone_relay

ROOT = Path(__file__).resolve().parents[1]
RELAY = os.environ.get("CODEBEE_RELAY_TEST_URL", "")


class _FakeResponse:
    def __init__(self, chunks, headers, status=200):
        self.chunks, self.headers, self.status = list(chunks), headers, status

    def getheaders(self):
        return list(self.headers.items())

    def read1(self, n):
        if not self.chunks:
            return b""
        size = min(n, len(self.chunks[0]))
        head, self.chunks[0] = self.chunks[0][:size], self.chunks[0][size:]
        if not self.chunks[0]:
            self.chunks.pop(0)
        return head


class _FakeConnection:
    def __init__(self, response):
        self.response = response

    def request(self, *args, **kwargs):
        pass

    def getresponse(self):
        return self.response

    def close(self):
        pass


class _FakeSocket:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(json.loads(text))


def _ack_responder(item, socket):
    """Mirror the real relay: one ack message per received chunk frame."""
    seen = 0

    def loop():
        nonlocal seen
        while not item["cancel"].is_set():
            chunks = [m for m in socket.sent if m.get("type") == "chunk"]
            while seen < len(chunks):
                seen += 1
                item["acked"] += 1
                item["ack"].set()
            time.sleep(0.005)
    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread


class StreamingPolicyTest(unittest.TestCase):
    def stream(self, chunks, headers, gzip_requested):
        connector = phone_relay.Connector.__new__(phone_relay.Connector)
        connector.lock = threading.RLock()
        connector.port = 19999  # evaluated before the patched constructor
        connector.active = {}
        socket = _FakeSocket()
        item = {"cancel": threading.Event(), "ack": threading.Event(), "acked": 0}
        response = _FakeResponse(chunks, headers)
        message = {"id": "a" * 8 + "-0000", "method": "GET", "path": "/api/state"}
        if gzip_requested:
            message["gzip"] = True
        watcher = _ack_responder(item, socket)
        # Patch both the dial target (never touch a live local service) and
        # the token read (never touch real user config from a unit test).
        with patch.object(phone_relay.http.client, "HTTPConnection",
                          lambda *args, **kwargs: _FakeConnection(response)), \
             patch.object(phone_relay.remote, "token", return_value="isolated-token"):
            connector._request(message, item, socket)
        item["cancel"].set()
        watcher.join(timeout=2)
        return socket.sent

    def payload(self, sent):
        headers = [m for m in sent if m["type"] == "headers"][0]
        body = b"".join(base64.b64decode(m["data"]) for m in sent if m["type"] == "chunk")
        self.assertEqual(sent[-1]["type"], "end")
        self.assertTrue(all(len(m["data"]) <= 4 * 1024 * 1024 for m in sent if m["type"] == "chunk"))
        for m in [m for m in sent if m["type"] == "chunk"]:
            self.assertLessEqual(len(m["data"]), (phone_relay.FRAME_BYTES + 2) // 3 * 4, "frame cap")
        return headers, body

    def test_plain_stream_reassembles_in_windowed_frames(self):
        payload = bytes(range(256)) * 1500  # ~384 KiB -> several frames
        sent = self.stream([payload[i:i + 128 * 1024] for i in range(0, len(payload), 128 * 1024)],
                           {"Content-Type": "application/json", "Content-Length": str(len(payload))},
                           gzip_requested=False)
        headers, body = self.payload(sent)
        self.assertEqual(body, payload)
        self.assertNotIn("enc", headers)
        self.assertEqual(headers["headers"]["content-length"], str(len(payload)))
        self.assertGreater(len([m for m in sent if m["type"] == "chunk"]), 3, "multiple frames")

    def test_gzip_stream_announces_and_roundtrips(self):
        payload = b"tutti-frutti-" * 40000  # ~520 KiB compressible
        sent = self.stream([payload[i:i + 512 * 1024] for i in range(0, len(payload), 512 * 1024)],
                           {"Content-Type": "application/json", "Content-Length": str(len(payload))},
                           gzip_requested=True)
        headers, body = self.payload(sent)
        self.assertEqual(headers["enc"], "gzip")
        self.assertNotIn("content-length", headers["headers"])
        self.assertEqual(gzip.decompress(body), payload)
        plain = len(payload)
        self.assertLess(len(body), plain // 3, "compression actually shrank the body")

    def test_gzip_declined_for_byte_range_bodies(self):
        payload = b"range-body" * 5000
        sent = self.stream([payload],
                           {"Content-Type": "application/json", "Content-Range": "bytes 0-100/500000"},
                           gzip_requested=True)
        headers, body = self.payload(sent)
        self.assertNotIn("enc", headers)
        self.assertEqual(body, payload)
        self.assertEqual(headers["headers"]["content-range"], "bytes 0-100/500000")

    def test_empty_body_with_gzip_stays_empty(self):
        sent = self.stream([], {"Content-Type": "application/json"}, gzip_requested=True)
        self.assertEqual([m["type"] for m in sent], ["headers", "end"])

    def test_sse_chunks_flow_without_compression(self):
        events = [b"event: state\ndata: {%d}\n\n" % i for i in range(50)]
        sent = self.stream(events, {"Content-Type": "text/event-stream"}, gzip_requested=True)
        headers, body = self.payload(sent)
        self.assertNotIn("enc", headers)
        self.assertIn(b"event: state", body)


class ConnectorPolicyTest(unittest.TestCase):
    def test_local_targets_only(self):
        for path in ("http://127.0.0.1/", "//other/", "/\\other", "/%2fother",
                     "/api/connect/relay", "/%61pi/connect", "/relay/pair", "/x\r\nHost: other"):
            self.assertFalse(phone_relay.safe_path(path), path)
        self.assertTrue(phone_relay.safe_path("/api/state?client=phone"))

    def test_tls_required_and_no_credential_urls(self):
        for url in ("http://example.com", "https://user:secret@example.com", "https://example.com/path"):
            with patch.dict(os.environ, {"CODEBEE_RELAY_URL": url}):
                with self.assertRaises(ValueError):
                    phone_relay.relay_url()

    def test_corrupt_identity_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory(prefix="codebee-relay-policy-") as directory:
            with patch.object(phone_relay.paths, "DATA_DIR", Path(directory)):
                file = Path(directory) / "phone-relay.json"
                file.write_text('{"secret":"broken","enabled":true}', encoding="utf-8")
                with self.assertRaises(ValueError):
                    phone_relay._config()
                self.assertIn("broken", file.read_text(encoding="utf-8"))

    def test_identity_persists(self):
        with tempfile.TemporaryDirectory(prefix="codebee-relay-policy-") as directory:
            with patch.object(phone_relay.paths, "DATA_DIR", Path(directory)):
                config = phone_relay._config()
                phone_relay._save(config)
                self.assertEqual(config, phone_relay._config())
                self.assertEqual(len(config["secret"]), 64)

    def test_pair_code_survives_connector_reconnect(self):
        config = {"secret": secrets.token_hex(32), "enabled": True}
        with patch.dict(os.environ, {"CODEBEE_RELAY_URL": "http://127.0.0.1:18791"}):
            connector = phone_relay.Connector(18891, config)
            first = connector.new_pair()
            self.assertFalse(first["pair_ready"])
            self.assertTrue(first["pair_url"])
            self.assertTrue(first["pair_code"])
            with connector.lock:
                connector.pair_ready = True
            confirmed = connector.view()
            self.assertTrue(confirmed["pair_url"])
            connector.connected = False
            still_visible = connector.view()
            self.assertEqual(still_visible["pair_code"], confirmed["pair_code"])
            self.assertEqual(still_visible["pair_url"], confirmed["pair_url"])
            self.assertTrue(still_visible["pair_ready"])


@unittest.skipUnless(RELAY, "Set CODEBEE_RELAY_TEST_URL to a running local Worker")
class RelayIntegrationTest(unittest.TestCase):
    def call(self, base, path, body=None, cookie="", headers=None):
        request_headers = dict(headers or {})
        request_headers.setdefault("User-Agent", "Codebee-Phone-Integration/1.0")
        if cookie:
            request_headers["Cookie"] = cookie
        if body is not None:
            request_headers.setdefault("Origin", base)
            request_headers["Content-Type"] = "application/json"
        request = Request(base + path, headers=request_headers,
                          data=json.dumps(body).encode() if body is not None else None)
        try:
            response = urlopen(request, timeout=20)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            # Local Wrangler and some HTTP clients may combine or omit the
            # content-encoding token while preserving the gzip wire bytes.
            # Decode based on both signals so the integration assertion tests
            # the relay payload rather than urllib's header normalization.
            while raw.startswith(b"\x1f\x8b") and "gzip" in response.headers.get("Content-Encoding", "").lower():
                raw = gzip.decompress(raw)
            value = json.loads(raw) if "application/json" in response.headers.get("Content-Type", "") else raw
            return response.status, value, response.headers

    def wait(self, function, timeout=60):
        end = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < end:
            try:
                value = function()
                if value:
                    return value
            except Exception as error:
                last_error = error
            time.sleep(.3)
        raise AssertionError("Timed out waiting for isolated test service: %s" % last_error)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codebee-phone-e2e-")
        self.processes = []
        self.logs = []
        self.local = []
        self.specs = []
        for i in range(2):
            directory = Path(self.temp.name) / ("computer-%d" % i)
            directory.mkdir()
            secret = secrets.token_hex(32)
            (directory / "phone-relay.json").write_text(json.dumps({"secret": secret, "enabled": True}))
            (directory / "remote.json").write_text(json.dumps({"token": "isolated-local-%d" % i}))
            (directory / "tasks").mkdir()
            (directory / "tasks" / "task-own.json").write_text(json.dumps({
                "id": "task-own", "title": "computer-%d-only" % i, "type": "code",
                "goal": "Never execute this fixture", "workdir": str(directory),
                "status": "done", "archived": False, "created_at": "2099-01-01 00:00:00",
            }))
            log = (directory / "service.log").open("wb")
            self.logs.append(log)
            env = dict(os.environ, TUTTI_DATA=str(directory), CODEBEE_RELAY_URL=RELAY,
                       TUTTI_PET_DISABLED="1", TUTTI_CLASSROOM_SYNC="0", PYTHONIOENCODING="utf-8")
            port = 18891 + i
            process = subprocess.Popen([sys.executable, "app/main.py", "--host", "127.0.0.1",
                                        "--port", str(port), "--no-browser", "--no-public-tunnel"],
                                       cwd=str(ROOT), env=env, stdout=log, stderr=log)
            self.processes.append(process)
            self.specs.append((env, port, log))
            self.local.append("http://127.0.0.1:%d" % port)
        try:
            for base in self.local:
                self.wait(lambda: self.call(base, "/api/connect")[1].get("relay", {}).get("connected"))
        except Exception:
            self.tearDown()
            raise

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=15)
        for log in self.logs:
            log.close()
        self.temp.cleanup()

    def pair(self, computer):
        base = self.local[computer]
        self.assertEqual(self.call(base, "/api/connect/relay", {"action": "pair"})[0], 200)
        relay = self.wait(lambda: self.call(base, "/api/connect")[1].get("relay") if
                          self.call(base, "/api/connect")[1].get("relay", {}).get("pair_code") else None)
        device, code = relay["pair_code"].split(".")
        body = {"device": device, "code": code}
        status, value, headers = self.call(RELAY, "/relay/pair", body)
        self.assertEqual((status, value), (200, {"ok": True}))
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        return cookie, body

    def test_two_computers_pairing_streaming_revocation_and_reconnect(self):
        self.assertEqual(self.call(RELAY, "/")[0], 200)
        self.assertEqual(self.call(RELAY, "/api/state")[0], 401)
        cookies = []
        bodies = []
        for i in range(2):
            cookie, body = self.pair(i)
            cookies.append(cookie)
            bodies.append(body)
            status, state, _ = self.call(RELAY, "/api/state", cookie=cookie)
            self.assertEqual(status, 200)
            titles = [task["title"] for task in state["tasks"]]
            self.assertIn("computer-%d-only" % i, titles)
            self.assertNotIn("computer-%d-only" % (1 - i), titles)
            self.assertEqual(self.call(RELAY, "/relay/pair", body)[0], 401, "single-use pairing")
        wrong = cookies[0].replace(bodies[0]["device"], bodies[1]["device"])
        self.assertEqual(self.call(RELAY, "/api/state", cookie=wrong)[0], 401, "cross-device cookie denied")
        self.assertEqual(self.call(RELAY, "/relay/pair", bodies[0], headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.call(RELAY, "/api/control", {"action": "acquire"}, cookies[0],
                                   {"X-CodeBee-Client": "phone-a"})[0], 200)
        self.assertEqual(self.call(RELAY, "/api/control", cookie=cookies[1])[1]["control"]["mode"], "free")
        self.assertEqual(self.call(RELAY, "/api/control", {"action": "acquire"}, cookies[0],
                                   {"X-CodeBee-Client": "phone-second"})[0], 423)
        self.assertEqual(self.call(RELAY, "/api/state", cookie=cookies[0],
                                   headers={"X-CodeBee-Token": "wrong-token", "X-Forwarded-For": "127.0.0.1"})[0], 200)
        self.assertEqual(self.call(RELAY, "/api/connect/relay", {"action": "revoke"}, cookies[0])[0], 403)
        self.assertEqual(self.call(self.local[0], "/api/connect/relay", {"action": "pair"},
                                   headers={"X-Forwarded-For": "198.18.0.1"})[0], 401)
        # Read a real SSE event over the reverse bridge (not buffered to EOF).
        request = Request(RELAY + "/api/events?client=phone-a", headers={
            "Cookie": cookies[0], "User-Agent": "Codebee-Phone-Integration/1.0"})
        with urlopen(request, timeout=15) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "text/event-stream")
            line = response.readline()
            self.assertTrue(line.startswith((b"event:", b"data:", b":")), line[:100])
        # A real process restart preserves identity and the phone's cloud session.
        self.processes[1].terminate()
        self.processes[1].wait(timeout=15)
        self.assertEqual(self.call(RELAY, "/api/state", cookie=cookies[1])[0], 503)
        self.assertEqual(self.call(RELAY, "/", cookie=cookies[1])[0], 200, "offline has a usable landing page")
        env, port, log = self.specs[1]
        self.processes[1] = subprocess.Popen([sys.executable, "app/main.py", "--host", "127.0.0.1",
                                             "--port", str(port), "--no-browser", "--no-public-tunnel"],
                                            cwd=str(ROOT), env=env, stdout=log, stderr=log)
        self.wait(lambda: self.call(RELAY, "/api/state", cookie=cookies[1])[0] == 200)
        # One code submitted concurrently yields exactly one session.
        self.call(self.local[0], "/api/connect/relay", {"action": "pair"})
        pairing = self.wait(lambda: self.call(self.local[0], "/api/connect")[1]["relay"].get("pair_code"))
        device, code = pairing.split(".")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(lambda _: self.call(RELAY, "/relay/pair", {"device": device, "code": code})[0], range(2)))
        self.assertEqual(sorted(statuses), [200, 401])
        self.assertEqual(self.call(self.local[0], "/api/connect/relay", {"action": "revoke"})[0], 200)
        self.assertEqual(self.call(RELAY, "/api/state", cookie=cookies[0])[0], 401)
        self.assertEqual(self.call(RELAY, "/api/state", cookie=cookies[1])[0], 200)
        self.assertEqual(self.call(RELAY, "/relay/logout", {}, cookies[1])[0], 200)
        self.assertEqual(self.call(RELAY, "/api/state", cookie=cookies[1])[0], 401)


if __name__ == "__main__":
    unittest.main()
