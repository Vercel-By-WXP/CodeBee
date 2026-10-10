# -*- coding: utf-8 -*-
"""自动发布：枚举任务待发章节，按护栏顺序发布（P2 护栏层）。

与 manager 的分工：manager 管「一次动作」（连接/建书/发一章），
auto 管「一批章节」——枚举任务成品里的章节文件，减去台账已发章号，
逐章调 manager 发，每章之间隔一段防风控节奏。

护栏（每章发起前复查，发布中途护栏状态变了也拦得住）：
- 每日上限：ledger.today_count >= settings.publish_daily_cap（默认 10）
- 连败退避：ledger.consecutive_failures >= settings.publish_fail_streak（默认 3）
  ——连续失败说明疑似风控/改版，自动发布暂停转人工；
- 幂等：已成功发布的章号跳过（manager 内还有第二道闸）；
- 单飞：同任务同时只有一个自动发布线程。

发布确认闸沿 manager 口径：auto_submit=False（默认）时每章只填好表单，
提交权留给用户在浏览器窗口里人工点——自动发布≠自动直发。
"""
from __future__ import annotations

import os
import threading
import time

PACE_S = 45                  # 章间间隔（防风控节奏），测试里可置 0
IDLE_POLL_S = 2              # 等 manager busy 结束的轮询步长
IDLE_TIMEOUT_S = 420         # 单章最长等待（含浏览器操作与人工确认窗口）
TRANSIENT_RETRY_S = 5        # 连接类瞬断的重试前静默（等浏览器缓过来）

_CAP_DEFAULT = 10
_STREAK_DEFAULT = 3

_running = {}                # task_id → {platform, at, done, total, status, error}
_LOCK = threading.Lock()


def _isdir(p):
    try:
        return os.path.isdir(p)
    except (OSError, ValueError):
        return False


def _book_ready(info):
    return bool(info and (info.get("book_id") or info.get("source") == "manual"))


# ---------------------------------------------------------------- 护栏配置
def _settings():
    from .. import settings
    try:
        return settings.load() or {}
    except Exception:
        return {}


def daily_cap():
    try:
        v = int(_settings().get("publish_daily_cap") or _CAP_DEFAULT)
    except (TypeError, ValueError):
        v = _CAP_DEFAULT
    return max(0, min(50, v))


def fail_streak():
    try:
        v = int(_settings().get("publish_fail_streak") or _STREAK_DEFAULT)
    except (TypeError, ValueError):
        v = _STREAK_DEFAULT
    return max(1, min(10, v))


def guards(task_id, platform):
    """两道护栏：每日上限 + 连败退避。返回 (ok, 人话原因)。

    publish_daily_cap=0 视为不限制（2026-10-08 用户要求关闭每日上限）。"""
    from . import ledger
    cap = daily_cap()
    if cap > 0:
        used = ledger.today_count(task_id, platform)
        if used >= cap:
            return False, ("今日已发 %d 章（上限 %d），为防风控明天再发；"
                           "确需多发请在设置调 publish_daily_cap（0=不限制）"
                           % (used, cap))
    streak = ledger.consecutive_failures(platform)
    limit = fail_streak()
    if streak >= limit:
        return False, ("平台近 %s 内连续 %d 次发布失败（疑似风控或改版），自动发布"
                       "已暂停；代码问题修复后护栏自动解除，也可单章「发一章」"
                       "恢复" % (_fail_window_text(), streak))
    return True, ""


def _fail_window_text():
    from . import ledger
    h = ledger.fail_window_h()
    return ("%g 小时" % h) if h < 24 else ("%g 天" % (h / 24))


# 连接类瞬断标记（2026-10-10 存草稿实案：45 章 fill 时「连接被关闭」整链
# 停摆）——命中即对该章重试一次再弃，不因浏览器抖动废掉整批
_TRANSIENT_MARKS = ("连接断开", "连接被关闭", "10053", "10054", "等响应超时",
                    "已中止", "目标已关闭")
# 幂等拒起标记（manager 层台账/实况对账的拒绝话术）——命中即跳过该章，
# 不是失败
_IDEMPOTENT_MARKS = ("已成功发布过", "已存过草稿")


def _is_transient(err):
    e = str(err or "")
    return any(m in e for m in _TRANSIENT_MARKS)


def _is_idempotent_reject(err):
    e = str(err or "")
    return any(m in e for m in _IDEMPOTENT_MARKS)


def other_batch_running(task_id):
    """除本任务外是否还有别的自动发布/存草稿批次在跑。命中返回对方
    task_id，否则空串——草稿链与之互斥（共用平台浏览器会互踩页签，
    2026-10-10 实案：连载发章占着浏览器，草稿流 fill 被掐断连接）。"""
    with _LOCK:
        for tid, ent in _running.items():
            if tid != str(task_id) and ent.get("status") == "running":
                return str(tid)
    return ""


# ---------------------------------------------------------------- 待发枚举
# 文档目录里的编号文件不是章节稿：章纲/大纲按章编号（第85章-xxx.md、
# vol-1-ch-16.md），全按章号算会把待发清单撑出一堆「假章」——批量选择
# 发布时一旦选中就会把章纲当正文填进编辑器（2026-10-08 实案：全书树 88
# 个章号，其中 34 个来自 章纲/已成稿/作废稿 目录）。
_DOC_DIR_MARKS = ("章纲/", "大纲/", "设定/", "参考资料/", "已成稿/",
                  "作废稿", "docs/", "outline/")
_WALK_PRUNE = {"node_modules", "target", "build", "dist", "__pycache__"}


def _workdir_chapter_files(wd):
    """任务工作目录的**全量**章节稿（不看时间窗，2026-10-08 实案）。

    run 成品口径的时间窗起点是「各自任务的首跑」——续写链上每个批次任务
    只能看到自己出生以后新写的章节：今天的批次任务待发只剩 73-78，昨天
    的 65-78，根任务才有全量 31-78。发布待发的正确口径是「工作目录里
    还没发过的章节稿」，与哪个任务/何时跑无关，所以这里直接全树扫。"""
    import os as _os
    out = []
    for dirpath, dirnames, filenames in _os.walk(wd):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d not in _WALK_PRUNE]
        for name in filenames:
            if not name.lower().endswith((".md", ".txt")):
                continue
            fp = _os.path.join(dirpath, name)
            rel = _os.path.relpath(fp, wd).replace("\\", "/")
            if rel.startswith(_DOC_DIR_MARKS):
                continue
            try:
                out.append({"name": rel, "size": _os.path.getsize(fp)})
            except OSError:
                pass
    return out


def pending(task_id, platform):
    """待发章节清单：任务工作目录里的章节文件 − 已发章号，按章号升序。

    已发口径连载链合并（含校准所得平台实况），所以任何一个批次任务上发
    起发布，看到的都是整本书的待发清单。已存草稿的章同样剔除（内容已在
    平台草稿箱，再跑只会造重复稿；提交发布走平台草稿箱由用户手工完成）。
    返回 (list, err)；list 项
    {chapter_no, file, size}，file 为工作目录相对路径。章号解析不出的
    文件不进自动发布（防同章多文件误发），API 单章发（manager 直调）
    不受此限。
    """
    from .. import store
    from . import ledger
    task = store.get_task(task_id)
    if not task:
        return [], "任务不存在"
    wd = str(task.get("workdir") or "").strip()
    files = _workdir_chapter_files(wd) if wd and _isdir(wd) else []
    if not files:
        # workdir 缺失/异常：退回 run 成品口径（最新 run 优先）
        for r in store.task_runs(task_id):
            _wd, fs = store.run_artifacts(r.get("id") or "", limit=800)
            if fs:
                files = fs           # 任一 run 的成品口径都从任务首跑起，取到即够
                break
    done = ledger.published_chapters(task_id, platform) \
        | ledger.drafted_chapters(task_id, platform)
    # 分卷标注（2026-10-08 用户需求：待发清单按卷展示）：卷计划解析不出时
    # volume 为空串，前端不分组平铺。
    plan = []
    try:
        from . import volumes as _volumes
        plan = _volumes.load_plan(wd)
    except Exception:
        plan = []
    out, seen = [], set()
    for f in files:
        name = str(f.get("name") or "")
        if not name.lower().endswith((".md", ".txt")):
            continue
        if name.startswith(_DOC_DIR_MARKS):
            continue
        n = ledger.parse_chapter_no(name)
        if n <= 0 or n in done or n in seen:
            continue
        seen.add(n)
        vol = _volumes.volume_for(n, plan) if plan else None
        out.append({"chapter_no": n, "file": name, "size": f.get("size") or 0,
                    "volume": (vol.get("name") or "") if vol else ""})
    out.sort(key=lambda x: x["chapter_no"])
    return out, ""


def status(task_id):
    """前端视图：待发清单 + 护栏状态 + 自动发布进度。

    护栏分两层给前端：guard_ok/guard_reason=完整发布闸（硬护栏+质量闸），
    管「发布」类按钮；draft_ok/draft_reason=硬护栏（每日上限/连败退避/建书
    确认），管「存草稿」按钮——草稿不上线，质量闸拦它只会堵死「先落平台
    再人工把关」的通路；quality_blockers 单独带出去做提示性展示。"""
    from .. import store
    from . import ledger, manager
    ent = ledger.books_for(str(task_id))   # 沿连载链继承：续写批次同书同账
    task = store.get_task(task_id)
    books = []
    for plat, info in ent.items():
        pend, err = pending(task_id, plat)
        hard_ok, hard_why = guards(task_id, plat)
        if not _book_ready(info):
            hard_ok, hard_why = False, "该平台建书结果未确认（缺少作品 ID），请重试创建作品或登记已有作品"
        blockers = []
        if task and _book_ready(info):
            quality = manager._quality_release_guard(task, plat, action="publish")
            if not quality.get("allowed"):
                blockers = [str(b) for b in (quality.get("blockers") or [])]
        ok, why = hard_ok, hard_why
        if ok and blockers:
            ok, why = False, "质量门禁拦截：%s" % "；".join(blockers)
        books.append({"platform": plat, "bound": True,
                      "title": info.get("title") or "",
                      "pending": len(pend),
                      "items": pend[:200],
                      "guard_ok": ok, "guard_reason": why,
                      "draft_ok": hard_ok, "draft_reason": hard_why,
                      "quality_blockers": blockers,
                      "drafted": len(ledger.drafted_chapters(task_id, plat)),
                      "calibrated": calibrated(plat)})
    run = _running.get(task_id) or None
    if run:
        run = dict(run)
    return {"books": books, "running": run}


# ---------------------------------------------------------------- 顺序发布
def _wait_idle(platform, timeout=IDLE_TIMEOUT_S):
    """等 manager 的平台动作结束（busy → 其他）。人工确认模式下用户在
    浏览器里点提交的时间也算在内，超时给足。"""
    from . import manager
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            st = (manager.view().get("platforms") or {}).get(platform) or {}
            if st.get("status") != "busy":
                return True
        except Exception:
            return False               # manager 异常：别傻等
        time.sleep(IDLE_POLL_S)
    return False


def calibrated(platform):
    """该平台的发布流程是否已校准（data/publish/flows-<plat>.json 在场）。

    内置默认表的选择器是「合理推测」；auto_submit 无人值守直发必须先经
    真机校准（探测→写 flows 覆盖文件），否则填错表单还会自动提交出去。"""
    from .. import paths
    return (paths.PUBLISH_DIR / ("flows-%s.json" % platform)).is_file()


def flow_calibrated(plat, action):
    """某动作的步骤表是否有真机校准来源（用户校准文件或仓库校准模板里
    有该动作的表）。内置推测表不算——存草稿虽不上线，批量乱点照样惹
    风控/攒一箱废稿，无人值守跑之前必须有验证过的步骤表。"""
    import json as _json
    from pathlib import Path as _Path
    from .. import paths as _paths
    for fp in (_paths.PUBLISH_DIR / ("flows-%s.json" % plat),
               _Path(__file__).with_name("flows-%s-calibrated.json" % plat)):
        try:
            data = _json.loads(fp.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get(action), list):
                return True
        except Exception:
            pass
    return False


def publish_pending_async(task_id, platform, auto_submit=False, only=None,
                          force=False, force_confirmed=False, force_reason="",
                          as_draft=False):
    """把任务的待发章节按章号顺序发出（后台线程）。返回 (ok, err)。

    only=章号清单（批量选择发布）：从待发清单里挑出所选章号，保持章号
    升序；所选全都不在待发清单里（已发过/解析不出章号）时报错不空跑。
    auto_submit=True（直发）逐章提交走完全程；False（人工确认）每轮只填
    **一章**就停在 manual_pause——表单填好后提交权在用户，walker 若直接
    填下一章会导航离开未提交的编辑器，把上一章内容丢掉（平台草稿自动
    保存不可依赖）。用户在浏览器提交后再次发起即发下一章。
    as_draft=True（全部发草稿，2026-10-09）：逐章自动填稿并点「存草稿」，
    不上线、不受质量闸拦、无需人工确认，一章接一章连跑到清空待发清单
    （章间仍按 PACE_S 防风控节奏）；提交发布由用户到平台草稿箱手工完成，
    届时质量闸照常把关。与 auto_submit 互斥（草稿模式忽略 auto_submit）。
    草稿链三条专属护栏（2026-10-10 假成功案）：起跑前先 attach-only 对账
    平台实况并重算待发（已发布/已存稿剔除，对不了账就拒起）；跑批期间
    检测到另一批自动发布即让位停止（平台浏览器互斥）。"""
    from .. import store
    from . import ledger, manager
    if platform not in manager.PLATFORMS:
        return False, "未知平台"
    if as_draft:
        auto_submit = False                # 草稿模式没有「提交」语义
        if not flow_calibrated(platform, "upload_chapter_draft"):
            return False, ("存草稿模式需要该平台有 upload_chapter_draft 步骤表"
                           "（仓库校准模板或 data/publish/flows-%s.json）；"
                           "未校准不许无人值守批量动表单" % platform)
    elif auto_submit and not calibrated(platform):
        return False, ("自动提交模式需要先校准该平台发布流程：用「探测」按钮 dump "
                       "表单后把真实步骤写进 data/publish/flows-%s.json（缺省选择器"
                       "只是推测，未校准不许无人值守直发）" % platform)
    with _LOCK:
        cur = _running.get(task_id) or {}
        if cur.get("status") == "running":
            return False, "该任务已有自动发布进行中，请等本轮结束"
    if as_draft:
        other = other_batch_running(task_id)
        if other:
            return False, ("检测到另一批自动发布/存草稿进行中（任务 %s）——两边"
                           "共用平台浏览器会互踩，请等它跑完再存草稿" % other)
    task = store.get_task(task_id)
    if not task:
        return False, "任务不存在"
    from .. import contracts
    contract = contracts.get(task_id)
    if ((contract and contract.get("approval_required")) or task.get("approval_required")) \
            and not contracts.release_allowed(task_id):
        return False, "任务需要先完成审批，才能发布产物"
    book = ledger.book_for(task_id, platform)
    if not book:
        return False, "该任务尚未在此平台建书，请先「创建作品」"
    if not _book_ready(book):
        return False, "该平台建书结果未确认（缺少作品 ID），请重试创建作品或登记已有作品"
    if as_draft:
        # 草稿不上线：质量闸不适用（同 manager 口径），硬护栏照查
        quality = {"allowed": True, "status": "draft_only"}
    else:
        quality = manager._quality_release_guard(
            task, platform, action="publish", force=force,
            force_confirmed=force_confirmed, force_reason=force_reason)
    if not quality.get("allowed"):
        return False, "质量门禁拦截：%s" % "；".join(quality.get("blockers") or [])
    ok, why = guards(task_id, platform)
    if not ok:
        return False, why
    pend, err = pending(task_id, platform)
    if err:
        return False, err
    want = None
    if only is not None:
        try:
            want = {int(n) for n in (only or []) if n}
        except (TypeError, ValueError):
            return False, "chapters 必须是章号数组"
        pend = [p for p in pend if p["chapter_no"] in want]
        if not pend:
            return False, ("所选章节都不在待发清单里（已发过，或成品里没有"
                           "对应章节文件）；可先点「校准」对齐平台实况")
    if not pend:
        return False, "没有待发章节（全部已发布，或成品里没有可识别的章节文件）"

    from pathlib import Path
    wd = task.get("workdir") or ""
    st = {"platform": platform, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
          "done": 0, "total": len(pend), "status": "running", "error": "",
          "auto_submit": bool(auto_submit), "as_draft": bool(as_draft),
          "last_chapter": 0, "skipped": 0, "notes": []}
    with _LOCK:
        _running[task_id] = st

    def run():
        try:
            items = pend
            if as_draft:
                # 起草前对账（2026-10-10 假成功案）：台账只记得自己发的章，
                # 手工补交的只有平台知道——attach-only 刷一次校准再重算待发，
                # 已发布/已存稿章就地剔除，不去撞序号重复。对不了账（浏览器
                # 未连接/忙）整个拒起：存草稿本来就需要平台浏览器，宁等空窗
                # 不盲跑；平台没配章节计数时跳过对账维持旧路径。
                ok_r, why_r = manager.sync_published(task_id, platform,
                                                     manual=False)
                if not ok_r and "未配置章节计数" not in str(why_r or ""):
                    st["status"] = "error"
                    st["error"] = ("存草稿前对账失败：%s。为防把已发布章重复存稿，"
                                   "本次未起草；请连接平台浏览器并等它空闲后重试"
                                   % why_r)
                    return
                fresh, _ = pending(task_id, platform)
                if want is not None:
                    fresh = [p for p in fresh if p["chapter_no"] in want]
                if not fresh:
                    st["status"] = "done"
                    st["message"] = ("对账后没有可存草稿的章节（所选均已发布或"
                                     "已存稿）；确需重存请先在平台删旧稿")
                    return
                if len(fresh) != st["total"]:
                    st["notes"].append("对账剔除 %d 章已发布/已存稿，实存 %d 章"
                                       % (st["total"] - len(fresh), len(fresh)))
                st["total"] = len(fresh)
                items = fresh
            for item in items:
                if as_draft:
                    other = other_batch_running(task_id)
                    if other:
                        st["status"] = "error"
                        st["error"] = ("检测到另一批自动发布已启动（任务 %s），"
                                       "存草稿让位停止；已存 %d 章落平台草稿箱，"
                                       "等对方跑完再续" % (other, st["done"]))
                        return
                g_ok, why = guards(task_id, platform)   # 每章前复查（中途也能拦）
                if not g_ok:
                    st["status"] = "error"
                    st["error"] = "第 %d 章前护栏拦截：%s" % (item["chapter_no"], why)
                    return
                upload_kwargs = {"auto_submit": auto_submit, "as_draft": as_draft}
                if force or force_confirmed or force_reason:
                    upload_kwargs.update(force=force,
                                         force_confirmed=force_confirmed,
                                         force_reason=force_reason)
                ok2, err2 = manager.upload_chapter_async(
                    task_id, platform, str(Path(wd) / item["file"]),
                    **upload_kwargs)
                if not ok2 and as_draft and _is_idempotent_reject(err2):
                    # 幂等拒起＝这章已发布/已存过稿：跳过，不是失败——
                    # 整链不该因「没活干」停摆
                    st["skipped"] += 1
                    st["notes"].append("第 %d 章跳过：%s"
                                       % (item["chapter_no"], str(err2)[:60]))
                    continue
                if not ok2 and as_draft and _is_transient(err2):
                    # 连接类瞬断（连载流互踩/浏览器抖动）：重试一次再弃
                    st["notes"].append("第 %d 章连接中断，%d 秒后重试一次"
                                       % (item["chapter_no"],
                                          TRANSIENT_RETRY_S))
                    time.sleep(TRANSIENT_RETRY_S)
                    ok2, err2 = manager.upload_chapter_async(
                        task_id, platform, str(Path(wd) / item["file"]),
                        **upload_kwargs)
                if not ok2:
                    st["status"] = "error"
                    st["error"] = "第 %d 章发起失败：%s" % (item["chapter_no"], err2)
                    return
                if not _wait_idle(platform):
                    st["status"] = "error"
                    if as_draft:
                        st["error"] = ("第 %d 章存草稿等待超时（%.0f 分钟），后续"
                                       "章节未存" % (item["chapter_no"],
                                                     IDLE_TIMEOUT_S / 60))
                    else:
                        st["error"] = ("第 %d 章发布等待超时（%.0f 分钟）；若在等人工提交，"
                                       "请提交后重跑剩余章节" % (item["chapter_no"],
                                                                 IDLE_TIMEOUT_S / 60))
                    return
                if as_draft:
                    # manager 走完草稿流程（含平台侧验证）才返回；台账落了
                    # upload_chapter_draft 才算数，防「流程跑完但平台没存上」。
                    if item["chapter_no"] not in ledger.drafted_chapters(
                            task_id, platform):
                        st["status"] = "error"
                        st["error"] = ("第 %d 章存草稿未确认（台账未落草稿账），"
                                       "后续章节未存；截图存证见发布台账，若实际"
                                       "已保存成功，把页面反馈文案照截图校准进"
                                       "流程表 expect_text 步骤"
                                       % item["chapter_no"])
                        return
                    st["done"] += 1
                    st["last_chapter"] = item["chapter_no"]
                    if st["done"] + st["skipped"] < st["total"]:
                        time.sleep(PACE_S)
                    continue
                if not auto_submit:
                    # The manager stopped before the submit step. There is no
                    # remote success receipt until the user clicks submit.
                    st["last_chapter"] = item["chapter_no"]
                    st["status"] = "manual_pause"
                    st["message"] = ("第 %d 章已填好，请在浏览器里确认提交；"
                                     "提交后请重新校准，再选择下一章（剩 %d 章）"
                                     % (item["chapter_no"], st["total"] - st["done"]))
                    return
                if item["chapter_no"] not in ledger.published_chapters(
                        task_id, platform):
                    st["status"] = "error"
                    st["error"] = ("第 %d 章发布失败，后续章节未发（详见发布台账与"
                                   "截图存证）" % item["chapter_no"])
                    return
                st["done"] += 1
                st["last_chapter"] = item["chapter_no"]
                if st["done"] < st["total"]:
                    time.sleep(PACE_S)
            st["status"] = "done"
            if as_draft:
                st["message"] = ("已存草稿 %d 章%s（未发布，不受质量闸拦不代表可"
                                 "直发）；请到平台章节管理/草稿箱逐章检查后提交"
                                 "发布"
                                 % (st["done"], ("、跳过 %d 章" % st["skipped"])
                                    if st["skipped"] else ""))
            elif not auto_submit:
                st["message"] = "第 %d 章已填好，请在浏览器里确认提交，再重新校准" % st["last_chapter"]
        except Exception as e:                 # 线程内绝不能悬挂无终态
            st["status"] = "error"
            st["error"] = "自动发布异常：%s" % e
        finally:
            st["at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    threading.Thread(target=run, daemon=True,
                     name="pub-auto-%s" % platform).start()
    return True, ""


# ---------------------------------------------------------------- 定时联动（P2.5）
# 任务级标记 task.auto_publish = {enabled, platform, time:"HH:MM", auto_submit}
# automation._tick 每 25s 调 fire_due()：到点且今日未触发 → publish_pending_async。
# 「今日已触发」记在 data/publish/auto_publish.json（task_id → YYYY-MM-DD）：
# 触发过就不再重试当日（护栏/单飞自身也防重），成败都等明天——连败退避
# 场景下避免到点后每 25s 撞一次护栏。
_AP_FILE = None            # paths.PUBLISH_DIR / "auto_publish.json"（导入惰性定）
_AP_FIRED = None           # 内存缓存 {task_id: "YYYY-MM-DD"}
_AP_LOCK = threading.RLock()   # 可重入：_mark_fired 持锁内调 _load_fired 再进同锁


def _ap_file():
    global _AP_FILE
    if _AP_FILE is None:
        from .. import paths
        _AP_FILE = paths.PUBLISH_DIR / "auto_publish.json"
    return _AP_FILE


def _load_fired():
    global _AP_FIRED
    with _AP_LOCK:
        if _AP_FIRED is None:
            try:
                import json
                d = json.loads(_ap_file().read_text(encoding="utf-8"))
                _AP_FIRED = d if isinstance(d, dict) else {}
            except Exception:
                _AP_FIRED = {}
        return _AP_FIRED


def _mark_fired(task_id, day):
    import json
    with _AP_LOCK:
        fired = _load_fired()
        fired[str(task_id)] = day
        try:
            _ap_file().parent.mkdir(parents=True, exist_ok=True)
            tmp = _ap_file().with_suffix(".tmp")
            tmp.write_text(json.dumps(fired, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(_ap_file())
        except Exception:
            pass                            # 记录失败：最坏是当日重复触发，护栏会拦


def norm_auto_publish(ap):
    """校验并归一 auto_publish 配置。非法返回 (None, 人话原因)。"""
    from . import manager
    if not isinstance(ap, dict):
        return None, "auto_publish 必须是对象"
    platform = str(ap.get("platform") or "").strip()
    if platform not in manager.PLATFORMS:
        return None, "platform 必须是 fanqie 或 qimao"
    hhmm = str(ap.get("time") or "").strip()
    import re
    m = re.match(r"^(\d{1,2}):(\d{2})$", hhmm)
    if not m:
        return None, "time 必须是 24 小时制 HH:MM"
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None, "time 超出 0-23:00-59"
    return {"enabled": bool(ap.get("enabled")),
            "platform": platform,
            "time": "%02d:%02d" % (h, mi),
            "auto_submit": bool(ap.get("auto_submit")),
            "at": time.strftime("%Y-%m-%d %H:%M:%S")}, ""


def due_tasks(now=None):
    """到期待触发的定时发布任务清单：[(task, auto_publish)]。

    条件：enabled + 已在该平台建书 + 今日未触发 + 当前时间已过当日 time。
    未建书的任务跳过（触发也只会被 publish_pending_async 拒绝，白记一次
    fired 反而把当天额度烧掉）。"""
    from .. import store
    from . import ledger
    now = now or time.localtime()
    today = time.strftime("%Y-%m-%d", now)
    hhmm_now = time.strftime("%H:%M", now)
    fired = _load_fired()
    out = []
    for task in store.list_tasks(limit=10 ** 9):
        ap = task.get("auto_publish")
        if not isinstance(ap, dict) or not ap.get("enabled"):
            continue
        if ap.get("auto_submit"):
            from .. import contracts
            contract = contracts.get(task["id"])
            if ((contract and contract.get("approval_required")) or
                    task.get("approval_required")) and not contracts.release_allowed(task["id"]):
                continue
        if str(ap.get("time") or "") <= hhmm_now and fired.get(task["id"]) != today:
            plat = ap.get("platform")
            if plat and _book_ready(ledger.book_for(task["id"], plat)):
                out.append((task, ap))
    return out


def fire_due(now=None):
    """tick 入口：把所有到期的定时发布触发一轮。返回触发条数；单条异常不拖累其余。"""
    from . import ledger                # 惰性导入（同模块惯例）：漏了会 NameError
    now = now or time.localtime()       # 且被双层 except 双重静默成幽灵 0
    today = time.strftime("%Y-%m-%d", now)
    n = 0
    for task, ap in due_tasks(now=now):
        try:
            ok, err = publish_pending_async(
                task["id"], ap.get("platform"),
                auto_submit=bool(ap.get("auto_submit")))
            _mark_fired(task["id"], today)  # 成败都记：当日不重试
            if ok:
                ledger.record(ap.get("platform"), "auto_fire", task_id=task["id"],
                              title="定时触发 %s" % (ap.get("time") or ""), ok=True)
                n += 1
            else:
                ledger.record(ap.get("platform"), "auto_fire", task_id=task["id"],
                              title="定时触发 %s" % (ap.get("time") or ""),
                              ok=False, error=str(err)[:200])
        except Exception as e:
            try:
                _mark_fired(task["id"], today)
                ledger.record(ap.get("platform"), "auto_fire", task_id=task["id"],
                              ok=False, error=str(e)[:200])
            except Exception:
                pass
    return n
