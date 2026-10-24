"""Bounded, TLS-verified RFC 6455 client for the outbound phone connector."""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
import threading
from urllib.parse import urlsplit


class RelaySocket:
    MAX_MESSAGE = 24 * 1024 * 1024

    def __init__(self, url, secret):
        parsed = urlsplit(url)
        if parsed.scheme not in ("wss", "ws") or not parsed.hostname:
            raise ValueError("Invalid connector URL")
        if parsed.scheme == "ws" and parsed.hostname not in ("localhost", "127.0.0.1"):
            raise ValueError("TLS required")
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        # IPv4 优先拨号：部分网络/本机的 IPv6 出口常坏（SYN_SENT 挂死），
        # socket.create_connection 按 getaddrinfo 顺序 AAAA 在前，会把 15s
        # 预算先烧在死 v6 上；网络再劣化一点 v4 握手也轮不上，手机桥就陷
        # 入「一直连接中，请求超时」（2026-10-24 实案）。先试全部 IPv4，
        # 全败再退回其余族。
        try:
            infos = socket.getaddrinfo(parsed.hostname, port,
                                       type=socket.SOCK_STREAM)
        except socket.gaierror:
            infos = []
        self.sock = None
        last_err = None
        for family in (socket.AF_INET, socket.AF_INET6):
            for af, _ty, _pr, _cn, sa in [i for i in infos if i[0] == family]:
                try:
                    cand = socket.socket(af, socket.SOCK_STREAM)
                    cand.settimeout(15)
                    cand.connect(sa)
                    self.sock = cand
                    break
                except OSError as exc:
                    last_err = exc
                    try:
                        cand.close()
                    except OSError:
                        pass
            if self.sock is not None:
                break
        if self.sock is None:
            raise last_err or OSError("relay dial failed")
        self.lock = threading.Lock()
        self.buffer = b""
        try:
            if parsed.scheme == "wss":
                self.sock = ssl.create_default_context().wrap_socket(
                    self.sock, server_hostname=parsed.hostname)
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            host = parsed.netloc
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            head = ("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
                    "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                    "Sec-WebSocket-Version: 13\r\nUser-Agent: Codebee-Phone-Connector/1.0\r\n"
                    "Authorization: Bearer %s\r\n\r\n")
            self.sock.sendall((head % (path, host, key, secret)).encode("ascii"))
            while b"\r\n\r\n" not in self.buffer:
                chunk = self.sock.recv(4096)
                if not chunk or len(self.buffer) > 16384:
                    raise ConnectionError("Connector handshake failed")
                self.buffer += chunk
            raw, _, self.buffer = self.buffer.partition(b"\r\n\r\n")
            lines = raw.decode("latin-1").split("\r\n")
            headers = {name.lower(): value for name, value in
                       (line.split(":", 1) for line in lines[1:] if ":" in line)}
            expected = base64.b64encode(hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()).decode()
            if lines[0].split()[1] != "101" or headers.get("sec-websocket-accept", "").strip() != expected:
                raise ConnectionError("Connector handshake rejected")
            self.sock.settimeout(90)
        except Exception:
            self.sock.close()
            raise

    def send(self, text):
        self._send(1, text.encode("utf-8"))

    def _send(self, opcode, data):
        if len(data) > self.MAX_MESSAGE:
            raise ValueError("Connector message too large")
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x80 | opcode])
        head += (bytes([0x80 | n]) if n < 126 else
                 bytes([0xFE]) + struct.pack(">H", n) if n < 65536 else
                 bytes([0xFF]) + struct.pack(">Q", n))
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        with self.lock:
            self.sock.sendall(head + mask + masked)

    def _read(self, n):
        while len(self.buffer) < n:
            chunk = self.sock.recv(min(65536, max(4096, n - len(self.buffer))))
            if not chunk:
                raise ConnectionError("Connector disconnected")
            self.buffer += chunk
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data

    def receive(self):
        parts, kind = bytearray(), None
        while True:
            b1, b2 = self._read(2)
            op, final = b1 & 15, bool(b1 & 128)
            if b1 & 112 or b2 & 128:
                raise ConnectionError("Invalid server frame")
            n = b2 & 127
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            if n > self.MAX_MESSAGE - len(parts):
                raise ConnectionError("Connector message too large")
            if op >= 8 and (not final or n > 125):
                raise ConnectionError("Invalid control frame")
            data = self._read(n)
            if op == 8:
                raise ConnectionError("Connector closed")
            if op == 9:
                self._send(10, data)
                continue
            if op == 10:
                continue
            if op == 1 and kind is None:
                kind = op
            elif op != 0 or kind is None:
                raise ConnectionError("Invalid message frame")
            parts.extend(data)
            if final:
                return parts.decode("utf-8")

    def close(self):
        # Shutdown also wakes the blocked reader; no unbounded close handshake.
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
