# -*- coding: utf-8 -*-
"""专家团（2026-11-03）：把「好几位专家一起干一件事」变成任务上可复现的配置。

与 experts.py 的关系：
    专家模板（experts.py）描述**单人**的工作方法，绑一个流程；专家团是
    **多人分工**，第一位是主责（决定任务的流程/引擎），其余是协作（提供分工
    要求与验收角度）。团不新建执行引擎，只在任务创建时钉一份快照，由 pipeline
    注入成「团队协作」上下文——所以流程、评审、产物链路一行都不用改。

数据落点：
    内置团写在 _BUILTIN 里（只读，与插件市场「精选场景卡」一一对应）；
    自定义团存 <数据目录>/expert-teams.json（atomic 写、0600），重装不丢。
"""
from __future__ import annotations

import json
import re

from . import atomic, flows, paths


# 与前端 MK_PROMO 的六张场景卡同源：改这里前端自动跟着变（前端只读接口）。
_BUILTIN = [
    {
        "id": "team-long-fiction", "name": "长篇小说团",
        "desc": "先看榜单定题材，再由主笔铺长篇，最后逐章打磨钩子。",
        "members": [
            {"flow_id": "novel", "role": "主笔"},
            {"flow_id": "rank_scan", "role": "选题"},
            {"flow_id": "article", "role": "打磨"},
        ],
    },
    {
        "id": "team-short-video", "name": "短视频与短剧团",
        "desc": "脚本立住前三秒，分集成型，镜头对得上旁白。",
        "members": [
            {"flow_id": "video_script", "role": "脚本"},
            {"flow_id": "short_drama", "role": "分集"},
            {"flow_id": "handdraw_video", "role": "画面"},
        ],
    },
    {
        "id": "team-longform", "name": "长文与演讲团",
        "desc": "正文有干货，讲稿能讲，配图不抢戏。",
        "members": [
            {"flow_id": "article", "role": "主笔"},
            {"flow_id": "speech", "role": "讲稿"},
            {"flow_id": "image", "role": "配图"},
        ],
    },
    {
        "id": "team-code", "name": "代码工程团",
        "desc": "最小改动先跑通，再复盘同类坑，方案写得清取舍。",
        "members": [
            {"flow_id": "code", "role": "主责"},
            {"flow_id": "defect_retro", "role": "复盘"},
            {"flow_id": "tech_proposal", "role": "方案"},
        ],
    },
    {
        "id": "team-office", "name": "职场文书团",
        "desc": "主体成文，数据与结论先行，口径不乱。",
        "members": [
            {"flow_id": "doc", "role": "主笔"},
            {"flow_id": "weekly_report", "role": "汇报"},
            {"flow_id": "email", "role": "邮件"},
        ],
    },
    {
        "id": "team-research", "name": "选题与调研团",
        "desc": "榜单找需求，调研给证据，最后落成能发的选题包。",
        "members": [
            {"flow_id": "rank_scan", "role": "选题"},
            {"flow_id": "research", "role": "证据"},
            {"flow_id": "article", "role": "落稿"},
        ],
    },
]

MAX_MEMBERS = 6
MAX_NAME = 40
MAX_DESC = 160
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")


def _copy(item):
    if not isinstance(item, (dict, list)):
        return None
    return json.loads(json.dumps(item, ensure_ascii=False))


def _persona(flow_id):
    """流程 → 专家人设。flows.get_flow 只给流程名（「小说」「扫榜选材」），
    专家名/关注点要经 agents.decorate 才算得出来（「小说家」「选材官」）。"""
    flow = flows.get_flow(flow_id)
    if not flow:
        return {}
    try:
        from . import agents as agents_mod
        decorated = agents_mod.decorate([flow])[0]
    except Exception:
        decorated = flow
    return decorated.get("agent") or {}


def _expert_name(flow_id):
    agent = _persona(flow_id)
    flow = flows.get_flow(flow_id) or {}
    return (agent.get("name") or flow.get("name") or flow_id or "").strip()


def _expert_focus(flow_id):
    return (_persona(flow_id).get("focus") or "").strip()


def _norm(team):
    """校验并归一化一个团；字段不全/成员非法一律抛 ValueError（不静默修补）。"""
    if not isinstance(team, dict):
        raise ValueError("专家团必须是对象")
    name = str(team.get("name") or "").strip()
    if not name or len(name) > MAX_NAME:
        raise ValueError("团名不能为空且最多 %d 字" % MAX_NAME)
    desc = str(team.get("desc") or "").strip()
    if len(desc) > MAX_DESC:
        raise ValueError("团简介最多 %d 字" % MAX_DESC)
    raw_members = team.get("members")
    if not isinstance(raw_members, list) or not 2 <= len(raw_members) <= MAX_MEMBERS:
        raise ValueError("专家团至少 2 位、最多 %d 位专家" % MAX_MEMBERS)
    members, seen = [], set()
    for raw in raw_members:
        if isinstance(raw, str):
            raw = {"flow_id": raw}
        if not isinstance(raw, dict):
            raise ValueError("团成员格式不正确")
        fid = str(raw.get("flow_id") or "").strip()
        if not fid:
            raise ValueError("团成员缺少 flow_id")
        if flows.get_flow(fid) is None:
            raise ValueError("团成员流程不存在：%s" % fid)
        if fid in seen:
            raise ValueError("同一专家不能在一个团里出现两次")
        seen.add(fid)
        members.append({"flow_id": fid, "role": str(raw.get("role") or "").strip()[:12]})
    # 主责＝第一位；没写角色时给个默认称呼，界面就不用再兜一次
    members[0]["role"] = members[0]["role"] or "主责"
    return {"name": name, "desc": desc, "members": members}


def _user_path():
    return paths.DATA_DIR / "expert-teams.json"


def _load_user():
    try:
        raw = json.loads(_user_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for team_id, team in raw.items():
        if isinstance(team, dict):
            item = _copy(team) or {}
            item["id"] = str(item.get("id") or team_id)
            item["builtin"] = False
            out[str(team_id)] = item
    return out


def _save_user(items):
    atomic.write_json(_user_path(), items, mode=0o600)


def list_teams():
    items = []
    for team in _BUILTIN:
        item = _copy(team) or {}
        item["builtin"] = True
        items.append(item)
    items.extend(_load_user().values())
    # 界面按「内置在前、自定义在后」陈列；同组内保持定义顺序
    items.sort(key=lambda t: (0 if t.get("builtin") else 1,))
    for item in items:
        item["member_count"] = len(item.get("members") or [])
    return items


def get_team(team_id):
    key = str(team_id or "").strip()
    if not key:
        return None
    for team in _BUILTIN:
        if team["id"] == key:
            item = _copy(team) or {}
            item["builtin"] = True
            return item
    return _load_user().get(key)


def save_team(team):
    """新建或覆盖一个自定义团；内置 id 一律拒绝（改名会破坏任务快照对账）。"""
    data = _norm(team)
    team_id = str((team or {}).get("id") or "").strip().lower()
    if any(t["id"] == team_id for t in _BUILTIN):
        raise ValueError("内置专家团不能修改，请另存为新团")
    if not team_id:
        base = re.sub(r"[^a-z0-9]+", "-", str(data["name"]).encode("utf-8").hex()[:12]).strip("-")
        team_id = "team-user-" + (base or "custom")[:24]
    if not _ID_RE.match(team_id) or not team_id.startswith("team-"):
        raise ValueError("专家团 ID 必须以 team- 开头，只能用小写字母、数字与连字符")
    items = _load_user()
    data.update({"id": team_id, "builtin": False, "updated_at": _now()})
    items[team_id] = data
    _save_user(items)
    return _copy(data)


def delete_team(team_id):
    key = str(team_id or "").strip()
    if any(t["id"] == key for t in _BUILTIN):
        raise ValueError("内置专家团不能删除")
    items = _load_user()
    if key not in items:
        raise ValueError("专家团不存在：%s" % key)
    items.pop(key)
    _save_user(items)
    return True


def primary_flow(team):
    members = (team or {}).get("members") or []
    return (members[0] or {}).get("flow_id") if members else ""


def snapshot(team):
    """任务创建时钉住：成员名字与关注点一起存下来，之后改名/改团不影响旧任务。"""
    item = _copy(team) or {}
    members = []
    for raw in item.get("members") or []:
        fid = str((raw or {}).get("flow_id") or "").strip()
        if not fid:
            continue
        members.append({
            "flow_id": fid,
            "name": _expert_name(fid),
            "focus": _expert_focus(fid),
            "role": str((raw or {}).get("role") or "").strip()[:12],
        })
    item["members"] = members
    item["primary_flow"] = primary_flow(item)
    item["captured_at"] = _now()
    return item


def context_block(team_snapshot):
    """把团队分工写进任务上下文（主责先干，协作按职责复核）。"""
    if not isinstance(team_snapshot, dict):
        return ""
    members = team_snapshot.get("members") or []
    if len(members) < 2:
        return ""
    head = members[0]
    lines = [
        "## 专家团分工（任务创建时已固定）",
        "- 团队：%s" % (team_snapshot.get("name") or "专家团"),
        "- 主责：%s（%s）——本任务由该专家主导推进，其余成员是协作与复核" % (
            head.get("name") or head.get("flow_id") or "", head.get("role") or "主责"),
    ]
    for member in members[1:]:
        focus = ("：" + member["focus"]) if member.get("focus") else ""
        lines.append("- 协作：%s · %s%s" % (
            member.get("role") or "协作", member.get("name") or member.get("flow_id") or "", focus))
    lines.append(
        "执行要求：主责按自己的工作方法推进；在关键节点（方案/大纲确定、初稿完成、终稿前）"
        "逐条对照协作成员的职责自查；最终交付里用一小段说明每位协作成员的意见如何被采纳或拒绝。")
    return "\n".join(lines)


def _now():
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")
