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

import threading
import time

PACE_S = 45                  # 章间间隔（防风控节奏），测试里可置 0
IDLE_POLL_S = 2              # 等 manager busy 结束的轮询步长
IDLE_TIMEOUT_S = 420         # 单章最长等待（含浏览器操作与人工确认窗口）

_CAP_DEFAULT = 10
_STREAK_DEFAULT = 3

_running = {}                # task_id → {platform, at, done, total, status, error}
_LOCK = threading.Lock()


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
    return max(1, min(50, v))


def fail_streak():
    try:
        v = int(_settings().get("publish_fail_streak") or _STREAK_DEFAULT)
    except (TypeError, ValueError):
        v = _STREAK_DEFAULT
    return max(1, min(10, v))


def guards(task_id, platform):
    """两道护栏：每日上限 + 连败退避。返回 (ok, 人话原因)。"""
    from . import ledger
    used = ledger.today_count(task_id, platform)
    cap = daily_cap()
    if used >= cap:
        return False, ("今日已发 %d 章（上限 %d），为防风控明天再发；"
                       "确需多发请在设置调 publish_daily_cap" % (used, cap))
    streak = ledger.consecutive_failures(platform)
    limit = fail_streak()
    if streak >= limit:
        return False, ("平台连续 %d 次发布失败（疑似风控或改版），自动发布已暂停，"
                       "请人工检查后再试" % streak)
    return True, ""


# ---------------------------------------------------------------- 待发枚举
def pending(task_id, platform):
    """待发章节清单：任务成品文件中的章节文件 − 台账已发章号，按章号升序。

    返回 (list, err)；list 项 {chapter_no, file, size}，file 为工作目录相对
    路径。章号解析不出的文件不进自动发布（防同章多文件误发），API 单章
    发（manager 直调）不受此限。
    """
    from .. import store
    from . import ledger
    task = store.get_task(task_id)
    if not task:
        return [], "任务不存在"
    files = []
    for r in store.task_runs(task_id):
        _wd, fs = store.run_artifacts(r.get("id") or "", limit=800)
        if fs:
            files = fs           # 任一 run 的成品口径都从任务首跑起，取到即够
            break
    done = ledger.published_chapters(task_id, platform)
    out, seen = [], set()
    for f in files:
        name = str(f.get("name") or "")
        if not name.lower().endswith((".md", ".txt")):
            continue
        n = ledger.parse_chapter_no(name)
        if n <= 0 or n in done or n in seen:
            continue
        seen.add(n)
        out.append({"chapter_no": n, "file": name, "size": f.get("size") or 0})
    out.sort(key=lambda x: x["chapter_no"])
    return out, ""


def status(task_id):
    """前端视图：待发清单 + 护栏状态 + 自动发布进度。"""
    from . import ledger
    ent = ledger.load_books().get(str(task_id)) or {}
    books = []
    for plat, info in ent.items():
        pend, err = pending(task_id, plat)
        ok, why = guards(task_id, plat)
        books.append({"platform": plat, "bound": True,
                      "title": info.get("title") or "",
                      "pending": len(pend), "guard_ok": ok, "guard_reason": why,
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


def publish_pending_async(task_id, platform, auto_submit=False):
    """把任务的待发章节按章号顺序发出（后台线程）。返回 (ok, err)。

    auto_submit=True（直发）逐章提交走完全程；False（人工确认）每轮只填
    **一章**就停在 manual_pause——表单填好后提交权在用户，walker 若直接
    填下一章会导航离开未提交的编辑器，把上一章内容丢掉（平台草稿自动
    保存不可依赖）。用户在浏览器提交后再次发起即发下一章。"""
    from .. import store
    from . import ledger, manager
    if platform not in manager.PLATFORMS:
        return False, "未知平台"
    if auto_submit and not calibrated(platform):
        return False, ("自动提交模式需要先校准该平台发布流程：用「探测」按钮 dump "
                       "表单后把真实步骤写进 data/publish/flows-%s.json（缺省选择器"
                       "只是推测，未校准不许无人值守直发）" % platform)
    with _LOCK:
        cur = _running.get(task_id) or {}
        if cur.get("status") == "running":
            return False, "该任务已有自动发布进行中，请等本轮结束"
    task = store.get_task(task_id)
    if not task:
        return False, "任务不存在"
    if not ledger.book_for(task_id, platform):
        return False, "该任务尚未在此平台建书，请先「创建作品」"
    ok, why = guards(task_id, platform)
    if not ok:
        return False, why
    pend, err = pending(task_id, platform)
    if err:
        return False, err
    if not pend:
        return False, "没有待发章节（全部已发布，或成品里没有可识别的章节文件）"

    from pathlib import Path
    wd = task.get("workdir") or ""
    st = {"platform": platform, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
          "done": 0, "total": len(pend), "status": "running", "error": "",
          "auto_submit": bool(auto_submit), "last_chapter": 0}
    with _LOCK:
        _running[task_id] = st

    def run():
        try:
            for item in pend:
                g_ok, why = guards(task_id, platform)   # 每章前复查（中途也能拦）
                if not g_ok:
                    st["status"] = "error"
                    st["error"] = "第 %d 章前护栏拦截：%s" % (item["chapter_no"], why)
                    return
                ok2, err2 = manager.upload_chapter_async(
                    task_id, platform, str(Path(wd) / item["file"]),
                    auto_submit=auto_submit)
                if not ok2:
                    st["status"] = "error"
                    st["error"] = "第 %d 章发起失败：%s" % (item["chapter_no"], err2)
                    return
                if not _wait_idle(platform):
                    st["status"] = "error"
                    st["error"] = ("第 %d 章发布等待超时（%.0f 分钟）；若在等人工提交，"
                                   "请提交后重跑剩余章节" % (item["chapter_no"],
                                                             IDLE_TIMEOUT_S / 60))
                    return
                if item["chapter_no"] not in ledger.published_chapters(
                        task_id, platform):
                    st["status"] = "error"
                    st["error"] = ("第 %d 章发布失败，后续章节未发（详见发布台账与"
                                   "截图存证）" % item["chapter_no"])
                    return
                st["done"] += 1
                st["last_chapter"] = item["chapter_no"]
                if not auto_submit and st["done"] < st["total"]:
                    # 人工确认模式：填好一章就停，等用户在浏览器提交后再发起
                    st["status"] = "manual_pause"
                    st["message"] = ("第 %d 章已填好，请在浏览器里确认提交；"
                                     "提交后再点一次发布即发下一章（剩 %d 章）"
                                     % (item["chapter_no"], st["total"] - st["done"]))
                    return
                if st["done"] < st["total"]:
                    time.sleep(PACE_S)
            st["status"] = "done"
            if not auto_submit:
                st["message"] = "第 %d 章已填好，请在浏览器里确认提交" % st["last_chapter"]
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
        if str(ap.get("time") or "") <= hhmm_now and fired.get(task["id"]) != today:
            plat = ap.get("platform")
            if plat and ledger.book_for(task["id"], plat):
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
