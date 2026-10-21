# -*- coding: utf-8 -*-
"""内置执行智能体的工具层：文件/命令/技能/网页/任务工具与协议工具清单。

从 builtin_agent 拆出（单文件行数治理），不改变对外行为：
  - 文件工具锁死工作目录内（路径逐段校验 + resolve 后边界确认）；
  - run_command 复用 runner.run_process 执行/杀树/解码；
  - exec_tool 统一走 action_policy 预检、审计与检查点；
  - TOOLS_SPEC / _openai_tools / _anthropic_tools 供三种协议复用。
"""
from __future__ import annotations

import base64
import fnmatch
import io
import mimetypes
import os
import re
import shutil
import stat
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, cast

from . import runner


READ_MAX_BYTES = 64 * 1024   # read_file 单次读取上限
READ_HEAD_BYTES = 44 * 1024  # TokenJuice 借鉴（openhuman）：超限文件头尾保留、
READ_TAIL_BYTES = 16 * 1024  # 中段省略——日志/代码的报错常在尾部，纯截头会丢关键信息
LIST_MAX_ENTRIES = 200
MAX_IMAGES = 8                 # 单次最多随消息传几张图（请求体防爆）
IMAGE_MAX_EDGE = 1568          # 长边上限（视觉模型通行建议值，超出等比缩）
IMAGE_JPEG_BYTES = 3500 * 1024   # 缩放后仍超此大小 → 转 JPEG q85
IMAGE_MAX_BYTES = 5 * 1024 * 1024   # 单张最终字节硬上限（超出剔除并日志）
CMD_DEFAULT_TIMEOUT = 600    # run_command 默认超时（与 CLI 停滞看门狗同口径）
CMD_MAX_TIMEOUT = 1800       # 上限：模型传 timeout_sec 超过即钳到这
CMD_OUT_HEAD = 24 * 1024     # 回灌给模型的输出：头 24K + 尾 8K（报错常在尾部）
CMD_OUT_TAIL = 8 * 1024

# ---------------------------------------------------------------- 工作目录工具
# 模型给的相对路径不可信：逐段拒绝 .. 与盘符，resolve() 归一后确认仍位于
# 工作目录之下（base not in p.parents 即越界，同 skills/market 的守卫惯用法）。

def _is_linklike(path):
    """Treat symlinks and Windows junctions alike when enumerating a sandbox root."""
    try:
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            return True
        # Path.is_junction() was added in Python 3.12. On supported older
        # Windows versions, detect all reparse points directly from lstat so
        # junctions cannot bypass a narrowed allowed_roots policy.
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return bool(attributes & reparse_point)
    except OSError:
        return True


def _sandbox_path_allowed(path, sandbox):
    """Check a path against a policy while canonicalizing macOS /var aliases.

    Some callers pass an inherited, already-shaped sandbox dictionary instead
    of the normalized policy object.  On macOS ``/var`` is a symlink to
    ``/private/var``; resolving both sides here keeps that alias from turning a
    valid child workdir into a false sandbox rejection.
    """
    if not isinstance(sandbox, dict):
        return False
    roots = []
    for value in sandbox.get("allowed_roots") or []:
        try:
            root = Path(value).expanduser().resolve()
        except (TypeError, OSError, ValueError):
            continue
        if str(root) not in roots:
            roots.append(str(root))
    if not roots:
        return False
    from . import policy
    checked = dict(sandbox)
    checked["allowed_roots"] = roots
    return policy.path_allowed(path, checked)


def _tool_list_files(workdir, args):
    rel = str(args.get("path") or "").replace("\\", "/").strip("/")
    if ".." in Path(rel).parts or any(":" in seg for seg in Path(rel).parts):
        return "（非法路径: %s）" % rel
    base = Path(workdir or ".").resolve()
    p = (base / rel).resolve() if rel else base
    if base not in p.parents and p != base:
        return "（路径越界: %s）" % rel
    if not p.is_dir():
        return "（目录不存在: %s）" % (rel or ".")
    out = []
    for root, dirs, files in os.walk(str(p)):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules")
                   and not _is_linklike(Path(root) / d)]
        for f in files:
            fp = os.path.join(root, f)
            if _is_linklike(Path(fp)):
                continue
            relp = os.path.relpath(fp, str(base)).replace(os.sep, "/")
            try:
                size = os.path.getsize(fp)
            except OSError:
                size = -1
            out.append("%s (%d B)" % (relp, size))
            if len(out) >= LIST_MAX_ENTRIES:
                return "\n".join(out) + "\n…（截断，共 200+ 项）"
    return "\n".join(out) or "（空目录）"


_BIN_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "PNG 图片"),
    (b"\xff\xd8\xff", "JPEG 图片"),
    (b"GIF8", "GIF 图片"),
    (b"%PDF-", "PDF 文档"),
    (b"PK\x03\x04", "ZIP 压缩包"),
    (b"\x1f\x8b", "GZIP 压缩包"),
    (b"7z\xbc\xaf\x27\x1c", "7z 压缩包"),
    (b"Rar!\x1a\x07", "RAR 压缩包"),
    (b"\x00\x00\x01\x00", "ICO 图标"),
    (b"OggS", "OGG 音频"),
    (b"ID3", "MP3 音频"),
)


def _bin_format(head):
    """常见二进制魔数 → 人类可读格式名（写进给模型的提示，让它立刻止损）。"""
    if len(head) >= 12 and head[:4] == b"RIFF":
        return {"WEBP": "WEBP 图片", "WAVE": "WAV 音频",
                "AVI ": "AVI 视频"}.get(head[8:12].decode("ascii", "replace"),
                                        "RIFF 媒体")
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "MP4/MOV 视频"
    for magic, name in _BIN_MAGIC:
        if head.startswith(magic):
            return name
    return "二进制文件"


def _looks_binary(head):
    """文本判定从宽：NUL 字节即判二进制（git 同款启发式），控制字符占比兜底
    （部分二进制头 8KB 内无 NUL）。"""
    if not head:
        return False
    if b"\x00" in head:
        return True
    sample = head[:8192]
    ctrl = sum(1 for b in sample if b < 32 and b not in (9, 10, 12, 13))
    return ctrl * 20 > len(sample)   # >5% 视为二进制


def _trim_partial_utf8(chunk):
    """头部截断可能把多字节字符切成两半：剥掉尾部不完整序列，否则 UTF-8
    strict 失败会误落 GBK 解出错字（runner.tail_decoded 的头截断对偶）。"""
    i = len(chunk) - 1
    while i >= 0 and i >= len(chunk) - 3 and (chunk[i] & 0xC0) == 0x80:
        i -= 1
    if 0 <= i < len(chunk):
        b = chunk[i]
        need = 4 if (b & 0xF8) == 0xF0 else 3 if (b & 0xF0) == 0xE0 \
            else 2 if (b & 0xC0) == 0xC0 else 1
        if len(chunk) - i < need:
            return chunk[:i]
    return chunk


def _guess_image_mime(path, raw=b""):
    m = mimetypes.guess_type(str(path))[0]
    if m:
        return m
    if raw.startswith(b"\x89PNG"):
        return "image/png"
    if raw.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if raw.startswith(b"GIF8"):
        return "image/gif"
    if raw.startswith(b"RIFF"):
        return "image/webp"
    return "image/png"


def _prep_images(paths, log=None):
    """图片路径 → [(mime, b64)]。Pillow 可用时：长边超 1568px 等比缩（万级像素
    原图既烧 token 又常被网关拒），仍超 3.5MB 转 JPEG q85；Pillow 缺失或解码
    失败时原图直传，单张超 5MB 剔除并日志。最多 MAX_IMAGES 张。"""
    try:
        from PIL import Image
    except Exception:
        Image = None
    out = []
    for p in (paths or [])[:MAX_IMAGES]:
        try:
            raw = Path(p).read_bytes()
        except OSError:
            if log:
                log("[图片] 跳过（读取失败）: %s" % os.path.basename(str(p)))
            continue
        mime = _guess_image_mime(p, raw)
        data = raw
        if Image is not None:
            try:
                im = Image.open(io.BytesIO(raw))
                im.load()
                w, h = im.size
                edge = max(w, h, 1)
                if edge > IMAGE_MAX_EDGE:
                    r = IMAGE_MAX_EDGE / float(edge)
                    im = im.resize((max(1, round(w * r)), max(1, round(h * r))),
                                   getattr(Image, "LANCZOS", 1))
                fmt = {"image/png": "PNG", "image/jpeg": "JPEG",
                       "image/gif": "GIF", "image/webp": "WEBP"}.get(mime, "PNG")
                buf = io.BytesIO()
                im.save(buf, format=fmt)   # GIF 多帧仅存首帧，可接受
                data = buf.getvalue()
            except Exception:
                data = raw                 # 解码/编码失败退回原始字节
        if (Image is not None and mime != "image/jpeg"
                and len(data) > IMAGE_JPEG_BYTES):
            try:                           # 仍过大：转 JPEG q85（RGB 拍平 alpha）
                im = Image.open(io.BytesIO(data)).convert("RGB")
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=85)
                data = buf.getvalue()
                mime = "image/jpeg"
            except Exception:
                pass
        if len(data) > IMAGE_MAX_BYTES:
            if log:
                log("[图片] 剔除（过大 %d MB）: %s"
                    % (len(data) // (1024 * 1024), os.path.basename(str(p))))
            continue
        out.append((mime, base64.b64encode(data).decode("ascii")))
    return out


def _tool_read_file(workdir, args):
    rel = str(args.get("path") or "").replace("\\", "/").strip("/")
    if not rel or ".." in Path(rel).parts or any(":" in seg for seg in Path(rel).parts):
        return "（非法路径: %s）" % rel
    base = Path(workdir or ".").resolve()
    p = (base / rel).resolve()
    if base not in p.parents:
        return "（路径越界: %s）" % rel
    if not p.is_file():
        return "（文件不存在: %s）" % rel
    # 分段读取（P0 基础件）：offset/lines 任一非空即按行号范围读——超 64KB
    # 被「头尾保留、中段省略」截断的大文件靠它读全（用户实测 _lessons_dump.txt
    # 68KB 被截案）。行号 1-based；逐行切片后不强行拼回整份。
    offset_raw = str(args.get("offset") or "").strip()
    lines_raw = str(args.get("lines") or "").strip()
    if offset_raw or lines_raw:
        try:
            size = p.stat().st_size
        except OSError as e:
            return "（读取失败: %s）" % e
        if size > READ_EDIT_MAX_BYTES:
            return ("（文件过大 %d MB，分段读取上限 %d MB；请用 run_command 处理）"
                    % (size // (1024 * 1024), READ_EDIT_MAX_BYTES // (1024 * 1024)))
        try:
            offset = max(1, int(float(offset_raw or 1)))
            lines = max(1, min(READ_SEGMENT_MAX_LINES,
                               int(float(lines_raw or READ_SEGMENT_DEFAULT_LINES))))
        except (TypeError, ValueError):
            return "（offset/lines 须为整数行号）"
        all_lines = runner.decode_output(p.read_bytes()).splitlines()
        total = len(all_lines)
        if total == 0:
            return "（空文件）"
        if offset > total:
            return "（offset %d 超出文件总行数 %d）" % (offset, total)
        seg = all_lines[offset - 1:offset - 1 + lines]
        head = "第 %d–%d 行（共 %d 行；用 offset=%d 续读下一段）" % (
            offset, offset - 1 + len(seg), total, offset + len(seg))
        return head + "\n" + ("\n".join(seg) or "（该范围无内容）")
    data = p.read_bytes()[:READ_MAX_BYTES + 1]
    truncated = len(data) > READ_MAX_BYTES
    if truncated:
        # 头尾保留、中段省略（TokenJuice 借鉴）：头 44k 给结构/开头上下文，
        # 尾 16k 给报错/结论（日志与代码的关键信息常在末尾），中段标注省略量
        try:
            size = p.stat().st_size
        except OSError:
            size = len(data)
        with open(p, "rb") as fh:
            head = fh.read(READ_HEAD_BYTES)
            fh.seek(max(0, size - READ_TAIL_BYTES))
            tail = fh.read(READ_TAIL_BYTES)
        head = _trim_partial_utf8(head)
        # 尾段开头的残缺多字节序列剥头（对偶于 _trim_partial_utf8 剥尾）
        while tail:
            try:
                tail.decode("utf-8")
                break
            except UnicodeDecodeError:
                tail = tail[1:]
        text = runner.decode_output(head) \
            + "\n…（中段省略 %d 字节；头 %dKB + 尾 %dKB 保留）\n" % (
                max(0, size - READ_HEAD_BYTES - READ_TAIL_BYTES),
                READ_HEAD_BYTES // 1024, READ_TAIL_BYTES // 1024) \
            + runner.decode_output(tail)
        return "…（文件超 64KB，已头尾保留读取）\n" + text
    head = data
    if not head:
        return "（空文件）"
    if _looks_binary(head):
        try:
            size = p.stat().st_size
        except OSError:
            size = len(head)
        fmt = _bin_format(head)
        if "图片" in fmt:
            return ("（图片文件: %s — %s，%d B，无法按文本读取。用户以附件发来的图片会"
                    "直接出现在对话中，无需用工具读取；若需要目录里这张图的内容，"
                    "请提示用户把它作为附件发送。）" % (rel, fmt, size))
        return ("（二进制文件，无法按文本读取: %s — %s，%d B。read_file 只支持文本文件；"
                "请如实告知用户该文件内容无法以文本方式查看，不要反复重读。）"
                % (rel, fmt, size))
    text = runner.decode_output(head)
    return text


def _tool_write_file(workdir, args, sandbox=None):
    rel = str(args.get("path") or "").replace("\\", "/").strip("/")
    if not rel or ".." in Path(rel).parts or any(":" in seg for seg in Path(rel).parts):
        return "（非法路径: %s）" % rel
    content = str(args.get("content") if args.get("content") is not None else "")
    base = Path(workdir or ".").resolve()
    dest = (base / rel).resolve()
    if base not in dest.parents:
        return "（路径越界: %s）" % rel
    # Keep the legacy path checks above, then use the shared boundary for an
    # atomic replacement. This prevents a crashed model write from leaving a
    # truncated file while preserving the existing newline behavior.
    try:
        from .workspace_boundary import WorkspaceBoundary
        WorkspaceBoundary(
            workdir,
            format_locks=(sandbox or {}).get("format_locks") if isinstance(sandbox, dict) else None,
            internal_dir=(sandbox or {}).get("internal_dir", ".codebee")
        ).write(rel, content)
    except Exception as exc:
        return "（写入失败: %s）" % str(exc)[:240]
    return "已写入 %s（%d 字符，UTF-8）" % (rel, len(content))


def _tool_run_command(workdir, args, cancel_event=None, deadline=None, sandbox=None):
    """本机执行一条 shell 命令，返回 (退出码, 合并输出)。

    薄适配层：执行/杀树/取消/解码全部复用 runner.run_process（shell_cmd 分支
    → cmd /c 或 /bin/sh -c），仓库唯一的 shell 落点，不另起炉灶。全信任档
    （用户拍板 2026-09-22）：无白名单无确认闸，与 codex/claude 全权沙箱同档。
    提权靠模型自己包 Start-Process -Verb RunAs——UAC 必须人点，绕不过也不该绕。
    输出按字符头尾截断（解码已在 run_process 完成，无残缺多字节问题）。
    沙箱闸策略感知（与外部 CLI 闸 b267107 同口径）：Linux 有 bwrap 全策略
    强制；Win/Mac 无 OS 后端，进程内无法兑现的收窄仍拒（禁网、env 白名单
    ——run_process 的 env 是注入语义滤不掉），默认宽策略保持全信任档直跑，
    目录闸由 path_allowed 兑现。"""
    cmdline = str(args.get("command") or "").strip()
    if not cmdline:
        return "（命令为空，未执行）"
    from . import policy
    sandbox = sandbox or policy.normalize_sandbox({}, workdir)
    timeout = _command_timeout(args, sandbox)
    cwd = os.path.abspath(workdir or ".")
    if not _sandbox_path_allowed(cwd, sandbox):
        return "（沙箱拒绝：工作目录不在允许范围内，命令未执行）"
    # Enforce the declared boundary at the process boundary where an OS
    # backend exists (bubblewrap on Linux). Windows/macOS have no equivalent:
    # stay policy-aware like the external-CLI gate (b267107) — the narrowings
    # this in-process runner cannot honor fail closed, while a default wide
    # policy keeps the user-approved full-trust tier (2026-09-22) instead of
    # refusing every command.
    from .sandbox_runtime import native_sandbox_available
    has_bwrap = native_sandbox_available()
    if sandbox.get("network") is False and not has_bwrap and sandbox.get("backend") != "docker":
        return "（沙箱拒绝：当前系统没有可用的网络隔离后端，命令未执行）"
    if sandbox.get("env_allowlist") and sandbox.get("backend") != "docker":
        # run_process 的 env 是注入语义（合并进完整父环境，供凭据注入），
        # 不是白名单替换——环境收窄在子进程侧无法兑现，宁拒不假装
        return "（沙箱拒绝：该任务带环境变量白名单，命令执行无法兑现该策略，未执行）"
    if sandbox.get("backend") == "docker":
        try:
            from .sandbox_runtime import docker_argv, docker_env
            argv = docker_argv(cmdline, sandbox=sandbox, workdir=cwd)
            launch_env = docker_env(sandbox)
        except Exception as exc:
            return "（沙箱拒绝：%s，命令未执行）" % str(exc)[:240]
    elif has_bwrap:
        argv = ["bwrap", "--die-with-parent", "--new-session", "--tmpfs", "/"]
        # Mount only the runtime needed by ordinary commands. Do not expose the
        # entire host root read-only: read access can still leak user secrets.
        for system_path in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc"):
            if os.path.exists(system_path):
                argv.extend(["--dir", system_path, "--ro-bind", system_path, system_path])
        for volatile_path in ("/proc", "/dev"):
            argv.extend(["--dir", volatile_path])
        argv.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"])
        if sandbox.get("network") is False:
            argv.append("--unshare-net")
        for allowed_root in sandbox.get("allowed_roots") or []:
            root_path = str(Path(allowed_root).resolve())
            parents = list(reversed(Path(root_path).parents))
            for parent in parents:
                if str(parent) != "/":
                    argv.extend(["--dir", str(parent)])
            argv.extend(["--dir", root_path])
            argv.extend(["--bind", root_path, root_path])
        argv.extend(["--chdir", cwd, "--", "/bin/sh", "-lc", cmdline])
        launch_env = None
    else:
        argv = None   # 全信任档：runner 按平台自包 cmd /c 或 /bin/sh -c
        launch_env = None
    r = runner.run_process(argv=argv, shell_cmd=cmdline if argv is None else None,
                           cwd=cwd, env=launch_env,
                           timeout=min(timeout, CMD_MAX_TIMEOUT),
                           deadline=deadline, cancel_event=cancel_event)
    out = runner.clean_cli_text(
        (r.get("stdout") or "") + (("\n" + r["stderr"]) if r.get("stderr") else ""))
    limit = min(CMD_OUT_HEAD + CMD_OUT_TAIL,
                max(1024, int(sandbox.get("max_output_bytes") or CMD_OUT_HEAD + CMD_OUT_TAIL)))
    if not out.strip():
        body = "（无输出）"
    elif len(out) <= limit:
        body = out
    else:
        body = (out[:CMD_OUT_HEAD]
                + "\n…（中段省略 %d 字符；头 %dK + 尾 %dK 保留）\n" % (
                    len(out) - limit, CMD_OUT_HEAD // 1024, CMD_OUT_TAIL // 1024)
                + out[-CMD_OUT_TAIL:])
    if r.get("cancelled"):
        note = "已取消，进程树已终止"
    elif r.get("timed_out"):
        note = "超时 %d 秒，进程树已强制终止" % timeout
    else:
        note = ""
    prefix = ("（%s）\n" % note) if note else ""
    code = r.get("exit_code")
    return ("%s退出码: %s\n%s\n（注意：shell 命令副作用不纳入文件检查点，"
            "不能保证可回滚）") % (prefix, "未知" if code is None else code, body)


def _command_timeout(args, sandbox):
    """Resolve the shell timeout without exceeding the task's sandbox budget."""
    try:
        requested = int(float((args or {}).get("timeout_sec") or CMD_DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        requested = CMD_DEFAULT_TIMEOUT
    try:
        policy_limit = int(float((sandbox or {}).get("timeout_s") or CMD_MAX_TIMEOUT))
    except (TypeError, ValueError):
        policy_limit = CMD_MAX_TIMEOUT
    return max(1, min(requested, policy_limit, CMD_MAX_TIMEOUT))


# ---------------------------------------------------------------- P0 基础件扩展
# 2026-09-25 用户实测反馈：改大文件只能整份读写、删/移/改名只能绕 shell、
# 大文件 64KB 中段被截、找内容只能逐文件读——补齐四个基础件（局部编辑、
# 文件管理、分段读取、全文搜索），全部锁死工作目录内。

READ_EDIT_MAX_BYTES = 16 * 1024 * 1024   # edit/append 分段读全文解码上限
SEARCH_MAX_FILE_BYTES = 2 * 1024 * 1024  # search_content 单文件参与搜索上限
SEARCH_DEFAULT_RESULTS = 50              # 命中行默认上限
READ_SEGMENT_DEFAULT_LINES = 200         # read_file 分段默认行数
READ_SEGMENT_MAX_LINES = 2000            # read_file 分段行数上限

_WALK_PRUNE = {".git", "__pycache__", "node_modules", ".venv", "venv",
               "dist", "build", "target", ".idea", ".vscode", "__MACOSX",
               ".pytest_cache", ".mypy_cache"}


def _guard_rel(workdir, rel, allow_root=False):
    """工作目录相对路径守卫（list/read/write 同款惯用法抽公共）。

    返回 (resolve 后的 Path, 错误串)；合法时错误串为 None。空 rel 仅在
    allow_root=True 时代表工作目录根。"""
    rel = str(rel or "").replace("\\", "/").strip("/")
    if (not rel and not allow_root) or ".." in Path(rel).parts \
            or any(":" in seg for seg in Path(rel).parts):
        return None, "（非法路径: %s）" % rel
    base = Path(workdir or ".").resolve()
    p = (base / rel).resolve() if rel else base
    if base not in p.parents and p != base:
        return None, "（路径越界: %s）" % rel
    return p, None


def _read_text_capped(p, limit=READ_EDIT_MAX_BYTES):
    """读文本并识别编码（bytes 上限防巨型文件）。返回 (text, err)。"""
    try:
        size = p.stat().st_size
    except OSError as e:
        return "", "（读取失败: %s）" % e
    if size > limit:
        return "", ("（文件过大 %d MB，超过 %d MB 上限；请用 run_command 处理）"
                    % (size // (1024 * 1024), limit // (1024 * 1024)))
    data = p.read_bytes()
    if _looks_binary(data[:8192]):
        return "", "（二进制文件，%s 不适用: %s）" % ("文本编辑", _bin_format(data[:64]))
    return runner.decode_output(data), None


def _tool_edit_file(workdir, args):
    """精确查找替换：old_text 逐字符匹配，默认仅允许 1 处命中。"""
    p, err = _guard_rel(workdir, args.get("path"))
    if err:
        return err
    p = cast(Path, p)
    old_text = str(args.get("old_text") or "")
    new_text = str(args.get("new_text") if args.get("new_text") is not None else "")
    if not old_text:
        return "（old_text 不能为空；改全文请用 write_file 整份写入）"
    if not p.is_file():
        return "（文件不存在: %s）" % p.name
    text, err = _read_text_capped(p)
    if err:
        return err
    count = text.count(old_text)
    crlf = False
    if count == 0 and "\r\n" in text and "\r\n" not in old_text:
        # Windows 文件 CRLF：按 LF 形态重试匹配，命中则在原文上以 CRLF 替换，
        # 保持文件既有换行风格（否则整个文件换行符被翻转）
        crlf_text = text.replace("\r\n", "\n")
        count = crlf_text.count(old_text)
        crlf = count > 0
    if count == 0:
        return ("（old_text 在文件中找不到逐字符匹配——read_file 超过 64KB 会"
                "中段省略，请用 offset/lines 分段读全后重试）")
    replace_all = str(args.get("replace_all") or "").strip().lower() in ("1", "true", "yes")
    if count > 1 and not replace_all:
        return ("（old_text 命中 %d 处；请补充更多上下文使命中唯一，"
                "或传 replace_all=true 全部替换）" % count)
    if crlf:
        old_text = old_text.replace("\n", "\r\n")
        new_text = new_text.replace("\n", "\r\n")
    # newline="" 禁止 Python 的 \n→os.linesep 翻译：文件既有 CRLF 时会被
    # 二次翻译成 \r\r\n（Windows 实测损坏案），写什么落什么
    with p.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text.replace(old_text, new_text))
    return "已替换 %d 处（%s）" % (count if replace_all else 1, p.name)


def _tool_append_file(workdir, args):
    """文件末尾追加（bytes 级追加，大文件不整份重写）；不存在则创建。"""
    p, err = _guard_rel(workdir, args.get("path"))
    if err:
        return err
    p = cast(Path, p)
    content = str(args.get("content") if args.get("content") is not None else "")
    if not content:
        return "（content 不能为空）"
    if p.is_file():
        with open(p, "rb") as fh:
            head = fh.read(8192)
        if _looks_binary(head):
            return "（二进制文件，不能按文本追加: %s — %s）" % (p.name, _bin_format(head))
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "ab") as fh:
        fh.write(content.encode("utf-8"))
    return "已追加 %d 字符到 %s（UTF-8）" % (len(content), p.name)


def _tool_fs_manage(workdir, args):
    """删除 / 移动 / 重命名 / 复制（工作目录内；目标已存在一律拒绝防覆盖）。"""
    action = str(args.get("action") or "").strip().lower()
    if action not in ("delete", "move", "rename", "copy"):
        return "（action 须为 delete/move/rename/copy）"
    p, err = _guard_rel(workdir, args.get("path"))
    if err:
        return err
    p = cast(Path, p)
    if not p.exists() and not p.is_symlink():
        return "（不存在: %s）" % p
    if action == "delete":
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(str(p))
            return "已删除目录（含全部内容）: %s" % p.name
        p.unlink()
        return "已删除: %s" % p.name
    dest, err = _guard_rel(workdir, args.get("dest"))
    if err:
        return err.replace("非法路径", "dest 非法路径").replace("路径越界", "dest 路径越界")
    dest = cast(Path, dest)
    if dest.exists() or dest.is_symlink():
        return "（目标已存在，拒绝覆盖: %s；先删除或换名）" % dest.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if action == "copy":
        if p.is_dir():
            shutil.copytree(str(p), str(dest))
        else:
            shutil.copy2(str(p), str(dest))
        return "已复制 %s → %s" % (p.name, dest.name)
    shutil.move(str(p), str(dest))
    return "已%s %s → %s" % ("移动" if action == "move" else "重命名",
                             p.name, dest.name)


def _tool_search_content(workdir, args):
    """grep 式内容搜索：正则（编译失败回退字面量），逐行命中带行号。"""
    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        return "（pattern 不能为空）"
    try:
        rx = re.compile(pattern)
    except re.error:
        rx = re.compile(re.escape(pattern))
        pattern_note = "（正则无效，已按字面量搜索）\n"
    else:
        pattern_note = ""
    sub, err = _guard_rel(workdir, args.get("path"), allow_root=True)
    if err:
        return err
    sub = cast(Path, sub)
    if not sub.is_dir():
        return "（目录不存在: %s）" % args.get("path")
    glob_pat = str(args.get("glob") or "").strip()
    try:
        max_results = max(1, min(200, int(float(args.get("max_results") or 0)
                                        or SEARCH_DEFAULT_RESULTS)))
    except (TypeError, ValueError):
        max_results = SEARCH_DEFAULT_RESULTS
    base = Path(workdir or ".").resolve()
    out, hits, scanned = [], 0, 0
    for root, dirs, files in os.walk(str(sub)):
        dirs[:] = [d for d in dirs if d not in _WALK_PRUNE
                   and not _is_linklike(Path(root) / d)]
        for f in files:
            if glob_pat:
                relp = os.path.relpath(os.path.join(root, f), str(base)).replace(os.sep, "/")
                if not (fnmatch.fnmatch(f, glob_pat) or fnmatch.fnmatch(relp, glob_pat)):
                    continue
            fp = os.path.join(root, f)
            try:
                if _is_linklike(Path(fp)):
                    continue
                if os.path.getsize(fp) > SEARCH_MAX_FILE_BYTES:
                    continue
                with open(fp, "rb") as fh:
                    head = fh.read(8192)
                if _looks_binary(head):
                    continue
                with open(fp, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            scanned += 1
            rel = os.path.relpath(fp, str(base)).replace(os.sep, "/")
            for i, line in enumerate(runner.decode_output(data).splitlines(), 1):
                if rx.search(line):
                    out.append("%s:%d: %s" % (rel, i, line.strip()[:200]))
                    hits += 1
                    if hits >= max_results:
                        out.append("…（已达 %d 行上限，缩小 pattern/glob 或调大 max_results）"
                                   % max_results)
                        return pattern_note + "\n".join(out)
    if not out:
        return pattern_note + ("（无命中；已扫描 %d 个文本文件）" % scanned)
    return pattern_note + "\n".join(out) + "\n（共 %d 行命中 / 扫描 %d 个文件）" % (hits, scanned)


def _tool_find_files(workdir, args):
    """文件名通配查找（fnmatch；pattern 不含 / 时匹配任意深度的文件名）。"""
    pattern = str(args.get("pattern") or "").strip().replace("\\", "/")
    if not pattern or ".." in pattern or ":" in pattern:
        return "（非法 pattern: %s；示例 *.py 或 data/*.json）" % pattern
    base = Path(workdir or ".").resolve()
    sub, err = _guard_rel(workdir, args.get("path"), allow_root=True)
    if err:
        return err
    sub = cast(Path, sub)
    if not sub.is_dir():
        return "（目录不存在: %s）" % (args.get("path") or ".")
    match_name = "/" not in pattern
    out = []
    for root, dirs, files in os.walk(str(sub)):
        dirs[:] = [d for d in dirs if d not in _WALK_PRUNE
                   and not _is_linklike(Path(root) / d)]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), str(base)).replace(os.sep, "/")
            if not (fnmatch.fnmatch(rel, pattern)
                    or (match_name and fnmatch.fnmatch(f, pattern))):
                continue
            if _is_linklike(Path(root) / f):
                continue
            try:
                size = os.path.getsize(os.path.join(root, f))
            except OSError:
                size = -1
            out.append("%s (%d B)" % (rel, size))
            if len(out) >= LIST_MAX_ENTRIES:
                return "\n".join(out) + "\n…（截断，共 %d+ 项）" % LIST_MAX_ENTRIES
    return "\n".join(out) or "（无匹配文件）"


def _available_skill_rows():
    from . import plugins
    return [r for r in plugins.capabilities_view().get("skills") or []
            if r.get("enabled") and r.get("installed") and r.get("available") is not False
            and r.get("state") not in ("broken", "blocked")
            and (r.get("compatibility") or {}).get("compatible") is not False]


def _tool_list_skills():
    enabled = _available_skill_rows()
    return "\n".join("%s · %s" % (r.get("id"), r.get("description") or r.get("display_name") or "")
                     for r in enabled[:200]) or "（没有已启用的技能）"


def _tool_use_skill(args):
    name = str((args or {}).get("name") or "").strip()
    if not name:
        return "（请指定技能名称或 ID；可先调用 list_skills）"
    rows = _available_skill_rows()
    matches = [r for r in rows if r.get("id") == name]
    if not matches:
        matches = [r for r in rows if str(r.get("name") or "").lower() == name.lower()]
    if len(matches) > 1:
        return "（技能名称有歧义，请用 ID：%s）" % ", ".join(r["id"] for r in matches)
    if not matches:
        return "（未找到已启用技能：%s）" % name[:120]
    skill = matches[0]
    source = skill.get("source")
    if source:
        try:
            offset = int((args or {}).get("offset") or 0)
        except (TypeError, ValueError):
            return "（offset 必须是非负整数）"
        if offset < 0 or offset > 200000:
            return "（offset 超出允许范围）"
        try:
            with Path(source).open(encoding="utf-8", errors="replace") as handle:
                raw = handle.read(offset + 20001)
        except OSError:
            return "（技能内容不可读）"
        body = "技能文件：%s\n相对引用以此文件目录为基准。\n%s" % (
            source, raw[offset:offset + 20000])
        if len(raw) > offset + 20000:
            body += "\n…[后续内容请继续 use_skill，offset=%s]…" % (offset + 20000)
    else:
        body = str(skill.get("prompt") or "")[:20000]
    return body or "（技能内容不可读）"


def _tool_web_search(args, sandbox=None, deadline=None):
    if sandbox and sandbox.get("network") is False:
        return "（沙箱禁止联网，网页搜索未执行）"
    query = str((args or {}).get("query") or "").strip()[:300]
    if not query:
        return "（query 不能为空）"
    if deadline is not None and deadline <= time.monotonic():
        return "（任务总时限已到，网页搜索未执行）"
    url = "https://www.bing.com/search?format=rss&q=" + urllib.parse.quote(query)
    timeout = min(10.0, max(0.1, deadline - time.monotonic())) if deadline else 10.0
    req = urllib.request.Request(url, headers={"User-Agent": "CodeBee/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = response.read(256 * 1024)
        root = ET.fromstring(data)
        items = root.findall("./channel/item")[:8]
    except Exception as exc:
        return "（网页搜索失败：%s）" % str(exc)[:200]
    if not items:
        return "（未找到网页结果）"
    return "\n\n".join("%s\n%s\n%s" % (
        (item.findtext("title") or "").strip()[:200],
        (item.findtext("link") or "").strip()[:500],
        (item.findtext("description") or "").strip()[:700]) for item in items)


def _tool_read_task_output(workdir, args, sandbox=None):
    from . import store
    task_id = str((args or {}).get("task_id") or "").strip()
    task = store.get_task(task_id)
    if not task:
        return "（任务不存在）"
    root = Path(workdir or ".").resolve()
    task_dir = Path(task.get("workdir") or ".").resolve()
    if task_dir != root and root not in task_dir.parents:
        return "（任务工作目录不在当前工作目录内，拒绝读取）"
    if sandbox and not _sandbox_path_allowed(task_dir, sandbox):
        return "（沙箱拒绝：任务工作目录超出允许范围）"
    runs = store.task_runs(task_id)
    if not runs:
        return "任务状态：%s；暂无运行输出" % task.get("status", "")
    run = runs[0]
    lines = ["任务：%s", "状态：%s", "运行：%s"]
    out = [lines[0] % (task.get("title") or task_id), lines[1] % (run.get("status") or ""),
           lines[2] % run.get("id")]
    for step in (run.get("steps") or [])[-10:]:
        body = step.get("output") or step.get("summary") or ""
        out.append("步骤 %s（%s）：%s" % (step.get("n"), step.get("status"), str(body)[:2000]))
    if run.get("error"):
        out.append("错误：%s" % str(run["error"])[:1000])
    return "\n".join(out)[:20000]


TOOLS_SPEC = [
    {"name": "list_skills", "description": "列出当前已启用的技能及其 ID，不加载技能正文", "args": {}},
    {"name": "use_skill", "description": "按名称或 ID 加载已启用的技能指令；长文可用 offset 继续读取", "args": {"name": "技能名称或 ID", "?offset": "下一段起始位置"}},
    {"name": "web_search", "description": "搜索公开网页，返回标题、网址和摘要；仅用于需要外部最新信息的任务", "args": {"query": "搜索关键词"}},
    {"name": "read_task_output", "description": "读取当前工作目录内另一项 CodeBee 任务的最近运行状态和步骤输出", "args": {"task_id": "任务 ID"}},
    {"name": "list_files", "description": "列出工作目录（或其子目录）下的文件",
     "args": {"path": "子目录相对路径，留空=根目录"}},
    {"name": "read_file", "description": "读取工作目录内一个文本文件（UTF-8/GBK 自动识别；默认超 64KB 时保留开头与结尾、中段省略，此时用 offset/lines 分段读全；图片等二进制文件无法读取）",
     "args": {"path": "文件相对路径",
              "?offset": "可选，起始行号（1-based），配合 lines 分段读取大文件",
              "?lines": "可选，读取行数（默认 200，最大 2000）"}},
    {"name": "edit_file", "description": "对文件做精确查找替换（局部编辑，不必整份重写）：old_text 必须与文件内容逐字符一致，默认要求命中唯一，配合 read_file 使用",
     "args": {"path": "文件相对路径",
              "old_text": "要被替换的原文（逐字符精确匹配）",
              "new_text": "替换后的新文本（可为空串=删除）",
              "?replace_all": "可选，true=替换全部命中（默认仅允许 1 处命中）"}},
    {"name": "append_file", "description": "把文本追加到文件末尾（bytes 级追加，大文件不必整份重写）；文件不存在则创建。追加日志、逐条记录时优先用它",
     "args": {"path": "文件相对路径", "content": "追加到末尾的文本"}},
    {"name": "fs_manage", "description": "文件管理：删除 / 移动 / 重命名 / 复制（工作目录内；目标已存在一律拒绝覆盖，删目录会连内容一起删）",
     "args": {"action": "delete | move | rename | copy",
              "path": "源相对路径（目录或文件）",
              "?dest": "move/rename/copy 的目标相对路径"}},
    {"name": "search_content", "description": "全文内容搜索（grep 式）：在工作目录内逐文件逐行匹配正则，命中行带「路径:行号:」前缀返回；自动跳过二进制与依赖目录（node_modules/.git 等）",
     "args": {"pattern": "搜索文本或正则表达式",
              "?path": "可选，限定搜索的子目录，默认根目录",
              "?glob": "可选，文件名过滤，如 *.py",
              "?max_results": "可选，命中行数上限（默认 50，最大 200）"}},
    {"name": "find_files", "description": "按文件名通配查找文件（如 *.py、data/*.json、report*.md）；pattern 不含 / 时匹配任意深度的文件名",
     "args": {"pattern": "文件名通配模式",
              "?path": "可选，限定搜索的子目录，默认根目录"}},
    {"name": "read_tool_output", "description": "按 ref 重新读取本次运行中卸载到本机的较早工具输出；大结果可用 offset 分段读取",
     "args": {"ref": "压缩提示提供的工具结果引用",
              "?offset": "可选，字节偏移，默认 0",
              "?limit": "可选，返回字符数，最大 20000"}},
    {"name": "write_file", "description": "把文本内容写入工作目录内一个文件（UTF-8，整份覆盖，父目录自动创建；只改一小段请用 edit_file）",
     "args": {"path": "文件相对路径", "content": "完整文本内容"}},
    {"name": "run_command",
     "description": "在本机执行一条 shell 命令，返回退出码与合并输出。诊断、修复、改配置、重启服务等操作真实生效；先跑命令看结果再定下一步。默认超时 600 秒，可用 timeout_sec 调整（5~1800）。Windows 需要管理员权限的命令（flushdns/防火墙/系统服务等）用 powershell -Command \"Start-Process <程序> -ArgumentList '<参数>' -Verb RunAs -Wait\" 触发 UAC（用户点允许后提权执行；提权进程输出拿不到，事后用普通命令复核）。避免长驻/交互式命令（ping -t、top、按键应答的安装器）。",
     "args": {"command": "完整命令行（shell 语法，本机直接执行）",
              "timeout_sec": "可选，超时秒数"}},
]

_TOOL_IMPL = {"list_files": _tool_list_files, "read_file": _tool_read_file,
              "write_file": _tool_write_file, "run_command": _tool_run_command,
              "edit_file": _tool_edit_file, "append_file": _tool_append_file,
              "fs_manage": _tool_fs_manage, "search_content": _tool_search_content,
              "find_files": _tool_find_files}


CREATE_TASK_SPEC = {
    "name": "create_task",
    "description": "发起一个独立的 CodeBee 任务。用户要求开始另一类工作（例如创建连载小说、写代码或生成文档）时使用；任务会出现在任务列表并异步运行，不要把它当作当前对话里的普通回答。",
    "args": {
        "type": "任务类型 ID，例如 serial_novel、novel、code、doc、article",
        "goal": "新任务的明确目标",
        "?title": "新任务标题",
        "?workdir": "绝对工作目录；留空沿用当前工作目录",
        "?context": "补充背景或约束",
        "?chapters": "serial_novel 首批章节数",
        "?words_per_chapter": "serial_novel 每章目标字数",
        "?start_chapter": "续写起始章节号",
        "?variants": "连载同章候选稿数量（1-3）",
        "?branches": "连载剧情分支数量（1-3）",
        "?story_bible": "连载小说初始故事圣经",
    },
}


def _tool_create_task(workdir, args, task_creator=None, sandbox=None):
    """Create a separate task through the caller-owned task boundary."""
    if task_creator is None:
        return "（当前对话不支持发起新任务）"
    task_type = str(args.get("type") or "").strip().lower()
    goal = str(args.get("goal") or "").strip()
    if not task_type:
        return "（type 不能为空；可用类型请使用 serial_novel、novel、code、doc 等）"
    if not goal:
        return "（goal 不能为空）"
    requested_workdir = str(args.get("workdir") or workdir or "").strip()
    if sandbox and requested_workdir:
        if not _sandbox_path_allowed(requested_workdir, sandbox):
            return "（沙箱拒绝：子任务工作目录超出允许范围，未创建）"
    child_sandbox = dict(sandbox) if isinstance(sandbox, dict) else None
    if child_sandbox and requested_workdir:
        parent_root = Path(workdir or ".").expanduser().resolve()
        child_root = Path(requested_workdir).expanduser().resolve()
        if child_root != parent_root:
            # The requested child directory is already checked against the
            # parent's roots above. Rebase the inherited allowlist to that
            # directory so task normalization cannot widen it back to a parent.
            child_sandbox["allowed_roots"] = [str(child_root)]
    payload: dict[str, Any] = {
        "type": task_type,
        "goal": goal,
        "title": str(args.get("title") or "").strip(),
        "workdir": requested_workdir,
        "context": str(args.get("context") or "").strip(),
    }
    if child_sandbox is not None:
        payload["sandbox"] = child_sandbox
    if task_type == "serial_novel":
        serial = {}
        for key in ("chapters", "words_per_chapter", "start_chapter", "variants", "branches"):
            if args.get(key) not in (None, ""):
                serial[key] = args.get(key)
        if serial:
            payload["serial"] = serial
        if args.get("story_bible") not in (None, ""):
            payload["story_bible"] = str(args.get("story_bible") or "").strip()
    try:
        result = task_creator(payload)
    except Exception as exc:
        return "（发起任务失败：%s）" % str(exc)[:300]
    if not isinstance(result, dict) or not result.get("task_id"):
        return "（发起任务失败：未返回任务编号）"
    return "已发起新任务：%s（运行编号：%s，类型：%s）" % (
        result["task_id"], result.get("run_id") or "", task_type)


def _exec_tool_impl(workdir, name, args, cancel_event=None, deadline=None, task_creator=None,
               disabled_tools=None, sandbox=None, checkpoint_run_id="",
               tool_output_run_id=""):
    from . import policy
    denied = set(policy.normalize_disabled_tools(disabled_tools))
    if sandbox is None:
        sandbox = policy.normalize_sandbox({}, workdir)
    denied.update(sandbox.get("disabled_tools") or [])
    from . import action_policy
    from . import agent_approval
    approval_task = str(sandbox.get("approval_task") or checkpoint_run_id or "").strip()
    known_mcp = sandbox.get("known_mcp_tools") if isinstance(sandbox, dict) else None
    spec = action_policy.spec_for(name, known_mcp=known_mcp)
    if (spec is not None and spec.zone == "yellow" and
            sandbox.get("require_approval") and approval_task and checkpoint_run_id and
            agent_approval.is_approved(approval_task, checkpoint_run_id,
                                       action=name, group=spec.group)):
        sandbox = dict(sandbox)
        sandbox["approved_tools"] = list(set(sandbox.get("approved_tools") or ()) | {name})
    decision = action_policy.preflight(name, args=args, sandbox=sandbox, known=True,
                                       known_mcp=known_mcp)
    if decision.get("decision") != "autoExecute":
        if decision.get("decision") == "needConfirm":
            return "（工具需要显式审批，未执行: %s）" % str(name or "")[:120]
        return "（工具裁定拒绝，未执行: %s）" % (decision.get("reason") or "denied")
    if name in denied:
        return "（工具已禁用，拒绝执行: %s）" % str(name or "")[:120]
    # All built-in file tools are constrained to the task's allowed roots, not
    # merely hidden from the model. Shell/MCP effects are handled separately.
    if name in {"list_files", "read_file", "write_file", "edit_file", "append_file",
                "fs_manage", "search_content", "find_files"}:
        keys = ["path", "dest"]
        # Enumeration tools default to the whole workspace. If policy grants
        # only a narrower root, require an explicit in-scope path instead of
        # silently exposing names/content outside that root.
        if name in {"list_files", "search_content", "find_files"} \
                and not (args or {}).get("path") \
                and not _sandbox_path_allowed(workdir or ".", sandbox):
            return "（沙箱拒绝：此工具必须指定允许范围内的 path，未执行）"
        for key in keys:
            value = (args or {}).get(key)
            if not value:
                continue
            candidate = Path(str(value)).expanduser()
            if not candidate.is_absolute():
                candidate = Path(workdir or ".") / candidate
            if not _sandbox_path_allowed(candidate, sandbox):
                return "（沙箱拒绝：%s 超出允许工作目录，未执行）" % key
    if name == "create_task":
        return _tool_create_task(workdir, args or {}, task_creator=task_creator,
                                 sandbox=sandbox)
    if name == "read_tool_output":
        from . import tool_outputs
        result = tool_outputs.read(tool_output_run_id, (args or {}).get("ref"),
                                   offset=(args or {}).get("offset", 0),
                                   limit=(args or {}).get("limit", 20000))
        if not result.get("ok"):
            return "（%s）" % result.get("error", "工具结果不可用")
        text = result.get("text") or ""
        if result.get("next_offset") is not None:
            text += "\n…[后续内容可继续 read_tool_output，offset=%s]…" % result["next_offset"]
        return text
    if name == "list_skills":
        return _tool_list_skills()
    if name == "use_skill":
        return _tool_use_skill(args or {})
    if name == "web_search":
        return _tool_web_search(args or {}, sandbox=sandbox, deadline=deadline)
    if name == "read_task_output":
        return _tool_read_task_output(workdir, args or {}, sandbox=sandbox)
    if str(name or "").startswith("mcp__"):
        # MCP process launch enforces this policy through bubblewrap; platforms
        # without the isolation backend fail closed in mcp_client._sandbox_launch.
        remaining = None
        if deadline is not None:
            remaining = float(deadline) - time.monotonic()
            if remaining <= 0:
                return "（任务总时限已到，MCP 工具未执行）"
        # MCP 工具：透传给配置的服务器（stdio JSON-RPC）；超时给足但封顶
        try:
            from . import mcp_client
            timeout_s = min(float(sandbox.get("timeout_s") or CMD_MAX_TIMEOUT),
                            remaining if remaining is not None else CMD_MAX_TIMEOUT)
            r = mcp_client.dispatch_full_name(
                name, args or {}, sandbox=sandbox, workdir=workdir,
                disabled_tools=denied, timeout_s=timeout_s,
                cancel_event=cancel_event, deadline=deadline)
            return r.get("text") or ("（MCP 工具失败: %s）" % r.get("error") if not r.get("ok") else "")
        except Exception as e:
            return "MCP 工具执行失败: %s" % e
    fn = _TOOL_IMPL.get(name or "")
    if fn is None:
        return "（未知工具: %s）" % name
    try:
        snapshot_paths = []
        if checkpoint_run_id and name in {"write_file", "edit_file", "append_file", "fs_manage"}:
            action = str((args or {}).get("action") or "").lower()
            src_value = (args or {}).get("path")
            src = Path(str(src_value)).expanduser() if src_value else None
            if src is not None and not src.is_absolute():
                src = Path(workdir or ".") / src
            if src is not None:
                if name == "fs_manage" and src.is_dir() and not src.is_symlink():
                    return "（检查点拒绝：目录级操作尚不支持完整快照，未执行；请明确改为逐文件操作）"
                snapshot_paths.append(src)
            if action in ("move", "rename", "copy") and (args or {}).get("dest"):
                dest = Path(str(args["dest"])).expanduser()
                if not dest.is_absolute():
                    dest = Path(workdir or ".") / dest
                snapshot_paths.append(dest)
            # Keep first-touch order while avoiding duplicate writes.
            snapshot_paths = list(dict.fromkeys(snapshot_paths))
            try:
                from . import checkpoints
                for path in snapshot_paths:
                    saved = checkpoints.capture_file(checkpoint_run_id, workdir, path)
                    if not saved.get("ok"):
                        return "（检查点拒绝：无法安全保存 %s 的原始版本（%s），未执行文件操作）" % (
                            path.name, saved.get("reason") or "unknown")
            except Exception:
                return "（检查点拒绝：文件原始版本保存失败，未执行文件操作）"
        if name == "run_command":
            try:
                from . import command_auth
                approval = command_auth.authorize(
                    str((args or {}).get("command") or ""),
                    run_id=checkpoint_run_id, workdir=workdir,
                    deadline=deadline, cancel_event=cancel_event,
                    source="run_command")
            except Exception:
                approval = {"allowed": False, "reason": "authorization_unavailable"}
            if not approval.get("allowed"):
                reason = approval.get("reason") or "denied"
                return "（命令授权%s，未执行；拒绝后不会自动重放，请模型检查并重新请求）" % reason
            result = fn(workdir, args or {}, cancel_event=cancel_event,
                        deadline=deadline, sandbox=sandbox)
        elif name == "write_file":
            result = fn(workdir, args or {}, sandbox=sandbox)
        else:
            result = fn(workdir, args or {})
        if snapshot_paths:
            checkpoint_warning = ""
            try:
                from . import checkpoints
                for path in snapshot_paths:
                    if not checkpoints.mark_file_after(checkpoint_run_id, workdir, path):
                        checkpoint_warning = "（检查点警告：操作后状态未能记账，此文件可能无法自动恢复）"
            except Exception:
                checkpoint_warning = "（检查点警告：操作后状态未能记账，此文件可能无法自动恢复）"
            if checkpoint_warning:
                result += "\n" + checkpoint_warning
        return result
    except Exception as e:
        return "工具执行失败: %s" % (e)


def _exec_tool(workdir, name, args, cancel_event=None, deadline=None, task_creator=None,
               disabled_tools=None, sandbox=None, checkpoint_run_id="",
               tool_output_run_id=""):
    """Run one tool and emit a compact decision receipt for every call."""
    from . import action_policy, audit, policy
    effective = sandbox if isinstance(sandbox, dict) else policy.normalize_sandbox({}, workdir)
    from . import agent_approval
    approval_task = str(effective.get("approval_task") or checkpoint_run_id or "").strip()
    known_mcp = effective.get("known_mcp_tools")
    spec = action_policy.spec_for(name, known_mcp=known_mcp)
    if (spec is not None and spec.zone == "yellow" and effective.get("require_approval")
            and approval_task and checkpoint_run_id
            and agent_approval.is_approved(approval_task, checkpoint_run_id,
                                           action=name, group=spec.group)):
        effective = dict(effective)
        effective["approved_tools"] = list(set(effective.get("approved_tools") or ()) | {name})
    decision = action_policy.preflight(name, args=args, sandbox=effective, known=True,
                                       known_mcp=known_mcp)
    result = _exec_tool_impl(
        workdir, name, args, cancel_event=cancel_event, deadline=deadline,
        task_creator=task_creator, disabled_tools=disabled_tools,
        sandbox=effective, checkpoint_run_id=checkpoint_run_id,
        tool_output_run_id=tool_output_run_id)
    text = str(result or "")
    failed = any(token in text for token in ("拒绝", "未执行", "失败", "错误"))
    audit.record_decision(
        name, decision.get("decision"), decision.get("zone"),
        decision.get("reason"), status="error" if failed else "returned",
        run_id=checkpoint_run_id or tool_output_run_id, args=args, result=text)
    return result


# ---------------------------------------------------------------- 工具清单（协议适配）

def _split_tool_args(t):
    """args 键以 ? 开头 = 可选参数（模型侧名字去掉 ?，不进 required）。"""
    props, required = {}, []
    for k, v in t["args"].items():
        opt = k.startswith("?")
        props[k[1:] if opt else k] = {"type": "string", "description": v}
        if not opt:
            required.append(k)
    return props, required


def _mcp_specs(sandbox=None, workdir=None):
    """MCP 工具清单（带缓存）；取不到/未配置返回空表——增强不挡主流程。"""
    try:
        from . import mcp_client
        return mcp_client.tool_specs_cached(sandbox=sandbox, workdir=workdir)
    except Exception:
        return []


def _openai_tools(extra_specs=None, disabled_tools=None, sandbox=None, workdir=None):
    disabled = set(disabled_tools or [])
    out = []
    for t in list(TOOLS_SPEC) + list(extra_specs or []):
        if t["name"] in disabled:
            continue
        props, required = _split_tool_args(t)
        out.append({"type": "function", "function": {
            "name": t["name"], "description": t["description"],
            "parameters": {"type": "object", "properties": props,
                           "required": required}}})
    for m in _mcp_specs(sandbox=sandbox, workdir=workdir):
        if m["full_name"] in disabled:
            continue
        # MCP 工具自带 JSON Schema（透传，不套字符串化的 _split_tool_args）
        out.append({"type": "function", "function": {
            "name": m["full_name"],
            "description": (m["description"] + "（MCP 工具，来自服务器 %s）" % m["server"]).strip(),
            "parameters": m["input_schema"]}})
    return out


def _anthropic_tools(extra_specs=None, disabled_tools=None, sandbox=None, workdir=None):
    disabled = set(disabled_tools or [])
    out = []
    for t in list(TOOLS_SPEC) + list(extra_specs or []):
        if t["name"] in disabled:
            continue
        props, required = _split_tool_args(t)
        out.append({"name": t["name"], "description": t["description"],
                    "input_schema": {"type": "object", "properties": props,
                                     "required": required}})
    for m in _mcp_specs(sandbox=sandbox, workdir=workdir):
        if m["full_name"] in disabled:
            continue
        out.append({"name": m["full_name"],
                    "description": (m["description"] + "（MCP 工具，来自服务器 %s）" % m["server"]).strip(),
                    "input_schema": m["input_schema"]})
    return out