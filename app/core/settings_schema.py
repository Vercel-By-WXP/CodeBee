# -*- coding: utf-8 -*-
"""Schema 化设置：namespace 注册 + revision CAS + 脱敏 describe。

设计稿：docs/migration/05-model-seams.md §4C（按 Tutti 实际架构落地：
不替换现有 settings.py——它正被并行迭代且已有原子写+钳制；本模块提供
「schema 驱动 + revision CAS + secret 脱敏」能力，管理自己的
data/settings_v2.json，供新增配置项使用，存量 key-value 不迁移）。

参考 dsh packages/settings/settings/src/index.ts:419-637：
  register(ns, schema, base) / mutate(ops) / describe({redactSecrets}) /
  SettingsConflictError（revision 单调，乐观并发）。

冲突语义：mutate 带 expected_revision 时做 CAS；不带则最后写赢。
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

_LOCK = threading.RLock()
_FILE = None  # init() 注入
_NAMESPACES = {}   # ns -> {"fields": {path: FieldDef}, "validate": fn}
_VALUES = {}       # ns -> {path: value}
_REVISIONS = {}    # ns -> int


class SettingsConflictError(Exception):
    """CAS 失败：调用方持有的是陈旧 revision。"""


class FieldDef:
    __slots__ = ("path", "ftype", "default", "description", "redact", "choices", "clamp")

    def __init__(self, path, ftype, default, description="", redact=False,
                 choices=None, clamp=None):
        self.path = path
        self.ftype = ftype            # "int" | "float" | "str" | "bool"
        self.default = default
        self.description = description
        self.redact = redact          # True = describe() 输出 __REDACTED__
        self.choices = choices        # 可选：合法值列表
        self.clamp = clamp            # 可选：(min, max)，int/float 用


def init(data_dir=None):
    global _FILE, _NAMESPACES, _VALUES, _REVISIONS
    with _LOCK:
        base = Path(data_dir) if data_dir else Path(_default_dir())
        base.mkdir(parents=True, exist_ok=True)
        _FILE = base / "settings_v2.json"
        _NAMESPACES, _VALUES, _REVISIONS = {}, {}, {}
        try:
            data = json.loads(_FILE.read_text(encoding="utf-8"))
            _VALUES = data.get("values") or {}
            _REVISIONS = data.get("revisions") or {}
        except (OSError, json.JSONDecodeError):
            pass


def _default_dir():
    from . import paths
    return str(paths.DATA_DIR)


def _persist():
    if _FILE is None:
        return
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"values": _VALUES, "revisions": _REVISIONS},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_FILE)


# ---- 路径读写（支持 "a.b" 点路径；不实现数组下标——Tutti 配置用不到） ----

def _get_path(d, path):
    cur = d
    for seg in path.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


def _set_path(d, path, value):
    segs = path.split(".")
    cur = d
    for seg in segs[:-1]:
        cur = cur.setdefault(seg, {})
        if not isinstance(cur, dict):
            raise ValueError("路径冲突：%s" % path)
    cur[segs[-1]] = value


# ---- 注册 ----

def register_namespace(ns, fields, validate=None):
    """注册 namespace 与字段 schema。已注册的 namespace 不可重复注册。"""
    with _LOCK:
        if ns in _NAMESPACES:
            raise ValueError("namespace 已注册：%s" % ns)
        fmap = {}
        for f in fields:
            fmap[f.path] = f
            if _get_path(_VALUES.get(ns, {}), f.path) is None:
                _VALUES.setdefault(ns, {})
                _set_path(_VALUES[ns], f.path, f.default)
        _NAMESPACES[ns] = {"fields": fmap, "validate": validate}
        _REVISIONS.setdefault(ns, 1)
        _persist()


def coerce(field, value):
    """按字段类型强转 + 钳制 + choices 校验。返回 (规范化值, 错误)。"""
    try:
        if field.ftype == "int":
            v = int(value)
        elif field.ftype == "float":
            v = float(value)
        elif field.ftype == "bool":
            v = bool(value) if not isinstance(value, str) else (
                value.strip().lower() in ("1", "true", "yes", "on"))
        else:
            v = str(value)
    except (TypeError, ValueError):
        return None, "%s 必须是 %s" % (field.path, field.ftype)
    if field.clamp:
        lo, hi = field.clamp
        v = max(lo, min(hi, v))
    if field.choices is not None and v not in field.choices:
        return None, "%s 必须是 %s 之一" % (field.path, field.choices)
    return v, None


# ---- 读写 ----

def get(ns, path=None):
    with _LOCK:
        vals = _VALUES.get(ns)
        if vals is None:
            return None
        return _get_path(vals, path) if path else dict(vals)


def revision(ns):
    with _LOCK:
        return _REVISIONS.get(ns, 0)


def mutate(ns, ops, expected_revision=None):
    """写一组操作。ops = [{"op":"set","path":..., "value":...}, ...]。

    值按注册的 FieldDef 强转/钳制/校验；namespace 有 validate 钩子时整体验证。
    expected_revision 非 None 时做 CAS。成功返回新 revision。
    """
    with _LOCK:
        ndef = _NAMESPACES.get(ns)
        if not ndef:
            raise ValueError("namespace 未注册：%s" % ns)
        if expected_revision is not None and _REVISIONS.get(ns) != expected_revision:
            raise SettingsConflictError(
                "%s 期望 rev %s，实际 %s" % (ns, expected_revision, _REVISIONS.get(ns)))
        # 先全部校验，后统一写入（部分失败不落盘）
        staged = []
        for op in ops:
            if op.get("op") != "set":
                raise ValueError("仅支持 set 操作：%r" % op.get("op"))
            path = op.get("path")
            fdef = ndef["fields"].get(path)
            if not fdef:
                raise ValueError("未注册字段：%s" % path)
            v, err = coerce(fdef, op.get("value"))
            if err:
                raise ValueError(err)
            staged.append((path, v))
        ns_values = dict(_VALUES.get(ns, {}))
        for path, v in staged:
            _set_path(ns_values, path, v)
        if ndef["validate"]:
            err = ndef["validate"](ns_values)
            if err:
                raise ValueError("namespace 校验失败：%s" % err)
        _VALUES[ns] = ns_values
        _REVISIONS[ns] = _REVISIONS.get(ns, 1) + 1
        _persist()
        return _REVISIONS[ns]


def describe(ns, redact_secrets=True):
    """给 UI 的视图：默认把 redact 字段替换为 __REDACTED__（不泄露真值）。"""
    with _LOCK:
        vals = json.loads(json.dumps(_VALUES.get(ns, {})))  # deep copy
        ndef = _NAMESPACES.get(ns) or {"fields": {}}
    for f in ndef["fields"].values():
        if f.redact and redact_secrets:
            if _get_path(vals, f.path) not in (None, ""):
                _set_path(vals, f.path, "__REDACTED__")
    return {"ns": ns, "revision": _REVISIONS.get(ns, 0), "values": vals}


# ---- 默认 namespace（编排阈值的 schema 化存放处；pipeline 可渐进接入） ----

def register_default_namespaces():
    """注册 Tutti 默认 namespace。幂等（已注册则跳过）。"""
    if "orchestrator" in _NAMESPACES:
        return
    register_namespace(
        "orchestrator",
        fields=[
            FieldDef("compaction.enabled", "bool", False,
                     "上下文压缩总开关（灰度：与 TUTTI_COMPACTION 任一开启即生效）"),
            FieldDef("compaction.pressure_threshold", "float", 0.8,
                     "触发压缩的压力比阈值",
                     clamp=(0.1, 0.99)),
            FieldDef("compaction.retain_tail_tokens", "int", 8000,
                     "压缩时保留尾部预算（token 估算）",
                     clamp=(0, 100000)),
            FieldDef("max_goal_rounds", "int", 5,
                     "Goal 续行上限",
                     clamp=(1, 20)),
        ],
        validate=lambda v: None,
    )