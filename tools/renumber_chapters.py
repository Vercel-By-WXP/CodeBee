# -*- coding: utf-8 -*-
"""章节标题重排：让 chapter-NN.md 的标题编号与文件序号一致（9/14-15 续写时
模型编号漂移：chapter-09 内是第10章、11/12 重号第12章、20/21 重号第21章）。
只改标题行里的编号，正文一字不动；改前落 .numbak 备份。
用法：python tools/renumber_chapters.py <workdir> [--apply]（缺省 dry-run）
"""
import re
import shutil
import sys
from pathlib import Path

CN = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
      "六": 6, "七": 7, "八": 8, "九": 9}

NAME_RE = re.compile(r"^chapter-\d{2}\.md$")
HEAD = re.compile(
    r"^\ufeff?\s*(#{1,6}\s*)?第\s*([0-9一二三四五六七八九十百零两]+)\s*章\s*(.*)$")


def cn2int(s):
    """中文数字→int（章级规模：一到九百九十九足够）。"""
    if s.isdigit():
        return int(s)
    total, seg = 0, 0
    for ch in s:
        if ch in CN:
            seg = CN[ch]
        elif ch == "十":
            total += (seg or 1) * 10
            seg = 0
        elif ch == "百":
            total += (seg or 1) * 100
            seg = 0
        else:
            return None
    return total + seg


def safe_chapter_path(wd, name):
    """章节文件路径守卫：文件名白名单 + resolve 后必须仍在工作目录内。"""
    if not NAME_RE.match(name):
        raise ValueError("非法文件名: %r" % name)
    p = (wd / name).resolve()
    if wd.resolve() not in p.parents:
        raise ValueError("路径越界: %s" % p)
    return p


def scan(p):
    """返回 (行号, 原行, 编号, #前缀, 标题)；找不到返回 None。只看前 3 个非空行。"""
    txt = p.read_bytes().decode("utf-8", errors="replace").lstrip("\ufeff")
    seen = 0
    for ln, line in enumerate(txt.splitlines()):
        if not line.strip():
            continue
        seen += 1
        if seen > 3:
            break
        m = HEAD.match(line.strip("\ufeff"))
        if m:
            num = cn2int(m.group(2))
            if num is not None:
                return ln, line, num, m.group(1) or "", m.group(3)
    return None


def main():
    wd = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    apply = "--apply" in sys.argv
    changed = 0
    for i in range(1, 200):
        name = "chapter-%02d.md" % i
        p = safe_chapter_path(wd, name)
        if not p.exists():
            break
        info = scan(p)
        if not info:
            print("%s: 未找到标题行，跳过" % name)
            continue
        ln, line, num, hash_, title = info
        if num == i:
            continue
        changed += 1
        print("%s: 「%s」→ 第%d章 %s" % (name, line.strip()[:40], i, title.strip()[:24]))
        if apply:
            shutil.copy2(p, Path(str(p) + ".numbak"))
            lines = p.read_bytes().decode("utf-8", errors="replace").splitlines(True)
            eol = "\r\n" if lines[ln].endswith("\r\n") else "\n"
            lines[ln] = "%s第%d章 %s%s" % (hash_, i, title.strip(), eol)
            p.write_text("".join(lines), encoding="utf-8")
    print("=== %d 个文件需要重排%s"
          % (changed, "，已写入（原文件备份为 .numbak）" if apply else "（dry-run，加 --apply 生效）"))


if __name__ == "__main__":
    main()
