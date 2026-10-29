# -*- coding: utf-8 -*-
"""任务型 Agent：把「一个流程」升级成「一个有主张的专职 Agent」。

profile 回答「这是哪个版本的产品」（一整个发行版），agents 回答
「这个版本里有哪些专家」（一版里的每个流程）。

两者的区别不是换个名字，而是**每个 Agent 只优化一件事**：

- 小说家只管「读者会不会追下去」；
- 标书编制只管「会不会因为缺项被废标」；
- 代码只管「改完还能不能跑」；
- 复盘只管「同一个坑为什么又踩了」。

所以它必须同时声明三样东西，缺一样就会退化成「同一个页面换个标题」：

1. ``focus``   唯一负责的判断（决定评审席位怎么问、门禁卡什么）；
2. ``surface`` 它该长什么页面（决定作者看到什么，而不是内核有什么）；
3. ``metric``  它拿什么指标证明自己干得好（决定经验库回流什么）。

页面不做 21 套。21 个流程归到 4 个**页面原型**（archetype），Agent 只声明
自己属于哪个原型 + 要哪几个面板；原型才有完整实现。这样新增一个 Agent
是写一份 JSON，不是写一个新前端。

自定义：往 ``data/agents/<flow>.json`` 放同名覆盖即可，字段同下。

草稿与首发：内置专家默认只有 ``LAUNCH`` 里的几个可见，其余算草稿，
只在被形态点名（``profile.agents``）或显式要求（``include_hidden``）时出现。
这条规则的来历见 ``LAUNCH`` 上方注释。
"""
from __future__ import annotations

import json
import threading

from . import paths

_LOCK = threading.RLock()
_CACHE = {"sig": None, "data": None}

# 四个页面原型。一个原型 = 一套信息架构 + 一个交互范式。
ARCHETYPES = {
    "studio": {
        "name": "执行台",
        "layout": "队列 + 实时步骤 + 产物",
        "question": "它现在做到哪一步了？做出来的东西对不对？",
        "panels": ("queue", "live_steps", "artifacts", "verify"),
        "default_page": "",
    },
    "craft": {
        "name": "创作台",
        "layout": "书架 + 正文 + 评审意见",
        "question": "这一章能不能留住读者？哪里该改？",
        "panels": ("shelf", "body", "review", "wordcount", "publish"),
        "default_page": "",
    },
    "dossier": {
        "name": "成稿台",
        "layout": "章节树 + 模板校验 + 定稿导出",
        "question": "要件齐不齐？格式会不会被打回？",
        "panels": ("outline_tree", "checklist", "style_check", "export"),
        "default_page": "",
    },
    "radar": {
        "name": "分析台",
        "layout": "样本看板 + 结论卡 + 转任务",
        "question": "这堆数据说明什么？下一步该做什么？",
        "panels": ("sample_board", "findings", "to_task"),
        "default_page": "",
    },
}

_ALL_ON = {
    "code_tasks": True, "git_workbench": True, "zentao": True, "mcp": True,
    "mobile": True, "publish": True, "decisions": True, "market": True,
}

# 首发名单：只有这几个在「没被形态显式点名」时默认可见。
#
# 为什么默认藏起来而不是默认放出来：一个 Agent 放出去，用户第一次用完就知道
# 它是不是半成品，而口碑反噬比「少一个入口」贵得多。所以规矩反过来定 ——
# **新增专家默认是草稿**，要进首发名单（或由某个形态点名）才可见。
#
# 进这份名单的门槛是三问都能答上来（见 docs/profiles-and-agents.md）：
#   1. 谁来说它不合格？  2. 怎么算它干好了？  3. 它错了怎么办？
#
# 现在只放 5 个：
#   novel / short_story / serial_novel —— 唯一有真实闭环（发布链 8900 行、43 次实案）
#   bid_doc    —— 验收口径最硬：废标项必须为 0
#   code       —— 有回归验证口径，且是开发者自己的日常
LAUNCH = ("novel", "short_story", "serial_novel", "bid_doc", "code")

# 内置 Agent 表：flow -> Agent 定义。
# 顺序即产品里的出现顺序；同一原型的 Agent 共享页面，但 focus/metric 各不同。
_BUILTIN = {
    # ---- 执行台：目标是「做完且可验证」 ----
    "direct": {
        "name": "快手", "focus": "一次说清、一次做对，不绕弯",
        "archetype": "studio", "metric": "一次通过率",
        "deliverable": "结论与产物", "primary_action": "直接交代任务",
    },
    "image": {
        "name": "画师", "focus": "主体、风格、用途说清楚，出的图才用得上",
        "archetype": "studio", "metric": "一次可用率",
        "deliverable": "图片文件", "primary_action": "描述画面并出图",
    },
    "code": {
        "name": "工程师", "focus": "改完还能不能跑",
        "archetype": "studio", "metric": "回归通过率",
        "deliverable": "代码改动 + 验证记录", "primary_action": "开一个改码任务",
        "gates": ["必须跑通验证再交付"],
    },
    "defect_retro": {
        "name": "复盘官", "focus": "同一个坑为什么又踩了",
        "archetype": "studio", "metric": "同类缺陷复发率",
        "deliverable": "复盘报告 + 回归用例", "primary_action": "复盘一次故障",
    },
    # ---- 创作台：目标是「让人追下去」 ----
    "novel": {
        "name": "小说家", "focus": "故事能不能立住、读者会不会追",
        "archetype": "craft", "metric": "签约率 / 追读率",
        "deliverable": "正文 + 评审意见", "primary_action": "开新书",
        "seats": 3,
    },
    "short_story": {
        "name": "短篇手", "focus": "五千字内有没有一个完整的反转",
        "archetype": "craft", "metric": "过稿率",
        "deliverable": "单篇成稿 + 字数闸结论", "primary_action": "写一篇短篇",
        "seats": 3,
    },
    "serial_novel": {
        "name": "连载家", "focus": "断更风险与单章钩子",
        "archetype": "craft", "metric": "连续更新天数 / 追读率",
        "deliverable": "卷纲 + 逐章正文", "primary_action": "开一本连载",
        "seats": 3,
    },
    "article": {
        "name": "主笔", "focus": "标题党之外，正文有没有干货",
        "archetype": "craft", "metric": "完读率",
        "deliverable": "成稿 + 标题备选", "primary_action": "写一篇稿",
    },
    "video_script": {
        "name": "分镜手", "focus": "前三秒留人、镜头能不能拍出来",
        "archetype": "craft", "metric": "前三秒完播率",
        "deliverable": "分镜脚本 + 口播稿", "primary_action": "写一条脚本",
    },
    "short_drama": {
        "name": "编剧", "focus": "每集有没有一个必须点下一集的理由",
        "archetype": "craft", "metric": "集均完播率",
        "deliverable": "分集大纲 + 剧本", "primary_action": "写一部短剧",
    },
    "handdraw_video": {
        "name": "画师", "focus": "画面与旁白是否对得上",
        "archetype": "craft", "metric": "成片可用镜头率",
        "deliverable": "分镜 + 画面描述", "primary_action": "做一支手绘视频",
    },
    "speech": {
        "name": "讲者", "focus": "开口三十秒能不能抓住场子",
        "archetype": "craft", "metric": "讲稿通过率",
        "deliverable": "讲稿 + 逐段节拍", "primary_action": "写一篇演讲稿",
    },
    # ---- 成稿台：目标是「要件齐全、格式打不回」 ----
    "doc": {
        "name": "文书", "focus": "结构清楚、要素齐全",
        "archetype": "dossier", "metric": "一次通过率",
        "deliverable": "成稿文档", "primary_action": "写一份文档",
    },
    "translation": {
        "name": "译匠", "focus": "意思不走样、术语不打架",
        "archetype": "dossier", "metric": "术语一致率",
        "deliverable": "译文 + 术语表", "primary_action": "译一份材料",
    },
    "presentation": {
        "name": "策划", "focus": "一页一个观点，讲得完",
        "archetype": "dossier", "metric": "页均信息密度",
        "deliverable": "大纲 + 逐页文案", "primary_action": "做一份演示",
    },
    "weekly_report": {
        "name": "汇报人", "focus": "结论先行、数据支撑",
        "archetype": "dossier", "metric": "被追问次数",
        "deliverable": "汇报稿 + 数据要点", "primary_action": "写一份汇报",
    },
    "email": {
        "name": "信使", "focus": "一句话说清诉求，不惹误会",
        "archetype": "dossier", "metric": "一次回复率",
        "deliverable": "邮件正文 + 主题行", "primary_action": "写一封邮件",
    },
    "tech_proposal": {
        "name": "方案师", "focus": "约束、取舍、风险都交代清楚",
        "archetype": "dossier", "metric": "评审打回率",
        "deliverable": "技术方案 + 风险清单", "primary_action": "写一份方案",
    },
    "resume": {
        "name": "简历师", "focus": "每条经历都能被追问并站得住",
        "archetype": "dossier", "metric": "面试邀约率",
        "deliverable": "简历 + 追问预案", "primary_action": "改一份简历",
    },
    "bid_doc": {
        "name": "标书师", "focus": "会不会因为缺项被废标",
        "archetype": "dossier", "metric": "废标项数（应为 0）",
        "deliverable": "标书正文 + 偏离表", "primary_action": "编一份标书",
        "gates": ["必须逐条核对招标文件的废标项"],
    },
    # ---- 分析台：目标是「结论能落地」 ----
    "rank_scan": {
        "name": "选材官", "focus": "什么题材现在真的有人看",
        "archetype": "radar", "metric": "选题命中率",
        "deliverable": "榜单结论 + 选题建议", "primary_action": "扫一次榜",
    },
    "research": {
        "name": "调研员", "focus": "结论有没有证据支撑",
        "archetype": "radar", "metric": "结论被推翻率",
        "deliverable": "调研报告 + 证据链", "primary_action": "做一次调研",
    },
}


def _agents_dir():
    return paths.DATA_DIR / "agents"


def _signature():
    d = _agents_dir()
    try:
        items = sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size)
                       for p in d.glob("*.json"))
    except OSError:
        items = []
    return (str(d), tuple(items))


def _hidden_of(flow_id, raw):
    """这个 Agent 是否只在「被显式点名」时才出现。

    三条规则，按顺序：

    1. 配置里写了 ``hidden`` —— 听配置的（``data/agents/x.json`` 里
       ``"hidden": false`` 就能把一个草稿提上来验证界面）；
    2. 覆盖了某个内置 Agent 但没写 ``hidden`` —— **继承内置的状态**。
       否则「只想改个名字」会顺手把一个草稿放出去，那不是他的本意；
    3. 全新 Agent（内置表里没有）—— 默认可见。他专门写了配置文件，
       说明他知道自己要什么，不该再猜。
    """
    if "hidden" in raw:
        return bool(raw.get("hidden"))
    if flow_id in _BUILTIN:
        return flow_id not in LAUNCH
    return False


def _norm(flow_id, raw):
    if not isinstance(raw, dict):
        return None
    arch = str(raw.get("archetype") or "studio").strip().lower()
    if arch not in ARCHETYPES:
        arch = "studio"
    spec = {
        "flow": flow_id,
        "name": str(raw.get("name") or flow_id).strip(),
        "focus": str(raw.get("focus") or "").strip(),
        "archetype": arch,
        "surface": str(raw.get("surface") or ARCHETYPES[arch]["default_page"]).strip(),
        "panels": list(raw.get("panels") or ARCHETYPES[arch]["panels"]),
        "metric": str(raw.get("metric") or "").strip(),
        "deliverable": str(raw.get("deliverable") or "").strip(),
        "primary_action": str(raw.get("primary_action") or "开一个任务").strip(),
        "seats": raw.get("seats"),
        "gates": [str(x) for x in (raw.get("gates") or [])],
        "hidden": _hidden_of(flow_id, raw),
        "builtin": flow_id in _BUILTIN,
    }
    return spec


def registry():
    """flow_id -> Agent 定义。用户可覆盖同 flow 的内置定义。"""
    sig = _signature()
    with _LOCK:
        if _CACHE["sig"] == sig and _CACHE["data"] is not None:
            return _CACHE["data"]
        table = {}
        for flow_id, raw in _BUILTIN.items():
            spec = _norm(flow_id, raw)
            if spec:
                table[flow_id] = spec
        d = _agents_dir()
        if d.is_dir():
            for path in sorted(d.glob("*.json")):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                spec = _norm(path.stem, raw)
                if spec:
                    table[path.stem] = spec
        _CACHE["sig"] = sig
        _CACHE["data"] = table
        return table


def get(flow_id):
    """该流程的 Agent 定义；没登记返回 None（不伪造默认值）。"""
    return registry().get(str(flow_id or "").strip())


def decorate(flow_rows):
    """给 /api/flows 的每一行挂上 agent 字段，前端可直接渲染「专家」入口。

    ``hidden`` 一并给出：前端可以据此决定「任务类型下拉里要不要出现这一项」，
    而不用自己维护一份名单。
    """
    table = registry()
    out = []
    for row in flow_rows:
        item = dict(row)
        agent = table.get(str(row.get("id") or ""))
        if agent:
            item["agent"] = {
                "name": agent["name"],
                "focus": agent["focus"],
                "archetype": agent["archetype"],
                "surface": agent["surface"],
                "metric": agent["metric"],
                "primary_action": agent["primary_action"],
                "hidden": agent["hidden"],
            }
        out.append(item)
    return out


def describe_all(include_hidden=False, only=None):
    """给 GET /api/agents：一版里有哪些专家、各自负责什么、长什么页面。

    参数决定「谁能出现」，三条路各管一件事：

    - ``only``     形态点名的专家清单。**点名即放出来**，即使它是草稿 ——
                   这是「某个发行版明知风险也要用它」的唯一入口。
    - ``include_hidden``  连草稿一起给（开发者自用的CodeBee版、排查问题时用）。
    - 都不给      只给非草稿的，也就是首发名单。
    """
    table = registry()
    allow = None if only is None else {str(x) for x in only}
    rows = []
    for flow_id, spec in table.items():
        if allow is not None:
            if flow_id not in allow:
                continue
        elif spec["hidden"] and not include_hidden:
            continue
        arch = ARCHETYPES[spec["archetype"]]
        rows.append({
            "flow": flow_id,
            "name": spec["name"],
            "focus": spec["focus"],
            "archetype": spec["archetype"],
            "archetype_name": arch["name"],
            "surface": spec["surface"],
            "panels": spec["panels"],
            "metric": spec["metric"],
            "deliverable": spec["deliverable"],
            "primary_action": spec["primary_action"],
            "seats": spec["seats"],
            "gates": spec["gates"],
            "hidden": spec["hidden"],
            "builtin": spec["builtin"],
        })
    order = {k: i for i, k in enumerate(_BUILTIN)}
    rows.sort(key=lambda r: (order.get(r["flow"], 999), r["flow"]))
    return {"archetypes": ARCHETYPES, "agents": rows}
