# -*- coding: utf-8 -*-
"""产品形态（profile）：一个内核，多个垂直发行版。

设计约束只有一条：**形态是数据，不是代码。** 新增一个版本 = 往
``data/profiles/`` 放一个 JSON，不改内核、不新建分支、不复制 UI。

一个形态声明四件事：

1. ``flows``        可建/可见的任务类型白名单（前端按 ``/api/flows`` 动态渲染，
                    改这一处下拉、菜单、任务网格一起变，前端零改动）；
2. ``ui``           自己的界面入口（相对 ``paths.UI_DIR``）；留空用内核默认界面；
3. ``features``     入口开关（代码任务、Git 工作台、禅道、MCP…）；
4. ``default_port`` 可选，独立端口，方便同一台机器上并存两个版本。

另有两个可选字段，管的是「这一版放哪些专家」：

- ``agents``  显式点名。点名即放出来，**即使那个专家还是草稿**。适合「我这一版
              就是要用它，风险我认」；不写则按内核的可见规则（非草稿）来。
- ``drafts``  是否放草稿。给开发者自用的 CodeBee 版用，正式发行版不要开。

``flows`` 和 ``agents`` 是两件事：前者管「能不能创建这类任务」，后者管
「界面里出不出现这个专家」。隐藏一个专家不等于删掉能力 —— 已经存在的任务
照样能跑完，这很重要，否则一次收敛就会把用户手里正在写的东西变成孤儿。

选择顺序（``select()``）：

1. ``--profile`` 显式指定 —— **严格**：名字写错直接报错并列出可选，不猜；
2. 环境变量 ``CODEBEE_PROFILE`` —— **宽松**：可能是残留变量，写错就警告后继续；
3. ``--profile`` 都没给且 stdin 是终端时 —— 交互式菜单（12 秒无输入自动选上次）；
4. 上次选择（``data/profile_state.json``）；
5. ``default``。

之所以 1 严格、2 宽松：命令行参数是人当场敲的，错了必须立刻告诉他；
环境变量可能来自很久以前的 shell 配置，它不该拖住服务启动。

数据不隔离：所有形态共用同一个 ``paths.DATA_DIR``。所以**不要**拿两个端口指向
同一个数据目录同时跑（双写进程会互相踩），要么换端口换目录，要么停一个再起。
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time

from . import paths

_LOCK = threading.RLock()
# None = 尚未显式设置，按「选择顺序」解析；设置后在本进程内固定。
_active = None
# 本次进程的形态是怎么定下来的：cli / env / prompt / last / default。
# 只有 ``default`` 意味着「用户从没表过态」——启动器据此决定要不要先给选择页。
# 命令行切形态（POST /api/profiles/active）也会把它改成 ``ui``。
_source = ""

DEFAULT = "default"
ENV_VAR = "CODEBEE_PROFILE"
NO_PROMPT_VAR = "CODEBEE_NO_PROMPT"
SCAFFOLD_HINT = 'python app/main.py --new-profile <id> --profile-name "名字"'

# features 的每一项都是「入口是否出现」的声明，不是权限控制。
# 权限仍由 action_policy / 发布契约审批负责，这里只决定界面给不给入口。
_ALL_ON = {
    "code_tasks": True,
    "git_workbench": True,
    "zentao": True,
    "mcp": True,
    "mobile": True,
    "publish": True,
    "decisions": True,
    "market": True,
}

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# 内置形态。能内置的只有「内核自己的界面」和「随内核发布的发行版」；
# 第三方/自建形态一律走 data/profiles/*.json，避免每次都要改这个文件。
_BUILTIN = {
    DEFAULT: {
        "id": DEFAULT,
        "name": "CodeBee",
        "tagline": "All agents, one CodeBee.",
        "order": 0,
        # None = 不收敛，全部内置流程可见
        "flows": None,
        "agents": None,
        "drafts": False,
        "default_flow": "",
        "ui": "",
        "features": dict(_ALL_ON),
    },
    # CodeBee 版：全部流程都在，但按「专职 Agent」组织成四种工作台。
    # 它演示的是内核的通用形态——新增垂直版只需再写一份定义。
    # ``drafts`` 是它为开发者自己留的：草稿专家也放出来，方便边做边看；
    # 正式发行版不要开这一项。
    "hive": {
        "id": "hive",
        "name": "CodeBee",
        "tagline": "每个专家只管一件事",
        "order": 5,
        "flows": None,
        "agents": None,
        "drafts": True,
        "default_flow": "",
        "ui": "",
        "features": dict(_ALL_ON),
    },
    # 小说发行版：只留创作类的四个流程。
    # ``agents`` 点名放开 rank_scan（选材官）：内核里它还是草稿，但这一版
    # 的「选题」环节离不开它，风险只是给建议，不给不可回滚的动作。
    "novel": {
        "id": "novel",
        "name": "CodeBee 小说版",
        "tagline": "你只管故事，剩下的交给 CodeBee",
        "order": 10,
        "flows": ("novel", "short_story", "serial_novel", "rank_scan"),
        "agents": ("novel", "short_story", "serial_novel", "rank_scan"),
        "drafts": False,
        "default_flow": "short_story",
        "ui": "",
        "features": dict(
            _ALL_ON,
            code_tasks=False,
            git_workbench=False,
            zentao=False,
            mcp=False,
        ),
    },
    # 成稿台发行版：文档/标书作者的入口。
    # 与小说版同一内核，只换入口页——界面差异来自产物形态（要件/章节树），
    # 不来自流程数量。发文类流程全部收敛到这里，避免作者面对 21 个入口。
    "dossier": {
        "id": "dossier",
        "name": "CodeBee 成稿台",
        "tagline": "要件齐不齐，交稿前先自己查一遍",
        "order": 20,
        "flows": ("bid_doc", "tech_proposal", "doc", "presentation",
                  "weekly_report", "email", "resume", "translation"),
        "agents": ("bid_doc", "tech_proposal", "doc", "presentation",
                   "weekly_report", "email", "resume", "translation"),
        "drafts": False,
        "default_flow": "bid_doc",
        "ui": "",
        "features": dict(
            _ALL_ON,
            code_tasks=False,
            git_workbench=False,
            zentao=False,
            mcp=False,
        ),
    },
}


class ProfileError(ValueError):
    """显式指定的形态不存在（命令行参数写错）。"""


# ---------------------------------------------------------------- 注册表

def spec_dir():
    return paths.DATA_DIR / "profiles"


def state_file():
    return paths.DATA_DIR / "profile_state.json"


def _signature():
    """spec 目录的指纹：文件增删改都会变，用来判断缓存是否过期。"""
    d = spec_dir()
    try:
        items = sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size)
                       for p in d.glob("*.json"))
    except OSError:
        items = []
    return (str(d), tuple(items))


_REG_CACHE = {"sig": None, "data": None}


def _known_flow_ids():
    try:
        from . import flows as flows_mod
        return {str(f.get("id") or "") for f in flows_mod.list_flows()}
    except Exception:
        # 流程注册表读不出来时不做收敛校验（宁可放过，也不要让界面空白）
        return None


def _known_agent_ids():
    try:
        from . import agents as agents_mod
        return set(agents_mod.registry().keys())
    except Exception:
        return None


def _norm_spec(raw, origin=""):
    """校验并补齐一个形态定义。返回 None 表示这条定义不可用。"""
    if not isinstance(raw, dict):
        return None
    pid = str(raw.get("id") or "").strip().lower()
    if not _ID_RE.match(pid):
        print("[profile] 跳过 %s：id %r 不合法（小写字母/数字/下划线/中划线）"
              % (origin or "内置", pid), file=sys.stderr)
        return None

    flows = raw.get("flows")
    if flows is not None:
        if not isinstance(flows, (list, tuple)):
            print("[profile] %s：flows 必须是数组，按「不收敛」处理" % pid, file=sys.stderr)
            flows = None
        else:
            known = _known_flow_ids()
            wanted = [str(x).strip() for x in flows if str(x).strip()]
            if known is None:
                flows = wanted
            else:
                missing = [x for x in wanted if x not in known]
                if missing:
                    # 写错的流程 id 只会让界面少一个入口，不该让整个形态失效。
                    print("[profile] %s：忽略不存在的流程 %s"
                          % (pid, "、".join(missing)), file=sys.stderr)
                flows = [x for x in wanted if x in known]

    # 点名放行的专家：写错的 id 只让这一条失效，不影响整个形态。
    agents_allow = raw.get("agents")
    if agents_allow is not None:
        if not isinstance(agents_allow, (list, tuple)):
            print("[profile] %s：agents 必须是数组，按「不点名」处理" % pid,
                  file=sys.stderr)
            agents_allow = None
        else:
            known_agents = _known_agent_ids()
            wanted = [str(x).strip() for x in agents_allow if str(x).strip()]
            if known_agents is None:
                agents_allow = wanted
            else:
                missing = [x for x in wanted if x not in known_agents]
                if missing:
                    print("[profile] %s：忽略不存在的专家 %s"
                          % (pid, "、".join(missing)), file=sys.stderr)
                agents_allow = [x for x in wanted if x in known_agents]

    ui = str(raw.get("ui") or "").strip().lstrip("/\\")
    if ui:
        try:
            exists = (paths.UI_DIR / ui).is_file()
        except OSError:
            exists = False
        if not exists:
            print("[profile] %s：界面 %r 不存在，回落内核默认界面" % (pid, ui),
                  file=sys.stderr)
            ui = ""

    features = dict(_ALL_ON)
    got = raw.get("features")
    if isinstance(got, dict):
        for key, value in got.items():
            features[str(key)] = bool(value)

    try:
        order = int(raw.get("order") or 0)
    except (TypeError, ValueError):
        order = 0
    try:
        port = int(raw.get("default_port") or 0) or None
    except (TypeError, ValueError):
        port = None

    return {
        "id": pid,
        "name": str(raw.get("name") or pid).strip() or pid,
        "tagline": str(raw.get("tagline") or "").strip(),
        "order": order,
        "flows": list(flows) if flows is not None else None,
        "agents": list(agents_allow) if agents_allow is not None else None,
        "drafts": bool(raw.get("drafts")),
        "default_flow": str(raw.get("default_flow") or "").strip(),
        "ui": ui,
        "features": features,
        "default_port": port,
        "builtin": pid in _BUILTIN,
        "origin": origin,
    }


def _merge_extends(raw, depth=0):
    """支持 ``extends``：新形态直接继承一个已有形态，只写要改的字段。"""
    if depth > 5:
        return raw
    base_id = str(raw.get("extends") or "").strip().lower()
    if not base_id:
        return raw
    base = _BUILTIN.get(base_id) or _load_one(base_id)
    if not base:
        print("[profile] 未知的 extends=%r，按独立形态处理" % base_id, file=sys.stderr)
        return raw
    base = _merge_extends(dict(base), depth + 1)
    merged = dict(base)
    merged.update({k: v for k, v in raw.items() if k != "extends"})
    if isinstance(base.get("features"), dict) and isinstance(raw.get("features"), dict):
        feats = dict(base["features"])
        feats.update(raw["features"])
        merged["features"] = feats
    return merged


def _load_one(pid):
    path = spec_dir() / ("%s.json" % pid)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("[profile] 读取 %s 失败：%s" % (path.name, exc), file=sys.stderr)
        return None


def registry():
    """内置 + 用户定义的合并视图（用户定义可覆盖同 id 的内置）。"""
    sig = _signature()
    with _LOCK:
        if _REG_CACHE["sig"] == sig and _REG_CACHE["data"] is not None:
            return _REG_CACHE["data"]

        table = {}
        for pid, raw in _BUILTIN.items():
            spec = _norm_spec(_merge_extends(dict(raw)), origin="内置")
            if spec:
                table[spec["id"]] = spec

        d = spec_dir()
        if d.is_dir():
            for path in sorted(d.glob("*.json")):
                raw = _load_one(path.stem)
                if raw is None:
                    continue
                if not isinstance(raw, dict):
                    continue
                raw.setdefault("id", path.stem)
                # 文件名优先于文件内 id，避免同 id 出现两份定义
                raw["id"] = str(raw.get("id") or path.stem).strip().lower()
                spec = _norm_spec(_merge_extends(raw), origin=str(path))
                if spec:
                    table[spec["id"]] = spec

        if DEFAULT not in table:
            table[DEFAULT] = _norm_spec(dict(_BUILTIN[DEFAULT]), origin="内置")

        _REG_CACHE["sig"] = sig
        _REG_CACHE["data"] = table
        return table


def names():
    """已注册的形态名（按 order、name 排序）。"""
    return tuple(s["id"] for s in list_specs())


def list_specs():
    table = registry()
    return sorted(table.values(), key=lambda s: (s.get("order") or 0, s["id"]))


def spec(name):
    """按名取形态定义；不存在返回 None。"""
    key = str(name or "").strip().lower()
    return registry().get(key)


def default_spec():
    return registry()[DEFAULT]


# ---------------------------------------------------------------- 生效形态

def active():
    """当前生效的形态名。环境变量只在未显式设置时参与解析。"""
    global _active
    with _LOCK:
        if _active is not None:
            return _active
        name = (os.environ.get(ENV_VAR) or "").strip().lower()
        _active = name if name in registry() else DEFAULT
        return _active


def set_active(name):
    """显式指定形态。返回实际生效的名字。

    未知名称回落 default 而不是抛错：这条函数被 web 层和测试复用，
    它不该有「让进程崩掉」的副作用。命令行要严格报错请走 ``select()``。
    """
    global _active
    key = str(name or "").strip().lower()
    with _LOCK:
        _active = key if key in registry() else DEFAULT
        return _active


def source():
    """本次进程的形态是哪种来源决定的（cli / env / prompt / last / default / ui）。

    空串表示还没走过 ``select()``（矩阵测试、库调用场景），调用方应视为「已定」，
    不要因此弹选择页。
    """
    return _source


def _mark_source(why):
    global _source
    with _LOCK:
        _source = str(why or "")
        return _source


def needs_chooser():
    """是否该先给用户看「选一个产品」的页面。

    真条件只有一个：**用户还没表过态**。三种情况算没表态——
    没有命令行指定、没有环境变量、也没有上次记录（``default``），
    以及终端菜单超时（``prompt-timeout``：那是「没人回答」，不是「选了默认」）。

    一旦真选过一次（``last`` / ``prompt`` / ``ui``），下次直接进对应入口，
    选择页只在右上角「切换」时出现——不拿重定向反复拦人。
    """
    return source() in ("default", "prompt-timeout")


def switch_to(name):
    """运行时切换形态（欢迎页点击、页面右上角切换都走这里）。

    与 ``set_active`` 的差别只有两点：**严格**校验（未知 id 返回空串，
    由调用方回 400，而不是静默回落 default）、并记下来作为下次启动的默认。
    返回生效的形态 id；失败返回空串。
    """
    key = str(name or "").strip().lower()
    if not spec(key):
        return ""
    set_active(key)
    remember(key)
    _mark_source("ui")
    return key


def reset():
    """清掉进程内的固定值，回到按选择顺序解析（测试用）。"""
    global _active, _source
    with _LOCK:
        _active = None
        _source = ""


def current():
    """当前形态的完整定义（副本，调用方改不坏注册表）。"""
    profile = spec(active()) or default_spec()
    out = dict(profile)
    out["features"] = dict(profile.get("features") or {})
    out["flows"] = list(profile["flows"]) if profile.get("flows") else None
    out["agents"] = list(profile["agents"]) if profile.get("agents") else None
    return out


def agent_policy():
    """本形态的专家可见策略，交给 ``agents.describe_all()`` 执行。

    ``only`` 为 None 表示不点名，按内核的「非草稿」规则走。
    """
    profile = spec(active()) or default_spec()
    allow = profile.get("agents")
    return {
        "only": list(allow) if allow else None,
        "drafts": bool(profile.get("drafts")),
    }


def flow_allowed(flow_id):
    """该形态下这个流程是否允许创建任务。"""
    allow = (spec(active()) or default_spec()).get("flows")
    if not allow:
        return True
    return str(flow_id or "") in set(allow)


def filter_flows(rows):
    """按形态收敛流程列表。保持入参顺序，不改元素。"""
    allow = (spec(active()) or default_spec()).get("flows")
    if not allow:
        return list(rows)
    allow = set(allow)
    return [row for row in rows if row.get("id") in allow]


def ui_entry():
    """独立界面入口（相对 paths.UI_DIR）；空串表示用内核默认界面。"""
    return (spec(active()) or default_spec()).get("ui") or ""


def default_port():
    """形态建议端口；没声明就 8765。"""
    return (spec(active()) or default_spec()).get("default_port") or 8765


def describe():
    """给 GET /api/profile 的载荷：前端据此决定导航与文案。"""
    profile = current()
    return {
        "id": profile.get("id") or DEFAULT,
        "name": profile.get("name") or "CodeBee",
        "tagline": profile.get("tagline") or "",
        "flows": profile.get("flows"),
        "agents": profile.get("agents"),
        "drafts": bool(profile.get("drafts")),
        "default_flow": profile.get("default_flow") or "",
        "ui": profile.get("ui") or "",
        "features": profile.get("features") or {},
    }


def describe_all():
    """给 GET /api/profiles：形态清单（含当前）。启动器/切换器可用。"""
    live = active()
    # 工种名在这里一次带全：调用方（欢迎页）可能正跑在收敛过的形态里，
    # 那时 /api/flows 只剩几个，拿不到别的形态里有谁。
    # 用列表保序：注册表的顺序就是产品的书写顺序（代码、小说、标书…），
    # 按 id 字母排会变成「自媒体文章、标书编制、代码」，卡片第一眼就很乱。
    ordered = []
    names = {}
    try:
        from . import flows as flows_mod
        for f in flows_mod.list_flows():
            fid = str(f.get("id"))
            ordered.append(fid)
            names[fid] = str(f.get("name") or fid)
    except Exception:
        ordered, names = [], {}
    rows = []
    for s in list_specs():
        # flows=None 表示「不收敛」——把全部工种都列出来，欢迎页才能显示
        # 「代码 / 小说 / 标书 …」这样的代表项，而不是一句空的「全部工种」。
        picked = list(s["flows"]) if s["flows"] else list(ordered)
        rows.append({
            "id": s["id"],
            "name": s["name"],
            "tagline": s["tagline"],
            "flows": s["flows"],
            "flow_count": len(picked) if picked else None,
            "flow_names": [names.get(f, f) for f in picked] if picked else None,
            "ui": s["ui"],
            "order": s["order"],
            "builtin": s["builtin"],
            "default_port": s["default_port"],
            "active": s["id"] == live,
        })
    return {"active": live, "last": last_choice(), "profiles": rows}


# ---------------------------------------------------------------- 上次选择

def last_choice():
    """上次选过的形态名；没记录或已失效返回空串。"""
    try:
        raw = json.loads(state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    pid = str((raw or {}).get("last") or "").strip().lower()
    return pid if spec(pid) else ""


def remember(name):
    """记下这次的选择，下次直接进来。写失败不影响启动。"""
    key = str(name or "").strip().lower()
    if not spec(key):
        return ""
    try:
        state_file().parent.mkdir(parents=True, exist_ok=True)
        tmp = state_file().with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"last": key,
                                   "at": time.strftime("%Y-%m-%d %H:%M:%S")},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, state_file())
    except OSError:
        pass
    return key


# ---------------------------------------------------------------- 启动选择

def _ask(prompt_text, timeout_s):
    """带超时的 input()：超时返回 None（Windows 也不能用 select，所以开线程）。

    读 stdin 的线程是 daemon：超时后它可能还挂着，但本进程之后不再读 stdin，
    不会抢走别的输入。非交互场景根本不进这里。
    """
    box = {}

    def _read():
        try:
            box["v"] = input()
        except (EOFError, KeyboardInterrupt, OSError):
            box["v"] = ""

    worker = threading.Thread(target=_read, daemon=True)
    worker.start()
    worker.join(timeout_s)
    return box.get("v")


def _render_menu(rows, fallback):
    lines = ["", "启动哪个版本？"]
    for idx, s in enumerate(rows, 1):
        mark = "·" if s["id"] != fallback else "→"
        count = ("%d 个流程" % len(s["flows"])) if s["flows"] else "全部流程"
        lines.append("  %s %d) %-18s %s（%s）"
                     % (mark, idx, s["name"], s["tagline"] or "-", count))
    lines.append("")
    lines.append("输入序号或 id，直接回车 = %s；12 秒无输入自动继续。"
                 % (spec(fallback) or default_spec())["name"])
    return "\n".join(lines)


def _match_input(text, rows):
    value = str(text or "").strip().lower()
    if not value:
        return ""
    if value.isdigit():
        idx = int(value)
        if 1 <= idx <= len(rows):
            return rows[idx - 1]["id"]
        return ""
    for s in rows:
        if s["id"] == value or s["name"].lower() == value:
            return s["id"]
    return ""


def select(explicit="", *, allow_prompt=None, timeout_s=12, quiet=False):
    """决定本次启动用哪个形态。返回 ``(id, 原因)``。

    原因取值：cli / env / prompt / prompt-timeout / last / default，写进启动日志，
    方便日后排查「为什么我打开的是小说版」；同时记进 ``source()``，
    启动器据此决定要不要先给选择页。
    """
    applied, why = _select(explicit, allow_prompt=allow_prompt,
                           timeout_s=timeout_s, quiet=quiet)
    _mark_source(why)
    return applied, why


def _select(explicit="", *, allow_prompt=None, timeout_s=12, quiet=False):
    # 1. 命令行：严格
    want = str(explicit or "").strip().lower()
    if want:
        if not spec(want):
            raise ProfileError(
                "未知形态 %r。可选：%s\n新增形态：%s"
                % (explicit, "、".join(names()), SCAFFOLD_HINT))
        set_active(want)
        remember(want)
        return want, "cli"

    # 2. 环境变量：宽松
    env_want = (os.environ.get(ENV_VAR) or "").strip().lower()
    if env_want:
        if spec(env_want):
            set_active(env_want)
            return env_want, "env"
        print("[profile] %s=%r 不是已注册的形态，已忽略（可选：%s）"
              % (ENV_VAR, env_want, "、".join(names())), file=sys.stderr)

    fallback = last_choice() or DEFAULT

    # 3. 交互式菜单
    if allow_prompt is None:
        allow_prompt = (not os.environ.get(NO_PROMPT_VAR)
                        and getattr(sys.stdin, "isatty", lambda: False)())
    if allow_prompt:
        rows = list_specs()
        try:
            print(_render_menu(rows, fallback), flush=True)
        except (OSError, ValueError):
            pass
        answer = _ask("", timeout_s)
        if answer is None:
            # 超时不是选择：只决定「这次进程先用谁」，不写 state。
            # 写下去会让浏览器端的选择页永远不出现（服务被管理器拉起、
            # stdin 恰好是个不会输东西的管道时，每次都被当成「用户选了默认」）。
            print("[profile] 无输入，本次先用 %s" % fallback, flush=True)
            set_active(fallback)
            return fallback, "prompt-timeout"
        picked = _match_input(answer, rows)
        if not picked:
            print("[profile] 没看懂 %r，使用 %s" % (answer, fallback), flush=True)
            picked = fallback
        set_active(picked)
        remember(picked)
        return picked, "prompt"

    # 4. 上次选择（含用户上次主动选的 default）
    #    「选过 default」与「从没选过」必须能区分：否则在欢迎页选了全量版，
    #    下次启动又会被送回欢迎页——只剩一个选项的死循环。
    if last_choice():
        set_active(fallback)
        return fallback, "last"

    # 5. 默认
    set_active(DEFAULT)
    return DEFAULT, "default"


# ---------------------------------------------------------------- 新建形态

def scaffold(pid, name="", flows=None, tagline="", extends=DEFAULT, ui=""):
    """生成一个形态定义文件，返回其路径。

    命令行给的是「起点」不是「成品」：写出来的文件带注释性字段说明，
    用户改几行就能得到一个新版本。已存在的 id 不覆盖。
    """
    key = str(pid or "").strip().lower()
    if not _ID_RE.match(key):
        raise ProfileError("形态 id %r 不合法（小写字母/数字/下划线/中划线）" % pid)
    if key in registry():
        raise ProfileError("形态 %r 已存在（%s）" % (key, registry()[key]["name"]))

    base = spec(extends) or default_spec()
    picked = list(flows or base.get("flows") or [])
    known = _known_flow_ids()
    if known is not None:
        bad = [f for f in picked if f not in known]
        if bad:
            raise ProfileError("未知流程：%s\n可选：%s"
                               % ("、".join(bad), "、".join(sorted(known))))

    payload = {
        "id": key,
        "name": (name or key).strip(),
        "tagline": (tagline or "").strip(),
        "order": (base.get("order") or 0) + 1,
        "extends": extends,
        "flows": picked if picked else None,
        # 新建版本默认点名放开它选的这些专家：用户既然明确要这几个流程，
        # 就不该看到一个空荡荡的专家栏（删掉这一行即回到「只放首发名单」）。
        "agents": list(picked) if picked else None,
        "drafts": False,
        "default_flow": (picked[0] if picked else ""),
        "ui": ui or "",
        "features": dict(base.get("features") or _ALL_ON),
        "default_port": None,
    }
    path = spec_dir() / ("%s.json" % key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    _REG_CACHE["sig"] = None  # 立刻让下一次 reads 看到新文件
    return path
