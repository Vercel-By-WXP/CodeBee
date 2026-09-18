#!/usr/bin/env python3
"""原生「选择文件夹」对话框（main.py 的 /api/pick_folder 用）。

Tk 必须活在自家进程的主线程里：HTTP 请求线程里建 root 在 macOS 上会崩，
Windows 上反复建/销毁也不稳，所以隔离成独立进程。ask_directory() 是父端
入口，把本文件拉起为子进程，请求参数（JSON：initial 起始目录 / title 标题）
走 stdin，选中目录以 JSON（{"path": ...}）走 stdout，用户取消回空串；
对话框部分是文件尾的 main()。任何一步失败都以非零码退出，父端据此回
fallback=true 让前端回落网页目录弹框。"""
import json
import os
import subprocess
import sys
from pathlib import Path

# 非 Windows 置 0：POSIX 的 Popen 对非零 creationflags 抛 ValueError（manager 同款守卫）
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_SELF = str(Path(__file__).resolve())


def ask_directory(initial="", title="选择文件夹"):
    """父端入口：拉起子进程弹原生对话框，返回 (path, error, fallback)。

    用户取消时 path 为空串；子进程起不来（机器无 tkinter 等）时 error
    非空且 fallback=True，调用方原样转给前端回落网页目录弹框。"""
    if not Path(__file__).exists():
        return "", "pick_dialog.py 缺失", True
    req = json.dumps({"initial": initial, "title": title}).encode("utf-8")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        if os.name == "nt":
            cp = subprocess.run([sys.executable, _SELF], input=req,
                                capture_output=True, timeout=600, env=env,
                                creationflags=CREATE_NO_WINDOW)
        else:
            cp = subprocess.run([sys.executable, _SELF], input=req,
                                capture_output=True, timeout=600, env=env)
    except Exception as e:
        return "", str(e), True
    if cp.returncode != 0:
        err = (cp.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        return "", (err[-1] if err else "native picker unavailable"), True
    try:
        data = json.loads((cp.stdout or b"").decode("utf-8", "replace") or "{}")
    except Exception:
        return "", "对话框输出无法解析", True
    return str(data.get("path") or ""), "", False


def main():
    req = {}
    try:
        req = json.loads(sys.stdin.read() or "{}")
    except Exception:
        pass
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)   # 别被全屏浏览器盖住
    except Exception:
        pass
    kw = {"title": req.get("title") or "选择文件夹"}
    init = req.get("initial") or ""
    if init and os.path.isdir(init):
        kw["initialdir"] = init
    try:
        path = filedialog.askdirectory(**kw) or ""
    except Exception:
        sys.exit(1)
    sys.stdout.write(json.dumps({"path": path}))
    root.destroy()


if __name__ == "__main__":
    main()
