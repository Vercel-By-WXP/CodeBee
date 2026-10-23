# -*- coding: utf-8 -*-
"""Command discovery and safe CLI invocation helpers (standard library only)."""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import time


_BASH_CANDIDATES = [
    r"D:\Git\usr\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
    r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
]
_bash_cache = {"path": None, "checked_at": 0.0}
# 只永久缓存正结果。负结果缓存 60s：服务是长驻进程，用户在服务起跑之后才装
# Git Bash（或刚从 D:\Git 迁到 C:\Program Files\Git）时，进程级永久缓存会让
# claude 一直起不来，只有重启服务才自愈——与 _fallback_probe_cli 里那个
# 「PATH 是启动快照」是同一种病。60s 足够吸收「一次任务里反复 spawn」的探测，
# 又不至于让刚装好的用户一直等下去。
_BASH_NEGATIVE_TTL_S = 60.0


def find_git_bash():
    """Windows 专供：claude 原生 exe 找不到 bash 会拒绝启动，指给 Git Bash。
    macOS/Linux 有系统 bash，claude 自会找到；强行设 CLAUDE_CODE_GIT_BASH_PATH
    反而可能指错（/bin/bash 与 Git Bash 行为有差异），故非 Windows 一律 None。"""
    if os.name != "nt":
        return None
    if _bash_cache["path"]:
        return _bash_cache["path"]
    now = time.monotonic()
    if _bash_cache["checked_at"] and now - _bash_cache["checked_at"] < _BASH_NEGATIVE_TTL_S:
        return None
    _bash_cache["checked_at"] = now
    p = shutil.which("bash.exe") or shutil.which("bash")
    if p:
        _bash_cache["path"] = p
        return p
    for cand in _BASH_CANDIDATES:
        if os.path.isfile(cand):
            _bash_cache["path"] = cand
            return cand
    return None


_CLI_EXTS = (".exe", ".cmd", ".bat")


def _fallback_probe_cli(name):
    """which() 落空时按常见安装位兜底找 CLI，返回全路径或 None。

    服务进程的 PATH 是启动那一刻的快照：CLI 在服务起跑之后才安装/迁移（如
    2026-09-30 claude 转 winget 原生安装）时，快照里没有新目录，which 解析
    不到、裸名直传 Popen 直接 FileNotFoundError，直到重启服务才自愈。这里
    只探测磁盘已知落点，不改 PATH、不缓存（装没装是低频事件，探几次不亏）。
    Mac 同病：curl 脚本装的 uv/aider 落 ~/.local/bin，GUI 启动的服务常吃不到
    这条 PATH；brew 在 Apple Silicon 落 /opt/homebrew/bin。"""
    name = str(name)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        return None   # 带斜杠/参数形态的不是裸命令名，交给原路径逻辑
    # Unit/integration tests may provide Windows-style install roots while
    # running on Linux (the release gate is intentionally Ubuntu-hosted).
    # Treat those explicit roots as a Windows probe request; ordinary POSIX
    # callers still take the native ~/.local/bin path below.
    # The release gate runs on Linux but its Windows contract tests provide
    # explicit Windows install roots. Do not treat USERPROFILE alone as a
    # Windows signal: the HTTP guard uses it for a temporary POSIX home.
    test_windows = (os.environ.get("CODEBEE_RELEASE_TEST_PLATFORM") == "win32")
    windows_roots = ("LOCALAPPDATA", "APPDATA", "ProgramW6432",
                     "ProgramFiles", "ProgramFiles(x86)", "SystemRoot",
                     "NVM_SYMLINK")
    windows_probe = (os.name == "nt" or
                     (test_windows and any(os.environ.get(key)
                                           for key in windows_roots)))
    if not windows_probe:
        for d in (os.path.join(os.path.expanduser("~"), ".local", "bin"),
                  "/opt/homebrew/bin", "/usr/local/bin"):
            p = os.path.join(d, name)
            try:
                if os.path.isfile(p) and os.access(p, os.X_OK):
                    return p
            except OSError:
                pass
        return None
    # An explicit NVM_SYMLINK is the current Node installation, so it must
    # win over stale inherited PATH entries from an older nvm version.
    nvm_link = os.environ.get("NVM_SYMLINK")
    if nvm_link and os.path.isdir(nvm_link):
        for ext in _CLI_EXTS:
            p = os.path.join(nvm_link, name + ext)
            try:
                if os.path.isfile(p):
                    return p
            except OSError:
                pass
    # POSIX release-gate hosts do not make PATHEXT-style shims visible to
    # shutil.which.  When the caller has explicitly supplied Windows install
    # roots, inspect PATH for the same .cmd/.bat names before fixed roots.
    # A release-gate test root must win over the hosted runner's inherited
    # PATH (which normally contains the real C:\\Windows PowerShell path).
    test_system_root = os.environ.get("CODEBEE_RELEASE_TEST_SYSTEM_ROOT")
    if not test_system_root:
        for d in (os.environ.get("PATH") or "").split(os.pathsep):
            if not d:
                continue
            for ext in (".cmd", ".bat", ".exe"):
                p = os.path.join(d, name + ext)
                try:
                    if os.path.isfile(p):
                        return p
                except OSError:
                    pass
    local = os.environ.get("LOCALAPPDATA") or ""
    home = os.path.expanduser("~")
    if test_windows and os.environ.get("USERPROFILE"):
        home = os.environ["USERPROFILE"]
    if home == "~":
        home = os.environ.get("USERPROFILE") or home
    appdata = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
    program_files = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or r"C:\Program Files"
    program_files_x86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    # Tests provide a dedicated root instead of mutating the hosted runner's
    # real SystemRoot; production keeps the native environment fallback.
    system_root = (os.environ.get("CODEBEE_RELEASE_TEST_SYSTEM_ROOT") or
                   os.environ.get("SystemRoot") or r"C:\Windows")
    flat_dirs = (
        os.path.join(local, "Microsoft", "WinGet", "Links"),
        os.path.join(home, ".local", "bin"),
        os.path.join(appdata, "npm"),
        # Node.js 官方 Windows 安装器把 node/npm.cmd 放在这里；服务进程
        # 可能启动于安装前，PATH 快照没有刷新，但磁盘上的命令仍可直接使用。
        os.path.join(program_files, "nodejs"),
        os.path.join(program_files_x86, "nodejs"),
        # Windows PowerShell 系统必在，但独占一条 PATH 目录；流水线/GUI 启动
        # 的服务快照缺这条时，裸名 powershell 起跑即「不是内部或外部命令」
        # （2026-10-16 minimax-code 安装实案）。
        os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0"),
        # MiniMax Code 官方安装器落这里并写注册表用户 PATH，已在跑的进程
        # 不继承；固定落点探到即用，装完当场可检出、可编排。
        os.path.join(home, ".minimax-code"),
    )
    # nvm-windows 的当前 node 目录（NVM_SYMLINK，如 D:\nvm4w\nodejs）：nvm
    # 切版重链后启动的进程 PATH 快照常缺这条，装在里面的 npm 全局 CLI
    # （opencode/mimo 等）全体假「未安装」（2026-10-16 opencode 免费池
    # 服务侧探测落空实案）。
    # A test or service launcher may inject a temporary Windows-style root
    # while the host environment still has a stale machine-level NVM_SYMLINK.
    # Prefer an explicitly existing override, then ignore stale roots.
    if nvm_link and not os.path.isdir(nvm_link):
        nvm_link = None
    if nvm_link:
        flat_dirs += (nvm_link,)
    for d in flat_dirs:
        for ext in _CLI_EXTS:
            p = os.path.join(d, name + ext)
            if os.path.isfile(p):
                return p
    # winget 便携包：Packages\<厂商.Id_*>\name.EXE（目录名带来源哈希，glob 一层）
    pkgs = os.path.join(local, "Microsoft", "WinGet", "Packages")
    try:
        for p in glob.glob(os.path.join(pkgs, "*", name + ".*")):
            if p.lower().endswith(_CLI_EXTS) and os.path.isfile(p):
                return p
    except OSError:
        pass
    return None


def resolve_command(command):
    """命令名 → 可直接 spawn 的 argv 前缀（.cmd/.bat 垫片必须经 cmd /c）。"""
    path = shutil.which(command) or _fallback_probe_cli(command) or command
    low = str(path).lower()
    if low.endswith(".cmd") or low.endswith(".bat"):
        return ["cmd", "/c", path]
    return [path]


def _stable_timeout(value):
    """Keep whole-second budgets exact when passing them to subprocesses."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return value
    nearest = round(numeric)
    if abs(numeric - nearest) < 1e-3:
        return int(nearest)
    return numeric


def redact_command(argv):
    """Return an audit-safe argv snapshot with credential-like values masked."""
    out = []
    hide_next = False
    names = {"--api-key", "--apikey", "--token", "--auth-token", "--password",
             "--secret", "api_key", "api-key", "token", "password"}
    secret_pattern = re.compile(
        r"(?i)(sk-[A-Za-z0-9_-]+|bearer\s+[A-Za-z0-9._-]+|"
        r"(?:api[_ -]?key|token|password|secret)\s*[:=]\s*[^\s]+)")
    for raw in argv or ():
        value = str(raw)
        low = value.lower()
        if hide_next:
            out.append("<redacted>")
            hide_next = False
        elif low in names or any(k in low for k in ("api_key=", "api-key=", "token=", "password=")):
            if "=" in value:
                out.append(value.split("=", 1)[0] + "=<redacted>")
            else:
                out.append(value)
                hide_next = True
        else:
            out.append(secret_pattern.sub("<redacted>", value[:500]))
    return out[:80]


def _orphan_pack_files(workdir):
    """列出缺少同名 .idx 的 pack 文件名（无则空列表）。

    Git 本体扫目录时发现 pack 没有对应 .idx，只打一行 warning 就跳过该包，
    所以 git status/fsck 全都正常；aider 走的 gitpython/gitdb 却按
    pack-*.pack 逐个打开同名 .idx，缺一次就抛 FileNotFoundError，被它记成
    「is your git repo corrupted?」——2026-09-22 mo-so 实案：pack-a52e675e
    .idx 缺失，aider 换将后跑满 14 分钟才失败，报错头还是 prompt toolkit 噪声。
    """
    try:
        probe = subprocess.run(
            ["git", "-C", workdir, "rev-parse", "--git-path", "objects/pack"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    rel = (probe.stdout or "").strip()
    if probe.returncode != 0 or not rel:
        return []
    pack_dir = rel if os.path.isabs(rel) else os.path.join(workdir, rel)
    try:
        names = set(os.listdir(pack_dir))
    except OSError:
        return []
    return sorted(n for n in names
                  if n.startswith("pack-") and n.endswith(".pack")
                  and (n[:-len(".pack")] + ".idx") not in names)


def _git_repo_issue(workdir):
    """返回工作目录 Git 仓库的可读性问题；无 Git 仓库时返回空串。

    Aider 会在启动后自行读取索引。索引包缺失时它通常先写入 .gitignore，
    再在深层调用中才报错，用户会误以为任务卡住；提前做一次廉价预检能把
    错误变成可操作的任务失败信息。
    """
    if not workdir or not os.path.isdir(workdir):
        return ""
    git_dir = os.path.join(workdir, ".git")
    if not os.path.exists(git_dir):
        return ""
    try:
        probe = subprocess.run(
            ["git", "-C", workdir, "rev-parse", "--git-dir"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "Git 命令不可用，无法读取仓库；请安装 Git 或改用非 Aider 实现器"
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout or "仓库元数据不可读").strip()
        return "Git 仓库不可读：%s；请先执行 git fsck/恢复 .git 对象后重试" % detail[-300:]
    try:
        probe = subprocess.run(
            ["git", "-C", workdir, "status", "--porcelain", "--untracked-files=no"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=8, check=False,
        )
    except subprocess.TimeoutExpired:
        return "Git 仓库检查超时；请先执行 git status/git fsck 修复后重试"
    except OSError:
        return "Git 命令不可用，无法读取仓库；请安装 Git 或改用非 Aider 实现器"
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout or "仓库对象或索引不可读").strip()
        return "Git 仓库损坏或对象缺失：%s；请先执行 git fsck 并恢复仓库后重试" % detail[-300:]
    # git status 对「pack 缺 .idx」不敏感（只 warning 跳过该包），必须单独查：
    # aider 的 gitdb 按 pack-*.pack 逐个开同名 .idx，缺一个就整步失败。
    orphans = _orphan_pack_files(workdir)
    if orphans:
        return ("Git 仓库 pack 索引缺失：%s 没有同名 .idx（git 本体只警告跳过，"
                "Aider 的 gitdb 会直接报「Unable to list files in git repo」）。"
                "修复：在 %s 目录对每个 pack 执行 `git index-pack <pack文件>` "
                "重建索引，或临时改用 Codex/Claude 实现器。"
                % (", ".join(orphans[:3]), os.path.join(".git", "objects", "pack")))
    return ""


def _npm_shim_bypass(argv):
    """generic 类 CLI 把提示词嵌进 argv；经 cmd /c 重解析时引号/特殊字符会把
    提示词截烂（kimi 实测：模型只看到 UTF-8 须知、任务本体整段丢失）。识别
    标准 npm .cmd 垫片（%_prog% + %dp0% 目标脚本），改 node 直启绕开 cmd。
    识别不出标准形态原样返回。"""
    if len(argv) >= 3 and argv[0] == "cmd" and argv[1] == "/c" \
            and str(argv[2]).lower().endswith((".cmd", ".bat")):
        shim = argv[2]
        try:
            with open(shim, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except Exception:
            return argv
        m = re.search(r'%_prog%"\s+"?%dp0%(\\[^"\n]+?\.(?:mjs|js))"?', text)
        if not m:
            return argv
        script = os.path.normpath(
            os.path.join(os.path.dirname(shim), m.group(1).lstrip("\\")))
        node = os.path.join(os.path.dirname(shim), "node.exe")
        return [node if os.path.isfile(node) else "node", script] + list(argv[3:])
    return argv


