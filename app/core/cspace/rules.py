"""规则库只读层：加载、宏展开与规则匹配。

这层解决什么问题
----------------
把仓库自带的规则 JSON 读成统一清单，把 Windows 路径宏与通配符展开成可枚举
的具体路径，并回答「某个绝对路径由哪条规则命中」。只产出可直接 JSON 序列化
的 dict / list[dict]，不做任何清理、迁移或写入。

安全红线
--------
* 模块导入阶段绝不读磁盘、绝不展开宏、绝不扫描真实用户目录；所有 IO 都发生
  在函数被显式调用时，路径来源可由 base / env 注入，便于隔离测试。
* 宏无法解析时返回 None 并由调用方跳过，绝不用字面量伪造一个看似合法的路径。
* 本层只读，且不反向依赖包内扫描/分析模块，杜绝循环导入。

归属：规则数据来自上游 MIT 项目，见仓库根 THIRD_PARTY_NOTICES.md。
"""
from __future__ import annotations

import fnmatch
import glob
import json
import os
import re

RULE_FILES = ("system.json", "dev_browser.json", "apps.json")

_MACRO_RE = re.compile(r"%([^%]+)%")
_GLOB_CHARS = ("*", "?", "[")

# 等价宏名：只从已有值做确定性推导，不编造任何字面量。
_ALIASES = {
    "TEMP": ("TMP",),
    "TMP": ("TEMP",),
    "HOME": ("USERPROFILE",),
    "USERPROFILE": ("HOME",),
    "WINDIR": ("SYSTEMROOT",),
    "SYSTEMROOT": ("WINDIR",),
}

# 惰性缓存：导入时保持为空，首次调用才填充；避免 match_rule 反复读盘。
_CACHE = {}


def _build_table(env):
    """把注入的环境映射规整成「大写宏名 -> 值」的查找表。"""
    source = os.environ if env is None else env
    try:
        items = source.items()
    except AttributeError:
        items = os.environ.items()
    table = {}
    for key, value in items:
        name = str(key).strip("%").upper()
        if name and value:
            table[name] = str(value)
    return table


def _value(table, name):
    """精确查表，再按确定性别名推导；取不到返回 None。"""
    name = name.upper()
    for candidate in (name,) + _ALIASES.get(name, ()):
        found = table.get(candidate)
        if found:
            return found
    return None


def _norm_seps(text):
    """规则模板用反斜杠，注入值可能是另一种风格，统一到当前平台分隔符。"""
    return text.replace("\\", os.sep).replace("/", os.sep)


def expand_macros(text, env=None) -> str | None:
    """展开 %XXX% 宏，返回规范化的绝对路径；无法展开时返回 None。

    env 为 None 时使用真实环境；传入 dict 时严格只使用该 dict，不回落真实
    环境，方便测试用临时目录完全接管路径来源。未知宏或 env 缺值一律返回 None，
    结果非绝对路径也返回 None，绝不降级成相对路径或字面量。
    """
    if not isinstance(text, str) or not text:
        return None
    table = _build_table(env)
    failed = [False]

    def replace(match):
        found = _value(table, match.group(1))
        if found is None:
            failed[0] = True
            return ""
        return found

    merged = _MACRO_RE.sub(replace, text)
    if failed[0]:
        return None
    result = os.path.normpath(_norm_seps(merged))
    if not os.path.isabs(result):
        return None
    return result


def load_rules(base=None) -> list[dict]:
    """读取三个规则文件并摊平成规则 dict 列表。

    每条保留原始字段，并补 id（原始 id，缺失时按文件名+序号生成）与
    source_file；缺字段一律给最保守的安全默认值。单文件损坏/缺失时跳过而
    不是整包失败；但若最终一条规则都没有，则抛 RuntimeError，绝不静默返回
    空清单让上层误以为「没有目标」。
    """
    if base is None:
        base = os.path.join(os.path.dirname(__file__), "rules")
    base = os.fspath(base)
    out = []
    errors = []
    for filename in RULE_FILES:
        full = os.path.join(base, filename)
        try:
            with open(full, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            errors.append("%s: %s" % (filename, exc))
            continue
        if isinstance(data, dict):
            data = data.get("rules", []) or []
        if not isinstance(data, list):
            errors.append("%s: 顶层结构不是列表" % filename)
            continue
        for position, raw in enumerate(data):
            if not isinstance(raw, dict):
                errors.append("%s[%d]: 条目不是对象" % (filename, position))
                continue
            rule = dict(raw)
            rule["id"] = str(raw.get("id") or "%s-%d" % (filename, position))
            rule["source_file"] = filename
            rule.setdefault("name", "")
            rule.setdefault("category", "")
            rule.setdefault("purpose", "")
            rule.setdefault("risk", "danger")
            rule.setdefault("riskNote", "")
            rule.setdefault("cleanTarget", "")
            rule.setdefault("migrateNote", "")
            rule.setdefault("cleanable", False)
            rule.setdefault("migratable", False)
            rule.setdefault("paths", [])
            rule.setdefault("processes", [])
            if not isinstance(rule["paths"], list):
                rule["paths"] = []
            if not isinstance(rule["processes"], list):
                rule["processes"] = []
            rule["cleanable"] = bool(rule["cleanable"])
            rule["migratable"] = bool(rule["migratable"])
            if rule["risk"] not in ("safe", "caution", "danger"):
                rule["risk"] = "danger"
            out.append(rule)
    if not out:
        raise RuntimeError("规则库不可用，全部文件读取失败: " + "; ".join(errors))
    return out


def _all_rules():
    """match_rule 专用的惰性缓存；规则是静态资源，缓存一次即可。"""
    if "rules" not in _CACHE:
        _CACHE["rules"] = load_rules()
    return _CACHE["rules"]


def _expand_glob(pattern, exist_only):
    """展开通配符；glob 天然只能返回实际存在的路径。

    无通配符时 exist_only=False 允许返回不存在的路径；有通配符时必须靠 glob
    展开，无法为不存在的通配目标凭空生成条目，因此两种 exist_only 取值对通配
    路径的实际效果一致。
    """
    if any(char in pattern for char in _GLOB_CHARS):
        try:
            return sorted(glob.glob(pattern))
        except (OSError, re.error):
            return []
    if exist_only and not os.path.exists(pattern):
        return []
    return [pattern]


def resolved_targets(kind="all", env=None, exist_only=True) -> list[dict]:
    """按动作类型过滤规则并展开每条 paths（宏 + 通配符）。

    kind="all" 返回全部规则；"clean" / "migrate" 分别按 cleanable / migratable
    过滤。返回项字段固定，路径统一 str，可直接 JSON 序列化。
    """
    if kind not in ("all", "clean", "migrate"):
        raise ValueError("未知 kind: %r" % (kind,))
    out = []
    for rule in load_rules():
        cleanable = bool(rule.get("cleanable", False))
        migratable = bool(rule.get("migratable", False))
        if kind == "clean" and not cleanable:
            continue
        if kind == "migrate" and not migratable:
            continue
        for raw in rule.get("paths", []) or []:
            expanded = expand_macros(raw, env=env)
            if expanded is None:
                continue
            for path in _expand_glob(expanded, exist_only):
                out.append({
                    "rule_id": rule.get("id", ""),
                    "name": rule.get("name", ""),
                    "category": rule.get("category", ""),
                    "path": str(path),
                    "purpose": rule.get("purpose", ""),
                    "risk": rule.get("risk", "danger"),
                    "risk_note": rule.get("riskNote", rule.get("risk_note", "")),
                    "cleanable": cleanable,
                    "migratable": migratable,
                    "clean_target": rule.get("cleanTarget", rule.get("clean_target", "")),
                    "processes": list(rule.get("processes", []) or []),
                    "source_file": rule.get("source_file", ""),
                })
    return out


def _matches(target, pattern):
    """判断规范化后的路径是否命中模式，用分隔符边界防前缀误判。"""
    if any(char in pattern for char in _GLOB_CHARS):
        if fnmatch.fnmatch(target, pattern):
            return True
        suffix = pattern.rstrip(os.sep) + os.sep + "*"
        return fnmatch.fnmatch(target, suffix)
    if target == pattern:
        return True
    return target.startswith(pattern.rstrip(os.sep) + os.sep)


def match_rule(path) -> dict | None:
    """给定绝对路径，返回最长前缀命中的规则 dict；没有命中返回 None。

    含通配符的规则路径用 fnmatch 参与匹配；无通配符路径用「自身或其后代」
    判断。所有比较都经 normcase + normpath，避免大小写与前缀误判。
    """
    target = os.path.normcase(os.path.normpath(os.fspath(path)))
    best = None
    best_score = -1
    for rule in _all_rules():
        for raw in rule.get("paths", []) or []:
            expanded = expand_macros(raw)
            if expanded is None:
                continue
            pattern = os.path.normcase(os.path.normpath(expanded))
            if _matches(target, pattern) and len(pattern) > best_score:
                best_score = len(pattern)
                best = rule
    return best
