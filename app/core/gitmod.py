# -*- coding: utf-8 -*-
"""Git 仓库探测与任务版本检出（新建任务的「代码版本」选项）。

设计约束（对齐质量闸门原则：显式意图不容静默降级）：
- 探测只读：/api/git/info 只跑 rev-parse / status / log，不改仓库任何东西。
- 检出不破坏用户现场：不直接在所选版本上 detached checkout，而是从它创建
  任务分支 tutti/<task-id> 再检出——原分支指针不动，任务产物落在任务分支上。
- 工作区不必干净也能检出：未提交改动先 git stash 原样收起（_attachments/
  的附件除外，那是同一任务刚上传的素材），收尾切回后 apply 还原——开发仓库
  永远有并行改动，硬拒绝等于代码版本隔离不可用。stash 条目按 tutti-stash-*
  标记，还原失败时保留在 stash 列表里可手工找回，绝不静默丢弃。
"""
from __future__ import annotations

import os
import re

from . import paths as paths_mod
from . import runner

# 版本引用白名单：分支名/标签/短哈希及 rev^0、rev~2 组合，禁止以 - 开头
# （argv 传参本身不经过 shell，这里主要挡参数注入与 pathspec 歧义）
_REV_RE = re.compile(r"^[A-Za-z0-9_][\w./\u4e00-\u9fff~^\-]*$")


def valid_rev(rev):
    s = str(rev or "")
    return bool(s) and len(s) <= 200 and bool(_REV_RE.match(s))


def _git(workdir, *args, timeout=20):
    r = runner.run_process(argv=["git", *args], cwd=str(workdir), timeout=timeout)
    return r


def _attach_entry(line):
    """status --porcelain 的一行是否为任务附件目录下的未跟踪项（不算脏改动）。"""
    if not line.startswith("?? "):
        return False
    p = line[3:]
    return p.startswith("_attachments/") or p.startswith('"_attachments/')


def repo_info(workdir):
    """探测目录是否为 git 仓库，返回 UI 下拉所需信息。非仓库返回 {"repo": False}。"""
    r = _git(workdir, "rev-parse", "--is-inside-work-tree")
    if not r["ok"] or r["stdout"].strip() != "true":
        return {"repo": False}
    head = _git(workdir, "rev-parse", "--short", "HEAD")
    branch = _git(workdir, "rev-parse", "--abbrev-ref", "HEAD")
    st = _git(workdir, "status", "--porcelain")
    dirty = [ln for ln in (st["stdout"] or "").splitlines()
             if ln.strip() and not _attach_entry(ln.strip())] if st["ok"] else []
    branches = tags = []
    rb = _git(workdir, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    if rb["ok"]:
        branches = [x for x in (l.strip() for l in rb["stdout"].splitlines()) if x][:200]
    rt = _git(workdir, "tag", "--sort=-creatordate")
    if rt["ok"]:
        tags = [x for x in (l.strip() for l in rt["stdout"].splitlines()) if x][:200]
    recent = []
    rl = _git(workdir, "log", "--oneline", "-n", "30", "--format=%h\x01%s")
    if rl["ok"]:
        for ln in (rl["stdout"] or "").splitlines()[:30]:
            h, _, subj = ln.partition("\x01")
            if h.strip():
                recent.append({"hash": h.strip(), "subject": subj.strip()[:120]})
    br = (branch["stdout"] or "").strip() if branch["ok"] else ""
    return {
        "repo": True,
        "branch": br or "(游离 HEAD)",
        "head": (head["stdout"] or "").strip() if head["ok"] else "",
        "dirty": bool(dirty),
        "dirty_count": len(dirty),
        "branches": branches,
        "tags": tags,
        "recent": recent,
    }


def branch_name(task_id):
    return "tutti/" + str(task_id)


def _stash_dirty(workdir, label):
    """把用户未提交改动（含未跟踪文件）收进 stash，返回 (stash sha, 错误信息)。

    _attachments/ 除外：那是本任务刚上传的附件，必须留在工作目录供注入。
    pathspec 排除走 argv，不经过 shell。失败返回错误——绝不静默带着脏改动切分支。
    """
    m = "tutti-stash-%s" % label
    r = _git(workdir, "stash", "push", "-u", "-m", m,
             "--", ".", ":(exclude)_attachments", timeout=60)
    if not r["ok"]:
        return "", ("暂存未提交改动失败（请手工提交或清理后重试）：%s"
                    % (r["stderr"] or r["stdout"] or "")[-160:])
    top = _git(workdir, "rev-parse", "--quiet", "--verify", "stash@{0}")
    if not top["ok"]:
        return "", "暂存后找不到 stash 条目，请手工检查 git stash list"
    return (top["stdout"] or "").strip(), ""


def _restore_stash(workdir, sha, out):
    """把检出前收起的用户改动 apply 回工作区，成功才 drop 那一条。

    用 apply+drop 而非 pop：pop 在应用冲突时也会丢弃条目，用户的未保存
    改动就真丢了。任何失败都把 stash 原样留在列表里，错误写进 out["restore_error"]。
    """
    a = _git(workdir, "stash", "apply", "--quiet", str(sha), timeout=60)
    if not a["ok"]:
        out["restore_error"] += (
            "还原未提交改动失败（stash %s 已保留，git stash list 可找回；工作区可能有冲突标记）：%.160s"
            % (str(sha)[:12], (a["stderr"] or a["stdout"] or "")))
        return
    lst = _git(workdir, "stash", "list", "--format=%H %gd")
    target = ""
    for ln in (lst["stdout"] or "").splitlines():
        h, _, ref = ln.strip().partition(" ")
        if h == str(sha) and ref:
            target = ref
            break
    if target:
        d = _git(workdir, "stash", "drop", target)
        if not d["ok"]:
            out["restore_error"] += ("清理已还原的 stash 失败（内容无损，可手工 drop）：%s"
                                     % (d["stderr"] or "")[-120:])


def prepare_checkout(workdir, rev, task_id):
    """执行前检出任务分支。返回 (ok, 错误信息, 信息 dict)。

    成功时 dict：{rev, branch, commit, from_branch, base_commit, stash}——写入 run.git
    供 UI 展示；from_branch/base_commit 是收尾 finalize_run 切回的依据；
    stash 非空表示检出前收起了用户未提交改动（收尾时 apply 还原）。
    已有同名任务分支（重试场景）直接切过去继续，不重置——重试语义是续跑，
    把用户可能已提交到任务分支的改动 reset 掉是破坏性的。
    """
    if not valid_rev(rev):
        return False, "非法的版本引用：%s" % str(rev)[:40], None
    info = repo_info(workdir)
    if not info.get("repo"):
        return False, "工作目录不是 git 仓库，无法按所选版本运行", None
    if not info.get("head"):
        return False, "仓库还没有任何提交，请先提交基线", None
    stash_sha = ""
    if info.get("dirty"):
        # 脏工作区不再硬拒绝（开发仓库永远有并行改动）：原样 stash 收起，收尾还原
        stash_sha, err = _stash_dirty(workdir, task_id)
        if err:
            return False, err, None
    rv = _git(workdir, "rev-parse", "--verify", "--quiet", rev)
    if not rv["ok"]:
        if stash_sha:
            out = {"restore_error": ""}
            _restore_stash(workdir, stash_sha, out)
        return False, "仓库中不存在版本「%s」" % rev, None
    br = branch_name(task_id)
    exists = _git(workdir, "rev-parse", "--verify", "--quiet", "refs/heads/" + br)
    if exists["ok"]:
        co = _git(workdir, "checkout", "--quiet", br, timeout=60)
    else:
        co = _git(workdir, "checkout", "-q", "-b", br, rev, timeout=60)
    if not co["ok"]:
        tail = (co["stderr"] or co["stdout"] or "")[-200:]
        if stash_sha:
            out = {"restore_error": ""}
            _restore_stash(workdir, stash_sha, out)
            tail = "检出任务分支失败：%s（已还原你的未提交改动）" % tail \
                if not out["restore_error"] else \
                "检出任务分支失败：%s；还原未提交改动也失败，git stash list 可找回" % tail
        return False, tail, None
    head = _git(workdir, "rev-parse", "--short", "HEAD")
    return True, "", {
        "rev": rev,
        "branch": br,
        "commit": (head["stdout"] or "").strip() if head["ok"] else "",
        "from_branch": info.get("branch") or "",
        "base_commit": info.get("head") or "",
        "stash": stash_sha,
    }


# ---------------------------------------------------------------- 变更快照与收尾

_INSIDE_MAX_FILE_CHARS = 8000   # 单个新文件进 diff 的上限
_INSIDE_MAX_FILE_LINES = 200
_STAT_MAX_LINES = 20000         # +/- 统计的单文件行数封顶（统计不求全文）


def _inside(workdir, target):
    try:
        return os.path.commonpath(
            [os.path.abspath(str(workdir)), os.path.abspath(str(target))]) == os.path.abspath(str(workdir))
    except ValueError:
        return False


def _attach_path(path):
    """-z 格式的未跟踪路径是否落在任务附件目录下（目录整体未跟踪时带尾斜杠）。"""
    p = (path or "").rstrip("/\\")
    return p == "_attachments" or p.startswith("_attachments/")


def _porcelain_entries(workdir):
    """status --porcelain -z → [(xy, path)]；-z 不做引号/八进制转义，中文路径原样。
    重命名条目 (XY new\0old) 只取新路径（展示用足够）。失败返回 None。"""
    r = _git(workdir, "status", "--porcelain", "-z")
    if not r["ok"]:
        return None
    out = []
    fields = r["stdout"].split("\0")
    i = 0
    while i < len(fields):
        f = fields[i]
        if len(f) < 4:
            i += 1
            continue
        xy, path = f[:2], f[3:]
        if xy[0] in ("R", "C"):   # 下一字段是原路径，跳过
            i += 1
        out.append((xy, path))
        i += 1
    return out


def _numstat(workdir):
    """diff HEAD 的行级统计：{path: (add, del)}。+N -M 徽标的数据源。

    --no-renames 关掉改名探测：改名退化为删+增两行，路径是纯单段，解析零特判，
    对统计口径也无损。-z 下每条记录是 "add\\tdel\\tpath\\0"（记录内制表符分隔、
    记录间 NUL 分隔），二进制文件数值为 '-' 按 0 计。
    """
    r = _git(workdir, "diff", "HEAD", "--numstat", "--no-renames", "-z")
    out = {}
    if not r["ok"]:
        return out
    for rec in (r["stdout"] or "").split("\0"):
        if not rec:
            continue
        parts = rec.split("\t")
        if len(parts) < 3:
            continue
        add_s, del_s, path = parts[0], parts[1], "\t".join(parts[2:])
        if not path:
            continue
        try:
            add = int(add_s)
        except ValueError:
            add = 0
        try:
            deln = int(del_s)
        except ValueError:
            deln = 0
        out[path] = (add, deln)
    return out


def _count_lines(fp, cap=50000):
    """按块数行，封顶 cap——只为 +/- 统计，不为内容，超大文件不值得全读。"""
    n = 0
    try:
        with open(fp, "rb") as f:
            while True:
                chunk = f.read(1 << 16)
                if not chunk:
                    break
                n += chunk.count(b"\n")
                if n >= cap:
                    return cap
    except OSError:
        return 0
    return n


def _status_code(xy):
    """porcelain XY → 单字符状态码（前端 GIT_STATUS_LABEL 的键、快照的持久化形态）。
    原始 XY 是两列（如 " M"/"M "/"MM"/"??"），直接透传会让前端查表落空、
    把 "？?" 当文案渲染出来。规则：未跟踪 → "?"；任一列含 D 取 D（删除最醒目）；
    其余取首个非空列（MM/AM 等组合态收拢到首列）。"""
    s = (xy or "").strip()
    if s == "??":
        return "?"
    if not s:
        return "M"
    if "D" in s:
        return "D"
    return s[0]


def collect_changes(workdir, max_diff=30000):
    """只读收集工作区变更（不动 index、不碰分支）。

    返回 {"files": [{"status", "path", "add", "del"}...], "diff": "...",
    "add_total", "del_total"}。diff = 已跟踪改动（git diff HEAD）+ 未跟踪新文件
    逐段拼成 diff 形态——新文件不进 git diff，但恰是智能体产物的大头（新章节、
    新模块），缺了评审与人审都瞎一半。add/del 是行级统计（numstat + 新文件计数），
    供检查器面板的 +N -M 徽标；旧调用方不读这两个字段，向后兼容。
    附件目录不算变更；非 git 仓库/命令失败返回空集合，绝不抛错。
    """
    empty = {"files": [], "diff": "", "add_total": 0, "del_total": 0}
    entries = _porcelain_entries(workdir)
    if entries is None:
        return empty
    files, untracked = [], []
    for xy, path in entries:
        if not path:
            continue
        if xy == "??" and _attach_path(path):
            continue
        files.append({"status": _status_code(xy), "path": path})
        if xy == "??":
            untracked.append(path)
    stats = _numstat(workdir)
    add_total = del_total = 0
    for f in files:
        add, deln = stats.get(f["path"], (0, 0))
        f["add"], f["del"] = add, deln
        add_total += add
        del_total += deln
    parts, budget = [], max_diff
    r = _git(workdir, "diff", "HEAD")
    if r["ok"] and r["stdout"]:
        take = r["stdout"][:budget]
        parts.append(take)
        budget -= len(take)
    for p in untracked:
        if budget <= 0:
            break
        fp = os.path.abspath(os.path.join(str(workdir), p))
        if not os.path.isfile(fp) or not _inside(workdir, fp):
            continue
        lines_n = _count_lines(fp, cap=_STAT_MAX_LINES)
        add_total += lines_n
        try:
            text = runner.read_text_any_enc(fp)[:_INSIDE_MAX_FILE_CHARS]
        except OSError:
            continue
        lines = text.splitlines()[:_INSIDE_MAX_FILE_LINES]
        body = "\n".join("+" + ln for ln in lines)
        if len(text) > _INSIDE_MAX_FILE_CHARS or text.count("\n") >= _INSIDE_MAX_FILE_LINES:
            body += "\n+…（新文件过长，已截断）"
        section = ("diff --git a/%s b/%s\nnew file mode 100644\n"
                   "--- /dev/null\n+++ b/%s\n@@ -0,0 +1,%d @@\n%s\n"
                   % (p, p, p, len(lines), body))[:budget]
        parts.append(section)
        budget -= len(section)
        # 未跟踪文件在 numstat 里没有条目，把行数补进该文件的统计
        for f in files:
            if f["path"] == p:
                f["add"] = lines_n
                break
    return {"files": files[:200], "diff": "".join(parts),
            "add_total": add_total, "del_total": del_total}


def finalize_run(workdir, gitinfo, message):
    """run 结束后收尾：把产物提交到任务分支，再切回原分支/基线提交。

    语义（对齐 Baton/Codeband 的隔离模型；本轮只做「保存现场」，任务分支
    的合并/丢弃留给后续的人工裁决）：
    - 产物必须可找回：哪怕 run 失败/取消，已写盘的半成品也提交到
      tutti/<task-id>（WIP 语义），绝不留在用户工作区里污染原分支；
    - _attachments/ 不提交也不丢：用户上传的共享附件保持未跟踪，
      切分支后仍留在工作目录（未跟踪文件随分支切换保留）；
    - 任何收尾失败都不抛出：run 终态已定，问题记入 restore_error
      由 UI 呈现，绝不因此覆盖 run 的最终结论。
    返回 {commit, restored, restore_error}（由调用方并入 run.git）。
    """
    out = {"commit": "", "restored": False, "restore_error": ""}
    branch = (gitinfo or {}).get("branch") or ""
    if not branch:
        out["restore_error"] = "缺少任务分支信息，跳过收尾"
        return out
    info = repo_info(workdir)
    if not info.get("repo"):
        out["restore_error"] = "工作目录已不是 git 仓库，产物留在原处未提交"
        return out
    if (info.get("branch") or "") != branch:
        out["restore_error"] = ("工作区已不在任务分支 %s（当前 %s），不自动提交"
                                % (branch, info.get("branch") or "?"))
        return out
    # 产物提交（_attachments 除外：保持未跟踪，随分支切换保留在工作区）
    if info.get("dirty"):
        a = _git(workdir, "add", "-A")
        if not a["ok"]:
            out["restore_error"] = "git add 失败：%s" % (a["stderr"] or "")[-160:]
            return out
        staged_att = _git(workdir, "diff", "--cached", "--name-only", "--", "_attachments")
        if staged_att["ok"] and (staged_att["stdout"] or "").strip():
            rst = _git(workdir, "reset", "-q", "--", "_attachments")
            if not rst["ok"]:
                out["restore_error"] = "剔除附件暂存失败：%s" % (rst["stderr"] or "")[-160:]
                return out
        staged = _git(workdir, "diff", "--cached", "--name-only")
        if staged["ok"] and (staged["stdout"] or "").strip():
            # 一次性身份配置：用户仓库可能没配 user.name/email，缺身份不应让提交失败
            c = _git(workdir, "-c", "user.name=Tutti", "-c", "user.email=tutti@orchestra.local",
                     "commit", "-m", str(message)[:200], "--quiet")
            if not c["ok"]:
                out["restore_error"] = "提交任务分支失败：%s" % (c["stderr"] or "")[-160:]
                return out
            h = _git(workdir, "rev-parse", "--short", "HEAD")
            out["commit"] = (h["stdout"] or "").strip() if h["ok"] else ""
    # 切回原分支；原为游离 HEAD 时回到基线提交（同样游离）
    back = (gitinfo or {}).get("from_branch") or ""
    base = (gitinfo or {}).get("base_commit") or ""
    target = back if (back and back != "(游离 HEAD)" and back != branch) else base
    if target:
        co = _git(workdir, "checkout", "--quiet", target, timeout=60)
        if not co["ok"]:
            out["restore_error"] += "切回 %s 失败：%s" % (target, (co["stderr"] or "")[-160:])
            return out
    out["restored"] = True
    # 检出前收起的用户未提交改动，回到原分支后原样还原
    stash = (gitinfo or {}).get("stash") or ""
    if stash:
        _restore_stash(workdir, stash, out)
    return out


# ---------------------------------------------------------------- 任务分支裁决（人审出口）

def merge_task_branch(workdir, task):
    """把任务分支合并回原分支（人审后的「采纳」出口）。

    守卫（显式失败，不静默降级）：
    - 任务正在跑 → 拒绝（分支可能还要追加提交）；
    - 仓库缺失/分支不存在 → 拒绝；
    - 工作区仍在任务分支上 → 拒绝（说明收尾出错，先处理 restore_error）；
    - 工作区不在原分支（用户切去了别处）→ 拒绝，合并目标必须明确；
    - 工作区不干净 → 先 stash 原样收起（附件除外），合并在干净树上进行，
      结束（含冲突回滚）后原样还原——与检出的暂存语义一致。
    合并冲突 → 立即 git merge --abort 回滚，把冲突留给用户手工处理。
    返回 (ok, 错误信息, 信息 dict)；合并成功但还原 stash 失败时，
    错误放进 info["restore_error"]（stash 已保留，可手工找回）。
    """
    def refuse(msg):
        return False, msg, None

    if (task or {}).get("status") in ("queued", "running"):
        return refuse("任务正在运行，等结束再裁决任务分支")
    tid = (task or {}).get("id") or ""
    wd = str(workdir or "")
    info = repo_info(wd)
    if not info.get("repo"):
        return refuse("工作目录不是 git 仓库")
    br = branch_name(tid)
    if not _git(wd, "rev-parse", "--verify", "--quiet", "refs/heads/" + br)["ok"]:
        return refuse("任务分支 %s 不存在（可能已被合并或丢弃）" % br)
    cur = info.get("branch") or ""
    if cur == br:
        return refuse("工作区仍停在任务分支上（上一轮收尾出错），请先处理 run.git.restore_error")
    if cur == "(游离 HEAD)":
        return refuse("工作区处于游离 HEAD，无法作为合并目标；请先检出原分支")
    # 脏工作区不再拒绝合并：暂存用户的未提交改动，合并在干净树上进行，
    # 结束后原样还原（与检出的 stash 语义一致）
    stash_sha = ""
    if info.get("dirty"):
        stash_sha, err = _stash_dirty(wd, "merge-" + tid)
        if err:
            return refuse(err)
    # 合并目标 = 任务分支的创建基线分支（来自 run.git.from_branch）；
    # 查不到基线绝不猜「当前分支」——用户切到哪就合进哪等于无守卫。
    latest = _latest_run_git(tid) or {}
    origin = latest.get("from_branch") or ""
    if not origin:
        if stash_sha:
            out = {"restore_error": ""}
            _restore_stash(wd, stash_sha, out)
        return refuse("无法确定任务分支的基线分支（找不到带检出信息的运行记录），请手工合并")
    if cur != origin:
        if stash_sha:
            out = {"restore_error": ""}
            _restore_stash(wd, stash_sha, out)
        return refuse("工作区当前在 %s，而任务分支的基线是 %s；请先切回 %s 再合并"
                      % (cur, origin, origin))
    ahead = _git(wd, "rev-list", "--count", "%s..%s" % (origin, br))
    n_ahead = int((ahead["stdout"] or "0").strip() or 0) if ahead["ok"] else 0
    if n_ahead <= 0:
        if stash_sha:
            out = {"restore_error": ""}
            _restore_stash(wd, stash_sha, out)
        return refuse("任务分支没有领先基线的提交（产物为空），无需合并；可直接丢弃")
    m = _git(wd, "-c", "user.name=Tutti", "-c", "user.email=tutti@orchestra.local",
             "merge", "--no-ff", br, "-m",
             "tutti: 合并任务分支 %s（%s）" % (tid, (task or {}).get("title") or "")[:200])
    if not m["ok"]:
        ab = _git(wd, "merge", "--abort")
        tail = (m["stderr"] or m["stdout"] or "")[-200:]
        msg = ""
        if ab["ok"]:
            msg = "合并冲突，已回滚到合并前状态；请手工解决后自行合并：%s" % tail
        else:
            msg = "合并失败且回滚失败，工作区可能残留冲突标记：%s" % tail
        if stash_sha:
            out = {"restore_error": ""}
            _restore_stash(wd, stash_sha, out)
            if out["restore_error"]:
                msg += "；另：还原你的未提交改动失败，git stash list 可找回"
        return refuse(msg)
    h = _git(wd, "rev-parse", "--short", "HEAD")
    info_out = {
        "merged_into": origin,
        "commits": n_ahead,
        "commit": (h["stdout"] or "").strip() if h["ok"] else "",
    }
    if stash_sha:
        out = {"restore_error": ""}
        _restore_stash(wd, stash_sha, out)
        if out["restore_error"]:
            info_out["restore_error"] = out["restore_error"]
    return True, "", info_out


def discard_task_branch(workdir, task):
    """丢弃任务分支（人审后的「否决」出口，不可恢复）。

    守卫同 merge（活跃任务/仓库缺失/分支不存在拒绝）；若工作区恰好停在
    任务分支上且干净（收尾出错的遗留现场），先切回基线再删分支。
    返回 (ok, 错误信息)。
    """
    def refuse(msg):
        return False, msg

    if (task or {}).get("status") in ("queued", "running"):
        return refuse("任务正在运行，等结束再裁决任务分支")
    tid = (task or {}).get("id") or ""
    wd = str(workdir or "")
    info = repo_info(wd)
    if not info.get("repo"):
        return refuse("工作目录不是 git 仓库")
    br = branch_name(tid)
    if not _git(wd, "rev-parse", "--verify", "--quiet", "refs/heads/" + br)["ok"]:
        return refuse("任务分支 %s 不存在（可能已被合并或丢弃）" % br)
    cur = info.get("branch") or ""
    if cur == br:
        if info.get("dirty"):
            return refuse("工作区停在任务分支上且有未提交改动，请先处理再丢弃")
        latest = _latest_run_git(tid) or {}
        back = latest.get("from_branch") or ""
        base = latest.get("base_commit") or ""
        target = back if (back and back != "(游离 HEAD)" and back != br) else base
        if not target:
            return refuse("工作区停在任务分支上且找不到可切回的基线，请手工切走后再丢弃")
        co = _git(wd, "checkout", "--quiet", target, timeout=60)
        if not co["ok"]:
            return refuse("切回 %s 失败：%s" % (target, (co["stderr"] or "")[-160:]))
    d = _git(wd, "branch", "-D", br)
    if not d["ok"]:
        return refuse("删除任务分支失败：%s" % (d["stderr"] or "")[-160:])
    return True, ""


def _latest_run_git(task_id):
    """该任务最近一次 run 的 git 上下文（from_branch/base_commit 以它为准）。

    放在 gitmod 而非 store 是为了避免 store→gitmod 反向依赖；读 run.json
    走磁盘（裁决是低频人工操作，无需常驻内存索引）。
    """
    import json as _json
    best, best_id = None, ""
    try:
        for rj in paths_mod.RUNS_DIR.glob("*/run.json"):
            try:
                rdata = _json.loads(rj.read_text(encoding="utf-8"))
            except Exception:
                continue
            rid = str(rdata.get("id") or "")
            if rdata.get("task_id") == task_id and rdata.get("git") \
                    and rid > best_id:
                best, best_id = rdata.get("git"), rid
    except Exception:
        return None
    return best


# ---------------------------------------------------------------- GIT 工作台
# 详情页「版本」页签的完整 Git 操作面：状态聚合（只读）+ 白名单写操作。
# 与隔离链（prepare_checkout/finalize/merge/discard）互不改动；写操作的
# 「任务运行中拒绝」守卫在路由层（那里才拿得到 task.status），这里只管仓库。

_WB_MAX_FILES = 300        # 单组文件列表上限（超大仓库不拖死轮询）
_WB_DIFF_MAX_CHARS = 200000  # 单文件 diff 文本上限


def _safe_relpath(path):
    """工作台路径参数白名单：仓库内相对路径。挡绝对路径、.. 上跳、
    以 - 开头（git 会当选项解析）；通过返回规范化后的正斜杠路径。"""
    s = str(path or "").replace("\\", "/").strip()
    if not s or s.startswith("-") or len(s) > 500:
        return ""
    if s.startswith("/") or re.match(r"^[A-Za-z]:", s):
        return ""
    parts = [x for x in s.split("/") if x not in ("", ".")]
    if any(x == ".." for x in parts):
        return ""
    return "/".join(parts)


def _porcelain_grouped(workdir):
    """status --porcelain -z → (staged, unstaged, untracked) 三组 [{status,path}]。

    与 _porcelain_entries 的区别：按 XY 两列拆「已暂存/未暂存」——工作台的
    stage/unstage 语义需要知道改动落在 index 还是工作区。X 列非空非 ? → 有
    暂存改动；Y 列非空 → 有未暂存改动（同一文件可同时进两组，同 git UI 惯例）。
    """
    staged, unstaged, untracked = [], [], []
    entries = _porcelain_entries(workdir)
    if entries is None:
        return None
    for xy, path in entries:
        if not path:
            continue
        if _attach_path(path):
            continue   # 任务附件目录不算变更（与 collect_changes 同口径）
        x, y = xy[0], xy[1]
        if xy == "??":
            untracked.append({"status": "?", "path": path})
            continue
        if x not in (" ", "?"):
            staged.append({"status": _status_code(x + " "), "path": path})
        if y not in (" ", "?"):
            unstaged.append({"status": _status_code(" " + y), "path": path})
    return staged, unstaged, untracked


def workbench_status(workdir):
    """只读聚合工作台全貌：分支/HEAD/远程、三组变更文件、ahead/behind、stash。

    非 git 仓库返回 {"repo": False}；任何子命令失败只降级对应字段，绝不抛错。
    """
    r = _git(workdir, "rev-parse", "--is-inside-work-tree")
    if not r["ok"] or r["stdout"].strip() != "true":
        return {"repo": False}
    out = {"repo": True}
    head = _git(workdir, "rev-parse", "--short", "HEAD")
    out["head"] = (head["stdout"] or "").strip() if head["ok"] else ""
    br = _git(workdir, "rev-parse", "--abbrev-ref", "HEAD")
    cur = (br["stdout"] or "").strip() if br["ok"] else ""
    out["branch"] = cur or "(游离 HEAD)"
    out["detached"] = cur == "HEAD"
    groups = _porcelain_grouped(workdir)
    if groups is None:
        groups = ([], [], [])
    staged, unstaged, untracked = groups
    out["staged"], out["unstaged"], out["untracked"] = (
        staged[:_WB_MAX_FILES], unstaged[:_WB_MAX_FILES], untracked[:_WB_MAX_FILES])
    out["truncated"] = any(len(g) > _WB_MAX_FILES for g in groups)
    # +/- 行级统计：git diff HEAD（工作区 vs HEAD）已含暂存+未暂存的全貌，
    # 同一文件在两组都出现时共用同一份统计（不可两份相加——会重复计数）
    stats = {}
    rn = _git(workdir, "diff", "HEAD", "--numstat", "--no-renames", "-z")
    if rn["ok"]:
        for rec in (rn["stdout"] or "").split("\0"):
            parts = rec.split("\t")
            if len(parts) < 3 or not parts[2]:
                continue
            path = "\t".join(parts[2:])
            try:
                a = int(parts[0])
            except ValueError:
                a = 0
            try:
                d = int(parts[1])
            except ValueError:
                d = 0
            stats[path] = (a, d)
    for g in (staged, unstaged):
        for f in g:
            a, d = stats.get(f["path"], (0, 0))
            f["add"], f["del"] = a, d
    for f in untracked:
        fp = os.path.abspath(os.path.join(str(workdir), f["path"]))
        f["add"] = _count_lines(fp) if os.path.isfile(fp) else 0
        f["del"] = 0
    # 分支列表：本地 + 远程（origin/* 去前缀展示，checkout 时再映射回）
    branches = []
    rb = _git(workdir, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    if rb["ok"]:
        branches = [x for x in (l.strip() for l in rb["stdout"].splitlines()) if x][:200]
    remote_branches = []
    rr = _git(workdir, "for-each-ref", "--format=%(refname:short)", "refs/remotes/")
    if rr["ok"]:
        for x in (l.strip() for l in rr["stdout"].splitlines()):
            if x and not x.endswith("/HEAD"):
                remote_branches.append(x)
        remote_branches = remote_branches[:200]
    out["branches"] = branches
    out["remote_branches"] = remote_branches
    # ahead/behind 与上游：无上游时 upstream 为空，前端据此禁用 pull/push
    out["upstream"] = ""
    out["ahead"] = out["behind"] = 0
    up = _git(workdir, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if up["ok"]:
        out["upstream"] = (up["stdout"] or "").strip()
        ab = _git(workdir, "rev-list", "--left-right", "--count", "HEAD..." + out["upstream"])
        if ab["ok"]:
            toks = (ab["stdout"] or "").split()
            if len(toks) == 2:
                out["ahead"], out["behind"] = int(toks[0]), int(toks[1])
    # 远程名（默认 origin；多远程时取第一个）
    rv = _git(workdir, "remote")
    remotes = [x for x in ((rv["stdout"] or "").split()) if x] if rv["ok"] else []
    out["remotes"] = remotes
    # stash 列表（工作台收起/还原入口）
    st = _git(workdir, "stash", "list", "--format=%gd\x01%s")
    stashes = []
    if st["ok"]:
        for ln in (st["stdout"] or "").splitlines():
            h, _, subj = ln.partition("\x01")
            if h.strip():
                stashes.append({"ref": h.strip(), "subject": subj.strip()[:120]})
    out["stashes"] = stashes[:50]
    # 最近提交（时间线，给「本次修改」一个上下文锚点）
    lg = _git(workdir, "log", "-n", "8", "--format=%h\x01%an\x01%ar\x01%s")
    recent = []
    if lg["ok"]:
        for ln in (lg["stdout"] or "").splitlines():
            toks = ln.split("\x01")
            if len(toks) >= 4 and toks[0].strip():
                recent.append({"hash": toks[0], "author": toks[1],
                               "age": toks[2], "subject": toks[3][:120]})
    out["recent"] = recent
    return out


def file_diff(workdir, path):
    """单文件实时 diff 文本。已跟踪 → git diff HEAD -- path（含暂存区改动，
    即 HEAD 到当前工作区全貌）；未跟踪 → 读文件拼 new-file diff 形态。
    返回 {"diff": str, "untracked": bool}；路径非法/不在仓库内返回 error。"""
    rp = _safe_relpath(path)
    if not rp:
        return {"error": "非法文件路径"}
    fp = os.path.abspath(os.path.join(str(workdir), rp))
    if not _inside(workdir, fp):
        return {"error": "路径越出工作目录"}
    tracked = _git(workdir, "ls-files", "--error-unmatch", "--", rp)["ok"]
    if tracked:
        r = _git(workdir, "diff", "HEAD", "--", rp, timeout=40)
        if not r["ok"]:
            return {"error": (r["stderr"] or "git diff 失败")[-200:]}
        return {"diff": (r["stdout"] or "")[:_WB_DIFF_MAX_CHARS], "untracked": False}
    if not os.path.isfile(fp):
        return {"diff": "", "untracked": True}   # 未跟踪目录（整棵新树）：无单文件 diff
    try:
        text = runner.read_text_any_enc(fp)[:_WB_DIFF_MAX_CHARS]
    except OSError as e:
        return {"error": "读取失败：%s" % e}
    lines = text.splitlines()[:_INSIDE_MAX_FILE_LINES]
    body = "\n".join("+" + ln for ln in lines)
    if len(text) >= _WB_DIFF_MAX_CHARS or text.count("\n") >= _INSIDE_MAX_FILE_LINES:
        body += "\n+…（文件过长，已截断）"
    section = ("diff --git a/%s b/%s\nnew file mode 100644\n"
               "--- /dev/null\n+++ b/%s\n@@ -0,0 +1,%d @@\n%s\n"
               % (rp, rp, rp, len(lines), body))
    return {"diff": section, "untracked": True}


# 写操作白名单：action → 是否需要 confirm（不可恢复类）
_WB_ACTIONS = {"checkout", "fetch", "pull", "push", "stage", "unstage",
               "discard", "delete", "commit", "stage_all", "unstage_all",
               "stash_push", "stash_pop", "stash_drop"}


def _res(r, extra=None):
    """run_process 结果 → (ok, err, data)：错误取 stderr 尾（超时尾巴一并带上）。"""
    if r["ok"]:
        return True, "", (extra or {})
    tail = ((r["stderr"] or "") + (r["stdout"] or "")).strip()[-300:]
    return False, tail or "git 命令失败（exit %s）" % r.get("exit_code"), (extra or {})


def workbench_op(workdir, action, params=None):
    """工作台写操作统一入口。返回 (ok, 错误信息, 附带信息 dict)。

    守卫：action 白名单；path 走 _safe_relpath；分支/提交信息限长。
    丢弃（discard）与删除未跟踪文件（delete）不可恢复，必须 confirm=true。
    stash 系列只认 tutti-stash-* 标记条目——用户自己的 stash 不在工作台露出，
    避免误 pop 撞掉检出链收起的现场。
    """
    params = params or {}
    wd = str(workdir or "")
    if action not in _WB_ACTIONS:
        return False, "不支持的操作：%s" % action, {}
    info = workbench_status(wd)
    if not info.get("repo"):
        return False, "工作目录不是 git 仓库", {}

    def need_path():
        rp = _safe_relpath(params.get("path"))
        if not rp:
            return ""
        if not _inside(wd, os.path.abspath(os.path.join(wd, rp))):
            return ""
        return rp

    if action == "checkout":
        br = str(params.get("branch") or "").strip()
        if not br or len(br) > 200 or br.startswith("-"):
            return False, "分支名非法", {}
        dirty = (info.get("staged") or []) + (info.get("unstaged") or [])
        if dirty:
            return False, "工作区有未提交改动，切分支可能丢失现场：先提交或收起（stash）", {}
        if br in (info.get("branches") or []):
            return _res(_git(wd, "checkout", "--quiet", br, timeout=60))
        # 远程分支 origin/foo → 建本地跟踪分支 foo；已存在同名本地分支则直接切
        if "/" in br and br in (info.get("remote_branches") or []):
            name = br.split("/", 1)[1]
            r = _git(wd, "checkout", "-b", name, "--track", br, timeout=60)
            if not r["ok"]:
                r = _git(wd, "checkout", "--quiet", name, timeout=60)
            return _res(r)
        if not _git(wd, "rev-parse", "--verify", "--quiet", br)["ok"]:
            return False, "分支 %s 不存在" % br, {}
        return _res(_git(wd, "checkout", "--quiet", br, timeout=60))

    if action == "stage":
        rp = need_path()
        if not rp:
            return False, "非法文件路径", {}
        if _attach_path(rp):
            return False, "_attachments/ 是任务附件素材，不进版本库，不能暂存", {}
        return _res(_git(wd, "add", "--", rp, timeout=60))

    if action == "unstage":
        rp = need_path()
        if not rp:
            return False, "非法文件路径", {}
        # 未跟踪 → 直接撤出 index；已跟踪 → 回退 index 到 HEAD（保留工作区改动）
        if _git(wd, "ls-files", "--error-unmatch", "--", rp)["ok"]:
            return _res(_git(wd, "reset", "-q", "--", rp, timeout=60))
        return _res(_git(wd, "rm", "--cached", "-q", "--", rp, timeout=60))

    if action == "discard":
        rp = need_path()
        if not rp:
            return False, "非法文件路径", {}
        if not params.get("confirm"):
            return False, "丢弃未提交改动不可恢复，需要 confirm=true 二次确认", {}
        if _git(wd, "ls-files", "--error-unmatch", "--", rp)["ok"]:
            # checkout HEAD -- path：index 与工作区一起还原到 HEAD（含已暂存改动）
            return _res(_git(wd, "checkout", "HEAD", "--", rp, timeout=60))
        return _res(_git(wd, "clean", "-qfd", "--", rp, timeout=60))

    if action == "delete":
        rp = need_path()
        if not rp:
            return False, "非法文件路径", {}
        if not params.get("confirm"):
            return False, "删除未跟踪文件不可恢复，需要 confirm=true 二次确认", {}
        if _git(wd, "ls-files", "--error-unmatch", "--", rp)["ok"]:
            return False, "该文件已被 git 跟踪，请用丢弃改动还原", {}
        return _res(_git(wd, "clean", "-qfd", "--", rp, timeout=60))

    if action in ("stage_all", "unstage_all"):
        if action == "stage_all":
            r = _git(wd, "add", "-A", timeout=60)
            if r["ok"]:
                _git(wd, "reset", "-q", "--", "_attachments")   # 附件永不进提交
            return _res(r)
        return _res(_git(wd, "reset", "-q", timeout=60))

    if action == "commit":
        msg = str(params.get("message") or "").strip()[:500]
        if not msg:
            return False, "提交信息不能为空", {}
        staged = info.get("staged") or []
        att = [f for f in staged if f["path"] == "_attachments"
               or f["path"].startswith("_attachments/")]
        if att:
            _git(wd, "reset", "-q", "--", "_attachments")
            staged = [f for f in staged if f not in att]
        if not staged:
            return False, "暂存区为空：先暂存要提交的文件", {}
        # 用户手动提交优先用仓库自己的身份；没配置再回落 CodeBee 身份
        cfg = _git(wd, "config", "user.email")
        ident = [] if (cfg["stdout"] or "").strip() else [
            "-c", "user.name=CodeBee", "-c", "user.email=codebee@orchestra.local"]
        r = _git(wd, *ident, "commit", "-m", msg, "--quiet", timeout=60)
        ok, err, data = _res(r)
        if ok:
            h = _git(wd, "rev-parse", "--short", "HEAD")
            data = {"commit": (h["stdout"] or "").strip() if h["ok"] else "",
                    "files": len(staged)}
        return ok, err, data

    if action in ("fetch", "pull", "push"):
        remotes = info.get("remotes") or []
        if not remotes:
            return False, "该仓库没有配置远程（git remote），无法%s" % {
                "fetch": "抓取", "pull": "拉取", "push": "推送"}[action], {}
        remote = str(params.get("remote") or remotes[0]).strip()
        if not remote or remote.startswith("-") or len(remote) > 100:
            return False, "远程名非法", {}
        if action == "fetch":
            return _res(_git(wd, "fetch", "--prune", remote, timeout=120))
        br = info.get("branch") or ""
        if info.get("detached"):
            return False, "游离 HEAD 状态不能%s，请先切换到分支" % {
                "pull": "拉取", "push": "推送"}[action], {}
        if action == "pull":
            if info.get("upstream"):
                r = _git(wd, "pull", "--rebase=false", timeout=120)
            else:
                r = _git(wd, "pull", "--rebase=false", remote, br, timeout=120)
            ok, err, data = _res(r)
            if not ok and ("CONFLICT" in (r["stdout"] or "") + err
                           or "conflict" in err.lower()):
                err += "（存在冲突：请手工解决后提交，或 git merge --abort 回退）"
            return ok, err, data
        # push：有上游推上游；无上游首推建立跟踪
        if info.get("upstream"):
            return _res(_git(wd, "push", timeout=120))
        return _res(_git(wd, "push", "-u", remote, br, timeout=120))

    if action == "stash_push":
        msg = str(params.get("message") or "").strip()[:120]
        m = "tutti-stash-%s" % (msg or "workbench")
        r = _git(wd, "stash", "push", "-u", "-m", m, "--", ".", ":(exclude)_attachments",
                 timeout=90)
        ok, err, data = _res(r)
        if ok and not (info.get("staged") or info.get("unstaged") or info.get("untracked")):
            data = {"noop": True}   # 干净树：git 返回 ok 但没存东西
        return ok, err, data

    if action in ("stash_pop", "stash_drop"):
        ref = str(params.get("ref") or "").strip()
        if not re.match(r"^stash@\{[0-9]+\}$", ref):
            return False, "stash 引用非法", {}
        lst = _git(wd, "stash", "list", "--format=%gd\x01%s")
        found = None
        if lst["ok"]:
            for ln in (lst["stdout"] or "").splitlines():
                h, _, subj = ln.partition("\x01")
                if h.strip() == ref:
                    found = subj.strip()
                    break
        if found is None:
            return False, "stash 条目不存在", {}
        # %s 的形态是 "On master: <message>"/"WIP on ..."，认标记用包含判断
        if "tutti-stash-" not in found:
            return False, "工作台只处理编排台自己收起的 stash（tutti-stash-*），你自己的条目请手工处理", {}
        if action == "stash_pop":
            return _res(_git(wd, "stash", "pop", ref, timeout=90))
        if not params.get("confirm"):
            return False, "删除 stash 条目不可恢复，需要 confirm=true 二次确认", {}
        return _res(_git(wd, "stash", "drop", ref, timeout=60))

    return False, "不支持的操作：%s" % action, {}
