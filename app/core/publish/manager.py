# -*- coding: utf-8 -*-
"""发布管理：平台会话（attach-or-launch）、登录状态机、建书/发章后台线程。

状态机（data/publish/state.json 的 platforms.<id>.status）：
  none           从未连接
  waiting_login  浏览器已开等用户扫码（connect 后，轮询 15 分钟）
  connected      登录检测通过
  busy           发布动作进行中（结束回 connected / error）
  error          最后一次动作失败（error 字段带人话原因，可重试）

浏览器生命周期：每平台一个持久化 profile（data/publish/profiles/<id>），
登录态落在 profile 里。服务重启后按 state.json 记住的调试端口 attach
旧实例；实例已死才重新 launch——用户登录一次，之后无感。

与 bookmeta.generate_async 同款线程纪律：动作起后台线程即返回，前端靠
view()（SSE/轮询）看进度；线程内任何异常都落终态，绝不悬挂。
"""
from __future__ import annotations

import json
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
                    "profile": str(paths.PUBLISH_DIR / "profiles" / pid)}
    return {"platforms": out, "browser_found": bool(find_browser())}


def recover_orphans():
    """启动收尸：waiting_login / busy 的线程随进程重启死掉，统一改判 error。"""
    _load()
    n = 0
    for plat in PLATFORMS:
        if _st(plat).get("status") in ("waiting_login", "busy"):
            _set(plat, status="error", error="上次操作随服务重启中断，请重试")
            n += 1
    return n


# ---------------------------------------------------------------- 浏览器会话
def _profile_dir(plat):
    return paths.PUBLISH_DIR / "profiles" / plat


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
    """流程表：data/publish/flows-<plat>.json 覆盖内置默认（校准不改代码）。"""
    fp = paths.PUBLISH_DIR / ("flows-%s.json" % plat)
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
        _set(plat, status="error", error="等待登录超时（15 分钟），请重新点连接")

    threading.Thread(target=wait_login, daemon=True,
                     name="pub-login-%s" % plat).start()
    return True, ""


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
                                    mod.tag_groups(data), values)
            flow.run_flow(page, steps, values=values, config=mod.CONFIG,
                          auto_submit=auto_submit,
                          shot=lambda n: page.screenshot(ledger.shot_path(plat, task_id, n)),
                          log=logs.append)
            ledger.record(plat, "create_book", task_id=task_id, title=book_name,
                          ok=True, shot=str(ledger.shot_path(plat, task_id, "")))
            # book_id 拿不到（DOM 深链未知）：先用书名登记，发章按书名找书
            ledger.save_book(task_id, plat, {"book_id": "", "title": book_name})
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


def _with_tag_steps(steps, groups, values):
    """标签走数据驱动：清单进 values["_tags"]（[组名, 标签] 对），由 flow 的
    "tags" 步骤按组切换点选。组显示名映射来自平台模块 TAG_GROUP_LABELS
    （缺省平台无映射时退化为纯标签）。兼容旧流程表（无 tags 步骤时插桩）。"""
    from . import qimao as _qm
    flat = []
    for key, tags in groups or []:
        grp = getattr(_qm, "TAG_GROUP_LABELS", {}).get(key, "")
        flat.extend([grp, str(t)] if grp and str(t).strip() else str(t)
                    for t in tags if str(t).strip())
    if not flat:
        return steps
    if any(st.get("do") == "tags" for st in steps):
        values["_tags"] = flat
        return steps
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
    logs = []

    def run():
        try:
            b, page = _open_page(plat)
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
