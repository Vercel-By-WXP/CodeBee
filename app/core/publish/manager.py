# -*- coding: utf-8 -*-
"""发布管理：平台会话（attach-or-launch）、登录状态机、建书/发章后台线程。

状态机（data/publish/state.json 的 platforms.<id>.status）：
  none           从未连接
  waiting_login  平台要求登录或验证；保留页面，后续自动复核登录状态
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
import os  # noqa: F401 - 测试经 manager.os.environ 打桩
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict

from .. import atomic, chaptersafe, operations, paths
from . import fanqie, flow, ledger, qimao
from .browser import Browser, BrowserError, Page  # noqa: F401 - 测试 patch 面

PLATFORMS = {"fanqie": fanqie, "qimao": qimao}
LOGIN_WAIT_S = 15 * 60          # 扫码等待窗口
STATUS_FILE = paths.PUBLISH_DIR / "state.json"
# A login/challenge can outlive the interactive connect request (or the
# service can be restarted while the tab is still open).  Keep a small,
# passive watchdog so a completed verification is noticed without requiring
# another click on “连接平台”.  It never navigates a non-empty challenge tab
# and never attempts to solve credentials or CAPTCHA controls.
AUTH_WATCH_INTERVAL_S = 30.0
# Optional, already-issued browser auth state for unattended deployments.
# The default directory is outside the repository and is intentionally not
# created by the app.  A state file is a secret-bearing artifact: callers
# must provision it explicitly (or point CODEBEE_PUBLISH_AUTH_STATE at it).
AUTH_STATE_DIR_ENV = "CODEBEE_PUBLISH_AUTH_DIR"
AUTH_STATE_FILE_ENV = "CODEBEE_PUBLISH_AUTH_STATE"
AUTH_STATE_DEFAULT_DIR = Path.home() / ".codebee" / "publish_auth"

LOCK = threading.RLock()
AUTOMATIC_RELEASE_REASON = (
    "用户已启用全自动发布；系统按平台校准流程自动提交，"
    "质量门禁放行已写入发布台账"
)


def automatic_release_kwargs(auto_submit, force=False, force_confirmed=False,
                             force_reason="", as_draft=False):
    """Return explicit release override flags without granting auto bypasses.

    An unattended schedule requests the workflow; it does not replace task
    contract approval or prove that content passed review. A forced release
    remains an explicit operator action and must carry its audited confirmation
    and reason through unchanged.
    """
    return {"force": bool(force),
            "force_confirmed": bool(force_confirmed),
            "force_reason": str(force_reason or "")}
_state = {"platforms": {}}      # {plat: {status, port, at, error, last_login, last_action}}
_browsers: Dict[str, Browser] = {}  # plat → Browser（进程内缓存，alive 校验兜底）
_AUTH_WATCH_LOCK = threading.Lock()
_AUTH_WATCH_STOP = threading.Event()
_AUTH_WATCH_THREAD = None
_LOGIN_WAITERS_GUARD = threading.Lock()
_LOGIN_WAITERS = set()
_LOGIN_CANCEL_EVENTS = {}
_CREATE_BOOK_RECOVERY_GUARD = threading.Lock()
_CREATE_BOOK_RECOVERY = set()
_UPLOAD_RECOVERY_GUARD = threading.Lock()
_UPLOAD_RECOVERY = set()
_RECOVERY_CANCEL_EPOCH = {}
_AUTH_STATE_CACHE = {}
_AUTH_STATE_CACHE_LOCK = threading.Lock()
_AUTH_PROBE_CONNECTIONS = {}
_AUTH_PROBE_CONNECTIONS_LOCK = threading.Lock()

# A platform account slot owns one browser profile.  The persisted ``busy``
# flag is useful for the UI, but it is not an atomic reservation: two HTTP
# requests can both observe ``connected`` before either worker sets ``busy``.
# Keep an in-process single-flight lock per slot so create/upload/probe/sync
# cannot navigate the same profile concurrently, while different accounts
# retain independent locks and can run in parallel.
_ACTION_LOCKS = {}
_ACTION_LOCKS_GUARD = threading.Lock()
_LOGIN_CHALLENGE_MARKS = (
    "安全验证", "安全校验", "人机验证", "验证码", "请完成验证",
    "访问过于频繁", "异常请求", "拖动滑块", "captcha", "verify you are human",
)
# Some providers render the actual challenge in a cross-origin iframe. In
# that case the top-level document can expose neither the iframe text nor a
# useful title, while its URL still identifies the verification route.
_LOGIN_CHALLENGE_URL_MARKS = (
    "captcha", "challenge", "verify", "verification", "slider", "human-check",
    "security-check", "secverify", "geetest", "hcaptcha", "recaptcha",
)




# ---------------------------------------------------------------- 多账号
# 账号=独立浏览器 profile（~/.codebee/publish_profiles/<plat>@<acc>）+ 独立
# 状态槽（state.json platforms 键 = slot）。默认账号槽名就是平台名（老
# 安装零迁移）；书在哪台账号建的（books.json entry.account），发章/校准
# 就自动路由到那台——同平台双账号可并行操作（不同 profile 不同浏览器）。
ACCOUNTS_FILE = paths.PUBLISH_DIR / "accounts.json"


def base_plat(slot):
    """槽名（fanqie@a2）→ 基础平台名（fanqie）。"""
    return str(slot).split("@", 1)[0]


def slot_of(plat, account=""):
    """基础平台 + 账号 → 槽名。空账号=默认槽（=平台名，老数据零迁移）。"""
    acc = str(account or "").strip()
    if not acc:
        return str(plat)
    if plat not in PLATFORMS:
        raise ValueError("未知平台：%s" % plat)
    import re as _re
    if not _re.match(r"^[A-Za-z0-9_-]{1,16}$", acc):
        raise ValueError("账号 ID 只能是字母/数字/下划线/连字符，长度 1-16")
    return "%s@%s" % (plat, acc)


def load_accounts():
    try:
        d = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
        items = d.get("accounts") if isinstance(d, dict) else d
        return items if isinstance(items, list) else []
    except Exception:
        return []


def _save_accounts(items):
    with LOCK:
        # Account metadata may be used to select browser profiles.  Keep the
        # durable index private even though browser cookies live separately.
        atomic.write_json(ACCOUNTS_FILE, {"accounts": items}, mode=0o600, indent=1)


def list_accounts(plat=None):
    out = [dict(a) for a in load_accounts()
           if not plat or a.get("platform") == plat]
    out.sort(key=lambda a: (a.get("platform") or "", a.get("id") or ""))
    return out


def add_account(plat, acc_id, label=""):
    if plat not in PLATFORMS:
        return None, "未知平台：%s" % plat
    try:
        slot_of(plat, acc_id)
    except ValueError as e:
        return None, str(e)
    with LOCK:
        items = load_accounts()
        if any(a.get("platform") == plat and a.get("id") == acc_id for a in items):
            return None, "该账号已存在"
        items.append({"platform": plat, "id": acc_id,
                      "label": str(label or "")[:30],
                      "created_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        _save_accounts(items)
    return {"platform": plat, "id": acc_id, "label": str(label or "")[:30]}, ""


def remove_account(plat, acc_id):
    """删账号登记（不动 profile 目录与线上作品）。有书绑在该账号时拒绝——
    先解绑/换绑，防发章路由悬空。"""
    with LOCK:
        items = load_accounts()
        hit = next((a for a in items
                    if a.get("platform") == plat and a.get("id") == acc_id), None)
        if not hit:
            return False, "账号不存在"
        books = ledger.load_books()
        for _tid, plats in books.items():
            ent = (plats or {}).get(plat)
            if ent and str(ent.get("account") or "") == acc_id:
                return False, ("仍有作品绑定在该账号（%s），请先解绑"
                               % (ent.get("title") or ""))
        _save_accounts([a for a in items if a is not hit])
        slot = "%s@%s" % (plat, acc_id)
        _state["platforms"].pop(slot, None)
        _browsers.pop(slot, None)
        _save()
    return True, ""




# ---------------------------------------------------------------- 动作：建书


# ---------------------------------------------------------------- 动作：登记已有作品
def register_book(task_id, plat, title, book_id="", account=None):
    """人工登记平台已有作品：建书流程没走通、或用户纯手工在平台上建的
    书，补进台账让卡片翻到「已建书·发一章」，避免再点「创建作品」造出
    重复书。只动本地台账不碰浏览器；发章按书名找书，title 必填，
    book_id 选填（有直达 URL 时填）。已登记会覆盖更新（兼纠错口）。"""
    from .. import store
    if plat not in PLATFORMS:
        return False, "未知平台"
    if not store.get_task(task_id):
        return False, "任务不存在"
    if account is not None:
        try:
            slot_of(plat, account)
        except ValueError as e:
            return False, str(e)
    title = (title or "").strip()
    if not title:
        return False, "作品名必填（发章按作品名在平台找书）"
    book_id = str(book_id or "").strip()
    valid_id = getattr(PLATFORMS[plat], "book_id_valid", None)
    if book_id and callable(valid_id) and not valid_id(book_id):
        return False, ("作品 ID 格式不对；番茄请填章节管理页 URL 中的完整长 ID，"
                       "不要填短作品号")
    # 换绑另一本书（book_id/title 变了）时旧书的校准数作废，防张冠李戴
    old = ledger.book_for(task_id, plat) or {}
    if old and (old.get("book_id") != str(book_id or "").strip()
                or old.get("title") != title[:120]):
        ledger.update_book(task_id, plat, remote_total=None, remote_published=None,
                           remote_scheduled=None, remote_review=None,
                           remote_rejected=None,
                           remote_synced_at=None, remote_error=None,
                           remote_published_nos=None, remote_scheduled_nos=None,
                           remote_review_nos=None,
                           remote_last_publish_at=None, remote_last_schedule_at=None,
                           confirmed_published_through=None,
                           confirmed_published_at=None)
    ledger.save_book(task_id, plat, {"book_id": book_id,
                                     "title": title[:120], "source": "manual"},
                     account=account)
    return True, ""


def unbind_book(task_id, plat):
    """解除本地作品绑定：删 books.json 里沿链命中的平台登记条目。
    只动本地台账不碰浏览器/平台——线上书还在不在由用户先核实，这里
    一律清登记；清掉后卡片回「创建作品」态，可重建或改绑别的书。
    平台忙时拒改：在飞的建书/发章链收尾会 save_book 写回，边删边写
    会把绑定又立回来。"""
    from .. import store
    if plat not in PLATFORMS:
        return False, "未知平台"
    if not store.get_task(task_id):
        return False, "任务不存在"
    book = ledger.book_for(task_id, plat) or {}
    try:
        slot = slot_of(plat, book.get("account") or "")
    except (TypeError, ValueError):
        slot = plat
    action_lock = _try_action_lock(slot)
    if action_lock is None:
        return False, "该账号有操作正在进行中"
    if _st(slot).get("status") == "busy":
        action_lock.release()
        return False, "该平台有操作正在进行中"
    removed = ledger.remove_book(task_id, plat)
    action_lock.release()
    if not removed:
        return False, "该平台没有已登记的作品绑定"
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
    """动作前的登录前置，现场复核浏览器页面，避免 stale connected。"""
    ok_gate, why_gate = _unattended_attach_gate(plat)
    if not ok_gate:
        return False, why_gate
    try:
        _b, page = _open_page(plat, preserve_tabs=True)
        status = _st(plat).get("status")
        ok, url, checked_page = _probe_login_pages(plat, _b, page)
        auth_required = (not ok and _authentication_required(plat, url))
        if (not ok and (not auth_required or _page_is_empty(page)
                        or status in (None, ""))
                and (status != "waiting_login" or _page_is_empty(page))
                and checked_page is page):
            # Re-open an empty recovered tab once, including when the prior
            # challenge marker has disappeared. Keep non-empty challenge tabs
            # untouched while waiting for platform verification.
            ok, url = _check_login(plat, page, navigate=True)
            auth_required = (not ok and _authentication_required(plat, url))
        elif not ok and not auth_required and status != "waiting_login":
            ok, url = _check_login(plat, page, navigate=True)
        if ok:
            _set(plat, status="connected")
            return True, ""
        if _authentication_required(plat, url):
            error = "平台等待登录或安全验证，验证完成后自动重试：%s" % url[:160]
            _set(plat, status="waiting_login", error=error)
            return False, error
        return False, "平台未登录（当前页面 %s），请先点「连接平台」扫码" % url[:80]
    except BrowserError as e:
        return False, "浏览器不可用：%s" % e


def _url_values(mod, book):
    """平台模块提供的 URL 占位值（chapter_manage_url/draft_url/editor_url），
    逐键取、缺哪个跳哪个。曾按序连赋 + except AttributeError 兜底：draft_url
    只有七猫有，番茄走到第二行即断，editor_url 永远赋不上——{editor_url}
    残进 navigate，页面停在 about:blank 误报「未登录或改版」（0924 三连败）。"""
    out = {}
    for key in ("chapter_manage_url", "draft_url", "editor_url",
                "short_editor_url"):
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
# 裸标题行（中文数字章稿常态）：行首「第三十一章 绕开的路线」，无 # 前缀。
# 认不出会把标题行留进正文、标题回退成文件名 chapter-31——番茄编辑器
# 序号/标题分开填，两头全输（2026-10-08 实案）。
_BARE_HEAD_RE = re.compile(
    r"^\s*第\s*[0-9０-９一二三四五六七八九十百千零两]+\s*[章节回]")


def split_chapter_name(title):
    """「第三十一章 绕开的路线」→「绕开的路线」（番茄标题位不带章号前缀，
    序号单独填）。没有章号前缀、或前缀后没有正题 → 原样返回。"""
    title = str(title or "")
    m = ledger._CHAPTER_RE.search(title)
    if not m:
        return title
    rest = title[m.end():].strip(" ：:，,、.·-—－_　")
    return rest or title


def chapter_fill_values(ch_no, title, body, book_name) -> Dict[str, Any]:
    """发章流程的 values 字典。chapter_title=完整标题行（台账留痕/七猫流程
    锚点口径不变）；chapter_no=阿拉伯数字串（番茄「第 _ 章」序号框只认数字，
    2026-10-08 实案：流程有 fill 步骤但 values 从没给过这个键，序号永远
    空着被平台以「章节序号只支持阿拉伯数字」打回）；chapter_name=拆掉章号
    前缀的正题（番茄标题位）。章号解析不出给空串，fill 步骤按空值跳过。"""
    return {"chapter_title": str(title or ""),
            "chapter_scope": str(title or ""),
            "chapter_name": split_chapter_name(title),
            "chapter_no": str(int(ch_no)) if ch_no else "",
            "chapter_body": chaptersafe.strip_internal_blocks(body),
            "book_name": str(book_name or "")}


def read_chapter(fp):
    """读章节文件 → (章号, 标题, 正文, 错误)。UTF-8→GBK 回退（章稿乱码教训）。

    标题识别三档：markdown「# 」标题行 → 裸「第X章 标题」首行 → 文件名。
    标题行不进正文（平台编辑器序号/标题单独填，正文再带一遍读者会看到重复）。
    章号从标题/文件名的「第X章」解析，解析不出记 0（台账不按章号幂等，
    只按标题留痕）。"""
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
        if s.startswith("#"):                 # markdown 标题行
            s = s.lstrip("#").strip()
            if s:
                title, body_start = s, i + 1
                break
    if not title:
        for i, ln in enumerate(lines[:5]):    # 裸「第X章 …」首行
            s = ln.strip()
            if _BARE_HEAD_RE.match(s):
                title, body_start = s, i + 1
                break
    if not title:
        title = p.stem
    body = chaptersafe.strip_internal_blocks("\n".join(lines[body_start:]))
    ch_no = ledger.parse_chapter_no(title) or ledger.parse_chapter_no(p.name)
    return ch_no, title, body, ""


def upload_chapter_async(task_id, plat, chapter_file, auto_submit=True,
                         force=False, force_confirmed=False, force_reason="",
                         as_draft=False, scheduled=False, direct_today=False,
                         short_story=False,
                         _recovery=False, _batch=False, _recovery_epoch=None):
    """把一章自动发布到平台，或通过 as_draft 只保存草稿。

    幂等：已成功发布**或已存草稿**的章号拒绝重做——重复发会平台上出双章，
    重复存草稿会攒一箱重复稿。as_draft=True 走「存草稿」流程表
    （upload_chapter_draft）：内容落平台草稿箱不上线，不构成发布动作，
    质量闸不拦（发布时照拦）；存稿动作无需人工确认，适合批量连跑。
    short_story=True 走短故事单文档链（无章节标题/序号，正文 4000-70000），
    仅支持 as_draft，发布在草稿箱人工提交。"""
    from .. import contracts, store
    if plat not in PLATFORMS:
        return False, "未知平台"
    # Direct platform publication is always unattended. Keep accepting the
    # legacy argument so older clients remain compatible, but never let it
    # turn a single-chapter upload into a filled-form/manual-submit workflow.
    if not as_draft:
        auto_submit = True
    task = store.get_task(task_id)
    if not task:
        return False, "任务不存在"
    if not as_draft and not contracts.publish_allowed(task_id, unattended=True):
        return False, "任务需要先完成审批，才能发布产物"
    book = ledger.book_for(task_id, plat)
    if not book:
        return False, "该任务尚未在此平台建书，请先「创建作品」"
    if short_story and (scheduled or not as_draft):
        return False, ("短故事仅支持存草稿：正文按单文档整篇上（4000-70000 字），"
                       "发布请到平台草稿箱人工提交")
    if not book.get("book_id") and book.get("source") != "manual":
        return False, "该平台建书结果未确认（缺少作品 ID），请重试创建作品或登记已有作品"
    # 多账号路由：书在哪个账号建的（books entry.account），这章就发到
    # 那个账号的浏览器/状态槽——默认账号（无 account 字段）= 平台默认槽。
    acc = str(book.get("account") or "").strip()
    slot = slot_of(plat, acc)
    request_epoch = (int(_RECOVERY_CANCEL_EPOCH.get(slot, 0))
                     if _recovery_epoch is None else int(_recovery_epoch))
    if (_recovery and
            request_epoch != int(_RECOVERY_CANCEL_EPOCH.get(slot, 0))):
        return False, "平台恢复任务已被主动断开取消"
    # 闭包中需要在找回 book_id 后更新书籍引用；使用独立副本避免
    # Python 将 book 误判为 run() 的局部变量，导致前置解析触发
    # UnboundLocalError。
    book_ref = dict(book)
    valid_id = getattr(PLATFORMS[plat], "book_id_valid", None)
    if (book_ref.get("book_id") and callable(valid_id)
            and not valid_id(book_ref.get("book_id"))):
        # Legacy records sometimes stored the short public work number. It
        # opens an empty chapter page, so resolve the canonical id by title.
        book_ref["book_id"] = ""
    if _st(slot).get("status") == "busy":
        return False, "该平台有操作正在进行中"
    if as_draft:
        # 草稿与别的自动发布批次互斥（2026-10-10 实案：连载发章占着浏览器，
        # 草稿流 fill 被掐断连接）——单章草稿同样不让起
        from . import auto as _auto
        other = _auto.other_batch_running(task_id, plat, slot)
        if other:
            return False, ("检测到另一批自动发布进行中（任务 %s），平台浏览器会"
                           "被互踩；等它跑完再存草稿" % other)
    ch_no, title, body, err = read_chapter(chapter_file)
    if err:
        return False, err
    release_flags = {}
    if as_draft:
        # 草稿不上线：质量闸不适用（evaluate_release action=draft 直通），
        # 人工复核挪到「平台草稿箱 → 用户逐章检查后提交」那一步。
        quality = {"allowed": True}
    else:
        release_flags = automatic_release_kwargs(
            auto_submit, force=force, force_confirmed=force_confirmed,
            force_reason=force_reason)
        quality = _quality_release_guard(
            task, plat, action="publish", target_chapter=ch_no or None,
            **release_flags)
    if not quality.get("allowed"):
        return False, "质量门禁拦截：%s" % "；".join(quality.get("blockers") or [])
    # Keep the audit fields available to both the worker success path and its
    # exception path.  Previously they were only initialized in create_book,
    # so a failed Qimao upload masked the real flow error with NameError and
    # left the platform state harder to diagnose.
    quality_forced = bool(quality.get("forced") or release_flags.get("force"))
    quality_reason = str(release_flags.get("force_reason") or "")
    n_chars = len(body.replace("\n", "").replace(" ", ""))
    if n_chars < 100:
        return False, "正文过短（%d 字），疑似未完成章节" % n_chars
    if short_story:
        # 短故事单文档：编辑器顶栏明示 4000-70000（2026-10-22 实抓），
        # 与连载单章（1000 下限/约 2 万上限）不同口径
        min_chars = int(PLATFORMS[plat].CONFIG.get("short_story_body_min_chars")
                        or 4000)
        max_chars = int(PLATFORMS[plat].CONFIG.get("short_story_body_max_chars")
                        or 70000)
        if n_chars < min_chars:
            return False, ("短故事正文 %d 字未达平台下限（%d 字），补足后再存"
                           % (n_chars, min_chars))
        if n_chars > max_chars:
            return False, "短故事正文超长（%d 字），平台上限 %d 字" % (n_chars, max_chars)
    else:
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
    queued = ledger.scheduled_chapters(task_id, plat)
    if ch_no and ch_no in queued:
        return False, ("第 %d 章已进入定时发布队列（台账幂等拦截）；请等待平台发布"
                       % ch_no)
    if as_draft and ch_no and ch_no in ledger.drafted_chapters(task_id, plat):
        return False, ("第 %d 章已存过草稿（台账幂等拦截）；请到平台草稿箱检查后"
                       "提交发布，确需重存请先在平台删旧稿" % ch_no)
    # Reserve the account profile before the login recheck and keep the lock
    # until the worker finishes.  A persisted ``busy`` flag alone is subject
    # to a check-then-set race when two works share one account.
    action_lock = _try_action_lock(slot)
    if action_lock is None:
        return False, "该账号有操作正在进行中"
    if _st(slot).get("status") == "busy":
        action_lock.release()
        return False, "该平台有操作正在进行中"
    ok_login, why = _login_guard(slot)
    if not ok_login:
        if not _batch and _upload_retryable_error(plat, why):
            try:
                queued = _queue_upload_recovery(
                    task_id, plat, chapter_file, acc,
                    {"force": force, "force_confirmed": force_confirmed,
                     "force_reason": force_reason, "as_draft": as_draft,
                     "scheduled": scheduled, "direct_today": direct_today,
                     "_recovery_epoch": request_epoch},
                    ch_no, title, why)
            except Exception as exc:
                action_lock.release()
                return False, "登录态恢复任务启动失败：%s" % str(exc)[:160]
            if queued:
                action_lock.release()
                return True, ""
        action_lock.release()
        return False, why
    flow_key = "upload_chapter_draft" if as_draft else "upload_chapter"
    if short_story and as_draft:
        flow_key = "upload_short_story_draft"
    # A scheduled fallback uses the same editor fill chain unless a platform
    # ships a dedicated calibrated flow.  The action is still recorded as
    # scheduled so idempotency and progress accounting advance correctly.
    flow_action = "upload_chapter_scheduled" if scheduled else flow_key
    if scheduled:
        try:
            load_flow(plat, flow_action)
            flow_key = flow_action
        except Exception:
            action_lock.release()
            return False, "该平台未配置定时发布流程"
    if scheduled:
        auto_submit = True
    _set(slot, status="busy", last_action=flow_key, error="")

    mod = PLATFORMS[plat]
    values = chapter_fill_values(ch_no, title, body,
                                 book_ref.get("title") or "")
    values["publish_date"] = ""
    values["publish_time"] = "00:00"
    # 今天直发：平台弹「建议定时/有定时待发」也点否拒绝（新书首发指令）
    values["force_direct"] = bool(direct_today)
    # When the platform already has a queued chapter, continue its cadence.
    # The flow may still fall back to tomorrow/00:00 when no local slot is
    # known (for example a queue created manually before CodeBee was used).
    try:
        prior_slot = ledger.latest_scheduled_slot(task_id, plat)
    except Exception:
        prior_slot = None
    if prior_slot:
        # The local slot is the last chapter already in the queue.  The next
        # chapter must start on the following day; using the same date makes
        # Fanqie's daily quota reject the submission even when a later slot is
        # available.  Keep the platform prefill as a fallback only when it is
        # newer than this calculated cadence.
        try:
            import datetime as _dt
            _next_day = (_dt.datetime.strptime(prior_slot["date"], "%Y-%m-%d")
                         + _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        except Exception:
            _next_day = prior_slot["date"]
        values["scheduled_date"] = _next_day
        values["scheduled_time"] = prior_slot["time"]
        values["publish_date"] = _next_day
        values["publish_time"] = prior_slot["time"]
    # Qimao renders the numeric prefix itself in the chapter editor.  Passing
    # the source heading ("第1章 标题") would therefore produce
    # "第1章 第1章 标题" on the platform; keep the full heading for audit
    # records, but fill only the semantic title in this platform form.
    if plat == "qimao" and values.get("chapter_name"):
        values["chapter_title"] = values["chapter_name"]
    # 分卷：按本地卷计划解析目标卷（无计划=空串，volume 步骤按空值跳过，
    # 保持平台默认卷）；_volumes 是平台卷表共享缓存，弹窗新建的卷由流程
    # 步骤追加进来，跑完回写 books.json 供后续章节免建。
    try:
        from . import volumes as _volumes
        values["volume_name"] = _volumes.name_for(task.get("workdir"), ch_no, task=task)
    except Exception:
        values["volume_name"] = ""
    values["_volumes"] = list(book_ref.get("volumes") or [])
    values.update(_url_values(mod, book_ref))   # verify/draft/editor 页 URL（流程占位）
    if short_story:
        # 短故事流程的列表验证页占位（verify 步只吃 values 不吃 config）
        values["short_book_manage"] = str(mod.CONFIG.get("short_book_manage") or "")
        values["short_manage_url"] = str(mod.CONFIG.get("short_manage") or "")
    logs = []
    pending_key = ""
    if not _batch:
        pending_key = _remember_pending_upload(
            slot,
            {"task_id": task_id, "platform": plat, "account": acc,
             "chapter_file": str(chapter_file), "chapter_no": ch_no,
             "title": title, "auto_submit": True, "force": force,
             "force_confirmed": force_confirmed, "force_reason": force_reason,
             "as_draft": as_draft, "scheduled": scheduled,
             "direct_today": direct_today,
             "_recovery_epoch": request_epoch})
    try:
        operation_id = operations.begin(
            "publish:%s:upload_chapter" % plat,
            {"task_id": task_id, "chapter_no": ch_no, "title": title}, task_id=task_id,
            metadata={"platform": plat, "action": flow_key})
    except Exception as exc:
        _set(slot, status="error", error="发章操作登记失败：%s" % exc)
        action_lock.release()
        if not _batch and _upload_retryable_error(plat, exc):
            try:
                _queue_upload_recovery(
                    task_id, plat, chapter_file, acc,
                    {"force": force, "force_confirmed": force_confirmed,
                     "force_reason": force_reason, "as_draft": as_draft,
                     "scheduled": scheduled, "direct_today": direct_today,
                     "_recovery_epoch": request_epoch},
                    ch_no, title, exc)
            except Exception:
                pass
        return False, "发章操作登记失败：%s" % exc

    def run():
        started_at = time.monotonic()

        def elapsed_s():
            return round(max(0.0, time.monotonic() - started_at), 3)

        try:
            b, page = _open_page(slot)
            bid = _resolve_book_id(plat, page, book_ref)
            if bid:                          # 建书时没提上 id 的书，发章前补账
                book_ref.update(book_id=bid)
                values.update(_url_values(mod, book_ref))
                ledger.save_book(task_id, plat, book_ref)
                logs.append("已按书名找回 book_id=%s 并更新登记" % bid)
            elif not book_ref.get("book_id"):
                raise RuntimeError("未按作品名找到远端作品，未打开章节编辑器；"
                                   "请检查平台作品名或补填作品 ID")
            flow.run_flow(page, load_flow(plat, flow_key), values=values,
                          config=mod.CONFIG, auto_submit=auto_submit,
                          shot=lambda n: page.screenshot(ledger.shot_path(plat, task_id, n)),
                          log=logs.append)
            new_vols = values.get("_volumes")
            if isinstance(new_vols, list) and new_vols != (book_ref.get("volumes") or []):
                ledger.update_book(task_id, plat, volumes=new_vols)
            if as_draft:
                # 草稿已落平台草稿箱（流程含验证步，走到这=验证过）。发布确认权
                # 仍在用户：到平台草稿箱逐章检查后手工提交，质量闸在「发布」时
                # 照常把关。
                operations.confirm(operation_id, metadata={"platform": plat,
                                                           "action": "upload_chapter_draft",
                                                           "chapter_no": ch_no})
                ledger.record(plat, "upload_chapter_draft", task_id=task_id,
                              chapter_no=ch_no, book_id=book_ref.get("book_id") or "",
                              title=title, ok=True, operation_id=operation_id,
                              operation_status="confirmed",
                              remote_receipt="platform-draft", account=acc,
                              duration_s=elapsed_s())
            elif auto_submit:
                recorded_action = ("upload_chapter_scheduled"
                                    if scheduled or values.get("_scheduled_by_platform")
                                    else flow_key)
                operations.confirm(operation_id, metadata={"platform": plat,
                                                            "action": recorded_action,
                                                            "chapter_no": ch_no})
                ledger.record(plat, recorded_action, task_id=task_id, chapter_no=ch_no,
                              book_id=book_ref.get("book_id") or "", title=title, ok=True,
                              operation_id=operation_id, operation_status="confirmed",
                              scheduled_date=(str(values.get("scheduled_date") or "")
                                              if recorded_action == "upload_chapter_scheduled" else ""),
                              scheduled_time=(str(values.get("scheduled_time") or "")
                                              if recorded_action == "upload_chapter_scheduled" else ""),
                              quality_forced=quality_forced,
                              quality_reason=quality_reason, account=acc,
                              duration_s=elapsed_s())
            if pending_key:
                _forget_pending_upload(slot, pending_key)
            _set(slot, status="connected", error="")
        except Exception as e:
            operations.finish_exception(operation_id, e)
            operation_status = (operations.get(operation_id) or {}).get("status") or "failed"
            _set(slot, status="error", error="发章失败：%s" % e)
            ledger.record(plat, flow_key, task_id=task_id, chapter_no=ch_no,
                          book_id=book_ref.get("book_id") or "", title=title,
                          ok=False, error=str(e)[:300], operation_id=operation_id,
                          operation_status=operation_status,
                          quality_forced=quality_forced,
                          quality_reason=quality_reason, account=acc,
                          duration_s=elapsed_s())
            if not _batch and _upload_retryable_error(plat, e):
                try:
                    _queue_upload_recovery(
                        task_id, plat, chapter_file, acc,
                        {"force": force, "force_confirmed": force_confirmed,
                         "force_reason": force_reason, "as_draft": as_draft,
                         "scheduled": scheduled, "direct_today": direct_today,
                         "_recovery_epoch": request_epoch},
                        ch_no, title, e)
                except Exception:
                    pass
            elif not _batch:
                _forget_pending_upload(slot, pending_key)
        finally:
            _save()
            action_lock.release()

    try:
        threading.Thread(target=run, daemon=True,
                         name="pub-ch-%s" % plat).start()
    except Exception as exc:
        error = "发章任务启动失败：%s" % str(exc)[:180]
        operations.finish_exception(operation_id, exc)
        operation_status = (operations.get(operation_id) or {}).get("status") or "failed"
        _set(slot, status="connected", error=error)
        ledger.record(plat, flow_key, task_id=task_id, chapter_no=ch_no,
                      book_id=book_ref.get("book_id") or "", title=title,
                      ok=False, error=error[:300], operation_id=operation_id,
                      operation_status=operation_status,
                      quality_forced=quality_forced,
                      quality_reason=quality_reason, account=acc)
        _save()
        action_lock.release()
        if not _batch and _upload_retryable_error(plat, exc):
            try:
                _queue_upload_recovery(
                    task_id, plat, chapter_file, acc,
                    {"force": force, "force_confirmed": force_confirmed,
                     "force_reason": force_reason, "as_draft": as_draft,
                     "scheduled": scheduled, "direct_today": direct_today,
                     "_recovery_epoch": request_epoch},
                    ch_no, title, exc)
            except Exception:
                pass
        elif not _batch:
            _forget_pending_upload(slot, pending_key)
        return False, error
    return True, ""


def schedule_chapter_async(task_id, plat, chapter_file, force=False,
                           force_confirmed=False, force_reason=""):
    """Fill and submit one chapter through the platform's scheduled-publish path.

    This is the recovery path for an automatic immediate publish that did not
    produce a confirmed receipt.  It is deliberately separate from the normal
    upload action so the ledger and idempotency checks know the chapter is
    already queued on the platform.
    """
    # Fail before opening the browser when this platform has no explicit,
    # calibrated timed-publish flow.  This also prevents tests/mocks or a
    # generic immediate flow from being mistaken for a scheduling receipt.
    try:
        load_flow(plat, "upload_chapter_scheduled")
    except (KeyError, TypeError, ValueError, FileNotFoundError):
        return False, "该平台未配置定时发布流程"
    return upload_chapter_async(task_id, plat, chapter_file,
                                auto_submit=True, force=force,
                                force_confirmed=force_confirmed,
                                force_reason=force_reason,
                                scheduled=True)


def confirm_manual_chapter(task_id, plat, chapter_no):
    """Record an explicit user confirmation after submitting in the browser."""
    from .. import store
    from pathlib import Path
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
        # Auto-submit attempts do not leave an upload_chapter_pending row: the
        # worker records a failed upload instead.  If the user then submits
        # the already-filled platform dialog by hand, refusing the
        # confirmation leaves that chapter at the head of the queue forever
        # and the next run tries to publish it again.  Accept an explicit
        # manual confirmation only when the matching local chapter exists, so
        # a stray chapter number cannot be used to manufacture a receipt.
        task = store.get_task(task_id) or {}
        wd = str(task.get("workdir") or "").strip()
        candidate = None
        if wd and Path(wd).is_dir():
            direct = Path(wd) / ("chapter-%d.md" % chapter_no)
            if direct.is_file():
                candidate = direct
            else:
                for fp in Path(wd).rglob("*"):
                    if (fp.is_file() and fp.suffix.lower() in (".md", ".txt")
                            and ledger.parse_chapter_no(fp.name) == chapter_no):
                        candidate = fp
                        break
        if candidate is None:
            return False, "没有找到待确认的填稿记录或对应章节文件，请先重新填入该章"
        _no, title, _body, read_err = read_chapter(str(candidate))
        if read_err or _no != chapter_no:
            return False, "对应章节文件无法校验：%s" % (read_err or "章号不一致")
        book = ledger.book_for(task_id, plat) or {}
        if not book:
            # Older runtime/task snapshots can lose the serial-chain link even
            # though the failed publish rows still carry the authoritative
            # platform book id.  Reuse that id for an explicit user recovery;
            # never invent a binding from a bare chapter number.
            failed = next((r for r in ledger.recent(task_id=task_id,
                                                    platform=plat, limit=100)
                           if int(r.get("chapter_no") or 0) == chapter_no
                           and r.get("book_id")), None)
            if failed:
                book = {"book_id": failed.get("book_id"),
                        "title": failed.get("title") or title}
        if not book:
            return False, "该任务尚未登记此平台作品"
        op_id = operations.begin(
            "publish:%s:manual-confirm" % plat,
            {"task_id": task_id, "chapter_no": chapter_no, "title": title},
            task_id=task_id,
            metadata={"platform": plat, "action": "upload_chapter",
                      "manual": True})
        if not operations.confirm(op_id, remote_receipt="manual-confirmed",
                                  metadata={"platform": plat,
                                            "action": "upload_chapter",
                                            "chapter_no": chapter_no,
                                            "manual": True}):
            return False, "人工确认操作状态已变化，请稍后重试"
        ledger.record(plat, "upload_chapter", task_id=task_id,
                      chapter_no=chapter_no, book_id=book.get("book_id") or "",
                      title=title, ok=True, operation_id=op_id,
                      operation_status="confirmed",
                      remote_receipt="manual-confirmed")
        # There was no queued fill operation to resume.  Do not immediately
        # launch a new batch from this recovery path; the caller can inspect
        # the recalculated queue and explicitly start the next chapter.
        return True, "本章已登记为手工发布，后续章节未自动启动"
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
    # The browser confirmation is the hand-off point for a manual batch.  A
    # successful receipt must immediately resume the remaining chapters with
    # the fully automatic submit path; otherwise the UI appears stuck after
    # the user clicks "已发布".
    try:
        from . import auto
        started, reason = auto.publish_pending_async(task_id, plat,
                                                     auto_submit=True)
    except Exception as exc:
        started, reason = False, str(exc)
    if not started:
        # The chapter itself is confirmed, so surface a useful warning while
        # keeping the confirmation successful.  The client can retry the
        # batch after the platform becomes idle.
        if "没有待发章节" in str(reason or ""):
            return True, "本章已确认，已无待发章节"
        return True, "本章已确认，后续章节未启动：%s" % (reason or "未知原因")
    return True, ""


# ------------------------------------------------ 已发章数校准（平台实况对账）
# 台账只记得 CodeBee 自己发成功的章：用户在平台窗口里手工补交/平台驳回/
# 台账重复记录都会让「已发 N」漂移（2026-09-28 实案：番茄平台 8 章台账 7）。
# 校准 = 打开章节管理页数行（跨页+跨卷），按状态关键词分桶后写回 books.json
# 的 remote_*。行收集/分桶在 chaprows（本文件超行数基线拆出，2026-10-08）。
from .chaprows import bucket_chapter_rows, collect_all_volumes  # noqa: E402

from .publish_create import (  # noqa: E402,F401 - 旧调用面再导出
    _ALT_COUNT,
    _ALT_TIMEOUT,
    _TITLE_MAX,
    _alt_titles,
    _create_preflight,
    _forget_pending_create,
    _forget_pending_upload,
    _mech_titles,
    _queue_create_book_auth_recovery,
    _queue_upload_recovery,
    _reconcile_upload_before_retry,
    _remember_pending_create,
    _remember_pending_upload,
    _resume_pending_create_recoveries,
    _resume_pending_upload_recoveries,
    _sync_renamed_book,
    _upload_pending_key,
    _upload_receipt_exists,
    _upload_retryable_error,
    create_book_async,
)

from .publish_browser import (  # noqa: E402,F401 - 旧调用面再导出
    _authentication_required,
    _challenge_page_is_relevant,
    _check_login,
    _clear_auth_page,
    _connect_unattended,
    _ensure_browser,
    _finalize_login_wait,
    _flow_contract_ok,
    _flow_text_clean,
    _live_pages,
    _open_page,
    _page_is_empty,
    _probe_login_pages,
    _remember_auth_page,
    _unattended_attach_gate,
    browser_running,
    connect,
    disconnect,
    load_flow,
    probe_form_async,
)

from .publish_auth import (  # noqa: E402,F401 - 旧调用面再导出
    _action_lock,
    _auth_cookie_items,
    _auth_page_for_target,
    _auth_script_entries,
    _auth_state_changed,
    _auth_state_path,
    _auth_storage_origins,
    _auth_storage_script,
    _auth_watch_loop,
    _auth_watch_once,
    _connected_session_probe_once,
    _host_allowed_for_platform,
    _load,
    _migrate_legacy_profiles,
    _platform_host,
    _platform_origin,
    _profile_dir,
    _profiles_base,
    _quality_release_guard,
    _read_auth_state,
    _remove_auth_scripts,
    _save,
    _seed_auth_state,
    _set,
    _st,
    _try_action_lock,
    _wait_chapter_rows_ready,
    recover_orphans,
    resume_saved_sessions,
    saved_session_slots,
    start_auth_watchdog,
    stop_auth_watchdog,
    view,
)



def sync_published(task_id, plat, manual=False):
    """Run calibration under the same per-account browser lock as publishing."""
    book = ledger.book_for(task_id, plat)
    if not book:
        return False, "该任务未在此平台登记作品"
    try:
        slot = slot_of(plat, book.get("account") or "")
    except (TypeError, ValueError):
        slot = plat
    action_lock = _try_action_lock(slot)
    if action_lock is None:
        return False, "该账号有操作正在进行中"
    try:
        return _sync_published_impl(task_id, plat, manual=manual)
    finally:
        action_lock.release()


def _sync_published_impl(task_id, plat, manual=False):
    """同步执行一次校准（manager.sync_published_async 的线程体）。

    manual=False（打开作品页自动触发）只 attach 在跑的浏览器实例，绝不
    静默 launch 新窗口吓人；manual=True（用户点「校准」按钮）才允许
    attach-or-launch。行收集走 _collect_all_volumes（跨页+跨卷）；
    识别不到章节行（改版/未登录）时只记 note 不覆写 remote_*——宁可
    显示旧数也不把 0 当真相。"""
    from .browser import Browser
    book = ledger.book_for(task_id, plat)
    if not book:
        return False, "该任务未在此平台登记作品"
    try:
        slot = slot_of(plat, book.get("account") or "")
    except (TypeError, ValueError):
        slot = plat
    if _st(slot).get("status") == "busy":
        return False, "该平台有操作正在进行中"
    mod = PLATFORMS[plat]
    js = mod.CONFIG.get("count_rows_js")
    if not js:
        return False, "该平台未配置章节计数"
    try:
        if manual:
            b, page = _open_page(slot, preserve_tabs=True)
        else:
            port = _st(slot).get("port") or 0
            b = Browser.attach(int(port))       # 实例已死 → 异常 → 只记原因
            page = b.first_page(create=True)
    except Exception as e:
        return False, "浏览器未连接，点「连接平台」后再校准"
    waiting_login = _st(slot).get("status") == "waiting_login"
    # While a challenge is already being shown, inspect all tabs passively and
    # leave the verification page in place. A connected/stale session follows
    # the normal reconciliation navigation once, then probes every tab so a
    # second-tab challenge cannot be hidden by the author page.
    if waiting_login:
        ok_login, login_state, checked_page = _probe_login_pages(slot, b, page)
        if not ok_login:
            if _authentication_required(plat, login_state):
                _set(slot, status="waiting_login", error=str(login_state)[:160])
            return False, "平台登录态等待恢复，验证完成后自动重试：%s" % str(login_state)[:160]
        if checked_page is not None:
            page = checked_page
    _set(slot, status="connected", error="")
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
        ok_login, login_state, checked_page = _probe_login_pages(slot, b, page)
        if not ok_login:
            if _authentication_required(plat, login_state):
                _set(slot, status="waiting_login", error=str(login_state)[:160])
            return False, "平台登录态待恢复，验证完成后自动重试：%s" % str(login_state)[:160]
        if checked_page is not None:
            page = checked_page
        _wait_chapter_rows_ready(page, mod, timeout=4.0)
        rows, vols = collect_all_volumes(page, mod)
        st = bucket_chapter_rows(rows)
        if st["total"] <= 0:
            # A newly-created work has no table rows yet.  Accept only an
            # explicit platform empty-state marker; a blank page still means
            # the page changed or the session is not ready.
            markers = mod.CONFIG.get("empty_chapter_markers") or []
            body_text = ""
            evaluate = getattr(page, "evaluate", None)
            if callable(evaluate):
                try:
                    body_text = str(evaluate("document.body && document.body.innerText || ''") or "")
                except Exception:
                    body_text = ""
            if not any(str(marker) and str(marker) in body_text for marker in markers):
                return False, "章节管理页未识别到章节列表（可能改版）"
        extra = {"volumes": vols} if vols else {}
        ledger.update_book(task_id, plat,
                           remote_total=st["total"],
                           remote_published=st["published"],
                           remote_scheduled=st["scheduled"],
                           remote_review=st["review"],
                           remote_draft=st.get("draft", 0),
                           remote_rejected=st["rejected"],
                           remote_published_nos=st["published_nos"],
                           remote_scheduled_nos=st["scheduled_nos"],
                           remote_review_nos=st["review_nos"],
                           remote_draft_nos=st.get("draft_nos", []),
                           remote_last_publish_at=st["last_publish_at"],
                           remote_last_schedule_at=st["last_schedule_at"],
                           remote_synced_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                           **extra)
        return True, ""
    except Exception as e:
        return False, "校准失败：%s" % str(e)[:160]


def _sync_preflight(task_id, plat, manual):
    """校准前置检查（同步、不碰浏览器页面）：登记在案/没在忙/平台支持；
    自动触发（manual=False）时还要求浏览器实例活着——绝不静默 launch。"""
    from .browser import Browser
    if plat not in PLATFORMS:
        return False, "未知平台"
    book = ledger.book_for(task_id, plat)
    if not book:
        return False, "该任务未在此平台登记作品"
    try:
        slot = slot_of(plat, book.get("account") or "")
    except (TypeError, ValueError):
        slot = plat
    # The persisted status can still be ``connected`` while a calibration
    # worker owns the in-process reservation.  Check the same reservation used
    # by publishing so an async calibration request fails immediately instead
    # of opening a competing browser page.
    action_lock = _try_action_lock(slot)
    if action_lock is None:
        return False, "该账号有操作正在进行中"
    action_lock.release()
    if _st(slot).get("status") == "busy":
        return False, "该平台有操作正在进行中"
    if not PLATFORMS[plat].CONFIG.get("count_rows_js"):
        return False, "该平台未配置章节计数"
    if not manual:
        port = _st(slot).get("port") or 0
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
