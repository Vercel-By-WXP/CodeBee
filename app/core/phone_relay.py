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
import zlib
from urllib.parse import urlsplit

from . import paths, remote
from .relay_ws import RelaySocket

DEFAULT_URL = "https://app.codebeeagent.com"
MAX_BODY = 16 * 1024 * 1024
# Response frames travel base64 inside one WebSocket message; the cloud cap is
# 192 KiB of base64 text. Frames queue through a bounded window instead of
# strict lockstep so a high-latency path can have several frames in flight.
FRAME_BYTES = 96 * 1024
WINDOW = 4
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
            # Show the code as soon as it is generated.  Cloud confirmation is
            # still tracked separately in pair_ready, but waiting for that
            # acknowledgement made the button look dead whenever the relay
            # was connecting or briefly reconnecting.
            ready = bool(pair and self.pair_ready)
            code = self.device + "." + pair["code"] if pair else ""
            return {"enabled": True, "connected": self.connected, "error": self.error,
                    "entry": self.base_url, "device": self.device,
                    "pair_ready": ready,
                    "pair_url": self.base_url + "/relay/portal#pair=" + code if pair else "",
                    "pair_code": code, "expires_at": pair["expires"] if pair else 0}

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
                    # Advertise transport capabilities. Older clouds ignore the
                    # unknown type and keep the plain lockstep protocol; newer
                    # clouds persist it and start requesting gzip responses.
                    self._send({"type": "hello", "gzip": True}, socket)
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
                                # Only the connector thread writes this counter;
                                # readers tolerate the racy int under the GIL.
                                item["acked"] += 1
                                item["ack"].set()
                        elif kind == "request":
                            rid = message.get("id")
                            if not isinstance(rid, str) or not re.fullmatch(r"[a-f0-9-]{36}", rid):
                                continue
                            if rid in self.active or len(self.active) >= 24:
                                self._send({"type": "error", "id": rid}, socket)
                                continue
                            item = {"cancel": threading.Event(), "ack": threading.Event(),
                                    "acked": 0}
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

    def _await_window(self, item, sent):
        """Block while WINDOW frames are unacknowledged.

        The event is only a wakeup hint; the counter is the truth, and the
        5-second repoll bounds any lost wakeup. A 120-second gap with no
        counter progress means the relay stopped acknowledging.
        """
        quiet = 0
        while sent - item["acked"] >= WINDOW:
            if item["cancel"].is_set():
                raise TimeoutError("Response cancelled")
            if not item["ack"].wait(5):
                quiet += 5
                if quiet >= 120:
                    raise TimeoutError("Response stalled")
            else:
                item["ack"].clear()
                quiet = 0

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
            allowed = {"accept", "accept-language", "content-type", "range", "if-none-match", "accept-encoding",
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
            eligible_body = method != "HEAD" and response.status not in (204, 205, 304)
            gzip_requested = (message.get("gzip") is True and
                              not response_headers.get("content-type", "").lower().startswith("text/event-stream"))
            source = 512 * 1024 if gzip_requested else 128 * 1024
            # Only gzip needs a look-ahead to distinguish an empty response;
            # plain/SSE responses must announce headers before blocking on data.
            first_chunk = response.read1(source) if eligible_body and gzip_requested else None
            # The local hop is always identity, so only relay-advertised gzip
            # turns on compression; byte-range bodies must keep their exact
            # length semantics, and content-length is dropped for the new size.
            # Read one source chunk before announcing gzip so an empty 200
            # response remains a genuinely empty response.
            compress = (message.get("gzip") is True and
                        "content-range" not in response_headers and bool(first_chunk))
            if compress:
                response_headers.pop("content-length", None)
            announcement = {"type": "headers", "id": rid, "status": response.status,
                            "headers": response_headers}
            if compress:
                announcement["enc"] = "gzip"
            self._send(announcement, socket)
            if eligible_body and not gzip_requested:
                # Keep identity/SSE responses low-latency: headers go out
                # before the first blocking read and each frame is confirmed
                # exactly as in the original relay protocol.
                while not item["cancel"].is_set():
                    chunk = response.read1(source)
                    if not chunk:
                        break
                    for offset in range(0, len(chunk), FRAME_BYTES):
                        frame = chunk[offset:offset + FRAME_BYTES]
                        item["ack"].clear()
                        self._send({"type": "chunk", "id": rid,
                                    "data": base64.b64encode(frame).decode("ascii")}, socket)
                        if not item["ack"].wait(120):
                            raise TimeoutError("Response stalled")
                self._send({"type": "end", "id": rid}, socket)
                return
            if eligible_body:
                pump = zlib.compressobj(6, zlib.DEFLATED, 31) if compress else None
                # Compressed reads aggregate more source bytes per frame, so
                # the same bounded window moves several times the payload.
                pending = bytearray()
                sent = 0

                def emit(data):
                    self._await_window(item, sent)
                    self._send({"type": "chunk", "id": rid,
                                "data": base64.b64encode(data).decode("ascii")}, socket)
                    return sent + 1

                chunk = first_chunk if first_chunk is not None else response.read1(source)
                while not item["cancel"].is_set() and chunk:
                    if pump is not None:
                        # A sync flush per read keeps event streams flowing;
                        # for large reads the compressor emits nearly
                        # everything anyway, so the ratio cost is negligible.
                        pending.extend(pump.compress(chunk))
                        pending.extend(pump.flush(zlib.Z_SYNC_FLUSH))
                    else:
                        pending.extend(chunk)
                    while len(pending) >= FRAME_BYTES:
                        frame = bytes(pending[:FRAME_BYTES])
                        del pending[:FRAME_BYTES]
                        sent = emit(frame)
                    chunk = response.read1(source)
                if pump is not None:
                    pending.extend(pump.flush(zlib.Z_FINISH))
                if pending:
                    sent = emit(bytes(pending))
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
