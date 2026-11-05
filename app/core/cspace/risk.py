"""风险判定与动作准入：只读层的安全闸门。

这层解决什么问题
----------------
为「空间管家」提供三件事：判断路径是否为重解析点 / 硬红线目录、把规则或
启发式推断翻译成统一的风险等级、以及在执行清理 / 迁移 / 链接前做最后一道
准入校验。只产出 dict / bool / 异常，绝不改动文件系统。

安全红线
--------
* 任何拿不准的情况一律保守：OSError 视为重解析点、未知风险等级视为 danger、
  未收录目录默认 danger + 不可清。
* 硬红线目录采用「自身或其后代」判断，并用路径分隔符边界防止把名字相近的
  兄弟目录误判为红线；盘根 / 主目录根 / 当前工作目录只做精确匹配，避免把
  一整棵树一刀切。
* 重解析点禁止作为清理 / 迁移目标，防止动作穿透到链接指向的目标盘。
* 本模块只依赖标准库，不导入包内其它模块，杜绝循环导入。

归属：规则数据来自上游 MIT 项目，见仓库根 THIRD_PARTY_NOTICES.md。
"""
from __future__ import annotations

import os
import pathlib
import stat as _stat

RISK_SAFE = "safe"
RISK_CAUTION = "caution"
RISK_DANGER = "danger"

_LABELS = {
    RISK_SAFE: "可清理",
    RISK_CAUTION: "需确认",
    RISK_DANGER: "禁止",
}

# 启发式关键词：命中只给「需确认」，且不允许自动清理。
_KEYWORDS = ("cache", "temp", "tmp", "log", "logs", "crash", "dump", "dumps")


class UnsafePath(ValueError):
    """动作命中安全红线时抛出，供上层中止并提示用户。"""


def _norm(path):
    """统一的路径比较形态：大小写不敏感 + 规范化。"""
    return os.path.normcase(os.path.normpath(os.fspath(path)))


def _same_or_under(path, directory):
    """path 是否等于 directory 或位于其下；用分隔符边界防前缀误判。"""
    p = _norm(path)
    d = _norm(directory)
    if p == d:
        return True
    return p.startswith(d.rstrip(os.sep) + os.sep)


def is_reparse(path) -> bool:
    """判断是否为重解析点 / 符号链接；任何 OSError 一律返回 True。

    Windows 用 lstat 的 st_file_attributes 与 FILE_ATTRIBUTE_REPARSE_POINT；
    其它平台用 Path.is_symlink()。拿不准就当链接，绝不冒险往里走。
    """
    p = os.fspath(path)
    try:
        if os.name == "nt":
            flag = getattr(_stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            try:
                attrs = getattr(os.lstat(p), "st_file_attributes", 0)
            except OSError:
                attrs = 0
            if flag and attrs:
                return bool(attrs & flag)
        return pathlib.Path(p).is_symlink()
    except OSError:
        return True
    except (ValueError, TypeError):
        return True


def _system_drive():
    for key in ("SYSTEMDRIVE", "SystemDrive"):
        value = os.environ.get(key)
        if value:
            return value
    try:
        drive = os.path.splitdrive(os.path.abspath(os.getcwd()))[0]
        if drive:
            return drive
    except OSError:
        pass
    return "C:"


def _windir():
    for key in ("WINDIR", "SystemRoot", "SYSTEMROOT", "windir"):
        value = os.environ.get(key)
        if value:
            return value
    root = _system_drive().rstrip("\\/") + os.sep
    return os.path.join(root, "Windows")


def _codebee_dirs():
    """本产品自身数据目录候选；只用于构造红线，不做任何 IO。"""
    env = os.environ
    dirs = []
    custom = env.get("CODEBEE_DATA_DIR")
    if custom:
        dirs.append(custom)
    for key in ("APPDATA", "LOCALAPPDATA", "PROGRAMDATA"):
        value = env.get(key)
        if value:
            dirs.append(os.path.join(value, "CodeBee"))
    home = env.get("USERPROFILE") or env.get("HOME")
    if home:
        dirs.append(os.path.join(home, ".codebee"))
    # 包所在目录：防止把自身代码目录当作清理 / 迁移目标。
    dirs.append(os.path.dirname(os.path.abspath(__file__)))
    return dirs


def is_forbidden(path) -> bool:
    """硬红线：命中即禁止 clean / migrate / link。

    祖先型红线（自身或其后代）：系统目录树及其核心子目录、系统卷信息、
    回收站、本产品数据目录。
    精确型红线（仅该路径本身）：任意盘根、页面/休眠/交换文件、当前工作
    目录、用户主目录根。
    """
    p = _norm(path)

    # 1) 任意盘根：精确匹配。
    drive, tail = os.path.splitdrive(p)
    if tail in ("", os.sep, "/", "\\"):
        return True

    # 2) 精确型红线。
    exact = set()
    try:
        exact.add(_norm(os.getcwd()))
    except OSError:
        pass
    try:
        exact.add(_norm(os.path.expanduser("~")))
    except OSError:
        pass
    if p in exact:
        return True
    drive_root = (drive + os.sep) if drive else os.sep
    for name in ("pagefile.sys", "hiberfil.sys", "swapfile.sys"):
        if p == _norm(os.path.join(drive_root, name)):
            return True

    # 3) 系统核心目录：整棵系统目录树及其重点子目录，祖先型。
    windir = _windir()
    system_trees = (windir,) + tuple(
        os.path.join(windir, sub)
        for sub in ("System32", "WinSxS", "Installer", "servicing")
    )
    for directory in system_trees:
        if _same_or_under(p, directory):
            return True

    # 4) 特殊系统目录：任意盘根下，祖先型。
    for name in ("System Volume Information", "$Recycle.Bin"):
        if _same_or_under(p, os.path.join(drive_root, name)):
            return True

    # 5) 本产品自身数据目录，祖先型。
    for directory in _codebee_dirs():
        if _same_or_under(p, directory):
            return True

    return False


def assert_action_allowed(path, action) -> None:
    """动作准入校验；不通过抛 UnsafePath，通过返回 None。

    scan 为纯只读，一律放行（重解析点的规避由扫描层负责）；clean / migrate /
    link 命中硬红线即拒绝；clean / migrate 还额外拒绝重解析点目标。
    """
    if action not in ("scan", "clean", "migrate", "link"):
        raise ValueError("未知动作: %r" % (action,))
    if action == "scan":
        return None
    if is_forbidden(path):
        raise UnsafePath("路径命中安全红线，禁止 %s: %s" % (action, path))
    if action in ("clean", "migrate") and is_reparse(path):
        raise UnsafePath("目标是重解析点，禁止 %s: %s" % (action, path))
    return None


def heuristic(path) -> dict:
    """未收录目录的启发式推断：宁可少判，不可误判。

    名字含缓存/临时/日志/转储特征时给最保守的 caution + 需确认，且不允许
    自动清理；其余一律 danger + 不可清 + 不可迁。
    """
    name = os.path.basename(os.path.normpath(os.fspath(path or ""))).lower()
    if any(word in name for word in _KEYWORDS):
        return {
            "level": RISK_CAUTION,
            "label": _LABELS[RISK_CAUTION],
            "cleanable": False,
            "migratable": False,
            "reason": "目录名含缓存/临时/日志/转储特征，但未收录规则，仅提示需人工确认，不自动清理",
        }
    return {
        "level": RISK_DANGER,
        "label": _LABELS[RISK_DANGER],
        "cleanable": False,
        "migratable": False,
        "reason": "未收录目录，按最保守策略禁止清理与迁移",
    }


def classify(rule) -> dict:
    """把规则 dict 翻译成统一风险结论；无规则时回落 heuristic。"""
    if not rule:
        return heuristic("")
    level = str(rule.get("risk") or RISK_DANGER).lower()
    if level not in _LABELS:
        level = RISK_DANGER
    cleanable = bool(rule.get("cleanable", False))
    migratable = bool(rule.get("migratable", False))
    if level == RISK_DANGER:
        cleanable = False
    reason = (
        rule.get("riskNote")
        or rule.get("risk_note")
        or rule.get("purpose")
        or ("按规则等级 %s 判定" % level)
    )
    return {
        "level": level,
        "label": _LABELS[level],
        "cleanable": cleanable,
        "migratable": migratable,
        "reason": str(reason),
    }
