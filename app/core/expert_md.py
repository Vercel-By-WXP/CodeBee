# -*- coding: utf-8 -*-
"""专家导出/导入 Markdown（2026-11-01，借鉴 OpenMausBot 的 bot 团队 MD 导入导出）。

一个专家 = 一个 Markdown 文件（frontmatter + 自由正文）。这与仓库决策 #2
「形态是数据不是代码」同构，并补上「可分享/可导入」——文件即技能包雏形，
平台化第三阶段的分发格式从这里起步。

导出格式（人可直接读改）：
    ---
    name: 小说家
    flow: serial_novel
    focus: ...
    archetype: studio
    metric: ...
    deliverable: ...
    primary_action: 开一个任务
    gates: ["a", "b"]
    hidden: false
    ---

    （自由正文：给后来者/其他 agent 的人设说明，导入时原样保留）

frontmatter 解析用「行式 key: value，值疑似 JSON 就 json.loads」的轻量约定，
不引入 yaml 依赖。导入落点 = data/agents/<flow>.json（agents.registry 的
用户覆盖层，重启/签名失效后生效）。

纪律：
- 导入不覆盖已有专家除非 overwrite=True（撞名必须显式）；
- flow 为空时从 name 生成 slug（CJK 保留）；条目是否真能起任务取决于
  flows 是否有同名流程——如实回告，不伪造可用性；
- 敏感字段（seats 里的模型绑定）不导出——凭据不出机器。
"""
from __future__ import annotations

import json
import re
import threading
import time

from . import agents

_LOCK = threading.RLock()

# 不导出的字段：内部态 / 潜在敏感
_SKIP_KEYS = {"builtin", "surface", "panels", "seats", "group", "group_name",
              "group_order"}


def _slug(name):
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", str(name or "").strip(), flags=re.UNICODE)
    s = re.sub(r"-{2,}", "-", s).strip("-").lower()
    return s or "expert-%s" % time.strftime("%m%d%H%M%S")


def _dump_value(v):
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _load_value(raw):
    raw = raw.strip()
    if raw[:1] in "[{" :
        try:
            return json.loads(raw)
        except ValueError:
            pass
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(raw)
    except ValueError:
        return raw


def parse_frontmatter(text):
    """解析 frontmatter（--- 围栏）。返回 (meta_dict, body_str)。坏格式给空 meta。"""
    text = str(text or "").replace("\r\n", "\n").lstrip("\ufeff")
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    meta, body = {}, "\n".join(lines[end + 1:]).lstrip("\n")
    for line in lines[1:end]:
        if not line.strip() or line.strip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][\w\-]*)\s*:\s*(.*)$", line.strip())
        if m:
            meta[m.group(1).lower()] = _load_value(m.group(2))
    return meta, body


def export_md(flow_id):
    """导出专家为 Markdown 字符串。未登记返回 None。"""
    spec = agents.get(str(flow_id or "").strip())
    if not spec:
        return None
    meta = {}
    for k in ("name", "focus", "archetype", "metric", "deliverable",
              "primary_action", "hidden"):
        v = spec.get(k)
        if v not in (None, ""):
            meta[k] = v
    meta["flow"] = spec.get("flow") or flow_id
    if spec.get("gates"):
        meta["gates"] = spec.get("gates")
    body = str(spec.get("focus") or "").strip()
    lines = ["---"]
    for k, v in meta.items():
        lines.append("%s: %s" % (k, _dump_value(v)))
    lines.append("---")
    lines.append("")
    if body:
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


def import_md(text, flow_id=None, overwrite=False):
    """导入 Markdown 为专家配置。返回 (path_str, info) 或抛 ValueError。

    - flow_id 参数优先；否则用 frontmatter 的 flow；否则按 name 生成 slug；
    - 目标专家已存在且未 overwrite → ValueError（撞名显式处理）；
    - 写 data/agents/<flow>.json（agents.registry 用户覆盖层）；
    - 回告 flow_exists：该专家能否真的起任务取决于 flows 有没有同名流程。
    """
    meta, body = parse_frontmatter(text)
    name = str(meta.get("name") or "").strip()
    if not name:
        raise ValueError("frontmatter 缺少 name 字段")
    fid = str(flow_id or "").strip() or str(meta.get("flow") or "").strip() \
        or _slug(name)
    fid = re.sub(r"[\\/:*?\"<>|]", "-", fid).strip("-")
    if not fid:
        raise ValueError("flow id 为空")
    raw = {"name": name}
    for k in ("focus", "archetype", "metric", "deliverable", "primary_action"):
        if meta.get(k) not in (None, ""):
            raw[k] = meta[k]
    if isinstance(meta.get("gates"), list):
        raw["gates"] = [str(x) for x in meta["gates"]]
    if "hidden" in meta:
        raw["hidden"] = bool(meta.get("hidden"))
    note = str(body or "").strip()
    if note:
        raw["note"] = note[:4000]
    d = agents._agents_dir()
    with _LOCK:
        d.mkdir(parents=True, exist_ok=True)
        target = d / ("%s.json" % fid)
        if target.exists() and not overwrite:
            raise ValueError("专家「%s」已存在；确认覆盖请带 overwrite=true" % fid)
        target.write_text(json.dumps(raw, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    # agents.registry 按 data/agents/*.json 的 (mtime, size) 签名缓存，
    # 新文件落盘即自动失效，无需手动清。
    flow_exists = False
    try:
        from . import flows
        flow_exists = flows.get_flow(fid) is not None
    except Exception:
        pass
    return str(target), {"flow": fid, "name": name, "flow_exists": flow_exists,
                         "overwritten": bool(overwrite)}
