# -*- coding: utf-8 -*-
"""把内置市场目录导出为公开生态格式（marketplace.json + packs/*.md）。

产物是一个可直接发布到 GitHub 仓库的目录：
  <out>/marketplace.json   与 ZCode / Anthropic 生态同一份格式契约（plugins 数组，
                           每条 name/description/author/category/source）；
  <out>/packs/<id>.md      每个包的完整内容，source.url 指向发布仓库后即可被
                           任何兼容工具按 zip/git-subdir 之外的第三种来源消费，
                           也可作为人工审阅的定稿稿。

用法：python tools/export_market.py [--out DIR]（默认 docs/market-catalog，
产物不进运行时路径，发布动作由人执行：推到公开仓库即可让外部目录收录）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core import market, skills  # noqa: E402

# category 中文 → 生态英文类目（仅展示用，CodeBee 端仍按闭集中文枚举过滤）
_CAT_EN = {
    "情节逻辑": "plotting", "人物塑造": "characters", "节奏爽点": "pacing",
    "文笔风格": "writing-style", "一致性": "consistency", "流程规范": "workflow",
}


def main():
    ap = argparse.ArgumentParser(description="导出内置市场目录为生态格式")
    ap.add_argument("--out", default=str(ROOT / "docs" / "market-catalog"))
    ap.add_argument("--repo", default="",
                    help="发布仓库地址（https://github.com/<you>/<repo>.git），"
                         "写入每条的 source.url；留空则留空待发布时补")
    args = ap.parse_args()

    out = Path(args.out)
    packs = out / "packs"
    packs.mkdir(parents=True, exist_ok=True)

    plugins = []
    for p in market.BUILTIN_PACKS:
        content = (p.get("files") or {}).get("market-%s.md" % p["id"], "")
        if not content.strip():
            continue
        (packs / ("%s.md" % p["id"])).write_text(content, encoding="utf-8")
        entry = {
            "name": p["id"],
            "displayName": p["name"],
            "description": p["desc"],
            "author": {"name": "CodeBee"},
            "category": _CAT_EN.get(p.get("category"), "workflow"),
            "keywords": ["codebee", "skillpack", p.get("category") or ""],
            "version": time.strftime("%Y.%m.%d"),
            # 自有来源类型：生态工具不认识会跳过，CodeBee 端未来收录时可识别
            "source": {"source": "codebee-skillpack", "file": "packs/%s.md" % p["id"],
                       "url": args.repo},
            "homepage": args.repo,
        }
        plugins.append(entry)

    manifest = {
        "name": "codebee-market",
        "description": "CodeBee 官方插件市场：内置经验包目录（纯技能类，文档型）。",
        "description_i18n": {
            "en": "Official CodeBee plugin market: built-in experience packs (pure skill, docs-only).",
            "zh-CN": "CodeBee 官方插件市场：内置经验包目录（纯技能类，文档型）。",
        },
        "owner": {"name": "CodeBee"},
        "plugins": plugins,
    }
    (out / "marketplace.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("导出 %d 个包 → %s" % (len(plugins), out))


if __name__ == "__main__":
    main()
