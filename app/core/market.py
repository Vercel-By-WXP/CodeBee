# -*- coding: utf-8 -*-
"""插件市场（Market）：经验库（skills）之上的「发现 + 一键安装」层。

分工：skills 负责包的解析 / scope 匹配 / 注入 / 启停（运行机制），市场只负责
「发现与安装」——把人工维护的包目录（BUILTIN_PACKS）展示出来，用户点一下就把
包内容写进 data/skillpacks/，之后的一切（frontmatter 解析、按流程类型注入、启停）
全部复用 skills 的既有机制，市场不自建注入通道。

数据约定：
  - 目录内容源：app/core/skillpacks/market/<pack_id>.md（与内置包同一棵目录树，
    frontmatter 里带 source: market / market_id: <id> 作为安装标记）；
  - 安装目标：data/skillpacks/market-<pack_id>.md（skills 的用户包目录；market-
    前缀 + frontmatter 标记双保险，绝不覆盖用户自建文件，重名自动避让换名）；
  - 安装状态：data/market.json（原子写），记录装过哪些包、装到了哪个文件；
    记录丢失时按文件里的 market 标记扫描自愈（installed_ids）。
卸载只认「market.json 有记录 + 文件带 market 标记」的包；skills 内置包
（app/core/skillpacks/ 下）与无标记的用户自建包一律拒绝删除。
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time

from . import paths, skills

_LOCK = threading.RLock()

# 目录内容的源目录：与 skills 内置包同一棵目录树（app/core/skillpacks/），子目录分家
_SRC_DIR = paths.APP_DIR / "core" / "skillpacks" / "market"

# 文件 frontmatter 里的安装标记（本模块自己写的格式，正则足够）
_MARKER_RE = re.compile(r"(?m)^market_id:\s*([A-Za-z0-9_.-]+)\s*$")


def _registry_file():
    """安装状态文件：每次现取 paths.DATA_DIR（测试重定向后自动跟随）。"""
    return paths.DATA_DIR / "market.json"


def _user_pack_dir():
    """skills 的用户包目录（安装目标），随 paths.DATA_DIR 现取。"""
    return paths.DATA_DIR / "skillpacks"


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 包目录（catalog）

# 可安装的市场包目录：内容源文件在 skillpacks/market/<id>.md，import 时读入 files
# （{安装相对路径: 正文}）。category 必须取 skills.LESSON_CATEGORIES 闭集枚举值。
BUILTIN_PACKS = [
    {"id": "git-workflow", "name": "Git 提交与分支守则",
     "desc": "提交信息格式与粒度、分支模型、force push 与回滚等危险操作红线，附提交前自检清单。",
     "category": "流程规范", "scopes": ["*"], "file": "git-workflow.md"},
    {"id": "code-risk-checklist", "name": "代码风险自查清单",
     "desc": "提测/评审前按事故率过单：边界与异常、资源与并发、注入与泄密、跨平台与回退。",
     "category": "流程规范", "scopes": ["*"], "file": "code-risk-checklist.md"},
    {"id": "release-notes", "name": "版本发布说明撰写模板",
     "desc": "面向用户的更新公告写法：固定六段结构、破坏性变更三要素、可复制模板与反例对照。",
     "category": "文笔风格", "scopes": ["*"], "file": "release-notes.md"},
    {"id": "weekly-report", "name": "周报/晨报生成器守则",
     "desc": "先取材再总结：从任务与运行记录提炼晨报三段、周报四段，量化纪律与可复制模板。",
     "category": "流程规范", "scopes": ["*"], "file": "weekly-report.md"},
    {"id": "character-bible", "name": "角色小传与人物弧光模板",
     "desc": "角色档案模板（欲望/恐惧/语言指纹）、四拍弧光规划与连载防 OOC 纪律。",
     "category": "人物塑造", "scopes": ["novel", "serial_novel"], "file": "character-bible.md"},
    {"id": "worldview-consistency", "name": "世界观设定一致性台账守则",
     "desc": "设定台账五件套、设定变更三步流程、高频吃书场景排查与章前查章后记闭环。",
     "category": "一致性", "scopes": ["novel", "serial_novel"], "file": "worldview-consistency.md"},
]

# skills 里已内置的经验包 → 市场目录的展示信息（人工标注分类与一句话说明；
# skills 未来新增内置包时落「未分类」兜底，目录仍然可见、不可安装）
_BUILTIN_META = {
    "fanqie-novel": {"category": "节奏爽点",
                     "desc": "算法流量池与完读追读、黄金三章整体验、题材标签匹配、更新纪律与合同要点。"},
    "qimao-signing": {"category": "流程规范",
                      "desc": "七猫签约导向的写作规范：黄金一章、爽点纪律、期待感三源、人物红线与自检清单。"},
}


def _read_source(fname):
    """读目录内容源文件（限制在本目录内，防路径逃逸）；缺失返回空串。"""
    try:
        p = (_SRC_DIR / fname).resolve()
        if _SRC_DIR.resolve() not in p.parents or not p.is_file():
            return ""
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


for _p in BUILTIN_PACKS:
    # files: {安装相对路径: 内容}——单文件包即一个顶层 .md（装后成为 skills 用户包）；
    # 未来带子目录相对路径的条目会作为随包资料落到 market-assets/（见 _dest_path）
    _p["files"] = {"market-%s.md" % _p["id"]: _read_source(_p["file"])}


def _dest_path(pack_id, rel):
    """files 的相对路径 → 安装目标：无子目录的落 data/skillpacks/ 顶层（成为可注入
    用户包）；带子目录的附加件落 data/skillpacks/market-assets/<id>/（skills 只扫
    顶层 *.md，附加件作为随包资料不参与注入）。"""
    rel = str(rel).replace("\\", "/")
    if "/" not in rel:
        return _user_pack_dir() / rel
    return _user_pack_dir() / "market-assets" / pack_id / rel


def _is_market_file(path, pack_id):
    """文件是否带本包的 market 安装标记（frontmatter 的 source / market_id 两行）。"""
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:800]
    except OSError:
        return False
    return "source: market" in head and ("market_id: %s" % pack_id) in head


# ---------------------------------------------------------------- 安装状态（持久化）

def _load_registry():
    try:
        data = json.loads(_registry_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_registry(data):
    f = _registry_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)  # 原子替换（与 skills._save 同一手法）


def _scan_marked_files():
    """扫 data/skillpacks/*.md 里的 market 安装标记：id → 文件名列表。
    market.json 丢失（手工清理/换机）时据此自愈识别已装包。"""
    found = {}
    try:
        for p in sorted(_user_pack_dir().glob("*.md")):
            try:
                head = p.read_text(encoding="utf-8", errors="replace")[:800]
            except OSError:
                continue
            if "source: market" not in head:
                continue
            m = _MARKER_RE.search(head)
            if m:
                found.setdefault(m.group(1), []).append(p.name)
    except OSError:
        pass
    return found


def installed_ids():
    """市场已安装的包 id 集合 = market.json 记录 ∪ 文件标记扫描（自愈）。"""
    with _LOCK:
        ids = set((_load_registry().get("installed") or {}).keys())
    ids.update(_scan_marked_files().keys())
    return ids


# ---------------------------------------------------------------- 对外接口

def view():
    """市场目录（给 UI/API）：{catalog: [...], categories: [...]}。
    每项带 installed: bool；skills 内置包 installed=True 且 installable=False。"""
    installed = installed_ids()
    items = []
    for p in skills.BUILTIN_PACKS:            # 已内置：已存在，不可安装
        meta = _BUILTIN_META.get(p["id"]) or {}
        items.append({"id": p["id"], "name": p["name"],
                      "desc": meta.get("desc") or p.get("note") or "",
                      "category": meta.get("category") or skills.LESSON_UNCATEGORIZED,
                      "scopes": list(p.get("scopes") or ["*"]),
                      "builtin": True, "installed": True, "installable": False,
                      "chars": len(skills.pack_text(p))})
    for p in BUILTIN_PACKS:                   # 可安装市场包
        files = p.get("files") or {}
        items.append({"id": p["id"], "name": p["name"], "desc": p["desc"],
                      "category": p["category"], "scopes": list(p["scopes"]),
                      "builtin": False, "installed": p["id"] in installed,
                      "installable": True, "files": sorted(files),
                      "chars": sum(len(v) for v in files.values())})
    cats = list(skills.LESSON_CATEGORIES)     # 闭集全枚举先给全（UI 下拉不缺项）
    for it in items:
        if it["category"] not in cats:
            cats.append(it["category"])
    return {"catalog": items, "categories": cats}


def install(pack_id):
    """安装市场包：把 files 写进 skills 的用户包目录（data/skillpacks/），
    之后由 skills 正常解析、按 scopes 注入。返回 (结果 dict, 错误)。

    已装过（记录在且文件仍是本包的）→ 幂等返回 already=True，不覆盖用户可能的
    定制；记录在但文件丢了/被替换 → 换名重写修复，绝不覆盖无标记的用户文件；
    未知 id 与 skills 内置包 → 报错。
    """
    if any(p["id"] == pack_id for p in skills.BUILTIN_PACKS):
        return None, "「%s」是内置经验包（已存在），无需安装" % pack_id
    pack = next((p for p in BUILTIN_PACKS if p["id"] == pack_id), None)
    if not pack:
        return None, "市场目录中无此包: %s" % pack_id
    files = pack.get("files") or {}
    if not any(str(v).strip() for v in files.values()):
        return None, "包内容缺失（skillpacks/market/%s 不存在或为空）" % pack.get("file")
    with _LOCK:
        udir = _user_pack_dir()
        reg = _load_registry()
        installed = reg.setdefault("installed", {})
        rec = installed.get(pack_id)
        primary = "market-%s.md" % pack_id
        target = (rec or {}).get("file") or ""
        already = bool(target and _is_market_file(udir / target, pack_id))
        if not already:
            # 无记录 / 记录在但文件丢了或被替换：主文件选一个不踩用户文件的目标名
            target = primary
            n = 1
            while (udir / target).exists() and not _is_market_file(udir / target, pack_id):
                n += 1
                target = "market-%s-%d.md" % (pack_id, n)
            try:
                udir.mkdir(parents=True, exist_ok=True)
                written = []
                for rel in sorted(files):
                    # 主文件落避让后的名字；带子目录的附加件按原相对路径落 assets
                    name = target if rel == primary else rel
                    dest = _dest_path(pack_id, name)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(files[rel], encoding="utf-8")
                    written.append(name)
            except OSError as e:
                return None, "写入用户技能库失败: %s" % e
            installed[pack_id] = {"file": target, "files": written,
                                  "installed_at": _now()}
        _save_registry(reg)
    return {"ok": True, "id": pack_id, "name": pack["name"], "file": target,
            "already": already}, None


def remove(pack_id):
    """卸载市场包：只删 market.json 有记录（或文件标记可自愈）且文件带本包
    market 标记的包。skills 内置包与无标记的用户自建包一律拒绝。
    返回 None=成功，字符串=错误（沿用 skills.pack_op 的错误风格）。"""
    if any(p["id"] == pack_id for p in skills.BUILTIN_PACKS):
        return "内置经验包不可从市场卸载: %s" % pack_id
    with _LOCK:
        reg = _load_registry()
        installed = reg.get("installed") or {}
        rec = installed.get(pack_id)
        if not rec:
            marked = _scan_marked_files().get(pack_id)
            if marked:      # 记录丢失但标记还在：按标记清理（自愈路径）
                rec = {"file": None, "files": sorted(marked)}
            else:
                return "该包不是市场安装的: %s" % pack_id
        # 只删带本包标记的文件（顶层注入件）；标记被摘掉（多半已是用户内容）→
        # 拒绝，防误伤。market-assets/<id>/ 下的附加件由安装独占写入，按位置归属。
        victims = []
        for rel in (rec.get("files") or ([rec["file"]] if rec.get("file") else [])):
            p = _dest_path(pack_id, rel)
            if not p.is_file():
                continue
            toplevel = "/" not in str(rel).replace("\\", "/")
            if toplevel and not _is_market_file(p, pack_id):
                return "文件已不含市场标记，拒绝删除（确要删除请手工处理）: %s" % p.name
            victims.append(p)
        for p in victims:
            try:
                p.unlink()
            except OSError as e:
                return "删除失败: %s" % e
        # 随包附加件目录（market-assets/<id>/，安装独占命名空间）整棵清掉
        assets = _user_pack_dir() / "market-assets" / pack_id
        if assets.is_dir():
            shutil.rmtree(assets, ignore_errors=True)
        installed.pop(pack_id, None)
        reg["installed"] = installed
        _save_registry(reg)
    return None
