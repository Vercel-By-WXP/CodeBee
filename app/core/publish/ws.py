# -*- coding: utf-8 -*-
"""最小 WebSocket 客户端（RFC 6455 子集）——CDP 驱动专用。

只实现客户端角色：HTTP Upgrade 握手 → 文本帧收发。CDP 的用法非常受限，
所以刻意不做完整协议：
- 客户端只发文本帧（JSON 命令），服务端回文本帧（响应/事件）+ 偶尔 ping；
- 截图等大响应可能分片（continuation 帧），必须重组；
- 不协商 permessage-deflate 等扩展，服务端帧按规范不带掩码（仍兼容读取）。

不引第三方 websocket 库：32 位 Python + npm 分发是零依赖策略，
标准库 socket 手写两百行即可覆盖上述用法，行为对着真 Edge 验证。
"""
from __future__ import annotations

import base64
import os
import socket
import struct

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class WebSocketError(Exception):
    """连接层错误（握手失败/对端关闭/帧损坏）。业务超时抛 socket.timeout。"""


class MiniWS:
    """一次性连接：不做重连（CDP 页面会话由上层 browser.py 决定重开）。"""

    def __init__(self, host, port, path, timeout=30.0):
        self._timeout = timeout
        self._sock = socket.create_connection((host, int(port)), timeout=timeout)
        self._buf = b""
        self._handshake(host, int(port), path)

    # ------------------------------------------------------------ 握手
    def _handshake(self, host, port, path):
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
               "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n") % (path, host, port, key)
        self._sock.sendall(req.encode("ascii"))
        while b"\r\n\r\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise WebSocketError("握手失败：连接被关闭")
            self._buf += chunk
        head, _, self._buf = self._buf.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        if " 101 " not in status and not status.endswith(" 101"):
            raise WebSocketError("握手失败：%s" % status[:120])

    # ------------------------------------------------------------ 发送
    def _send_frame(self, opcode, payload):
        mask = os.urandom(4)                       # 客户端→服务端必须掩码
        n = len(payload)
        header = bytearray([0x80 | opcode])        # FIN + opcode
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def send_text(self, text):
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    # ------------------------------------------------------------ 接收
    def _recv_exact(self, n):
        while len(self._buf) < n:
            chunk = self._sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise WebSocketError("连接被关闭")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def recv_message(self, timeout=None):
        """收一条完整消息：自动应答 ping、重组分片。

        返回 str（文本帧）或 bytes（二进制帧）。超时抛 socket.timeout，
        对端关闭抛 WebSocketError。"""
        if timeout is not None:
            self._sock.settimeout(timeout)
        elif self._timeout:
            self._sock.settimeout(self._timeout)
        opcode_msg = None
        payload = bytearray()
        while True:
            b1, b2 = self._recv_exact(2)
            fin, opcode, masked = b1 & 0x80, b1 & 0x0F, b2 & 0x80
            n = b2 & 0x7F
            if n == 126:
                (n,) = struct.unpack(">H", self._recv_exact(2))
            elif n == 127:
                (n,) = struct.unpack(">Q", self._recv_exact(8))
            data = self._recv_exact(n)
            if masked:
                mkey = self._recv_exact(4)
                data = bytes(b ^ mkey[i % 4] for i, b in enumerate(data))
            if opcode == OP_CLOSE:
                raise WebSocketError("对端发送关闭帧")
            if opcode == OP_PING:
                self._send_frame(OP_PONG, data)
                continue
            if opcode == OP_PONG:
                continue
            if opcode in (OP_TEXT, OP_BINARY):
                opcode_msg, payload = opcode, bytearray(data)
            elif opcode == OP_CONT:
                if opcode_msg is None:
                    raise WebSocketError("收到无起始帧的续帧")
                payload += data
            else:
                raise WebSocketError("未知 opcode：%d" % opcode)
            if fin:
                return bytes(payload) if opcode_msg == OP_BINARY else \
                    payload.decode("utf-8", "replace")

    def close(self):
        try:
            self._send_frame(OP_CLOSE, b"")
        except Exception:
            pass
        try:
            self._sock.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
