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

from .. import paths
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


def create_book_async(task_id, plat, auto_submit=False):
    """按任务 book_meta 的资料在平台建书。返回 (ok, err)。"""
    from .. import store
    if plat not in PLATFORMS:
        return False, "未知平台"
    task = store.get_task(task_id)
    if not task:
        return False, "任务不存在"
    meta = ((task.get("book_meta") or {}).get(plat) or {})
    if meta.get("status") != "done" or not meta.get("data"):
        return False, "请先生成该平台的作品信息"
    if _st(plat).get("status") == "busy":
        return False, "该平台有操作正在进行中"
    if ledger.book_for(task_id, plat):
        return False, "该任务已在此平台登记过作品，请直接发章"
    why = _create_preflight(plat, meta["data"])
    if why:
        return False, why
    ok_login, why = _login_guard(plat)
    if not ok_login:
        return False, why
    _set(plat, status="busy", last_action="create_book", error="")

    data = meta["data"]
    mod = PLATFORMS[plat]
    logs = []

    def run():
        book_name = (data.get("book_name") or "").strip()
        try:
            b, page = _open_page(plat)
            values = mod.values_create_book(data)
            steps = _with_tag_steps(load_flow(plat, "create_book"),
                                    mod.tag_groups(data), values, mod)
            flow.run_flow(page, steps, values=values, config=mod.CONFIG,
                          auto_submit=auto_submit,
                          shot=lambda n: page.screenshot(ledger.shot_path(plat, task_id, n)),
                          log=logs.append)
            # book_id：创建成功后平台跳书籍详情/编辑器，从 URL 提取
            # （番茄 book-info/<id>；七猫 information?id=<id>）。url_any 过了
            # 但页面还在跳转链上时再等几轮；提取落空把最终 URL 记进日志，
            # 别再静默登记空 id（发章只能兜底找书，直达编辑器就废了）。
            book_id = ""
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
            ledger.record(plat, "create_book", task_id=task_id, title=book_name,
                          book_id=book_id, ok=True,
                          error="\n".join(logs)[:2000],
                          shot=str(ledger.shot_path(plat, task_id, "")))
            ledger.save_book(task_id, plat, {"book_id": book_id, "title": book_name})
            _set(plat, status="connected", error="")
        except Exception as e:
            _set(plat, status="error", error="建书失败：%s" % e)
            ledger.record(plat, "create_book", task_id=task_id, title=book_name,
                          ok=False, error=str(e)[:300],
                          shot=str(ledger.shot_path(plat, task_id, "")))
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
    ledger.save_book(task_id, plat, {"book_id": str(book_id or "").strip(),
                                     "title": title[:120]})
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


def upload_chapter_async(task_id, plat, chapter_file, auto_submit=False):
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
    if _st(plat).get("status") == "busy":
        return False, "该平台有操作正在进行中"
    ch_no, title, body, err = read_chapter(chapter_file)
    if err:
        return False, err
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
              "book_name": book.get("title") or ""}
    values.update(_url_values(mod, book))   # verify/draft/editor 页 URL（流程占位）
    logs = []

    def run():
        try:
            b, page = _open_page(plat)
            bid = _resolve_book_id(plat, page, book)
            if bid:                          # 建书时没提上 id 的书，发章前补账
                book = dict(book, book_id=bid)
                values.update(_url_values(mod, book))
                ledger.save_book(task_id, plat, book)
                logs.append("已按书名找回 book_id=%s 并更新登记" % bid)
            flow.run_flow(page, load_flow(plat, "upload_chapter"), values=values,
                          config=mod.CONFIG, auto_submit=auto_submit,
                          shot=lambda n: page.screenshot(ledger.shot_path(plat, task_id, n)),
                          log=logs.append)
            ledger.record(plat, "upload_chapter", task_id=task_id, chapter_no=ch_no,
                          book_id=book.get("book_id") or "", title=title, ok=True)
            _set(plat, status="connected", error="")
        except Exception as e:
            _set(plat, status="error", error="发章失败：%s" % e)
            ledger.record(plat, "upload_chapter", task_id=task_id, chapter_no=ch_no,
                          book_id=book.get("book_id") or "", title=title,
                          ok=False, error=str(e)[:300])
        finally:
            _save()

    threading.Thread(target=run, daemon=True,
                     name="pub-ch-%s" % plat).start()
    return True, ""


def history(task_id=None, plat=None, limit=50):
    return ledger.recent(task_id=task_id, platform=plat, limit=limit)


_load()
_migrate_legacy_profiles()
