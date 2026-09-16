#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import io
from pathlib import Path

p = Path(r"E:/GoOut/MultiAgentOrchestration/app/ui/app.js")
src = p.read_text(encoding="utf-8")

# 用三重引号构造含引号的锚点，避免引号冲突
anchors = [
    ('''<span class="name">' + esc(p.name) + "</span>" +''', 'mk-card p.name'),
    ('join("、")', 'join 顿号'),
    ('"：" + res.note', '冒号+note'),
    ('esc(s.desc)', 'skin desc'),
    ('''skin-name">' + esc(s.name)''', 'skin name'),
    ('name: t("经典")', 'SKINS 经典'),
    ('name: t("深海")', 'SKINS 深海'),
    ('name: t("森野")', 'SKINS 森野'),
    ('name: t("暖阳")', 'SKINS 暖阳'),
    ('name: t("霓虹")', 'SKINS 霓虹'),
    ('name: t("高对比")', 'SKINS 高对比'),
    ('desc: t("黑白灰', 'SKINS desc 1'),
    ('desc: t("藏青底色', 'SKINS desc 2'),
    ('desc: t("墨绿底色', 'SKINS desc 3'),
    ('desc: t("暖棕底色', 'SKINS desc 4'),
    ('desc: t("暗紫底色', 'SKINS desc 5'),
    ('desc: t("纯黑 / 纯白', 'SKINS desc 6'),
    ('desc: t("经典浅色', 'CODE_THEMES desc 1'),
    ('desc: t("VS 家族浅色', 'CODE_THEMES desc 2'),
    ('desc: t("苹果开发工具', 'CODE_THEMES desc 3'),
    ('desc: t("米黄纸感', 'CODE_THEMES desc 4'),
    ('desc: t("经典深色', 'CODE_THEMES desc 5'),
    ('desc: t("VS 家族深色', 'CODE_THEMES desc 6'),
    ('desc: t("高饱和黄紫粉', 'CODE_THEMES desc 7'),
    ('desc: t("Atom 出品的', 'CODE_THEMES desc 8'),
    ('''return \'<div class="card mk-card"><div class="head">' +''', 'mkCardHtml head'),
    ('esc(p.name) + "</span>" +', 'mkCard p.name'),
    ('esc(p.desc || "")', 'mkCard desc'),
    ('esc(t(p.category || ""))', 'mkCard category'),
    ('esc(s.name)', 'skin name use'),
    ('esc(s.desc)', 'skin desc use'),
    ('tag.textContent = (s ? s.name : cur)', 'skin-cur'),
    ('s ? s.name : id', 'setSkin'),
]

for label, pat in anchors:
    cnt = src.count(pat)
    print(f"{cnt:3d}  {label}  {pat[:52]!r}")
