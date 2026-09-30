# -*- coding: utf-8 -*-
"""发布管理：平台会话（attach-or-launch）、登录状态机、建书/发章后台线程。

状态机（data/publish/state.json 的 platforms.<id>.status）：
  none           从未连接
  waiting_login  浏览器已开等用户扫码（connect 后，轮询 15 分钟）
  connected      登录检测通过
  busy           发布动作进行中（结束回 connected / error）
  error          最后一次动作失败（error 字段带人话原因，可重试）

浏览器生命周期：每平台一个持久化 profile（~/.codebee/publish_profiles/<id>，
仓库外——GB 级浏览器运行时数据不进仓库目录），登录态落在 profile 里。
服务重启后按 state.json 记住的调试端口 attach 旧实例；实例已死才重新
launch——用户登录一次，之后无感。

与 bookmeta.generate_async 同款线程纪律：动作起后台线程即返回，前端靠
view()（SSE/轮询）看进度；线程内任何异常都落终态，绝不悬挂。
"""
from __future__ import annotations

import json
import re
import threading
import time

from .. import operations, paths, quality_gate
from . import fanqie, flow, ledger, qimao
from .browser import Browser, BrowserError, Page

PLATFORMS = {"fanqie": fanqie, "qimao": qimao}
LOGIN_WAIT_S = 15 * 60          # 扫码等待窗口
STATUS_FILE = paths.PUBLISH_DIR / "state.json"

LOCK = threading.RLock()
_state = {"platforms": {}}      # {plat: {status, port, at, error, last_login, last_action}}
_browsers = {}                  # plat → Browser（进程内缓存，alive 校验兜底）


# ---------------------------------------------------------------- 状态
def _load():
    global _state
    try:
        d = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("platforms"), dict):
            _state = d
            return
    except Exception:
        pass
    _state = {"platforms": {}}


def _save():
    with LOCK:
        try:
            paths.PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
            tmp = STATUS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(_state, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(STATUS_FILE)
        except Exception:
            pass


def _set(plat, **kv):
    with LOCK:
        ent = _state["platforms"].setdefault(plat, {})
        ent.update(kv)
        ent["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save()


def _st(plat):
    return (_state.get("platforms") or {}).get(plat) or {}


def _quality_release_guard(task, plat, action="publish", target_chapter=None,
                           force=False, force_confirmed=False, force_reason=""):
    """Return a release decision backed by the latest completed review.

    Non-fiction/code tasks keep their existing publish workflow; novel release
    actions must carry a verifiable quality verdict.  This keeps the gate
    focused on the signing failure mode without breaking generic file publish.
    """
    if str(task.get("type") or "").lower() not in ("novel", "serial_novel") \
            and (task.get("engine") or "") != "review":
        return {"allowed": True, "status": "not_applicable", "blockers": [],
                "warnings": [], "forced": False}
    from .. import store
    verdict = None
    for run in sorted(store.task_runs(task.get("id")) or [],
                      key=lambda item: str(item.get("id") or ""), reverse=True):
        if run.get("status") == "done" and isinstance(run.get("verdict"), dict):
            verdict = run["verdict"]
            break
    decision = quality_gate.evaluate_release(
        verdict or {}, action=action, target_chapter=target_chapter,
        platform_version=ledger.book_for(task.get("id"), plat) or {},
        force=force, force_confirmed=force_confirmed, force_reason=force_reason)
    decision["version_snapshot"] = quality_gate.local_version(task.get("workdir") or "")
    decision["platform_snapshot"] = ledger.book_for(task.get("id"), plat) or {}
    if verdict:
        decision["quality_status"] = verdict.get("quality_status") or "continue_only"
        decision["review_run_id"] = run.get("id")
    return decision


def view():
    """前端状态视图：平台状态 + 是否找到浏览器 + 作品登记。"""
    from .browser import find_browser
    out = {}
    for pid, mod in PLATFORMS.items():
        s = _st(pid)
        out[pid] = {"label": mod.CONFIG["label"], "status": s.get("status") or "none",
                    "at": s.get("at") or "", "error": s.get("error") or "",
                    "last_action": s.get("last_action") or "",
                    "profile": str(_profile_dir(pid))}
    return {"platforms": out, "browser_found": bool(find_browser())}


def recover_orphans():
    """启动收尸：waiting_login / busy 的线程随进程重启死掉，统一改判 error。

    升级自愈：旧版「等扫码窗口一关就冤判超时」留下的 error（error 文案带
    「等待登录超时」）排队后台复核——profile 登录态还在的直接翻 connected，
    别让升级完还挂着旧冤案；attach 不到活实例就维持原样（用户点重连时
    新终审逻辑自会兜住）。"""
    _load()
    n = 0
    stale = []
    for plat in PLATFORMS:
        s = _st(plat)
        if s.get("status") in ("waiting_login", "busy"):
            _set(plat, status="error", error="上次操作随服务重启中断，请重试")
            n += 1
        elif s.get("status") == "error" and "等待登录超时" in (s.get("error") or ""):
            stale.append(plat)
    if stale:
        threading.Thread(target=_recheck_stale_errors, args=(stale,),
                         daemon=True, name="pub-stale-recheck").start()
    return n


def _recheck_stale_errors(plats):
    """旧版假超时的后台复核：只 attach 还活着的浏览器实例（绝不 launch——
    升级重启时静默弹窗口吓人），登录态复核过了翻 connected；否则不动。"""
    for plat in plats:
        if _st(plat).get("status") != "error":
            continue                  # 用户已先行操作（重连/断开），别覆盖
        port = _st(plat).get("port") or 0
        if not port:
            continue
        try:
            b = Browser.attach(int(port))
        except Exception:
            continue                  # 实例已死：不 launch，维持原错误
        with LOCK:
            _browsers[plat] = b
        page = b.first_page(create=False)
        if page is None:
            continue
        try:
            ok, _u = _check_login(plat, page)
        except Exception:
            continue
        if ok and _st(plat).get("status") == "error":
            _set(plat, status="connected", error="",
                 last_login=time.strftime("%m-%d %H:%M"))
            ledger.record(plat, "connect", ok=True)


# ---------------------------------------------------------------- 浏览器会话
def _profiles_base():
    """profile 存放根：用户主目录 ~/.codebee/publish_profiles（仓库外）。

    浏览器 profile 是 GB 级运行时数据（缓存/扩展/字体），放仓库 data/ 里
    会拖垮静态扫描/备份（639M→986M 实测淹没整仓安全扫描），且登录态
    cookie 没必要进任何仓库周边流程。"""
    from pathlib import Path as _P
    return _P.home() / ".codebee" / "publish_profiles"


def _migrate_legacy_profiles():
    """一次性迁移：老位置 data/publish/profiles/<plat> 整体搬到新根（保登录态）。

    新位置已有同名平台目录时跳过（老数据视为已废弃）；搬家失败静默——
    大不了用户重扫一次码。"""
    from pathlib import Path as _P
    legacy = _P(paths.PUBLISH_DIR) / "profiles"
    if not legacy.is_dir():
        return
    base = _profiles_base()
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    for plat_dir in legacy.iterdir():
        if not plat_dir.is_dir():
            continue
        dst = base / plat_dir.name
        if dst.exists():
            continue
        try:
            import shutil
            shutil.move(str(plat_dir), str(dst))   # 跨盘（仓库盘→系统盘）也能搬
        except OSError:
            continue
    try:
        legacy.rmdir()                  # 空了才删得掉；还有残留就留给下次
    except OSError:
        pass


def _profile_dir(plat):
    return _profiles_base() / plat


def _ensure_browser(plat):
    """attach-or-launch：进程内缓存 → 记住的端口 → 全新 launch。"""
    with LOCK:
        b = _browsers.get(plat)
        if b and b.alive():
            return b
        port = _st(plat).get("port") or 0
        if port:
            try:
                b = Browser.attach(int(port))
                _browsers[plat] = b
                return b
            except BrowserError:
                pass                        # 旧实例已死：走 launch
        b = Browser(_profile_dir(plat))     # 有窗口：用户要扫码/人工确认
        _browsers[plat] = b
        _set(plat, port=b.port)
        return b


def _open_page(plat):
    b = _ensure_browser(plat)
    # 单标签纪律：平台点击（如「上传章节」）会开新 tab，流程引擎的 page
    # 对象可能留在旧 tab 上填错页面（0 字草稿案的乱源）。每次动作前收拢
    # 到一个 tab——发布是串行作业，多 tab 只会串台。
    try:
        for t in b.pages()[1:]:
            try:
                from .browser import _http_json
                _http_json("http://127.0.0.1:%d/json/close/%s" % (b.port, t["id"]))
            except Exception:
                pass
    except Exception:
        pass
    return b, b.first_page(create=True)


def _check_login(plat, page):
    """打开后台首页看 URL 是否被踢到登录页。返回 (ok, 当前url)。

    导航失败页（chrome-error://）不含登录标记，曾把「域名打不开」误判成
    已登录——假 connected 的来源，先排除。"""
    mod = PLATFORMS[plat]
    try:
        page.navigate(mod.CONFIG["home"], timeout=30)
    except BrowserError as e:
        return False, str(e)
    url = str(page.url() or "")
    if url.startswith("chrome-error://") or url.startswith("about:"):
        return False, url                      # 页面没打开：网络/域名问题，不是登录态
    if any(m in url for m in mod.CONFIG["login_url_marks"]):
        return False, url
    return True, url


# ---------------------------------------------------------------- 流程加载
def load_flow(plat, action):
    """流程表三层：data/publish/flows-<plat>.json（用户校准）→ 仓库校准模板
    flows-<plat>-calibrated.json（真机验证过的基线，随代码分发）→ 平台模块
    内置默认（待校准推测）。"""
    from pathlib import Path
    for fp in (paths.PUBLISH_DIR / ("flows-%s.json" % plat),
               Path(__file__).with_name("flows-%s-calibrated.json" % plat)):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get(action), list):
                return data[action]
        except Exception:
            pass
    return PLATFORMS[plat].FLOWS[action]


# ---------------------------------------------------------------- 动作：连接
def connect(plat):
    """开浏览器到平台首页，起后台线程轮询登录态（等扫码）。"""
    if plat not in PLATFORMS:
        return False, "未知平台"
    try:
        b, page = _open_page(plat)
    except BrowserError as e:
        _set(plat, status="error", error=str(e))
        return False, str(e)
    page.navigate(PLATFORMS[plat].CONFIG["home"])
    _set(plat, status="waiting_login", error="")

    def wait_login():
        mod = PLATFORMS[plat]
        deadline = time.time() + LOGIN_WAIT_S
        while time.time() < deadline:
            try:
                url = str(page.url() or "")
                if url and not any(m in url for m in mod.CONFIG["login_url_marks"]) \
                        and "about:blank" not in url and "chrome-error" not in url:
                    # 用户登录完成（离开登录页）。再主动开一次首页做复核，
                    # 复核被踢回登录页说明只是中间跳转，继续等。
                    ok, _u = _check_login(plat, page)
                    if ok:
                        _set(plat, status="connected", last_login=time.strftime("%m-%d %H:%M"))
                        ledger.record(plat, "connect", ok=True)
                        return
            except (BrowserError, Exception):
                pass                        # 页面被用户关掉等：继续等到超时
            time.sleep(5)
        _finalize_login_wait(plat)

    threading.Thread(target=wait_login, daemon=True,
                     name="pub-login-%s" % plat).start()
    return True, ""


def _finalize_login_wait(plat):
    """等扫码超时后的终审：用户可能已在本窗口登录后把窗口关了（profile
    里登录态还在），attach-or-launch 重拉页面再复核一次，别急着冤判
    超时——「明明登录着却报超时」是发布卡死感的最大来源。复核也过不了
    才落超时错误。返回 True 表示复核通过（已判 connected）。"""
    try:
        _b, page = _open_page(plat)
        ok, _u = _check_login(plat, page)
    except Exception:
        ok = False
    if ok:
        _set(plat, status="connected", last_login=time.strftime("%m-%d %H:%M"))
        ledger.record(plat, "connect", ok=True)
    else:
        _set(plat, status="error", error="等待登录超时（15 分钟），请重新点连接")
    return ok


def disconnect(plat):
    b = _browsers.pop(plat, None)
    if b:
        b.close()
    _set(plat, status="none", error="")
    return True


# ---------------------------------------------------------------- 动作：探测
def probe_form_async(plat):
    """「探测表单」：跑 probe 流程把页面真实可交互元素 dump 进台账 log。"""
    if plat not in PLATFORMS:
        return False, "未知平台"
    if _st(plat).get("status") not in ("connected", "error"):
        return False, "请先连接并登录平台"
    _set(plat, status="busy", last_action="probe", error="")

    logs = []

    def run():
        try:
            b, page = _open_page(plat)
            flow.run_flow(page, load_flow(plat, "probe_form"),
                          values={}, config=PLATFORMS[plat].CONFIG,
                          shot=lambda n: page.screenshot(ledger.shot_path(plat, "probe", n)),
                          log=logs.append)
            _set(plat, status="connected")
            ledger.record(plat, "probe", ok=True,
                          error="\n".join(logs)[:2000])
        except Exception as e:
            _set(plat, status="error", error="探测失败：%s" % e)
            ledger.record(plat, "probe", ok=False, error=str(e)[:300])
        finally:
            _save()

    threading.Thread(target=run, daemon=True, name="pub-probe-%s" % plat).start()
    return True, ""


# ---------------------------------------------------------------- 动作：建书
_ALT_COUNT = 3       # 撞名后自动换名最多试 3 个候选
_ALT_TIMEOUT = 90    # 候选书名一次轻调用的墙钟（发布线程内，不能久等）
_TITLE_MAX = 15      # 两平台书名统一截 15 字（bookmeta._norm 同口径）


def _mech_titles(base):
    """机械变体兜底（模型不可用时的最后手段）：最小改动、不重构词——
    同音字（了→啦）、去「我的」前缀、加「纪事」尾。丑但能建成书，
    烧在发布线程里的几万字稿费比书名门面贵得多。"""
    out, seen = [], {base}
    for v in (base.replace("了", "啦", 1),
              base[2:] if base.startswith("我的") else "",
              (base + "纪事") if len(base) <= 13 else ""):
        v = str(v or "").strip()[:_TITLE_MAX]
        if len(v) >= 4 and v not in seen:
            out.append(v)
            seen.add(v)
    return out


def _alt_titles(plat, data, task, logs):
    """撞名候选队列：编排者基于原名+简介一次轻调用重出书名，失败静默
    降级机械变体——候选生成绝不能阻断建书主流程。"""
    base = str(data.get("book_name") or "").strip()
    if not base:
        return []
    out = []
    try:
        from .. import modelhub, planner, runner
        orch = modelhub.resolve_orchestrator()
        if orch:
            prov, model = orch
            prompt = (
                "你是网文编辑。原书名《%s》在平台重名被拒，请为同一本书重起"
                " %d 个新书名：每个 15 字内；保持原题材气质与钩子感；避开原名"
                "的核心词组合与「我的XX藏不住」式高频套路组合；书名里带一个本"
                "书独有的专名（人名/金手指/设定词）最不易再撞。只输出一个 "
                "```json 字符串数组，如 [\"书名1\",\"书名2\"]，不要输出其它内容。"
                "\n\n## 简介（截选）\n%s"
                % (base, _ALT_COUNT + 2, str(data.get("summary") or "")[:300]))
            res = modelhub.chat(prov["id"], model, prompt,
                                max_tokens=600, timeout=_ALT_TIMEOUT)
            planner._log_usage("publish", "create_book", task or {"id": ""}, res,
                               model=model,
                               provider=prov.get("name", prov.get("id", "")),
                               provider_id=prov.get("id", ""))
            if res["ok"]:
                arr = runner.extract_json(res.get("text") or "")
                if isinstance(arr, dict):
                    arr = arr.get("titles") or arr.get("candidates") or []
                if isinstance(arr, list):
                    for x in arr:
                        t = str(x or "").strip().replace("\n", " ")[:_TITLE_MAX]
                        if len(t) >= 2 and t != base and t not in out:
                            out.append(t)
            if out:
                logs.append("候选书名（编排者 %s）：%s"
                            % (model, "、".join(out[:5])))
    except Exception as e:
        logs.append("候选书名生成降级（%s）" % str(e)[:100])
    for v in _mech_titles(base):
        if v not in out:
            out.append(v)
    return out[:_ALT_COUNT + 2]


def _sync_renamed_book(task_id, plat, task, data, new_name, logs):
    """建书撞名自动换名后的全链路同步。

    只换建书表单里的字、bookmeta/大纲/归档还留旧名的话：下轮发章按旧书名
    对账、用户点「重生成作品信息」又把旧名改回去——马甲案同款错位复发。
    这里把 bookmeta data、归档 Markdown、最新批次大纲的 book_title 统一到
    新名（台账 title 由调用方 save_book 落）。任一环节失败只记日志，
    不拦建书成功主流程。"""
    try:
        from pathlib import Path as _Path
        from .. import bookmeta as _bm
        from .. import store as _store
        data["book_name"] = new_name
        cur = _store.get_task(task_id)
        prev = ((cur or {}).get("book_meta") or {}).get(plat) or {}
        if isinstance(prev.get("data"), dict):
            payload = dict(prev)
            payload["data"] = dict(prev["data"], book_name=new_name)
            _store.set_book_meta(task_id, plat, payload)
            fp = _Path(task.get("workdir") or "") / _bm.PLATFORMS[plat]["file"]
            if str(fp.parent) and fp.parent.is_dir():
                fp.write_text(_bm.render_markdown(cur, plat, payload["data"]),
                              encoding="utf-8")
            logs.append("作品信息与归档已同步新书名《%s》" % new_name)
        # 大纲书名：续写链继承与「一键重生成」素材都读最新批次大纲，
        # 不同步会让下次重生成把书名改回旧名
        for r in _store.task_runs(task_id):
            o = r.get("outline")
            if isinstance(o, dict) and o.get("chapters") and o.get("book_title"):
                _store.update_run(r["id"], outline=dict(o, book_title=new_name))
                logs.append("大纲书名已同步为《%s》" % new_name)
                break
    except Exception as e:
        logs.append("书名同步部分失败（不拦建书）：%s" % str(e)[:120])


def _create_preflight(plat, data):
    """建书前置闸：平台表单对必填字段有硬校验（番茄简介 50-500 字），过不了
    校验时页面不跳转、流程只能误报「未登录或改版」——开浏览器之前先拦下，
    把人话原因还给用户。返回错误文案，空串=放行。"""
    placeholders = {"待补充", "（待补充）", "(待补充)"}
    title = str(data.get("book_name") or "").strip()
    if not title or title in placeholders:
        return "作品名称为空，请先补书名再点「创建作品」"
    summary = str(data.get("summary") or "").strip()
    if plat == "fanqie":
        if len(summary) < 50 or len(summary) > 500:
            return ("作品简介 %d 字，番茄建书表单要求 50-500 字；"
                    "请先补简介再点「创建作品」" % len(summary))
        if not str(data.get("category") or "").strip():
            return "作品分类为空，请先重生成作品信息再建书"
        for key in ("tags_theme", "tags_role", "tags_plot"):
            if not (data.get(key) or []):
                return "作品标签（%s）为空，请先重生成作品信息再建书" % key
    elif plat == "qimao":
        if not summary or summary in placeholders:
            return "作品简介为空，请先补简介再点「创建作品」"
        for key in ("category_main", "category_sub"):
            if not str(data.get(key) or "").strip():
                return "作品分类为空，请先重生成作品信息再建书"
        for key in ("tags_style", "tags_role", "tags_plot", "tags_bg"):
            if not (data.get(key) or []):
                return "作品标签（%s）为空，请先重生成作品信息再建书" % key
    return ""


def create_book_async(task_id, plat, auto_submit=False, force=False,
                      force_confirmed=False, force_reason=""):
    """按任务 book_meta 的资料在平台建书。返回 (ok, err)。"""
    from .. import store
    if plat not in PLATFORMS:
        return False, "未知平台"
    task = store.get_task(task_id)
    if not task:
        return False, "任务不存在"
    # 连载链沿链继承（都是用第一个）：续写批次从自己身上建书也用首批生成的资料
    meta = store.inherited_book_meta(task, plat)
    if meta.get("status") != "done" or not meta.get("data"):
        return False, "请先生成该平台的作品信息"
    if _st(plat).get("status") == "busy":
        return False, "该平台有操作正在进行中"
    existing = ledger.book_for(task_id, plat)
    # A title-only manual registration is usable as a binding. For legacy
    # entries without source metadata, only a recorded failed create-book
    # attempt is safe to retry; otherwise it may be a real manual binding.
    if existing:
        if existing.get("book_id") or existing.get("source") == "manual":
            return False, "该任务已在此平台登记过作品，请直接发章"
        failed_create = any(
            r.get("action") == "create_book" and not r.get("ok")
            for r in ledger.recent(task_id=task_id, platform=plat, limit=50))
        if not failed_create:
            return False, "该任务已有作品登记但未确认，请登记已有作品或先在平台核对"
    why = _create_preflight(plat, meta["data"])
    if why:
        return False, why
    quality = _quality_release_guard(
        task, plat, action="publish", force=force,
        force_confirmed=force_confirmed, force_reason=force_reason)
    if not quality.get("allowed"):
        return False, "质量门禁拦截：%s" % "；".join(quality.get("blockers") or [])
    ok_login, why = _login_guard(plat)
    if not ok_login:
        return False, why
    _set(plat, status="busy", last_action="create_book", error="")

    data = meta["data"]
    mod = PLATFORMS[plat]
    logs = []
    book_name = (data.get("book_name") or "").strip()
    operation_id = operations.begin(
        "publish:%s:create_book" % plat,
        {"task_id": task_id, "title": book_name}, task_id=task_id,
        metadata={"platform": plat, "action": "create_book"})

    def run():
        final_name = book_name
        try:
            b, page = _open_page(plat)
            # 书名全平台唯一（2026-09-30 马甲案）：撞名是建书常见死法，撞了
            # 原样报「疑似未建成」会白烧几万字稿费。试名队列=原名+自动候选
            # （编排者重出→机械变体兜底）；同名书恰在本账号下时对账复用不换
            # 名。人工提交模式（auto_submit=false）不自动换名——用户在页面
            # 上自己点提交，撞名他自己看得见。
            names = [book_name]
            cands_ready = False
            book_id = ""
            i = 0
            while i < len(names) and i < 1 + _ALT_COUNT + 2:
                name = names[i]
                i += 1
                final_name = name
                if i > 1:
                    logs.append("第 %d 次尝试，书名《%s》" % (i, name))
                values = mod.values_create_book(dict(data, book_name=name))
                steps = _with_tag_steps(load_flow(plat, "create_book"),
                                        mod.tag_groups(data), values, mod)
                try:
                    flow.run_flow(page, steps, values=values, config=mod.CONFIG,
                                  auto_submit=auto_submit, dup_check=auto_submit,
                                  shot=lambda n: page.screenshot(
                                      ledger.shot_path(plat, task_id, n)),
                                  log=logs.append)
                except flow.TitleDupError as e:
                    # 事前预检命中：平台实时校验已喊重名，点提交必被拒
                    logs.append(str(e))
                    bid = _resolve_book_id(plat, page, {"title": name})
                    if bid:
                        book_id, final_name = bid, name
                        logs.append("同名书就在本账号下，已复用 book_id=%s" % bid)
                        break
                    if not cands_ready:
                        names = names + _alt_titles(plat, data, task, logs)
                        cands_ready = True
                    if i >= len(names):
                        break               # 候选没准备出来（模型+机械全空）
                    continue
                # book_id：创建成功后平台跳书籍详情/编辑器，从 URL 提取
                # （番茄 book-info/<id>；七猫 information?id=<id>）。url_any 过了
                # 但页面还在跳转链上时再等几轮；提取落空把最终 URL 记进日志，
                # 别再静默登记空 id（发章只能兜底找书，直达编辑器就废了）。
                try:
                    for _try in range(6):
                        m_url = re.search(r"book-info/(\d+)|information\?id=(\d+)",
                                          str(page.url() or ""))
                        if m_url:
                            book_id = m_url.group(1) or m_url.group(2)
                            break
                        time.sleep(0.8)
                    if not book_id:
                        logs.append("book_id 提取落空，最终页面：%s"
                                    % str(page.url() or "")[:120])
                except Exception:
                    pass
                if book_id:
                    break
                if not auto_submit:
                    # 人工提交模式：按书名对账兜底即止，不自动换名
                    bid = _resolve_book_id(plat, page, {"title": name})
                    if bid:
                        book_id = bid
                        logs.append("book_id 提取落空，已按书名在平台对账找回 %s" % bid)
                    break
                # 自动提交被平台静默拒绝（不跳转）时，toast 常喊重名——抓
                # 回来判定，别落进「疑似未建成」的糊涂账
                hint = ""
                for _try in range(3):
                    hint = flow.dup_hint(page)
                    if hint:
                        break
                    time.sleep(1.0)
                if hint:
                    logs.append("页面提示：%s" % hint[:120])
                    bid = _resolve_book_id(plat, page, {"title": name})
                    if bid:
                        book_id, final_name = bid, name
                        logs.append("同名书就在本账号下，已复用 book_id=%s" % bid)
                        break
                    if not cands_ready:
                        names = names + _alt_titles(plat, data, task, logs)
                        cands_ready = True
                    continue
                # 非撞名失败：按书名对账兜底（原有逻辑），找不回就明说未建成
                bid = _resolve_book_id(plat, page, {"title": name})
                if bid:
                    book_id = bid
                    logs.append("book_id 提取落空，已按书名在平台对账找回 %s" % bid)
                break
            if book_id and final_name != book_name:
                # 换名成功（或复用同名旧书）：书名全链路同步到新名
                _sync_renamed_book(task_id, plat, task, data, final_name, logs)
            operation_status = "confirmed" if book_id else "unknown"
            if book_id:
                operations.confirm(operation_id, remote_receipt=book_id,
                                   metadata={"platform": plat, "action": "create_book"})
            else:
                operations.mark_unknown(
                    operation_id,
                    "建书流程走完，但平台作品列表按书名未找到《%s》，书很可能未建成；"
                    "请到平台作品管理确认，若已存在可人工登记" % final_name,
                    metadata={"platform": plat, "action": "create_book"})
            ledger.record(plat, "create_book", task_id=task_id, title=final_name,
                          book_id=book_id, ok=bool(book_id),
                          error=((("" if book_id else
                                   "建书流程走完但未取得 book_id，且平台按书名未找到"
                                   "《%s》，疑似未建成\n" % final_name)
                                  + "\n".join(logs))[:2000] or None),
                          shot=str(ledger.shot_path(plat, task_id, "")),
                          operation_id=operation_id, operation_status=operation_status,
                          remote_receipt=book_id)
            if book_id:
                # 未取得 id 不落台账：空条目会让界面亮「已建书」、校准盲跑，
                # 而发章/下次建书各自有按书名对账的兜底，空条目只剩害处。
                ledger.save_book(task_id, plat, {"book_id": book_id, "title": final_name,
                                                 "source": "create"})
            _set(plat, status="connected", error="")
        except Exception as e:
            operations.finish_exception(operation_id, e)
            operation_status = (operations.get(operation_id) or {}).get("status") or "failed"
            _set(plat, status="error", error="建书失败：%s" % e)
            ledger.record(plat, "create_book", task_id=task_id, title=book_name,
                          ok=False, error=str(e)[:300],
                          shot=str(ledger.shot_path(plat, task_id, "")),
                          operation_id=operation_id, operation_status=operation_status)
        finally:
            _save()

    threading.Thread(target=run, daemon=True,
                     name="pub-book-%s" % plat).start()
    return True, ""


# ---------------------------------------------------------------- 动作：登记已有作品
def register_book(task_id, plat, title, book_id=""):
    """人工登记平台已有作品：建书流程没走通、或用户纯手工在平台上建的
    书，补进台账让卡片翻到「已建书·发一章」，避免再点「创建作品」造出
    重复书。只动本地台账不碰浏览器；发章按书名找书，title 必填，
    book_id 选填（有直达 URL 时填）。已登记会覆盖更新（兼纠错口）。"""
    from .. import store
    if plat not in PLATFORMS:
        return False, "未知平台"
    if not store.get_task(task_id):
        return False, "任务不存在"
    title = (title or "").strip()
    if not title:
        return False, "作品名必填（发章按作品名在平台找书）"
    # 换绑另一本书（book_id/title 变了）时旧书的校准数作废，防张冠李戴
    old = ledger.book_for(task_id, plat) or {}
    if old and (old.get("book_id") != str(book_id or "").strip()
                or old.get("title") != title[:120]):
        ledger.update_book(task_id, plat, remote_total=None, remote_published=None,
                           remote_review=None, remote_rejected=None,
                           remote_synced_at=None, remote_error=None)
    ledger.save_book(task_id, plat, {"book_id": str(book_id or "").strip(),
                                     "title": title[:120], "source": "manual"})
    return True, ""


def _with_tag_steps(steps, groups, values, mod=None):
    """标签走数据驱动：清单进 values["_tags"]（[组名, 标签] 对），由 flow 的
    "tags" 步骤按组切换点选。组显示名映射来自**对应平台模块**的
    TAG_GROUP_LABELS（曾硬编码 qimao——番茄任务 key 撞名被套上七猫组名，
    插桩 text 变 list repr）。兼容旧流程表（无 tags 步骤时插桩）；流程表
    已用 {tag_x} 占位（番茄式 click_real）时不插桩——占位由 values 自答。"""
    flat = []
    labels = getattr(mod, "TAG_GROUP_LABELS", {}) if mod else {}
    for key, tags in groups or []:
        grp = labels.get(key, "")
        flat.extend([grp, str(t)] if grp and str(t).strip() else str(t)
                    for t in tags if str(t).strip())
    if not flat:
        return steps
    if any(st.get("do") == "tags" for st in steps):
        values["_tags"] = flat
        return steps
    if any("{tag_" in str(st.get("text") or "") for st in steps if st.get("text")):
        return steps                  # 流程表自带标签占位（番茄式）：values 已就绪
    tag_steps = [{"do": "click_text", "text": str(t), "contains": False,
                  "scope": "[class*=tag] li,span,label,[class*=label]"}
                 for t in flat]
    out, inserted = list(steps), False
    for i, st in enumerate(out):
        if st.get("do") in ("submit", "shot") and not inserted:
            out[i:i] = tag_steps
            inserted = True
    if not inserted:
        out += tag_steps
    return out


def _login_guard(plat):
    """动作前的登录前置：状态 connected 放行；否则现场复核一次。"""
    s = _st(plat)
    if s.get("status") == "connected":
        return True, ""
    try:
        _b, page = _open_page(plat)
        ok, url = _check_login(plat, page)
        if ok:
            _set(plat, status="connected")
            return True, ""
        return False, "平台未登录（当前页面 %s），请先点「连接平台」扫码" % url[:80]
    except BrowserError as e:
        return False, "浏览器不可用：%s" % e


def _url_values(mod, book):
    """平台模块提供的 URL 占位值（chapter_manage_url/draft_url/editor_url），
    逐键取、缺哪个跳哪个。曾按序连赋 + except AttributeError 兜底：draft_url
    只有七猫有，番茄走到第二行即断，editor_url 永远赋不上——{editor_url}
    残进 navigate，页面停在 about:blank 误报「未登录或改版」（0924 三连败）。"""
    out = {}
    for key in ("chapter_manage_url", "draft_url", "editor_url"):
        fn = getattr(mod, key, None)
        if callable(fn):
            out[key] = fn(book)
    return out


def _resolve_book_id(plat, page, book):
    """登记缺 book_id 时按书名在作家后台找回（平台模块可选提供
    resolve_book_id）。找不回返回空串：流程按无 id 兜底路径继续，让后续
    步骤如实报错，绝不在这里拦死。找回后回写 books.json，下次直达。"""
    fn = getattr(PLATFORMS[plat], "resolve_book_id", None)
    if not callable(fn) or str((book or {}).get("book_id") or "").strip():
        return ""
    try:
        return str(fn(page, book) or "").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------- 动作：发章
def read_chapter(fp):
    """读章节文件 → (章号, 标题, 正文, 错误)。UTF-8→GBK 回退（章稿乱码教训）。

    标题取首个「# 」标题行，没有则用文件名；章号从标题/文件名的
    「第X章」解析，解析不出记 0（台账不按章号幂等，只按标题留痕）。"""
    from pathlib import Path
    from .. import runner
    p = Path(fp)
    if not p.is_file():
        return 0, "", "", "章节文件不存在：%s" % p
    try:
        text = runner.read_text_any_enc(p)
    except Exception as e:
        return 0, "", "", "读章节文件失败：%s" % e
    lines = text.strip().splitlines()
    title = ""
    body_start = 0
    for i, ln in enumerate(lines[:5]):
        s = ln.strip()
        if s.startswith("#"):                 # 只认 markdown 标题行
            s = s.lstrip("#").strip()
            if s:
                title, body_start = s, i + 1
                break
    if not title:
        title = p.stem
    body = "\n".join(lines[body_start:]).strip()
    ch_no = ledger.parse_chapter_no(title) or ledger.parse_chapter_no(p.name)
    return ch_no, title, body, ""


def upload_chapter_async(task_id, plat, chapter_file, auto_submit=False,
                         force=False, force_confirmed=False, force_reason=""):
    """把一章发到平台（或填好待人工确认）。幂等：已成功发布的章号拒绝重发。"""
    from .. import store
    if plat not in PLATFORMS:
        return False, "未知平台"
    task = store.get_task(task_id)
    if not task:
        return False, "任务不存在"
    book = ledger.book_for(task_id, plat)
    if not book:
        return False, "该任务尚未在此平台建书，请先「创建作品」"
    if not book.get("book_id") and book.get("source") != "manual":
        return False, "该平台建书结果未确认（缺少作品 ID），请重试创建作品或登记已有作品"
    # 闭包中需要在找回 book_id 后更新书籍引用；使用独立副本避免
    # Python 将 book 误判为 run() 的局部变量，导致前置解析触发
    # UnboundLocalError。
    book_ref = dict(book)
    if _st(plat).get("status") == "busy":
        return False, "该平台有操作正在进行中"
    ch_no, title, body, err = read_chapter(chapter_file)
    if err:
        return False, err
    quality = _quality_release_guard(
        task, plat, action="publish", target_chapter=ch_no or None,
        force=force, force_confirmed=force_confirmed, force_reason=force_reason)
    if not quality.get("allowed"):
        return False, "质量门禁拦截：%s" % "；".join(quality.get("blockers") or [])
    n_chars = len(body.replace("\n", "").replace(" ", ""))
    if n_chars < 100:
        return False, "正文过短（%d 字），疑似未完成章节" % n_chars
    # 平台级下限（如七猫编辑器明示「最少 1000 字」：不足时发布被静默拦截）
    min_chars = int(PLATFORMS[plat].CONFIG.get("min_chapter_chars") or 100)
    if n_chars < min_chars:
        return False, ("正文 %d 字未达该平台下限（%d 字），补足后再发"
                       % (n_chars, min_chars))
    if n_chars > 30000:
        return False, "正文超长（%d 字），平台单章上限一般 2 万字" % n_chars
    done = ledger.published_chapters(task_id, plat)
    if ch_no and ch_no in done:
        return False, "第 %d 章已成功发布过（台账幂等拦截）；确需重发请手工处理" % ch_no
    ok_login, why = _login_guard(plat)
    if not ok_login:
        return False, why
    _set(plat, status="busy", last_action="upload_chapter", error="")

    mod = PLATFORMS[plat]
    values = {"chapter_title": title, "chapter_body": body,
              "book_name": book_ref.get("title") or ""}
    values.update(_url_values(mod, book_ref))   # verify/draft/editor 页 URL（流程占位）
    logs = []
    operation_id = operations.begin(
        "publish:%s:upload_chapter" % plat,
        {"task_id": task_id, "chapter_no": ch_no, "title": title}, task_id=task_id,
        metadata={"platform": plat, "action": "upload_chapter"})

    def run():
        try:
            b, page = _open_page(plat)
            bid = _resolve_book_id(plat, page, book_ref)
            if bid:                          # 建书时没提上 id 的书，发章前补账
                book_ref.update(book_id=bid)
                values.update(_url_values(mod, book_ref))
                ledger.save_book(task_id, plat, book_ref)
                logs.append("已按书名找回 book_id=%s 并更新登记" % bid)
            elif not book_ref.get("book_id"):
                raise RuntimeError("未按作品名找到远端作品，未打开章节编辑器；"
                                   "请检查平台作品名或补填作品 ID")
            flow.run_flow(page, load_flow(plat, "upload_chapter"), values=values,
                          config=mod.CONFIG, auto_submit=auto_submit,
                          shot=lambda n: page.screenshot(ledger.shot_path(plat, task_id, n)),
                          log=logs.append)
            if auto_submit:
                operations.confirm(operation_id, metadata={"platform": plat,
                                                            "action": "upload_chapter",
                                                            "chapter_no": ch_no})
                ledger.record(plat, "upload_chapter", task_id=task_id, chapter_no=ch_no,
                              book_id=book_ref.get("book_id") or "", title=title, ok=True,
                              operation_id=operation_id, operation_status="confirmed")
            else:
                # run_flow stops immediately before the submit step. Keep the
                # operation pending and never count the filled form as published.
                ledger.record(plat, "upload_chapter_pending", task_id=task_id,
                              chapter_no=ch_no, book_id=book_ref.get("book_id") or "",
                              title=title, ok=False, error="等待人工提交",
                              operation_id=operation_id, operation_status="pending")
            _set(plat, status="connected", error="")
        except Exception as e:
            operations.finish_exception(operation_id, e)
            operation_status = (operations.get(operation_id) or {}).get("status") or "failed"
            _set(plat, status="error", error="发章失败：%s" % e)
            ledger.record(plat, "upload_chapter", task_id=task_id, chapter_no=ch_no,
                          book_id=book_ref.get("book_id") or "", title=title,
                          ok=False, error=str(e)[:300], operation_id=operation_id,
                          operation_status=operation_status)
        finally:
            _save()

    threading.Thread(target=run, daemon=True,
                     name="pub-ch-%s" % plat).start()
    return True, ""


def confirm_manual_chapter(task_id, plat, chapter_no):
    """Record an explicit user confirmation after submitting in the browser."""
    from .. import store
    if plat not in PLATFORMS:
        return False, "未知平台"
    if not store.get_task(task_id):
        return False, "任务不存在"
    try:
        chapter_no = int(chapter_no or 0)
    except (TypeError, ValueError):
        chapter_no = 0
    if chapter_no <= 0:
        return False, "章号无效"
    if chapter_no in ledger.published_chapters(task_id, plat):
        return True, ""
    pending = next((r for r in ledger.recent(task_id=task_id, platform=plat, limit=100)
                    if r.get("action") == "upload_chapter_pending"
                    and int(r.get("chapter_no") or 0) == chapter_no), None)
    if not pending:
        return False, "没有找到待确认的填稿记录，请先重新填入该章"
    op_id = pending.get("operation_id") or ""
    current = operations.get(op_id) if op_id else None
    if current and current.get("status") == "pending":
        ok = operations.confirm(op_id, remote_receipt="manual-confirmed",
                                metadata={"platform": plat, "action": "upload_chapter"})
    elif current:
        ok = operations.reconcile(op_id, "confirmed", remote_receipt="manual-confirmed",
                                  metadata={"platform": plat, "action": "upload_chapter"})
    else:
        ok = True
    if not ok:
        return False, "待确认操作状态已变化，请重新校准后再试"
    ledger.record(plat, "upload_chapter", task_id=task_id, chapter_no=chapter_no,
                  book_id=pending.get("book_id") or "", title=pending.get("title") or "",
                  ok=True, operation_id=op_id, operation_status="confirmed",
                  remote_receipt="manual-confirmed")
    return True, ""


# ------------------------------------------------ 已发章数校准（平台实况对账）
# 台账只记得 CodeBee 自己发成功的章：用户在平台窗口里手工补交/平台驳回/
# 台账重复记录都会让「已发 N」漂移（2026-09-28 实案：番茄平台 8 章台账 7）。
# 校准 = 打开章节管理页数行，按状态关键词分桶后写回 books.json 的 remote_*。

_STATUS_PUB = "已发布"
_STATUS_REVIEW = ("待审核", "审核中", "排队")
_STATUS_BAD = ("未通过", "驳回")


def bucket_chapter_rows(rows):
    """章节管理页的行文本 → {total, published, review, rejected}。

    行里含状态关键词才计入对应桶；total=识别出的章节数据行数。"""
    out = {"total": 0, "published": 0, "review": 0, "rejected": 0}
    for t in rows or []:
        t = str(t)
        if not t:
            continue
        if "章节名称" in t:                     # 表头兜底（正常到不了这）
            continue
        out["total"] += 1
        if any(k in t for k in _STATUS_BAD):
            out["rejected"] += 1
        elif _STATUS_PUB in t:
            out["published"] += 1
        elif any(k in t for k in _STATUS_REVIEW):
            out["review"] += 1
    return out


def sync_published(task_id, plat, manual=False):
    """同步执行一次校准（manager.sync_published_async 的线程体）。

    manual=False（打开作品页自动触发）只 attach 在跑的浏览器实例，绝不
    静默 launch 新窗口吓人；manual=True（用户点「校准」按钮）才允许
    attach-or-launch。识别不到章节行（改版/未登录/分页）时只记 note
    不覆写 remote_*——宁可显示旧数也不把 0 当真相。"""
    from .browser import Browser
    book = ledger.book_for(task_id, plat)
    if not book:
        return False, "该任务未在此平台登记作品"
    if _st(plat).get("status") == "busy":
        return False, "该平台有操作正在进行中"
    mod = PLATFORMS[plat]
    js = mod.CONFIG.get("count_rows_js")
    if not js:
        return False, "该平台未配置章节计数"
    try:
        if manual:
            b, page = _open_page(plat)
        else:
            port = _st(plat).get("port") or 0
            b = Browser.attach(int(port))       # 实例已死 → 异常 → 只记原因
            page = b.first_page(create=True)
    except Exception as e:
        return False, "浏览器未连接，点「连接平台」后再校准"
    if not str(book.get("book_id") or "").strip():
        # book_id 空的登记（人工登记/未验证建书结果）别拿去拼 URL 盲跑：
        # 空 id 会落到作品列表页，数章节必然 0 行，报一句误导人的「可能改版」。
        # 先按书名对账，找回补账再校准；找不回就明说书可能没建成。
        bid = _resolve_book_id(plat, page, book)
        if bid:
            book = dict(book, book_id=bid)
            ledger.save_book(task_id, plat, book)   # 合并语义：保留 remote_*
        else:
            return False, ("平台作品列表按书名未找到《%s》，这本书可能尚未建成；"
                           "请到平台作品管理确认，若已存在可人工登记"
                           % (book.get("title") or ""))
    try:
        page.navigate(mod.chapter_manage_url(book), timeout=40)
        time.sleep(4.0)                         # SPA 表格慢渲染
        url = str(page.url() or "")
        if any(m in url for m in mod.CONFIG["login_url_marks"]) \
                or url.startswith("chrome-error://"):
            return False, "平台登录态已失效，请重连后校准"
        rows = page.call(js) or []
        st = bucket_chapter_rows(rows)
        if st["total"] <= 0:
            return False, "章节管理页未识别到章节列表（可能改版）"
        ledger.update_book(task_id, plat,
                           remote_total=st["total"],
                           remote_published=st["published"],
                           remote_review=st["review"],
                           remote_rejected=st["rejected"],
                           remote_synced_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        return True, ""
    except Exception as e:
        return False, "校准失败：%s" % str(e)[:160]


def _sync_preflight(task_id, plat, manual):
    """校准前置检查（同步、不碰浏览器页面）：登记在案/没在忙/平台支持；
    自动触发（manual=False）时还要求浏览器实例活着——绝不静默 launch。"""
    from .browser import Browser
    if plat not in PLATFORMS:
        return False, "未知平台"
    if not ledger.book_for(task_id, plat):
        return False, "该任务未在此平台登记作品"
    if _st(plat).get("status") == "busy":
        return False, "该平台有操作正在进行中"
    if not PLATFORMS[plat].CONFIG.get("count_rows_js"):
        return False, "该平台未配置章节计数"
    if not manual:
        port = _st(plat).get("port") or 0
        try:
            Browser.attach(int(port))
        except Exception:
            return False, "浏览器未连接，点「连接平台」后再校准"
    return True, ""


def sync_published_async(task_id, plat, manual=False):
    """校准进后台线程（浏览器操作绝不能堵 HTTP 线程）。

    返回 (ok, err)：预检失败时线程都不起，接口即时回报 skipped 原因。"""
    ok, err = _sync_preflight(task_id, plat, manual)
    if not ok:
        return False, err

    def run():
        ok2, err2 = sync_published(task_id, plat, manual=manual)
        if not ok2:
            ledger.update_book(task_id, plat, remote_error=err2,
                               remote_synced_at=time.strftime("%Y-%m-%d %H:%M:%S"))

    threading.Thread(target=run, daemon=True,
                     name="pub-sync-%s" % plat).start()
    return True, ""


def published_local(task_id, plat):
    """本地台账口径的已发章数（去重后的章号数，记录数会因重试虚高）。"""
    return len(ledger.published_chapters(task_id, plat))


def history(task_id=None, plat=None, limit=50):
    return ledger.recent(task_id=task_id, platform=plat, limit=limit)


_load()
_migrate_legacy_profiles()
