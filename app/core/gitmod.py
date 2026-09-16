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
