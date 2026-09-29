# -*- coding: utf-8 -*-
"""发版前清理：删除已合并的残留 worktree 与测试垃圾（每次发版前先跑）。

清理范围（只删确认安全的）：
1. worktree：HEAD 已是 main 祖先的检出目录（工作已合并，纯残留）；
   带未合并分支的保留（防误删在飞工作）。
2. 测试假家目录：/tmp 下的 cb-fakehome-* / cb-kn-ui* / cb-scroll* / cb-probe*。
3. 仓库根 tmp_* 垃圾：tmp_be_keys.json（含密钥，必须删）等明确死件。

绝不触碰：真实 data/、并行代理正在使用的数字命名 worktree、未合并分支。
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)


def git(args, **kw):
    return subprocess.run(["git"] + args, capture_output=True, text=True, **kw)


def is_ancestor(commit):
    return subprocess.run(["git", "merge-base", "--is-ancestor", commit, "main"],
                          capture_output=True).returncode == 0


removed_wt, kept_wt = 0, 0
out = git(["worktree", "list", "--porcelain"]).stdout
cur_wt = None
for line in out.splitlines():
    if line.startswith("worktree "):
        cur_wt = line[9:]
    elif line.startswith("HEAD ") and cur_wt:
        head = line[5:]
        main_wt = ROOT.replace("\\", "/")
        if cur_wt.rstrip("/").replace("\\", "/") == main_wt:
            continue  # 主检出目录不动
        if is_ancestor(head):
            r = git(["worktree", "remove", "--force", cur_wt])
            if r.returncode == 0:
                removed_wt += 1
                print("  已删 worktree:", cur_wt)
            else:
                kept_wt += 1
        else:
            kept_wt += 1
            print("  保留（HEAD 未合并）:", cur_wt)
        cur_wt = None

# 测试假家目录与探针残留（系统临时目录下）
tmp = tempfile.gettempdir()
n_tmp = 0
for name in os.listdir(tmp):
    if name.startswith(("cb-fakehome-", "cb-kn-ui", "cb-scroll", "cb-probe",
                        "cb-shots", "cb-dbg", "cb-gate-dbg")):
        p = os.path.join(tmp, name)
        import shutil
        try:
            shutil.rmtree(p, ignore_errors=True)
            n_tmp += 1
        except OSError:
            pass
if n_tmp:
    print("  已删测试假家/探针目录:", n_tmp)

# 仓库根 tmp_* 明确死件（含密钥的必须删）
for name in ("tmp_be_keys.json", "tmp_be_extract.py", "tmp_wm_nowm.png",
             "tmp_wm_withwm.png"):
    p = os.path.join(ROOT, name)
    if os.path.isfile(p):
        try:
            os.remove(p)
            print("  已删:", name)
        except OSError:
            pass

print("worktree 清理: 删 %d 保留 %d；临时目录清理: %d 项" % (removed_wt, kept_wt, n_tmp))
