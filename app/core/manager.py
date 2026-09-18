# -*- coding: utf-8 -*-
"""智能体管理器：安装检测、版本、安装/升级、模型配置读写。

安全约束：catalog 中的配置文件路径展开后必须落在用户主目录内，
防止相对路径穿越到预期之外的系统位置。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
import zlib
from pathlib import Path

from . import catalog, paths, runner

# 非 Windows 置 0：POSIX 的 Popen 对非零 creationflags 抛 ValueError（runner 同款守卫）
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
VERSION_TTL = 300  # 版本缓存 5 分钟

_LOCK = threading.RLock()
_STATE = {"detected": {}, "versions": {}, "detect_ts": 0.0, "detect_ev": None}

# 能自动写入默认模型的 config.format（其余格式只能手动编辑）
_WRITABLE_FORMATS = ("toml-line", "toml-section", "json", "json-path", "jsonc",
                     "yaml-line")


def _expand(p):
    return os.path.abspath(os.path.expanduser(os.path.expandvars(p)))


def _safe_config_path(raw):
    """展开并校验配置路径：必须在用户主目录内（防穿越）。"""
    if not raw:
        return None
    full = _expand(raw)
    home = os.path.abspath(os.path.expanduser("~"))
    try:
        if os.path.commonpath([full, home]) != home:
            return None
    except ValueError:
        return None
    return full


# ---------------------------------------------------------------- 检测

def detect_entry(entry):
    d = entry.get("detect") or {}
    if d.get("cli"):
        path = shutil.which(d["cli"])
        return {"installed": bool(path), "detail": path or ""}
    if d.get("exe"):
        full = _expand(d["exe"])
        return {"installed": os.path.isfile(full), "detail": full if os.path.isfile(full) else ""}
    if d.get("uwp"):
        base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages", d["uwp"])
        return {"installed": os.path.isdir(base), "detail": base if os.path.isdir(base) else ""}
    if d.get("dir"):
        full = _expand(d["dir"])
        return {"installed": os.path.isdir(full), "detail": full if os.path.isdir(full) else ""}
    return {"installed": False, "detail": ""}


def _orphan_signature(cl):
    """「Tutti 专属调用签名」判定（两个平台的清扫共用）。"""
    return (("opencode" in cl and "--model" in cl)
            or ("codex" in cl and "--skip-git-repo-check" in cl)
            or ("kimi-code" in cl and "main.mjs" in cl))


def _sweep_orphans_posix():
    """POSIX 版清扫：ps 一次拉全量，签名同 Windows。孤儿判据不同——POSIX 的
    孤儿进程会被内核过继给 1 号进程（launchd/init），Windows 则保留死掉的
    ppid，所以这里认 ppid==1；用户手动在终端里跑的同名 CLI 父进程是活着的
    shell（ppid!=1），不会被误杀。"""
    try:
        r = subprocess.run(["ps", "-eo", "pid=,ppid=,command="],
                           capture_output=True, timeout=60)
        killed = 0
        for ln in r.stdout.decode("utf-8", "replace").splitlines():
            parts = ln.strip().split(None, 2)
            if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
                continue
            pid, ppid, cmd = int(parts[0]), int(parts[1]), parts[2]
            if pid == 1 or ppid != 1 or not _orphan_signature(cmd.lower()):
                continue
            try:
                os.killpg(pid, signal.SIGKILL)  # 服务 spawn 用了 start_new_session，pgid==pid
                killed += 1
            except Exception:
                try:
                    os.kill(pid, signal.SIGKILL)
                    killed += 1
                except Exception:
                    pass
        return killed
    except Exception:
        return 0


def sweep_orphan_cli_processes():
    """启动清扫：服务重启会孤儿化正在跑的 CLI 孙进程（外部只杀服务 PID，不带
    /T），僵尸 opencode 更会劫持后续会话——opencode 是客户端-服务端架构，新
    `opencode run` 连上僵尸实例后 shell 全在僵尸的项目根里跑（2026-09-17
    mo-so 实测：agent 在 Temp 里找代码，汇报「工作目录没有源码」）。

    按「Tutti 调用签名 + 父进程已死」双条件匹配，不误杀用户自己在用的 CLI：
      opencode：命令行含 opencode + --model（同步写入的 provider 固定 orch）
      codex：命令行含 codex + --skip-git-repo-check（Tutti 专属 flag 组合）
      kimi：命令行含 kimi-code/dist/main.mjs（node 直启路径，2026-09-17 实测
      僵尸 kimi 会占住讯飞网关同钥请求队列，堵死后续所有 kimi 调用）
    claude 不扫（签名与用户手动使用难区分）。返回清扫数量。"""
    if os.name != "nt":
        return _sweep_orphans_posix()
    ps_exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if not os.path.isfile(ps_exe):
        ps_exe = "powershell"
    ps = ("Get-CimInstance Win32_Process | "
          "Where-Object { $_.Name -match '^(opencode|codex|node|cmd)\\.exe$' } | "
          "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress")
    try:
        r = subprocess.run([ps_exe, "-NoProfile", "-Command", ps],
                           capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=60)
        import json as _json
        raw = r.stdout.decode("utf-8", "replace").strip()
        items = _json.loads(raw) if raw else []
        if isinstance(items, dict):
            items = [items]
        live = {i.get("ProcessId") for i in items}
        killed = 0
        for i in items:
            cl = str(i.get("CommandLine") or "").lower()
            ppid = i.get("ParentProcessId")
            if not _orphan_signature(cl) or ppid in live or not i.get("ProcessId"):
                continue
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(i["ProcessId"])],
                               capture_output=True, creationflags=CREATE_NO_WINDOW,
                               timeout=15)
                killed += 1
            except Exception:
                pass
        return killed
    except Exception:
        return 0


def detect_all(force=False):
    """检测全部条目。检测（慢磁盘 IO）在锁外跑：shutil.which/isfile 在
    Windows 上遇到断链的 PATH 项可能卡数秒，持锁会把所有并发请求堵死
    （曾导致 SSE 多连接时服务假死）。等待方有界等待 30s 后拿旧结果。
    """
    with _LOCK:
        if not force and _STATE["detected"] and time.time() - _STATE["detect_ts"] < 60:
            return _STATE["detected"]
        ev = _STATE["detect_ev"]
        lead = ev is None  # 我是本次检测的执行者
        if lead:
            ev = _STATE["detect_ev"] = threading.Event()
    if not lead:
        ev.wait(30)  # 检测完成或超时；两种情况都拿当前最新快照
        with _LOCK:
            return dict(_STATE["detected"])
    try:
        detected = {}
        for entry in catalog.load():
            try:
                detected[entry["id"]] = detect_entry(entry)
            except Exception as e:
                detected[entry["id"]] = {"installed": False, "detail": "检测出错: %r" % e}
        if detected:
            with _LOCK:
                _STATE["detected"] = detected
    finally:
        with _LOCK:
            _STATE["detect_ts"] = time.time()
            _STATE["detect_ev"] = None
        ev.set()
    return detected


def _uwp_version(package_dir):
    import xml.etree.ElementTree as ET
    mf = os.path.join(package_dir, "AppxManifest.xml")
    if not os.path.isfile(mf):
        return None
    try:
        tree = ET.parse(mf)
        for el in tree.iter():
            if el.tag.endswith("}Identity") or el.tag == "Identity":
                return el.get("Version")
    except Exception:
        pass
    return None


def _exe_version(path):
    if os.name != "nt":
        return None  # exe 版本探测是 Windows 专属功能（PowerShell 读 PE 资源）
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Item -LiteralPath '%s').VersionInfo.ProductVersion" % path.replace("'", "''")],
            capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=25)
        out = r.stdout.decode("utf-8", "replace").strip()
        return out or None
    except Exception:
        return None


def version_of(entry):
    """版本探测：CLI 走 --version；UWP 读 AppxManifest；exe 走 PowerShell（惰性缓存）。"""
    eid = entry["id"]
    with _LOCK:
        cached = _STATE["versions"].get(eid)
        if cached and time.time() - cached[0] < VERSION_TTL:
            return cached[1]
    det = (detect_all() or {}).get(eid) or {}
    version = None
    cli = (entry.get("detect") or {}).get("cli")
    if cli and det.get("installed"):
        try:
            # Windows 下 CLI 可能是 npm .cmd 垫片，须经 cmd /c 才能直接点名跑；
            # POSIX 没有垫片，符号链接直接跑即可
            probe = ["cmd", "/c", cli, "--version"] if os.name == "nt" \
                else [cli, "--version"]
            r = subprocess.run(probe, capture_output=True,
                               creationflags=CREATE_NO_WINDOW, timeout=20)
            out = (r.stdout or b"").decode("utf-8", "replace").strip()
            if not out:
                out = (r.stderr or b"").decode("utf-8", "replace").strip()
            version = out.splitlines()[0][:60] if out else None
        except Exception:
            version = None
    elif det.get("detail") and os.path.isdir(det["detail"]):
        version = _uwp_version(det["detail"])
    elif det.get("detail") and os.path.isfile(det["detail"]):
        version = _exe_version(det["detail"])
    with _LOCK:
        _STATE["versions"][eid] = (time.time(), version or "-")
    return version or "-"


# ---------------------------------------------------------------- TOML 表内键

def _toml_span(lines, table):
    """定位顶层表 `[table]` 的行区间 [start, end)；start 为表头行。
    只认顶层表头（行首无空白），不误吞嵌套 `[[array]]` 之外的子表——
    子表在 TOML 里也是 `[a.b]` 顶层写法，同样按表头截断。"""
    header = re.compile(r"^\[([^\[\]]+)\]\s*$")
    start = None
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("["):
            m = header.match(ln.strip())
            if not m:
                continue
            if start is not None:
                return start, i
            if m.group(1).strip() == table:
                start = i
    if start is None:
        return None, None
    return start, len(lines)


def _toml_read_value(text, table, leaf):
    m = re.search(r'(?m)^\s*%s\s*=\s*"([^"]*)"\s*(#.*)?$' % re.escape(leaf), text) \
        if table is None else None
    if table is None:
        return m.group(1) if m else None
    start, end = _toml_span(text.splitlines(), table)
    if start is None:
        return None
    for ln in text.splitlines()[start + 1:end]:
        m = re.match(r'^\s*%s\s*=\s*"([^"]*)"\s*(#.*)?$' % re.escape(leaf), ln)
        if m:
            return m.group(1)
    return None


def _toml_write_value(text, table, leaf, value):
    """就地写入 TOML 的 [table] leaf（双引号标量），保留其余内容与换行风格。"""
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    new_line = '%s = "%s"' % (leaf, value.replace("\\", "\\\\").replace('"', '\\"'))
    # 换值不换行：保留行尾注释等其余内容
    pat = re.compile(r'^(\s*%s\s*=\s*)"(?:[^"\\]|\\.)*"(.*)$' % re.escape(leaf))
    if table is None:
        for i, ln in enumerate(lines):
            m = pat.match(ln)
            if m:
                lines[i] = "%s\"%s\"%s" % (m.group(1), value.replace("\\", "\\\\").replace('"', '\\"'), m.group(2))
                break
        else:
            lines.append(new_line)
        return eol.join(lines).rstrip("\r\n") + eol
    start, end = _toml_span(lines, table)
    if start is None:  # 表不存在：整段追加
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("[%s]" % table)
        lines.append(new_line)
        return eol.join(lines).rstrip("\r\n") + eol
    for i in range(start + 1, end):
        m = pat.match(lines[i])
        if m:
            lines[i] = "%s\"%s\"%s" % (m.group(1), value.replace("\\", "\\\\").replace('"', '\\"'), m.group(2))
            return eol.join(lines).rstrip("\r\n") + eol
    lines.insert(end, new_line)  # 表内末尾追加（表头区间终点即下一表头前）
    return eol.join(lines).rstrip("\r\n") + eol


# ---------------------------------------------------------------- 模型配置

def _config_path(entry):
    cfg = entry.get("config") or {}
    return _safe_config_path(cfg.get("path"))


def _yaml_model_path(cfg):
    """把 config.model_key 的点号路径拆成 (段, 键)；无点号时段为 None（顶层键）。"""
    return _dotted_key(cfg)


def _dotted_key(cfg):
    """model_key 点号路径拆 (表/段, 键)；无点号时第一元为 None（顶层键）。"""
    key = (cfg.get("model_key") or "model").strip()
    if "." in key:
        section, leaf = key.split(".", 1)
        return section.strip(), leaf.strip()
    return None, key


def _yaml_quote(value):
    """YAML 双引号标量。必须加引号：模型名可能以 [ 开头（YAML 流序列）或含 #。"""
    return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')


def _yaml_unquote(raw):
    """取 YAML 标量的值：剥引号、丢行尾注释。引号内的 # 不算注释。"""
    s = (raw or "").strip()
    if not s:
        return ""
    if s[0] in ("'", '"'):
        q = s[0]
        i = 1
        buf = []
        while i < len(s):
            ch = s[i]
            if q == '"' and ch == "\\" and i + 1 < len(s):
                nxt = s[i + 1]
                buf.append('"' if nxt == '"' else ("\\" if nxt == "\\" else nxt))
                i += 2
                continue
            if ch == q:
                if q == "'" and i + 1 < len(s) and s[i + 1] == "'":  # '' 转义
                    buf.append("'")
                    i += 2
                    continue
                break
            buf.append(ch)
            i += 1
        return "".join(buf).strip()
    # 无引号：截断行尾注释（# 前的空白才算注释起始）
    return re.split(r"\s+#", s, 1)[0].strip()


def _yaml_span(lines, section):
    """定位顶层段的行区间 [start, end)；start 为段名行，其子键在 start+1..end。"""
    start = None
    for i, ln in enumerate(lines):
        m = re.match(r"^([^\s#][^:]*):\s*(.*)$", ln)
        if not m:
            continue
        if m.group(1).strip() == section:
            start = i
            continue
        if start is not None:
            return start, i
    if start is None:
        return None, None
    return start, len(lines)


def _yaml_read_value(text, section, leaf):
    lines = text.splitlines()
    if section is None:
        m = re.search(r"(?m)^%s\s*:\s*(.+?)\s*$" % re.escape(leaf), text)
        return _yaml_unquote(m.group(1)) or None if m else None
    start, end = _yaml_span(lines, section)
    if start is None:
        return None
    for ln in lines[start + 1:end]:
        m = re.match(r"^\s+%s\s*:\s*(.+?)\s*$" % re.escape(leaf), ln)
        if m:
            return _yaml_unquote(m.group(1)) or None
    return None


def _yaml_write_value(text, section, leaf, value):
    """就地写入 YAML 的 section.leaf，保留其余内容、缩进与换行风格。"""
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    quoted = _yaml_quote(value)
    if section is None:
        for i, ln in enumerate(lines):
            if re.match(r"^%s\s*:" % re.escape(leaf), ln):
                lines[i] = "%s: %s" % (leaf, quoted)
                break
        else:
            lines.append("%s: %s" % (leaf, quoted))
        return eol.join(lines).rstrip("\r\n") + eol
    start, end = _yaml_span(lines, section)
    if start is None:  # 段不存在：整段追加
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("%s:" % section)
        lines.append("  %s: %s" % (leaf, quoted))
        return eol.join(lines).rstrip("\r\n") + eol
    for i in range(start + 1, end):
        m = re.match(r"^(\s+)%s\s*:" % re.escape(leaf), lines[i])
        if m:  # 键已存在：只换值，保留原缩进
            lines[i] = "%s%s: %s" % (m.group(1), leaf, quoted)
            return eol.join(lines).rstrip("\r\n") + eol
    indent, last = "  ", start  # 段存在但无该键：跟随段内缩进、追加到段尾
    for i in range(start + 1, end):
        if not lines[i].strip():
            continue
        if indent == "  ":
            m = re.match(r"^(\s+)\S", lines[i])
            if m:
                indent = m.group(1)
        last = i
    lines.insert(last + 1, "%s%s: %s" % (indent, leaf, quoted))
    return eol.join(lines).rstrip("\r\n") + eol


def _json_path_get(data, keys):
    """沿点号路径下钻 JSON 嵌套；终点必须是字符串。"""
    cur = data
    for k in keys:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return None
    return cur if isinstance(cur, str) else None


def _json_path_set(data, keys, value):
    """沿点号路径写入 JSON 嵌套，缺中间对象就地创建；
    中途遇到非 dict（如 string 简写形式）升级为对象。"""
    cur = data
    for k in keys[:-1]:
        if not isinstance(cur.get(k), dict):
            cur[k] = {}
        cur = cur[k]
    cur[keys[-1]] = value


def read_model(entry):
    path = _config_path(entry)
    cfg = entry.get("config") or {}
    if not path or not os.path.isfile(path) or not cfg.get("format"):
        return None
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except Exception:
        return None
    if cfg["format"] == "toml-line":
        m = re.search(r'(?m)^\s*model\s*=\s*"([^"]+)"', text)
        return m.group(1) if m else None
    if cfg["format"] == "toml-section":
        table, leaf = _dotted_key(cfg)
        return _toml_read_value(text, table, leaf)
    if cfg["format"] == "json":
        try:
            v = json.loads(text).get("model")
            if v:
                return v
        except Exception:
            pass
        m = re.search(r'"model"\s*:\s*"([^"]+)"', text)
        return m.group(1) if m else None
    if cfg["format"] == "jsonc":
        keys = (cfg.get("model_key") or "model").split(".")
        try:
            data = json.loads(_jsonc_strip_comments(text) or "{}")
        except Exception:
            return None
        return _json_path_get(data, keys)
    if cfg["format"] == "json-path":
        try:
            data = json.loads(text or "{}")
        except Exception:
            return None
        return _json_path_get(data, (cfg.get("model_key") or "model").split("."))
    if cfg["format"] == "yaml-line":
        return _yaml_read_value(text, *_yaml_model_path(cfg))
    return None


def write_model(entry, model):
    """写入默认模型（改动前自动备份 .bak）。支持 toml-line / toml-section /
    json / json-path / jsonc / yaml-line。"""
    path = _config_path(entry)
    cfg = entry.get("config") or {}
    fmt = cfg.get("format")
    if not path:
        return {"ok": False,
                "error": "配置路径无效或不在用户主目录内，已拒绝写入"}
    if fmt not in _WRITABLE_FORMATS:
        return {"ok": False, "error": "该工具的模型配置格式暂不支持自动写入，请手动编辑 %s" % path}
    model = (model or "").strip()
    if not model:
        return {"ok": False, "error": "模型名不能为空"}
    # 写入前二次校验：解析真实路径后必须仍在用户主目录内（防穿越/符号链接），
    # 校验通过后统一改用解析路径写入
    home = os.path.abspath(os.path.expanduser("~"))
    path = os.path.realpath(path)
    try:
        if os.path.commonpath([path, home]) != home:
            return {"ok": False, "error": "配置路径越出用户主目录，已拒绝写入"}
    except ValueError:
        return {"ok": False, "error": "配置路径越出用户主目录，已拒绝写入"}
    try:
        if os.path.isfile(path):
            shutil.copyfile(path, path + ".bak")
        else:
            # 各 CLI 首次运行都未必建主配置（grok 不建 config.toml、pi 不建
            # settings.json、openclaw 缺失即安全默认——官方文档明确「缺失即
            # 内置默认」）；它正是用户层覆盖的落点，缺了按需创建
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b"{}" if fmt in ("json", "json-path", "jsonc") else b"")
        if fmt == "jsonc":
            # mimo（mimocode.jsonc）有注释，整体重解析会丢注释——复用
            # _jsonc_set 做就地片段改写（只动目标键，其余原样保留）
            keys = (cfg.get("model_key") or "model").split(".")
            text = open(path, encoding="utf-8", errors="replace", newline="").read()
            new_text, ok = _jsonc_set(text, tuple(keys),
                                      json.dumps(model, ensure_ascii=False))
            if not ok:
                return {"ok": False,
                        "error": "jsonc 结构异常，未能就地写入 %s（已避免覆盖）" % path}
            Path(path).write_bytes(new_text.encode("utf-8"))
        elif fmt == "toml-line":
            text = open(path, encoding="utf-8", errors="replace").read()
            new_line = 'model = "%s"' % model
            if re.search(r'(?m)^\s*model\s*=\s*"[^"]*"', text):
                text = re.sub(r'(?m)^\s*model\s*=\s*"[^"]*"', new_line, text)
            else:
                text = text.rstrip("\n") + "\n" + new_line + "\n"
            Path(path).write_bytes(text.encode("utf-8"))
        elif fmt == "toml-section":
            text = open(path, encoding="utf-8", errors="replace", newline="").read()
            text = _toml_write_value(text, *_dotted_key(cfg), value=model)
            Path(path).write_bytes(text.encode("utf-8"))
        elif fmt == "yaml-line":
            # newline="" 关掉通用换行转换：文本层面看不出 \r\n 就会被静默改写成 LF，
            # 用户的 Windows 配置不该因为写个模型名而整篇换行符被替换
            text = open(path, encoding="utf-8", errors="replace", newline="").read()
            text = _yaml_write_value(text, *_yaml_model_path(cfg), value=model)
            Path(path).write_bytes(text.encode("utf-8"))
        elif fmt == "json-path":
            # openclaw（agents.defaults.model.primary）：默认模型藏在嵌套对象里，
            # 且 openclaw 对未知顶层键直接拒绝启动——绝不能写顶层 "model"
            text = open(path, encoding="utf-8", errors="replace", newline="").read()
            try:
                data = json.loads(text) if text.strip() else {}
            except Exception:
                return {"ok": False, "error": "配置文件不是合法 JSON，已中止（避免覆盖）"}
            _json_path_set(data, (cfg.get("model_key") or "model").split("."), model)
            # pi 的 settings.json：defaultModel 必须配 defaultProvider 才能解析出
            # (provider, model) 二元组；catalog 里声明了的伴随键一并落盘。
            # setdefault 语义：用户已设的值（如 defaultProvider: anthropic）不被空串覆盖
            for k, v in (cfg.get("model_extra_keys") or {}).items():
                cur = data
                ks = k.split(".")
                for kk in ks[:-1]:
                    if not isinstance(cur.get(kk), dict):
                        cur[kk] = {}
                    cur = cur[kk]
                if cur.get(ks[-1]) in (None, ""):
                    cur[ks[-1]] = v
            Path(path).write_bytes(
                json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
        else:
            try:
                data = json.loads(open(path, encoding="utf-8", errors="replace").read())
            except FileNotFoundError:
                return {"ok": False,
                        "error": "配置文件尚未生成（%s 首次运行后才有），暂无法写入" % path}
            except Exception:
                return {"ok": False, "error": "配置文件不是合法 JSON，已中止（避免覆盖）"}
            data["model"] = model
            Path(path).write_bytes(
                json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
        return {"ok": True, "model": read_model(entry)}
    except Exception as e:
        return {"ok": False, "error": repr(e)}


# ---------------------------------------------------------------- 一键打开

def _port_open(port, timeout=0.5):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def launch_env(entry):
    """打开交互/网页版时注入的子进程环境变量：与编排同源的绑定凭据
    （dsh=DEEPSEEK_*，claude=ANTHROPIC_*，codex=ORCH_API_KEY）。交互进程
    脱离了编排链路，没有这层注入就拿不到 API key。无绑定时返回 {}。"""
    try:
        from . import modelhub  # 惰性导入：modelhub 体量大且避免潜在环
        b = modelhub.resolve_binding(entry["id"]) or {}
    except Exception:
        return {}
    return dict(b.get("env") or {})


def _launch_log_path(entry):
    """web 类启动日志的落点：id 白名单化后仅作文件名成分，最终路径必须仍围栏
    在数据目录内（catalog.json 用户可编辑，id 不可信；非白名单 id 用 crc32
    稳定代称——跨进程重启不变，「已在运行」回读上次日志才找得到）。"""
    raw_id = str(entry.get("id") or "")
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", raw_id):
        stem = raw_id
    else:
        stem = "agent-%d" % (zlib.crc32(raw_id.encode("utf-8")) & 0xFFFFFFFF)
    data_root = os.path.abspath(str(paths.DATA_DIR))
    p = Path(data_root, "launch", stem + ".log")
    try:
        if os.path.commonpath([os.path.abspath(str(p)), data_root]) != data_root:
            return None
    except ValueError:
        return None
    return p


def _best_url(log_path, port):
    """从启动日志提取该端口的信任 URL（含 token 优先）。无日志/未匹配返回 None。"""
    try:
        text = open(str(log_path), encoding="utf-8", errors="replace").read()
    except Exception:
        return None
    urls = re.findall(r"https?://[^\s\"'<>]+", text)
    same = [u for u in urls if ":%d" % port in u]
    if not same:
        return None
    tokened = [u for u in same if "token=" in u]
    return (tokened or same)[0]


def _yaml_model_ids(text, section):
    """收集 section.models 序列里的全部模型 id（保持顺序）。段/键缺失返回 []。"""
    lines = text.splitlines()
    start, end = _yaml_span(lines, section)
    if start is None:
        return []
    m_indent = None
    for i in range(start + 1, end):
        m = re.match(r"^(\s+)models\s*:\s*(?:#.*)?$", lines[i])
        if m:
            m_indent = len(m.group(1))
            start = i
            break
    if m_indent is None:
        return []
    ids = []
    for ln in lines[start + 1:end]:
        if not ln.strip():
            continue
        if len(ln) - len(ln.lstrip(" ")) <= m_indent:
            break  # models 列表结束（遇到同级或更浅缩进的键）
        m = re.match(r"^\s*-\s+id\s*:\s*(.+?)\s*$", ln)
        if m:
            ids.append(_yaml_unquote(m.group(1)))
    return ids


def _yaml_ensure_model_entry(text, section, model, context_window=1000000):
    """确保 section.models 序列里有 id==model 的条目，缺则按现有条目形状追加
    （id/name/contextWindow——dsh 的 catalog 校验要求 id 与 name 非空）。
    已存在或段/列表结构不完整时原文返回。返回 (new_text, added)。"""
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    start, end = _yaml_span(lines, section)
    if start is None:
        return text, False
    m_idx = m_indent = None
    for i in range(start + 1, end):
        m = re.match(r"^(\s+)models\s*:\s*(?:#.*)?$", lines[i])
        if m:
            m_idx, m_indent = i, len(m.group(1))
            break
    if m_idx is None:
        return text, False  # 段内没有 models 键：不凭空造结构（保守）
    item_indent = None
    last_item = m_idx
    i = m_idx + 1
    while i < end:
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        if len(ln) - len(ln.lstrip(" ")) <= m_indent:
            break  # models 列表结束
        m = re.match(r"^(\s*)-\s+id\s*:\s*(.+?)\s*$", ln)
        if m:
            item_indent = m.group(1)
            last_item = i
            if _yaml_unquote(m.group(2)) == model:
                return text, False
        elif re.match(r"^\s+\S", ln):
            last_item = i  # 条目的续属性行（name/contextWindow…）
        i += 1
    item_indent = item_indent or (" " * (m_indent + 2))
    block = ["%s- id: %s" % (item_indent, _yaml_quote(model)),
             "%s  name: %s" % (item_indent, _yaml_quote(model)),
             "%s  contextWindow: %d" % (item_indent, context_window)]
    lines[last_item + 1:last_item + 1] = block
    return eol.join(lines).rstrip("\r\n") + eol, True


def _sync_dsh_settings(entry, model, base_url):
    """dsh 专属：把绑定模型的端点与模型目录写进 ~/.dsh/settings.yaml 的
    llm-deepseek 段（agent-default-model.model 由 write_model 负责）。
    端点不同步不行——settings 优先级高于 env，密钥会发给旧端点；
    models 列表不同步不行——dsh web 的模型下拉只列它，缺条目就选不中。
    返回错误串或 None。"""
    path = _config_path(entry)
    if not path:
        return "dsh 配置路径无效"
    path = os.path.realpath(path)
    home = os.path.abspath(os.path.expanduser("~"))
    try:
        if os.path.commonpath([path, home]) != home:
            return "dsh 配置路径越出用户主目录，已拒绝"
    except ValueError:
        return "dsh 配置路径越出用户主目录，已拒绝"
    try:
        if os.path.isfile(path):
            text = open(path, encoding="utf-8", errors="replace", newline="").read()
        else:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            text = ""
        text2 = _yaml_write_value(text, "llm-deepseek", "baseURL", base_url)
        text2, _added = _yaml_ensure_model_entry(text2, "llm-deepseek", model)
        if text2 != text:
            if os.path.isfile(path):
                shutil.copyfile(path, path + ".bak")
            Path(path).write_bytes(text2.encode("utf-8"))
        return None
    except Exception as e:
        return repr(e)


def _dsh_selfcheck_model(entry):
    """dsh 无绑定时自检：agent-default-model.model 必须在端点 models 列表内，
    否则 web UI 打开就是一个选不中的模型（glm-5.3-flash vs V4 端点的实况）。
    不在列表则改选列表第一个并写回。返回 (生效模型, note)。"""
    path = _config_path(entry)
    if not path or not os.path.isfile(path):
        return None, ""
    try:
        text = open(path, encoding="utf-8", errors="replace", newline="").read()
    except Exception:
        return None, ""
    cur = _yaml_read_value(text, "agent-default-model", "model")
    ids = _yaml_model_ids(text, "llm-deepseek")
    if not ids:
        return cur, "dsh 端点未登记任何模型，请先在 dsh 侧配置模型目录"
    if cur in ids:
        return cur, ""
    first = ids[0]
    w = write_model(entry, first)
    fixed = w.get("model") if w.get("ok") else None
    note = "dsh 默认模型 %s 不在端点模型列表，已改选 %s" % (cur or "（空）", first)
    if not fixed:
        note += "（写入失败：%s）" % w.get("error")
    return fixed, note


def _dsh_key_present():
    """dsh 的密钥是否有着落：进程 env 或它自己的 ~/.dsh/.env（credentials-local）。"""
    if os.environ.get("DEEPSEEK_API_KEY"):
        return True
    try:
        envfile = os.path.join(os.path.expanduser("~"), ".dsh", ".env")
        return "DEEPSEEK_API_KEY" in open(envfile, encoding="utf-8", errors="replace").read()
    except Exception:
        return False


def _toml_section_set(text, section, pairs):
    """就地写 TOML 段（[section] 下多键）。段存在则逐键替换，缺则整段追加在文末。
    只处理 codex config.toml 这种顶层简单段；返回 (new_text, changed)。"""
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    start = end = None
    header = "[%s]" % section
    for i, ln in enumerate(lines):
        if ln.strip() == header:
            start = i
        elif start is not None and ln.startswith("[") and ln.rstrip().endswith("]"):
            end = i
            break
    if start is None:
        block = [header] + ["%s = %s" % (k, v) for k, v in pairs]
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(block)
        return eol.join(lines).rstrip("\r\n") + eol, True
    end = end if end is not None else len(lines)
    changed = False
    todo = dict(pairs)
    for i in range(start + 1, end):
        m = re.match(r"^(\s*)([A-Za-z0-9_.-]+)\s*=", lines[i])
        if m and m.group(2) in todo:
            lines[i] = "%s%s = %s" % (m.group(1), m.group(2), todo.pop(m.group(2)))
            changed = True
    if todo:
        ins = start + 1
        while ins < end and not lines[ins].strip():
            ins += 1
        for k, v in list(todo.items())[::-1]:
            lines.insert(ins, "%s = %s" % (k, v))
        changed = True
    return eol.join(lines).rstrip("\r\n") + eol, changed


def _toml_top_set(text, key, value):
    """写 TOML 顶层键（第一个 [段] 之前的区域）。返回 (new_text, changed)。"""
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    pat = re.compile(r"^(\s*)%s\s*=\s*.+$" % re.escape(key))
    for i, ln in enumerate(lines):
        if pat.match(ln):
            lines[i] = "%s%s = %s" % (pat.match(ln).group(1), key, value)
            return eol.join(lines).rstrip("\r\n") + eol, True
    first_section = next((i for i, ln in enumerate(lines)
                          if ln.startswith("[") and ln.rstrip().endswith("]")), len(lines))
    lines.insert(first_section, "%s = %s" % (key, value))
    return eol.join(lines).rstrip("\r\n") + eol, True


def _sync_codex_settings(entry, model, cp):
    """codex 专属：把绑定供应商与模型写进 ~/.codex/config.toml
    （[model_providers.orch] 段 + 顶层 model_provider/model）。

    codex 交互 TUI 不认编排的 -c 一次性覆盖，也不认 ORCH_API_KEY env——
    没有 config.toml 里的 provider 段，绑定模型根本无处可用；而 model 单写
    不写 provider 会指到 codex 自带 openai 官方端点上（401）。与编排的
    _codex_provider_args 同构，但落 config 文件。返回错误串或 None。"""
    path = _config_path(entry)
    if not path:
        return "codex 配置路径无效"
    path = os.path.realpath(path)
    home = os.path.abspath(os.path.expanduser("~"))
    try:
        if os.path.commonpath([path, home]) != home:
            return "codex 配置路径越出用户主目录，已拒绝"
    except ValueError:
        return "codex 配置路径越出用户主目录，已拒绝"
    name = cp.get("name", "orch")
    def q(v):
        return '"%s"' % str(v).replace("\\", "\\\\").replace('"', '\\"')
    if (cp.get("wire_api") or "responses") == "chat":
        # codex 0.154+ 起 chat wire 被官方移除，写进 config.toml 会让 CLI 连配置
        # 都载入不了（Error loading config.toml）——宁可明确拒绝也不落坏配置。
        return ("供应商只有 chat completions wire，codex 0.154+ 已移除支持，未写入"
                " config.toml——请为 codex 绑定 responses 兼容的供应商")
    pairs = [("name", q(cp.get("name", name))),
             ("base_url", q(cp.get("base_url", ""))),
             ("env_key", q(cp.get("env_key", "ORCH_API_KEY"))),
             ("wire_api", q(cp.get("wire_api", "responses")))]
    try:
        if os.path.isfile(path):
            text = open(path, encoding="utf-8", errors="replace", newline="").read()
        else:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            text = ""
        text2, _ = _toml_section_set(text, "model_providers.%s" % name, pairs)
        text2, _ = _toml_top_set(text2, "model_provider", q(name))
        if model:
            text2, _ = _toml_top_set(text2, "model", q(model))
        if text2 != text:
            if os.path.isfile(path):
                shutil.copyfile(path, path + ".bak")
            Path(path).write_bytes(text2.encode("utf-8"))
        return None
    except Exception as e:
        return repr(e)


# ---------------------------------------------------------------- 打开前凭据注入

def _jsonc_scan_object(text, start):
    """扫描 text[start]（须为 '{'）起的 JSON(C) 对象：返回 (闭合偏移, 键表)。
    键表为 [key, key_start, key_end, value_start, value_end]（偏移相对整个 text，
    value_end 指向值结束后一格）。跳过字符串转义、// 与 /* */ 注释、嵌套括号，
    未闭合时容错返回文末。"""
    n = len(text)
    i = start + 1
    keys = []
    state = "key"
    depth = 0

    def skip_string(j):
        while j < n:
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == '"':
                return j + 1
            j += 1
        return n

    def skip_comment(j):
        if j + 1 < n and text[j + 1] == "/":
            e = text.find("\n", j)
            return n if e < 0 else e
        if j + 1 < n and text[j + 1] == "*":
            e = text.find("*/", j + 2)
            return n if e < 0 else e + 2
        return j + 1  # 非注释的孤立斜杠：当普通字符

    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if ch == "/":
            i = skip_comment(i)
            continue
        if state == "key":
            if ch == "}":
                return i, keys
            if ch == ",":
                i += 1
                continue
            if ch == '"':
                j = skip_string(i + 1)
                keys.append([text[i + 1:j - 1], i, j, 0, 0])
                state = "colon"
                i = j
                continue
            i += 1
            continue
        if state == "colon":
            if ch == ":":
                state = "value"
            i += 1
            continue
        # state == value
        if not keys:
            return n, keys  # 结构异常：放弃扫描
        keys[-1][3] = i
        if ch in "{[":
            depth = 0
            j = i
            while j < n:
                c = text[j]
                if c == '"':
                    j = skip_string(j + 1)
                    continue
                if c == "/":
                    j = skip_comment(j)
                    continue
                if c in "{[":
                    depth += 1
                elif c in "}]":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            keys[-1][4] = j + 1
            i = j + 1
            state = "key"
        elif ch == '"':
            j = skip_string(i + 1)
            keys[-1][4] = j
            i = j
            state = "key"
        else:  # 数字 / true / false / null（或值后直接撞上 , } 换行的容错）
            j = i
            while j < n and text[j] not in ",}\r\n":
                j += 1
            keys[-1][4] = j
            # j==i 说明值位置直接是分隔符/换行（空值或状态残留）——必须前进一格，
            # 否则 while i < n 永远停在原地（死循环）
            i = j if j > i else j + 1
            state = "key"
    return n, keys


def _jsonc_strip_comments(text):
    """剥掉 jsonc 的 // 行注释与 /* */ 块注释，供 json.loads 解析。
    字符串字面量内部的 //（$schema 的 https:// 等）不剥——逐字符扫描；
    简单的 re.sub(r"//[^\\n]*") 会把 URL 截断导致解析失败。"""
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    break
                j += 1
            out.append(text[i:min(j + 1, n)])
            i = j + 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            e = text.find("\n", i)
            i = n if e < 0 else e  # 行注释：换行符本身保留
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            e = text.find("*/", i + 2)
            i = n if e < 0 else e + 2
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _jsonc_set(text, path, value_json):
    """JSON(C) 顶层就地写键：path=("provider","orch") 或 ("model",)。
    只动目标片段，其余文本（含注释与原格式）原样保留。返回 (new_text, ok)。
    空文件按空对象起笔（write_model 缺文件按需创建的产物）。"""
    brace = text.find("{")
    if brace < 0 and not text.strip():
        return ('{\n  "%s": %s\n}' % (path[0], value_json), True)
    if brace < 0:
        return text, False
    end, keys = _jsonc_scan_object(text, brace)
    head = path[0]
    if len(path) == 1:
        for _k, _ks, _ke, vs, ve in keys:
            if _k == head:
                return text[:vs] + value_json + text[ve:], True
        return _jsonc_insert_entry(text, brace, end, head, value_json), True
    # 二级路径：先定位一级键的值对象
    tgt = None
    for k, _ks, _ke, vs, ve in keys:
        if k == head:
            tgt = (vs, ve)
            break
    if tgt is None:  # 一级键不存在：整体插入
        block = json.dumps({path[1]: json.loads(value_json)}, ensure_ascii=False)
        return _jsonc_insert_entry(text, brace, end, head, block), True
    vs, ve = tgt
    if text[vs:ve].lstrip()[0:1] != "{":
        return text, False  # 一级值不是对象：保守放弃（不覆盖用户的非标结构）
    end2, keys2 = _jsonc_scan_object(text, vs)
    for k, _ks, _ke, v2s, v2e in keys2:
        if k == path[1]:
            return text[:v2s] + value_json + text[v2e:], True
    seg = text[vs:end2 + 1]
    new_seg = _jsonc_insert_entry(seg, 0, end2 - vs, path[1], value_json)
    if new_seg is None:
        return text, False
    return text[:vs] + new_seg + text[end2 + 1:], True


def _jsonc_insert_entry(text, brace, end, key, value_json):
    """在 {brace..end} 对象的开头插入 "key": value（带尾逗号，不依赖原文件的
    逗号风格）；对象为空时去掉多余逗号。"""
    m = re.match(r"\{([ \t\r\n]*)", text[brace:end + 1])
    first = brace + (m.end(1) if m else 1)  # m 从 brace 起 match：end(1) 已含 '{'
    if first >= end:  # 空对象 {}
        return text[:brace + 1] + ' "%s": %s ' % (key, value_json) + text[end:]
    return text[:first] + '"%s": %s,\n  ' % (key, value_json) + text[first:]


def _sync_settings_env(path, updates, remove_keys=()):
    """把键值对写进目标 settings.json 的 env 段（claude/qwen 等同构：交互 TUI
    启动时把该段合并进进程环境）。只动 env 相关键，其余内容保留；坏 JSON 中止
    不覆盖；改动前 .bak。返回错误串或 None。path 必须已过主目录围栏校验。"""
    try:
        if os.path.isfile(path):
            text = open(path, encoding="utf-8", errors="replace").read()
            try:
                data = json.loads(text)
            except Exception:
                return "settings.json 不是合法 JSON，已中止（避免覆盖）"
        else:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            data = {}
        if not isinstance(data, dict):
            return "settings.json 结构异常（顶层不是对象），已中止"
        env = data.get("env")
        if not isinstance(env, dict):
            env = {}
        for k in remove_keys:
            env.pop(k, None)
        env.update(updates)
        data["env"] = env
        if os.path.isfile(path):
            shutil.copyfile(path, path + ".bak")
        Path(path).write_bytes(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
        return None
    except Exception as e:
        return repr(e)


def _guard_home(path):
    """展开并校验配置路径必须在用户主目录内；通过则返回 realpath，否则 None。"""
    if not path:
        return None
    path = os.path.realpath(path)
    home = os.path.abspath(os.path.expanduser("~"))
    try:
        if os.path.commonpath([path, home]) != home:
            return None
    except ValueError:
        return None
    return path


def _sync_claude_settings(entry, model, prov):
    """claude-code 专属：把 anthropic 供应商的端点+密钥+模型写进
    ~/.claude/settings.json 的 env 段（交互 TUI 与无头共用该文件，只认
    ANTHROPIC_*；编排降级链给的 ORCH_API_KEY 对它等于没 key）。
    返回错误串或 None。"""
    path = _guard_home(_config_path(entry))
    if not path:
        return "claude 配置路径无效或越出用户主目录，已拒绝"
    updates = {"ANTHROPIC_BASE_URL": prov.get("base_url") or "",
               # AUTH_TOKEN 走 Bearer 头（Z.ai 等原生 anthropic 网关的用法）；
               # 与 x-api-key 互斥，清掉可能残留的 ANTHROPIC_API_KEY 防止带错头
               "ANTHROPIC_AUTH_TOKEN": prov.get("api_key") or ""}
    if model:
        updates["ANTHROPIC_MODEL"] = model
    return _sync_settings_env(path, updates, remove_keys=("ANTHROPIC_API_KEY",))


def _sync_qwen_settings(entry, model, prov):
    """qwencode 专属：openai 兼容供应商写进 ~/.qwen/settings.json 的 env 段
    （qwen-code 实测认 OPENAI_API_KEY/OPENAI_BASE_URL/OPENAI_MODEL，存在即
    优先走 openai 兼容通道——2026-09-15 真机对维云端点实测请求到达并鉴权）。
    anthropic 协议未实证，不开（协议不匹配时宁可提示）。返回错误串或 None。"""
    path = _guard_home(_config_path(entry))
    if not path:
        return "qwen 配置路径无效或越出用户主目录，已拒绝"
    updates = {"OPENAI_API_KEY": prov.get("api_key") or "",
               "OPENAI_BASE_URL": prov.get("base_url") or ""}
    if model:
        updates["OPENAI_MODEL"] = model
    return _sync_settings_env(path, updates)


def _opencode_config_candidates(entry):
    """opencode 配置的候选路径：catalog 登记的 jsonc 优先，其次同目录的
    opencode.json（opencode 两种文件名都认，用户现有安装多用 json）。"""
    out = []
    primary = _config_path(entry)
    if primary:
        out.append(primary)
    alt = os.path.join(os.path.abspath(os.path.expanduser("~/.config/opencode")),
                       "opencode.json")
    if alt not in out:
        out.append(alt)
    return out


def _sync_opencode_settings(entry, model, prov):
    """opencode 专属：把绑定供应商写进其配置的 provider.orch 段 + 顶层 model
    （opencode 交互 TUI 只认自家配置文件里的凭据，ORCH_API_KEY env 对它等于
    没 key）。写入所有已存在的候选文件（避免新文件遮蔽旧文件的读取优先级），
    全不存在时建 catalog 登记的那个。纯 JSON 走整体读改写；带注释的 JSONC 走
    文本级就地 patch（保留注释）。返回错误串或 None。"""
    npm = "@ai-sdk/anthropic" if prov.get("protocol") == "anthropic" else "@ai-sdk/openai-compatible"
    base = prov.get("base_url") or ""
    if prov.get("protocol") == "anthropic" and not base.rstrip("/").endswith("/v1"):
        # @ai-sdk/anthropic 在 baseURL 后只拼 /messages（官方默认 baseURL 本身带
        # /v1），而 models.json 里 anthropic 供应商的 base 不带 /v1（modelhub
        # 发请求时自己补）——不补会打到 <host>/messages，网关回 "Not Allowed"
        # （2026-09-17 公司Anthropic 实测）。
        base = base.rstrip("/") + "/v1"
    block = {"npm": npm, "name": prov.get("name") or "CodeBee 绑定",
             "options": {"baseURL": base,
                         "apiKey": prov.get("api_key") or ""},
             "models": {model: {"name": model}} if model else {}}
    top_perms = {"edit": "allow", "bash": "allow", "webfetch": "allow"}
    top_model = ("orch/" + model) if model else ""
    targets = [p for p in _opencode_config_candidates(entry) if os.path.isfile(p)] \
        or [_opencode_config_candidates(entry)[0]]
    errs = []
    for path in targets:
        try:
            path = os.path.realpath(path)
            home = os.path.abspath(os.path.expanduser("~"))
            try:
                if os.path.commonpath([path, home]) != home:
                    errs.append("路径越出主目录：%s" % path)
                    continue
            except ValueError:
                errs.append("路径越出主目录：%s" % path)
                continue
            if os.path.isfile(path):
                text = open(path, encoding="utf-8", errors="replace", newline="").read()
            else:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                text = ""
            try:
                data = json.loads(text) if text.strip() else {}
            except Exception:
                data = None
            if data is not None:  # 纯 JSON：整体读改写（保序）
                if not isinstance(data, dict):
                    errs.append("结构异常（顶层不是对象）：%s" % path)
                    continue
                provs = data.get("provider")
                if not isinstance(provs, dict):
                    provs = {}
                provs["orch"] = block
                data["provider"] = provs
                if top_model:
                    data["model"] = top_model
                # 无人值守必配：headless 下 opencode 工具调用默认要审批，全部被
                # 拒（"The user rejected permission..."，2026-09-17 实测）——
                # 按用户拍板的「默认给全部权限」写入放行段
                data["permission"] = top_perms
                new_text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
            else:  # JSONC（带注释）：文本级 patch
                block_json = json.dumps(block, ensure_ascii=False)
                new_text, ok = _jsonc_set(text, ("provider", "orch"), block_json)
                if not ok:
                    errs.append("JSONC 就地改写失败：%s" % path)
                    continue
                if top_model:
                    new_text, _ = _jsonc_set(new_text, ("model",),
                                             json.dumps(top_model))
                new_text, _ = _jsonc_set(new_text, ("permission",),
                                         json.dumps(top_perms))
            if os.path.isfile(path):
                shutil.copyfile(path, path + ".bak")
            Path(path).write_bytes(new_text.encode("utf-8"))
        except Exception as e:
            errs.append("%s: %r" % (path, e))
    return "；".join(errs) or None


def _kimi_render(prov, model):
    """渲染 kimi-code 的 CodeBee 托管块（顶层键在前，表在后——TOML 语义）。"""
    import re as _re
    ptype = "anthropic" if prov.get("protocol") == "anthropic" else "openai"
    base = prov.get("base_url") or ""
    alias = model or "default"
    pname = (prov.get("name") or "CodeBee").replace("\"", "")
    return (
        "# >>> CodeBee managed (do not edit between markers) >>>\n"
        "defaultProvider = \"orch\"\n"
        "defaultModel = \"%s\"\n"
        "yolo = true\n"
        "defaultPermissionMode = \"yolo\"\n"
        "\n"
        "[providers.orch]\n"
        "type = \"%s\"\n"
        "name = \"%s\"\n"
        "baseUrl = \"%s\"\n"
        "apiKey = \"%s\"\n"
        "\n"
        "[models.\"%s\"]\n"
        "provider = \"orch\"\n"
        "model = \"%s\"\n"
        "maxContextSize = 131072\n"
        "displayName = \"%s · %s\"\n"
        "# <<< CodeBee managed <<<\n"
        % (alias, ptype, pname, base,
           (prov.get("api_key") or "").replace("\"", ""),
           alias, alias, pname, alias))


def _sync_kimi_settings(entry, model, prov):
    """kimi-code 专属：把绑定供应商写进 ~/.kimi-code/config.toml。

    schema 从官方 bundle 反推（2026-09-17）：顶层 defaultProvider/defaultModel/
    yolo/defaultPermissionMode（camelCase）+ [providers.<id>]（type/apiKey/
    baseUrl）+ [models.<别名>]（provider/model/maxContextSize 必填）。无人值守
    要 yolo——否则工具调用逐个要审批，headless 全被拒。文本级托管块（标记注释
    之间）幂等重写，用户自有内容保留在外。"""
    import re as _re
    top_keys = ("defaultProvider", "defaultModel", "yolo", "defaultPermissionMode")
    targets = [os.path.abspath(os.path.expanduser("~/.kimi-code/config.toml"))]
    errs = []
    for path in targets:
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            text = ""
            if os.path.isfile(path):
                text = open(path, encoding="utf-8", errors="replace", newline="").read()
                shutil.copyfile(path, path + ".bak")
            # 移除旧托管块与旧顶层键（防重复/防键落进别的表）
            text = _re.sub(r"# >>> CodeBee managed.*?# <<< CodeBee managed <<<\n?",
                           "", text, flags=_re.S)
            for k in top_keys:
                text = _re.sub(r"(?m)^%s\s*=.*$\n?" % k, "", text)
            body = _re.sub(r"\n+$", "\n", text).lstrip("\n")
            managed = _kimi_render(prov, model)
            has_table = _re.search(r"(?m)^\[", body) is not None
            top = "".join("%s\n" % ln for ln in managed.split("\n")
                          if _re.match(r"^(%s)\s*=" % "|".join(top_keys), ln))
            tables = "\n".join(ln for ln in managed.split("\n")
                               if not _re.match(r"^(%s)\s*=" % "|".join(top_keys), ln))
            if has_table:
                new_text = top + "\n" + body + "\n" + tables
            else:
                new_text = (body + "\n" if body else "") + managed
            Path(path).write_bytes(new_text.encode("utf-8"))
        except Exception as e:
            errs.append("%s: %r" % (path, e))
    return "；".join(errs) or None


def sync_cli_config_now(agent_id):
    """运行期自愈：CLI 本体没配置（如 kimi「No model configured」）→ 立即把
    绑定注入其自家配置，换将/下轮即可用。返回给日志的备注（空=无事发生）。"""
    try:
        from . import modelhub
        entry = next((a for a in catalog.load() if a.get("id") == agent_id), None)
        if not entry:
            return ""
        b = modelhub.bindings().get(agent_id) or {}
        note = _sync_agent_injection(entry, b)
        if note:
            return "已自动注入 %s 配置：%s" % (agent_id, note)
        return ""
    except Exception as e:
        return "自动注入失败: %r" % e


# 打开前专属注入通道：{agent_id: (可注入协议, 注入器)}。交互 TUI 脱离编排链路，
# 只认自家配置文件里的凭据，编排降级给的 env（ORCH_API_KEY 等）对它们无效。
_AGENT_INJECTORS = {
    "claude-code": (("anthropic",), _sync_claude_settings),
    "opencode": (("anthropic", "openai"), _sync_opencode_settings),
    "qwencode": (("openai",), _sync_qwen_settings),
    "kimi-code": (("openai", "anthropic"), _sync_kimi_settings),
}

# 无专属注入通道的专有协议 CLI：env 注入大概率无效，打开时明确告知而非静默废
# （mimo 系 opencode 衍生但配置路径未实证，先按提示类；grok 吃 XAI_API_KEY 但
# 无端点 env 可指中转，openai 协议供应商也用不上）
_NO_CHANNEL_HINT = ("grok-build", "pi", "mimo-code")


def _sync_agent_injection(entry, binding):
    """打开前把绑定链里第一个可注入供应商落进 CLI 自家配置。与编排降级链解耦：
    协议不匹配不降级（claude 拿 openai 的 key 等于没 key）。返回给用户看的提示
    （成功注入 / 不可用原因），None=该 CLI 无需处理。"""
    from . import modelhub
    spec = _AGENT_INJECTORS.get(entry["id"])
    if spec:
        protocols, injector = spec
        pick, note = modelhub.launch_pick(entry["id"], protocols)
        if pick:
            prov = pick["provider"]
            err = injector(entry, pick["model"], prov)
            if err:
                return "%s 凭据同步失败：%s（打开后可能需在其自带界面登录）" % (
                    entry.get("name", entry["id"]), err)
            return "已注入 %s（%s · %s）" % (prov.get("name") or "供应商",
                                            prov.get("protocol"), prov.get("base_url", ""))
        return note
    if entry["id"] in _NO_CHANNEL_HINT:
        if binding.get("env"):
            return "已按绑定注入 env，但该 CLI 未必认 CodeBee 的凭据通道，打开后若要求登录请在其界面内登录"
        return "未绑定可用供应商：打开后需在其自带界面登录；要打开即用请到「CLI 绑定」页绑定"
    return None


def _sync_launch_model(entry, binding):
    """打开前把「CLI 绑定」页选中的模型落到该 CLI 自己的配置文件——保证
    交互/网页版启动即选中绑定模型（绑定页是运行时模型唯一真源，目录页的
    「默认模型」只是手动快照，会滞后）。dsh 额外同步端点与 models 目录；
    codex 额外落 provider 段（交互 TUI 只认 config.toml，不认编排的 -c 覆盖
    与 ORCH_API_KEY env）。返回给用户看的同步笔记列表。"""
    notes = []
    model = (binding.get("model") or "").strip()
    prov = binding.get("provider") or {}
    fmt = (entry.get("config") or {}).get("format")
    is_dsh = entry["id"] in ("deepseek-harness", "dsh")
    is_codex = entry["id"] in ("codex-cli", "codex")
    cp = binding.get("codex_provider")
    if model and fmt in _WRITABLE_FORMATS:
        w = write_model(entry, model)
        notes.append("模型已同步为 %s" % w["model"] if w.get("ok")
                     else "模型同步失败：%s" % w.get("error"))
    elif model and not is_dsh and entry["id"] not in _AGENT_INJECTORS:
        # 有专属注入通道的 CLI 由 _sync_agent_injection 负责落模型（含 jsonc），
        # 不再报「只读配置」以免与注入成功的提示互相矛盾
        notes.append("该工具模型为只读配置，按其现有配置打开（绑定模型 %s 未自动写入）" % model)
    if is_dsh and model and prov.get("base_url"):
        err = _sync_dsh_settings(entry, model, prov["base_url"])
        notes.append("dsh 端点已同步为 %s" % prov["base_url"] if not err
                     else "dsh 端点同步失败：%s" % err)
    elif is_dsh and not model:
        _fixed, note = _dsh_selfcheck_model(entry)
        if note:
            notes.append(note)
    if is_codex and cp:
        err = _sync_codex_settings(entry, model, cp)
        notes.append("codex 供应商已同步为 %s" % cp.get("base_url", "") if not err
                     else "codex 供应商同步失败：%s" % err)
    inj = _sync_agent_injection(entry, binding)
    if inj:
        notes.append(inj)
    return notes


def launch(entry, open_browser=True):
    """一键打开：web 类后台起服务并自动开浏览器；console 类新开终端窗口跑交互 TUI。

    打开前把「CLI 绑定」页的模型落盘到该 CLI 配置文件（dsh 连端点与 models
    目录一起同步），并注入绑定密钥 env——保证打开即选中可用模型。"""
    if not detect_entry(entry).get("installed"):
        return {"ok": False, "error": "未安装，无法打开"}
    launch = entry.get("launch") or {}
    cmd = (launch.get("command") or "").strip()
    if not cmd:
        return {"ok": False, "error": "未配置打开命令（可在 data/catalog.json 补 launch 字段）"}
    is_dsh = entry["id"] in ("deepseek-harness", "dsh")
    try:
        from . import modelhub
        binding = modelhub.resolve_binding(entry["id"]) or {}
    except Exception:
        binding = {}
    env = os.environ.copy()
    env.update(binding.get("env") or {})
    notes = _sync_launch_model(entry, binding)
    if is_dsh and not (binding.get("env") or {}) and not _dsh_key_present():
        notes.append("未发现 dsh 密钥：打开后可能需在 dsh 内登录配置；"
                     "要打开即用，请到「CLI 绑定」页给 DeepSeek Harness 绑定 openai 协议供应商")
    name = entry.get("name", entry["id"])
    kind = (launch.get("kind") or "console").lower()
    extra = ("；".join(notes)) if notes else ""

    if kind == "web":
        try:
            port = int(launch.get("port") or 0)
        except (TypeError, ValueError):
            port = 0
        if port <= 0:
            return {"ok": False, "error": "web 类打开必须配置固定端口（launch.port）"}
        bare = "http://127.0.0.1:%d" % port
        log_path = _launch_log_path(entry)
        if not log_path:
            return {"ok": False, "error": "启动日志路径不可信，已拒绝打开"}
        if _port_open(port):
            # 已在运行：新实例抢不到端口、新 token 也拿不到——从上次启动日志
            # 恢复带 token 的信任 URL（dsh web 有 /?token=... 围栏，裸开是 401）。
            # 模型同步照做：正在跑的实例不重启，改的是它下次生效的配置
            url = _best_url(log_path, port) or bare
            if open_browser:
                webbrowser.open(url)
            msg = "服务已在运行，已打开 " + url
            if extra:
                msg += "（" + extra + "）"
            return {"ok": True, "kind": "web", "url": url, "message": msg}
        # 后台起服务：无窗口、不阻塞请求；子进程输出重定向到启动日志（cmd 层
        # 重定向，路径含空格才加引号）——web 类普遍会在启动行打印带 token 的
        # 信任 URL（如 dsh web），就绪线程从日志提取后再开浏览器
        ls = str(log_path)
        spawn_cmd = "%s > %s 2>&1" % (cmd, ('"%s"' % ls) if " " in ls else ls)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            # 重定向语法 sh 与 cmd 通用，只换解释器；start_new_session 让子服务
            # 脱离本服务进程组（杀树/退出互不牵连）
            shell = ["cmd", "/c"] if os.name == "nt" else ["/bin/sh", "-c"]
            subprocess.Popen(shell + [spawn_cmd], cwd=str(paths.ROOT), env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
                             start_new_session=(os.name != "nt"))
        except Exception as e:
            return {"ok": False, "error": "无法启动服务: %r" % e}
        if open_browser:
            threading.Thread(target=_open_when_ready, args=(port, bare, log_path),
                             name="launch-wait-%d" % port, daemon=True).start()
        return {"ok": True, "kind": "web", "url": bare,
                "message": "%s 正在启动，就绪后浏览器会自动打开（%s）%s"
                           % (name, bare, ("；" + extra) if extra else "")}

    if sys.platform == "darwin":
        # Terminal.app 新开窗口跑交互 TUI；do script 的命令串常驻窗口，等价
        # Windows 的 cmd /k（CLI 退出后窗口保留，报错不至于一闪而过）。
        # 两层转义各管各的：shlex.quote 管 shell 层（cd 路径的引号），
        # 反斜杠/双引号替换管 AppleScript 字符串层。
        shell_cmd = "cd %s && %s" % (shlex.quote(str(paths.ROOT)), cmd)
        asc = 'tell application "Terminal" to do script ' + \
              '"' + shell_cmd.replace("\\", "\\\\").replace('"', '\\"') + '"'
        try:
            subprocess.Popen(["osascript", "-e", asc], cwd=str(paths.ROOT), env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except Exception as e:
            return {"ok": False, "error": "无法打开终端窗口: %r" % e}
        return {"ok": True, "kind": "console", "message": "已在新的终端窗口打开 %s%s"
                % (name, ("（" + extra + "）") if extra else "")}

    if sys.platform != "win32":
        return {"ok": False, "error": "终端窗口拉起暂仅支持 Windows/macOS"}
    # start 为目标命令新开一个可见终端窗口；cmd /k 让 CLI 退出后窗口保留，
    # 报错不至于一闪而过。外层 cmd 用 CREATE_NO_WINDOW 隐藏。
    argv = ["cmd", "/c", "start", "CodeBee %s" % name, "/D", str(paths.ROOT),
            "cmd", "/k", cmd]
    try:
        subprocess.Popen(argv, cwd=str(paths.ROOT), env=env,
                         creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        return {"ok": False, "error": "无法打开终端窗口: %r" % e}
    return {"ok": True, "kind": "console", "message": "已在新的终端窗口打开 %s%s"
            % (name, ("（" + extra + "）") if extra else "")}


def _open_when_ready(port, url, log_path, timeout=30):
    """等 web 服务端口就绪后开浏览器。优先从启动日志提取带 token 的信任 URL
    （端口通了 token 行可能还差几十毫秒才落盘，故就绪后最多再等 6 秒）；
    超时兜底也开——服务可能只是慢，用户手动刷新即可。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_open(port):
            break
        time.sleep(0.5)
    token_deadline = time.time() + 6
    best = None
    while time.time() < token_deadline:
        best = _best_url(log_path, port)
        if best and "token=" in best:
            break
        time.sleep(0.5)
    try:
        webbrowser.open(best or url)
    except Exception:
        pass


# ---------------------------------------------------------------- 安装/升级

def run_mgmt_command(entry, op, cancel_event=None, log_path=None):
    """执行 install/upgrade/uninstall 命令（在任务队列里跑，日志实时落盘）。"""
    if op == "uninstall":
        cmd = catalog.uninstall_command(entry)
        if not cmd:
            return {"ok": False,
                    "error": "无法推导卸载命令：请在 data/catalog.json 的 \"%s\" 里配置 uninstall 字段"
                             % entry["id"]}
    else:
        cmd = entry.get(op)
        if not cmd:
            return {"ok": False,
                    "error": "未配置 %s 命令：请在 data/catalog.json 的 \"%s\" 里补充，或用官方渠道安装"
                             % (op, entry["id"])}
    res = runner.run_process(shell_cmd=cmd, cwd=str(paths.ROOT),
                             timeout=1800, cancel_event=cancel_event, log_path=log_path)
    detect_all(force=True)
    with _LOCK:
        _STATE["versions"].pop(entry["id"], None)
    if res["ok"]:
        # 安装/升级成功即作废该条目的更新检查缓存并后台复检：否则 10 分钟 TTL
        # 内徽章仍显示「有新版本」，诱导同版本重装（重装易撞 EBUSY 文件锁，
        # 2026-09-18 dsh 案）。
        refresh_update_async(entry)
    return {"ok": res["ok"], "exit_code": res["exit_code"], "command": cmd,
            "error": "" if res["ok"] else (res["stderr"][-800:] or "退出码 %s" % res["exit_code"])}


def refresh_update_async(entry):
    """作废单条目的更新检查缓存，并后台立刻复检一次远端最新版本。

    完成的安装/升级调用它，卡片徽章马上脱离旧结论（unknown → 几秒后
    「已是最新/有新版本」），不用等 10 分钟缓存过期。npm view 在后台线程
    跑，不阻塞 mgmt 任务收尾。
    """
    with _LOCK:
        _UPDATE_CACHE.pop(entry["id"], None)
    try:
        threading.Thread(target=check_update, args=(entry,), kwargs={"force": True},
                         name="update-recheck-%s" % entry.get("id"), daemon=True).start()
    except Exception:
        pass


# ---------------------------------------------------------------- 版本检查

_UPDATE_CACHE = {}   # agent_id → (ts, {current, latest, updatable, note})
UPDATE_TTL = 600


def _npm_pkg_name(cmd):
    """从 npm 安装命令里取包名（实现已统一到 catalog.npm_pkg_name）。"""
    return catalog.npm_pkg_name(cmd)


def _ver_tuple(s):
    return [int(x) for x in re.findall(r"\d+", str(s or ""))[:4]]


def check_update(entry, force=False):
    """检查是否有新版本可用。npm 走 npm view；winget 走 winget upgrade 列表；
    其他渠道标记为不支持。结果缓存 10 分钟。"""
    eid = entry["id"]
    if not force:
        cached = _UPDATE_CACHE.get(eid)
        if cached and time.time() - cached[0] < UPDATE_TTL:
            return cached[1]
    current = version_of(entry)
    cur_num = ".".join(str(x) for x in _ver_tuple(current)) or "-"
    result = {"current": current, "latest": None, "updatable": None, "note": ""}

    cmd = entry.get("install") or entry.get("upgrade") or ""
    pkg = _npm_pkg_name(cmd)
    if pkg:
        # Windows 的 npm 是 .cmd 垫片须经 cmd /c；POSIX 直接跑
        npm_view = ["cmd", "/c", "npm", "view", pkg, "version"] if os.name == "nt" \
            else ["npm", "view", pkg, "version"]
        r = runner.run_process(argv=npm_view, timeout=90)
        latest = ""
        if r["ok"]:
            for line in (r["stdout"] or "").splitlines():
                line = line.strip()
                if line and re.match(r"^\d", line):
                    latest = line
        if not latest:
            result["note"] = "查询 npm 失败（网络或 registry 问题）：" + (r["stderr"] or "")[-200:]
        else:
            result["latest"] = latest
            result["updatable"] = bool(_ver_tuple(latest) > _ver_tuple(cur_num))
            if not result["updatable"]:
                result["note"] = "已是最新版本"
    elif os.name == "nt" and "winget" in cmd:
        m = re.search(r"--id\s+([A-Za-z0-9._-]+)", cmd)
        wid = m.group(1) if m else None
        if not wid:
            result["note"] = "无法从命令中解析 winget 包 ID"
        else:
            r = runner.run_process(argv=["cmd", "/c", "winget", "upgrade"], timeout=180)
            if not r["ok"]:
                result["note"] = "winget upgrade 查询失败：" + (r["stderr"] or "")[-200:]
            else:
                hit = [l for l in (r["stdout"] or "").splitlines() if wid.lower() in l.lower()]
                result["updatable"] = bool(hit)
                if hit:
                    result["latest"] = "（winget 有可用更新）"
                else:
                    result["latest"] = cur_num
                    result["note"] = "已是最新版本"
    else:
        result["note"] = "该渠道暂不支持自动检查更新，可直接点升级尝试"

    with _LOCK:
        _UPDATE_CACHE[eid] = (time.time(), result)
    return result


# 「进入智能体目录页自动检查更新」的后台任务状态
_UPDATE_CHECK = {"running": False, "total": 0, "done": 0}


def updates_checking():
    with _LOCK:
        return bool(_UPDATE_CHECK["running"])


def update_info(entry):
    """单个 CLI 的更新检查结果（供管理页卡片展示）。没查过时为 unknown。"""
    with _LOCK:
        cached = _UPDATE_CACHE.get(entry["id"])
    if not cached:
        return {"status": "unknown", "latest": None, "updatable": None,
                "note": "", "checked_at": ""}
    res = cached[1] or {}
    up = res.get("updatable")
    status = "updatable" if up is True else ("current" if up is False else "unsupported")
    return {"status": status, "latest": res.get("latest"), "updatable": up,
            "note": res.get("note") or "",
            "checked_at": time.strftime("%H:%M", time.localtime(cached[0]))}


def _checkable_entries():
    """已安装且配了 install/upgrade 的条目——只有这些才谈得上「是否有新版本」。"""
    detected = detect_all()
    out = []
    for e in catalog.load():
        det = (detected or {}).get(e["id"]) or {}
        if det.get("installed") and (e.get("upgrade") or e.get("install")):
            out.append(e)
    return out


def check_updates_async(force=False):
    """后台逐个检查已安装 CLI 的远端最新版本，返回本次要检查的条目数。

    进入「智能体目录」页时自动触发。单条结果复用 check_update 的 10 分钟缓存，
    所以反复进出页面几乎不产生额外子进程/网络开销；已在跑时不重复起线程。
    """
    with _LOCK:
        if _UPDATE_CHECK["running"]:
            return 0
        entries = _checkable_entries()
        _UPDATE_CHECK.update(running=True, total=len(entries), done=0)

    def _worker():
        try:
            for e in entries:
                try:
                    check_update(e, force=force)
                except Exception:
                    pass
                finally:
                    with _LOCK:
                        _UPDATE_CHECK["done"] += 1
        finally:
            with _LOCK:
                _UPDATE_CHECK["running"] = False

    threading.Thread(target=_worker, name="catalog-update-check", daemon=True).start()
    return len(entries)


def catalog_view():
    """管理页数据：catalog + 检测 + 版本 + 模型 + 编排启用状态。"""
    from . import registry
    entries = catalog.load()
    detected = detect_all(force=False)
    enabled = registry.load_enabled()
    view = []
    for e in entries:
        det = detected.get(e["id"]) or {}
        pref = enabled.get(e["id"]) or {}
        orch_enabled = bool(pref.get("enabled", e.get("default_enabled", False))) if e.get("orch") else False
        view.append({
            "id": e["id"], "name": e.get("name", e["id"]), "note": e.get("note", ""),
            "group": "installed" if det.get("installed") else "installable",
            "installed": det.get("installed", False),
            "detail": det.get("detail", ""),
            "version": version_of(e),
            "config_path": _config_path(e),
            "config_writable": (e.get("config") or {}).get("format") in _WRITABLE_FORMATS,
            "model": read_model(e),
            "orch_kind": (e.get("orch") or {}).get("kind"),
            "orch_enabled": orch_enabled,
            "update": update_info(e),
            "has_install": bool(e.get("install")),
            "has_upgrade": bool(e.get("upgrade")),
            # 卸载命令由 install/upgrade 推导（或 catalog 显式配置），供 UI 确认框展示
            "uninstall_cmd": catalog.uninstall_command(e) if det.get("installed") else None,
            # 一键打开配置（kind=web/console + command）；没配的条目 UI 不出「打开」按钮
            "launch": e.get("launch"),
        })
    return view
