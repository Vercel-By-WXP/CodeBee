# -*- coding: utf-8 -*-
"""Agent Skills 开源标准接入（SKILL.md 技能包 → 经验库注入链）。

Anthropic 开源的 Agent Skills 标准：一个技能 = 一个文件夹，根放 SKILL.md
（YAML frontmatter：name / description 必填，可选 license / allowed-tools /
metadata），其余为随包资料（references/ assets/ 等）。社区生态见
anthropics/skills、ClawHub 等——market_remote 已覆盖「从市场源远程装」，
本模块补「本地导入」通道（用户自己下载的 zip/文件夹；直连 GitHub 不稳时
先手动下载再导入），并让标准技能进入现有注入链（skills.all_packs）：

  - 落盘零改写：SKILL.md 原样保存（标准字段全保留），注入时才剥 frontmatter；
  - 装前安检：复用 market_remote.inspect_tree（剥离 scripts/hooks/commands/
    agents 与一切可执行件，只放行文本+图片）+ skill_scan 静态扫描，
    外来内容默认 unverified、危险模式升 high-risk；
  - 导入后默认停用：装了不等于开了，审阅 + 安检后再手动启用（治理位与
    evolve 晋升草稿一致）；启停状态复用 skills.json 的 packs 账（id 前缀
    agentskill-），与经验库页的启停通道同一条；
  - scopes 未声明默认 ["*"]：走 block_for 的通配单包预算（2400 字），
    不会挤占项目教训的保底区；
  - 轻量渐进披露：注入正文尾部附「技能目录 + 随包文本文件清单」——有文件
    读取能力的 CLI 智能体（claude-code 等）可按需 Read，正文不够用时自查，
    不必等全文注入（无文件能力者忽略该行即可）。
"""
from __future__ import annotations

import re
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path

from . import paths, skill_scan

_LOCK = threading.RLock()

_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")   # 开源标准：小写字母/数字/连字符
_FM_RE = re.compile(r"(?s)\A---\s*\n(.*?)\n---\s*\n?")
MAX_NAME_CHARS = 64        # 标准上限
MAX_DESC_CHARS = 1024      # 标准上限
PREVIEW_FILE_CAP = 200 * 1024
PREVIEW_EXT_OK = {".md", ".markdown", ".txt", ".csv", ".json", ".yaml", ".yml"}

# mtime 缓存（与 skills._user_pack_cache 同套路：改文件即失效，零依赖替代 watchdog）
_cache = {}                                  # SKILL.md 路径 → (mtime_ns, entry)
_dir_cache = {"ts": None, "names": None}     # 根目录 mtime_ns → 技能目录名清单


def root_dir():
    """技能落盘根：每次现取 paths.DATA_DIR（测试重定向后自动跟随）。"""
    return Path(paths.DATA_DIR) / "agent_skills"


# ---------------------------------------------------------------- 解析与校验

def parse_skill_md(text):
    """解析标准 SKILL.md → (meta, body)。无 frontmatter 时 meta 为空、body 原文。

    YAML 子集：平铺 key: value、`- item` 列表（scopes 等）、内联 [a, b]、
    以及一层嵌套 map（标准可选字段 metadata:，子键只收标量）。注释行跳过。
    """
    text = str(text or "")
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    body = text[m.end():]
    meta, nested, cur_list = {}, None, None
    for line in m.group(1).splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        indented = line[:1] in (" ", "\t")
        if s.startswith("- "):
            if cur_list is not None and not indented:
                cur_list.append(s[2:].strip().strip("'\""))
            continue
        mm = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$", s)
        if not mm:
            continue
        key, val = mm.group(1), mm.group(2).strip()
        if indented:
            if nested is not None and val:
                nested[key] = val.strip("'\"")
            continue
        cur_list = None
        if val == "":
            if key == "metadata":
                nested = {}
                meta[key] = nested
            else:
                cur_list = []
                meta[key] = cur_list
            continue
        nested = None
        meta[key] = _inline_value(val)
    return meta, body


def _inline_value(val):
    """内联值：[a, b] 形态拆列表，其余剥引号当标量。"""
    if len(val) >= 2 and val[0] == "[" and val[-1] == "]":
        return [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
    return val.strip("'\"")


def validate(meta, folder=""):
    """按开源标准校验 frontmatter。返回 (errors, warnings)；errors 非空拒收。"""
    errs, warns = [], []
    name = str(meta.get("name") or "").strip()
    desc = str(meta.get("description") or "").strip()
    if not name:
        errs.append("缺少 name")
    elif len(name) > MAX_NAME_CHARS:
        errs.append("name 超过 %d 字符" % MAX_NAME_CHARS)
    elif not _NAME_RE.match(name):
        errs.append("name 只允许小写字母、数字与连字符: %s" % name)
    if not desc:
        errs.append("缺少 description")
    elif len(desc) > MAX_DESC_CHARS:
        errs.append("description 超过 %d 字符" % MAX_DESC_CHARS)
    if name and folder and name != folder:
        warns.append("目录名「%s」与 name「%s」不一致（按 frontmatter name 落地）"
                     % (folder, name))
    return errs, warns


# ---------------------------------------------------------------- 导入

def import_path(path):
    """从本地文件夹或 .zip 导入开源 Agent Skills（可一次多技能，多技能仓库
    逐个 SKILL.md 各成一技）。返回 (结果 dict, 错误)；结果含 imported/errors/
    stripped（剥离清单，给导入报告展示）。"""
    from . import market_remote
    p = Path(str(path or "").strip().strip('"'))
    if not str(p):
        return None, "请填技能文件夹或 .zip 的本地路径"
    if not p.exists():
        return None, "路径不存在: %s" % p
    tmp = None
    try:
        if p.is_file():
            if p.suffix.lower() != ".zip":
                return None, "仅支持文件夹或 .zip: %s" % p.name
            tmp = tempfile.TemporaryDirectory(prefix="codebee-agentskill-")
            with zipfile.ZipFile(p) as zf:
                market_remote._safe_extract(zf, Path(tmp.name) / "unzip")
            src = market_remote._plugin_root(Path(tmp.name) / "unzip")
        else:
            src = p
        return _import_tree(src)
    except (zipfile.BadZipFile, ValueError) as e:
        # BadZipFile=损坏包；ValueError=_safe_extract 的路径可疑/体量超限等安检拒绝
        return None, "导入被拒绝: %s" % e
    finally:
        if tmp:
            tmp.cleanup()


def _import_tree(src):
    from . import market_remote
    src = Path(src)
    try:
        files, stripped = market_remote.inspect_tree(src)
    except ValueError as e:
        return None, str(e)
    found = market_remote.find_skills(src, files)
    if not found:
        return None, "未找到 SKILL.md（剔除脚本/钩子后没有标准技能内容）"
    imported, errors, seen = [], [], set()
    for sk in found:
        label = str(sk.get("name") or sk.get("skill_md") or "")
        try:
            res = _import_one(src, sk, files)
        except (OSError, ValueError) as e:
            errors.append("%s: %s" % (label, e))
            continue
        if isinstance(res, str):
            errors.append("%s: %s" % (label, res))
            continue
        if res["name"] in seen:
            continue
        seen.add(res["name"])
        imported.append(res)
    with _LOCK:
        _dir_cache["ts"] = None
    return {"imported": imported, "errors": errors,
            "stripped": [str(s) for s in stripped[:20]]}, None


def _import_one(src, sk, all_files):
    """导入单个技能（find_skills 的一条）：校验 → 静态安检 → 原样落盘 → 默认停用。
    成功返回摘要 dict；技能不合格返回错误说明字符串。

    附件不从 find_skills 的 extras 取——它对根布局 SKILL.md（无子目录）算出的
    prefix 是 "/"，会把附件整批滤空；这里按技能目录自行筛选（文本件、同目录）。"""
    from . import market_remote, skills
    skill_md_rel = str(sk["skill_md"]).replace("\\", "/")
    raw = market_remote._read_text(src / sk["skill_md"])
    if not raw or not raw.strip():
        return "SKILL.md 为空或超过大小上限"
    meta, _ = parse_skill_md(raw)
    parts = skill_md_rel.split("/")
    folder = parts[-2] if len(parts) > 1 else ""
    if folder == "main":        # find_skills 对根布局 SKILL.md 记名 main，无目录名可言
        folder = ""
    errs, warns = validate(meta, folder)
    if errs:
        return "；".join(errs)
    name = str(meta["name"]).strip()
    body = _FM_RE.sub("", raw, count=1)
    scan = skill_scan.trust_tier(body, source="external")
    target = root_dir() / name
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(raw, encoding="utf-8")   # 原样保存，标准字段零改写
    prefix = "/".join(parts[:-1] + [""]) if len(parts) > 1 else ""
    extras = []
    for rel in sorted(str(r).replace("\\", "/") for r, _p in all_files):
        if rel == skill_md_rel or not rel.startswith(prefix):
            continue
        if Path(rel).suffix.lower() not in market_remote._TEXT_EXT:
            continue   # 只随包文本资料（与市场通道同口径；图片不进注入链）
        sub = rel[len(prefix):]
        if not sub:
            continue
        dest = target / sub
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / rel, dest)
        extras.append(sub)
    # 默认停用：装了不等于开了——启停走 skills.pack_op 同一条账（id 前缀 agentskill-）
    skills.pack_op("agentskill-" + name, "disable")
    return {"name": name, "description": str(meta.get("description") or ""),
            "warns": warns, "trust": scan["tier"], "label": scan["label"],
            "files": extras}


# ---------------------------------------------------------------- 加载与列表

def _skill_dirs():
    root = root_dir()
    try:
        root.mkdir(parents=True, exist_ok=True)
        ts = root.stat().st_mtime_ns
    except OSError:
        return []
    with _LOCK:
        if _dir_cache["names"] is not None and _dir_cache["ts"] == ts:
            return list(_dir_cache["names"])
        names = [d.name for d in sorted(root.iterdir())
                 if d.is_dir() and (d / "SKILL.md").is_file()]
        _dir_cache["ts"] = ts
        _dir_cache["names"] = list(names)
        return names


def _load_skill(name):
    """读取并解析一个技能（mtime 缓存）。缺失/不可读返回 None。"""
    smd = root_dir() / name / "SKILL.md"
    try:
        mt = smd.stat().st_mtime_ns
    except OSError:
        return None
    key = str(smd)
    with _LOCK:
        hit = _cache.get(key)
        entry = None if not hit or hit[0] != mt else hit[1]
    if entry is None:
        try:
            raw = smd.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        meta, body = parse_skill_md(raw)
        scan = skill_scan.trust_tier(body, source="external")
        files = []
        try:
            for p in sorted((root_dir() / name).rglob("*")):
                if p.is_file() and p.name != "SKILL.md":
                    rel = p.relative_to(root_dir() / name).as_posix()
                    files.append({"path": rel, "size": p.stat().st_size})
        except OSError:
            pass
        entry = {
            "id": "agentskill-" + name,
            "name": name,
            "title": str(meta.get("name") or name),
            "description": str(meta.get("description") or ""),
            "license": str(meta.get("license") or ""),
            "allowed_tools": str(meta.get("allowed-tools") or meta.get("allowed_tools") or ""),
            "metadata": meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {},
            "dir": str(root_dir() / name),
            "files": files,
            "chars": len(body),
            "scopes": [str(s) for s in (meta.get("scopes") or ["*"])] or ["*"],
            "trust": scan["tier"],
            "risk": scan["label"],
            "findings": scan.get("findings") or [],
            "injection": bool(scan.get("injection")),
        }
        with _LOCK:
            _cache[key] = (mt, entry)
    # 启停账在 skills.json（pack_op 会改），不进 mtime 缓存——每次现读
    entry = dict(entry, enabled=_enabled(name))
    return entry


def _enabled(name):
    """启停状态：读 skills.json 的 packs 账；无记录（手动拷进来的目录）默认启用。"""
    from . import skills
    return skills._pack_enabled("agentskill-" + name)


def list_skills():
    out = []
    for name in _skill_dirs():
        e = _load_skill(name)
        if e:
            out.append(e)
    return out


def packs():
    """折算成 skills.all_packs() 的包形态参与现有注入链（启停/预算/安检信封
    全部复用 block_for 的既有闸）。"""
    out = []
    for name in _skill_dirs():
        e = _load_skill(name)
        if not e:
            continue
        out.append({
            "id": e["id"],
            "name": e["title"] or e["name"],
            "file": str(root_dir() / name / "SKILL.md"),
            "scopes": e["scopes"],
            "note": e["description"][:160],
            "builtin": False,
            "user": False,
            "agent": True,
            "trust": e["trust"],
            "scan": {"tier": e["trust"], "label": e["risk"],
                     "findings": e["findings"], "injection": e["injection"]},
            "appendix": _appendix(e),
        })
    return out


def _appendix(e):
    """渐进披露附录：只在确有随包文本资料时生成，一行内说清目录与文件名。"""
    texts = [f["path"] for f in (e.get("files") or [])][:12]
    if not texts:
        return ""
    return ("随包资料（Agent Skills 渐进披露）：技能目录 %s，含 %s。上述正文不够用时，"
            "可直接读取对应文件获取完整说明。" % (e["dir"], "、".join(texts)))


# ---------------------------------------------------------------- 操作

def skill_op(name, op):
    """启用/停用/删除。启停复用 skills.pack_op 的状态账；删除连目录带状态记录
    一起清（重装同名牌从停用态重新开始）。返回错误说明或 None。"""
    from . import skills
    name = str(name or "").strip()
    if not name or not _NAME_RE.match(name):
        return "技能名不合法"
    d = root_dir() / name
    if not (d / "SKILL.md").is_file():
        return "技能不存在: " + name
    if op == "delete":
        shutil.rmtree(d, ignore_errors=True)
        with skills._LOCK:
            data = skills._load()
            packs = data.get("packs") or {}
            if "agentskill-" + name in packs:
                packs.pop("agentskill-" + name)
                data["packs"] = packs
                skills._save(data)
        with _LOCK:
            _cache.pop(str(d / "SKILL.md"), None)
            _dir_cache["ts"] = None
        return None
    if op in ("enable", "disable"):
        err = skills.pack_op("agentskill-" + name, op)
        return None if err == "经验包不存在" else err
    return "未知操作 " + str(op)


def read_file(name, rel):
    """读技能目录内的文本文件（UI 预览用）。穿越/超大/非文本一律拒绝。
    返回 (text, error)。"""
    name = str(name or "").strip()
    rel = str(rel or "").strip().replace("\\", "/")
    if not name or not _NAME_RE.match(name):
        return None, "技能名不合法"
    base = (root_dir() / name).resolve()
    if not (base / "SKILL.md").is_file():
        return None, "技能不存在: " + name
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        return None, "文件路径可疑"
    p = (base / rel).resolve()
    if base != p and base not in p.parents:
        return None, "文件路径可疑"
    if not p.is_file():
        return None, "文件不存在"
    try:
        if p.stat().st_size > PREVIEW_FILE_CAP:
            return None, "文件超过预览上限（200KB）"
    except OSError:
        return None, "文件不可读"
    if p.suffix.lower() and p.suffix.lower() not in PREVIEW_EXT_OK:
        return None, "仅支持预览文本文件"
    try:
        return p.read_text(encoding="utf-8", errors="replace"), None
    except OSError as e:
        return None, "文件不可读: %s" % e
