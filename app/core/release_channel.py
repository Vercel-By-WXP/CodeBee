"""发布渠道口径：0.y.z 是预览版，1.0.0 起是正式版。

为什么把这件事写成代码而不是只写文案：渠道是一条对用户的承诺，至少有三处要
同时成立——README/QUICKSTART 标注当前渠道、npm dist-tag 分流、发版清单核对。
历史上这三处各说各话（README 写「稳定版」，实际版本号还在 0.x），用户按「稳定」
的预期使用，遇到 CLI 参数变更就变成事故。

口径（2026-10-23 定，产品决策见 docs/release-checklist.md）：

- **0.y.z = 预览版**：次版本之间允许调整 CLI 参数、数据目录结构、HTTP 接口形状；
  不承诺向后兼容，但保证提供迁移或明确的不兼容说明。
- **1.0.0+ = 正式版**：遵守语义化版本——破坏性变更只在主版本，次版本只增不改。
- **npm dist-tag**：``latest`` 始终指向当前推荐版本（预览期就是最新预览版，
  ``npx codebee@latest`` 必须能用）；预览期额外发 ``preview``，供想钉住预览通道
  的用户 ``npm i -g codebee@preview``。
"""
import json
import os
import re

PREVIEW = "preview"
STABLE = "stable"
UNKNOWN = "unknown"

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


def channel_of(version):
    """按主版本号判渠道。解析不出主版本时返回 unknown，不猜。

    预发布后缀（``1.0.0-rc.1``）也算预览版：语义化版本里预发布本来就不是稳定版，
    把它标成「正式版」会让用户按正式版的兼容承诺去用 rc。
    """
    raw = str(version or "").strip()
    m = SEMVER.match(raw)
    if not m:
        return UNKNOWN
    if "-" in raw:
        return PREVIEW
    return PREVIEW if int(m.group(1)) == 0 else STABLE


def label_of(version):
    return {PREVIEW: "预览版", STABLE: "正式版"}.get(channel_of(version), "未知版本")


def dist_tags(version):
    """该版本应发布的 dist-tag：latest 必有；预览版额外打 preview。"""
    channel = channel_of(version)
    if channel == UNKNOWN:
        return ()
    return ("latest", "preview") if channel == PREVIEW else ("latest",)


def _package_json_path():
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "package.json")


def current_version():
    try:
        with open(_package_json_path(), encoding="utf-8") as fh:
            return str(json.load(fh).get("version") or "")
    except (OSError, ValueError):
        return ""


def current():
    """给状态接口/启动日志用的完整口径。"""
    version = current_version()
    return {"version": version, "channel": channel_of(version),
            "label": label_of(version), "dist_tags": list(dist_tags(version))}
