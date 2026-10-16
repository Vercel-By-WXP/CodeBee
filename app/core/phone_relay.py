"""Opt-in reverse connector: one device identity, local HTTP only, bounded streams.

No Cloudflare account credentials are shipped to end users. The cloud service
terminates TLS and forwards traffic, so this is not end-to-end encryption.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from . import paths, remote
from .relay_ws import RelaySocket

DEFAULT_URL = "https://app.codebeeagent.com"
MAX_BODY = 16 * 1024 * 1024
_INSTANCE = None
_INSTANCE_LOCK = threading.RLock()


def relay_url():
    value = os.environ.get("CODEBEE_RELAY_URL", DEFAULT_URL).rstrip("/")
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise ValueError("连接入口必须是独立域名")
    if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")):
        raise ValueError("连接入口必须使用 HTTPS")
    if not parsed.hostname:
        raise ValueError("连接入口域名无效")
    return value


def _config():
    path = paths.DATA_DIR / "phone-relay.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not re.fullmatch(r"[a-f0-9]{64}", data.get("secret", "")):
            raise ValueError("手机连接凭据损坏，请先备份 phone-relay.json 再重新配对")
        return data
    return {"secret": secrets.token_hex(32), "enabled": False}


def _save(config):
    path = paths.DATA_DIR / "phone-relay.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    # Restrict POSIX permissions before writing the credential.
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(config, handle)
    os.replace(str(temporary), str(path))


def safe_path(path):
    from urllib.parse import unquote
    if not isinstance(path, str) or len(path) > 8192 or not path.startswith("/") or path.startswith("//"):
        return False
    clean = unquote(path.split("?")[0])
    return not (re.search(r"[\r\n\\\x00]", path + clean) or clean.startswith("//") or
                clean.startswith("/relay/") or clean.startswith("/api/connect"))


class Connector:
    def __init__(self, port, config):
        self.port = int(port)
        self.config = config
        self.base_url = relay_url()
        self.device = hashlib.sha256(config["secret"].encode("ascii")).hexdigest()[:32]
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.socket = None
        self.connected = False
        self.error = ""
        self.pair = None
        self.pair_ready = False
        self.revoked = threading.Event()
        self.active = {}
        self.thread = threading.Thread(target=self._run, daemon=True, name="codebee-phone-relay")

    def start(self):
        # Every forwarded request contains a token and an explicit remote marker.
        remote.set_trusted_proxy(True)
        self.thread.start()

    def view(self):
        with self.lock:
            pair = self.pair if self.pair and self.pair["expires"] > time.time() * 1000 else None
            # Keep a cloud-confirmed code visible while the connector is
            # reconnecting.  The pairing record remains in the Durable Object
            # until expiry, so hiding it on a transient socket flap only makes
            # the desktop QR disappear and forces the user to start over.
            ready = bool(pair and self.pair_ready)
            code = self.device + "." + pair["code"] if ready else ""
            return {"enabled": True, "connected": self.connected, "error": self.error,
                    "entry": self.base_url, "device": self.device,
                    "pair_ready": ready,
                    "pair_url": self.base_url + "/relay/portal#pair=" + code if ready else "",
                    "pair_code": code, "expires_at": pair["expires"] if ready else 0}

    def new_pair(self):
        with self.lock:
            self.pair = {"type": "pair", "code": secrets.token_hex(16),
                         "expires": int((time.time() + 300) * 1000)}
            self.pair_ready = False
            if self.connected:
                try:
                    self._send(self.pair)
                except (ConnectionError, OSError):
                    # Keep the code; _run will resend it on the next connection.
                    self.error = "连接正在恢复，配对码会自动重试"
        return self.view()

    def _send(self, value, socket=None):
        target = socket or self.socket
        if not target:
            raise ConnectionError("连接尚未建立")
        target.send(json.dumps(value, separators=(",", ":")))

    def revoke(self):
        self.revoked.clear()
        with self.lock:
            if not self.connected:
                raise ValueError("电脑尚未连接服务，无法确认撤销；请连接成功后再试")
            self.pair = None
            self.pair_ready = False
            self._send({"type": "revoke"})
        if not self.revoked.wait(10):
            raise ValueError("撤销确认超时，请重试")

    def stop(self):
        self.stop_event.set()
        with self.lock:
            if self.socket:
                self.socket.close()
            self._cancel_all()

    def _cancel_all(self):
        for item in list(self.active.values()):
            item["cancel"].set()
            item["ack"].set()
            connection = item.get("connection")
            if connection:
                # Closing a live response's socket wakes reads as well as headers.
                try:
                    if connection.sock:
                        connection.sock.shutdown(2)
                except OSError:
                    pass
                connection.close()

    def _run(self):
        delay = 1
        while not self.stop_event.is_set():
            socket = None
            try:
                url = self.base_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
                socket = RelaySocket(url + "/relay/connector/" + self.device, self.config["secret"])
                with self.lock:
                    self.socket = socket
                    self.connected = True
                    self.error = ""
                    if self.pair and self.pair["expires"] > time.time() * 1000:
                        self._send(self.pair, socket)
                delay = 1
                heartbeat = threading.Thread(target=self._heartbeat, args=(socket,), daemon=True)
                heartbeat.start()
                while not self.stop_event.is_set():
                    raw = socket.receive()
                    if raw == "pong":
                        continue
                    message = json.loads(raw)
                    kind = message.get("type")
                    with self.lock:
                        if kind == "pair-ready" and self.pair and message.get("code") == self.pair["code"]:
                            self.pair_ready = True
                        elif kind == "paired":
                            self.pair = None
                            self.pair_ready = False
                        elif kind == "revoked":
                            self.revoked.set()
                        elif kind in ("ack", "cancel"):
                            item = self.active.get(message.get("id"))
                            if item:
                                if kind == "cancel":
                                    item["cancel"].set()
                                    conn = item.get("connection")
                                    if conn:
                                        try:
                                            if conn.sock:
                                                conn.sock.shutdown(2)
                                        except OSError:
                                            pass
                                        conn.close()
                                item["ack"].set()
                        elif kind == "request":
                            rid = message.get("id")
                            if not isinstance(rid, str) or not re.fullmatch(r"[a-f0-9-]{36}", rid):
                                continue
                            if rid in self.active or len(self.active) >= 24:
                                self._send({"type": "error", "id": rid}, socket)
                                continue
                            item = {"cancel": threading.Event(), "ack": threading.Event()}
                            self.active[rid] = item
                            threading.Thread(target=self._request, args=(message, item, socket), daemon=True).start()
            except Exception:
                # Network exceptions can contain URLs/credentials: expose a fixed message.
                with self.lock:
                    self.error = "连接服务暂时不可用，正在自动重连"
            finally:
                with self.lock:
                    self.connected = False
                    # Do not clear a confirmed code during a transient network
                    # disconnect; the cloud-side pairing record is still valid.
                    if not self.pair or self.pair["expires"] <= time.time() * 1000:
                        self.pair_ready = False
                    self.socket = None
                    self._cancel_all()
                if socket:
                    socket.close()
            self.stop_event.wait(delay)
            delay = min(delay * 2, 30)

    def _heartbeat(self, socket):
        while not self.stop_event.wait(20):
            with self.lock:
                if self.socket is not socket or not self.connected:
                    return
            try:
                socket.send("ping")
            except Exception:
                socket.close()
                return

    def _request(self, message, item, socket):
        rid = message["id"]
        connection = None
        try:
            method, path = message.get("method"), message.get("path")
            if method not in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE") or not safe_path(path):
                raise ValueError("Invalid request")
            encoded = message.get("body", "")
            if not isinstance(encoded, str) or len(encoded) > (MAX_BODY + 2) // 3 * 4:
                raise ValueError("Request too large")
            body = base64.b64decode(encoded, validate=True)
            if len(body) > MAX_BODY:
                raise ValueError("Request too large")
            allowed = {"accept", "accept-language", "content-type", "range", "if-none-match",
                       "if-modified-since", "x-codebee-client", "x-codebee-name", "origin", "host"}
            headers = {k: v for k, v in (message.get("headers") or {}).items()
                       if k in allowed and isinstance(v, str) and not re.search(r"[\r\n\x00]", v)}
            headers.update({"X-CodeBee-Token": remote.token(), "X-Forwarded-For": "198.18.0.1",
                            "Connection": "close", "Accept-Encoding": "identity"})
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
            with self.lock:
                item["connection"] = connection
                if item["cancel"].is_set():
                    return
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            response_headers = {k.lower(): v for k, v in response.getheaders() if k.lower() in
                                ("content-type", "content-disposition", "content-length", "content-range",
                                 "accept-ranges", "etag", "last-modified", "location")}
            self._send({"type": "headers", "id": rid, "status": response.status,
                        "headers": response_headers}, socket)
            if method != "HEAD" and response.status not in (204, 205, 304):
                while not item["cancel"].is_set():
                    # The relay acknowledges every chunk.  Larger chunks keep
                    # the same bounded backpressure while avoiding dozens of
                    # round trips for the ~1 MiB desktop bundle on a phone.
                    chunk = response.read1(128 * 1024)
                    if not chunk:
                        break
                    item["ack"].clear()
                    self._send({"type": "chunk", "id": rid,
                                "data": base64.b64encode(chunk).decode("ascii")}, socket)
                    if not item["ack"].wait(120):
                        raise TimeoutError("Response stalled")
            self._send({"type": "end", "id": rid}, socket)
        except Exception:
            try:
                self._send({"type": "error", "id": rid}, socket)
            except Exception:
                pass
        finally:
            if connection:
                connection.close()
            with self.lock:
                self.active.pop(rid, None)


def view():
    with _INSTANCE_LOCK:
        return _INSTANCE.view() if _INSTANCE else {"enabled": False, "connected": False, "entry": relay_url()}


def start_saved(port):
    global _INSTANCE
    with _INSTANCE_LOCK:
        config = _config()
        if config.get("enabled") and not _INSTANCE:
            _INSTANCE = Connector(port, config)
            _INSTANCE.start()


def operate(port, action):
    global _INSTANCE
    with _INSTANCE_LOCK:
        if action == "pair":
            if not _INSTANCE:
                config = _config()
                config["enabled"] = True
                connector = Connector(port, config)
                _save(config)
                _INSTANCE = connector
                _INSTANCE.start()
            return _INSTANCE.new_pair()
        if action in ("revoke", "disable"):
            if _INSTANCE:
                if action == "revoke" or _INSTANCE.connected:
                    _INSTANCE.revoke()
                if action == "disable":
                    # Rotate the identity as well: offline disabling cannot
                    # obtain a cloud ACK, but old sessions can never reconnect.
                    config = {"secret": secrets.token_hex(32), "enabled": False}
                    _save(config)
                    _INSTANCE.stop()
                    _INSTANCE = None
            return view()
        raise ValueError("操作必须是 pair、revoke 或 disable")


def stop():
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE:
            _INSTANCE.stop()
            _INSTANCE = None
