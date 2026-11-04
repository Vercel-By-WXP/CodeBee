# -*- coding: utf-8 -*-
"""章节文本与章节文件：**一章在哪儿、怎么切**，全仓只在这一处回答。

为什么单独一层（2026-11-04 P2-E）
--------------------------------
过签判据要读「前几章」，可「前几章在哪个文件里」原先有三套答案：
`publish/kit.py` 认任意位数、`bookmeta.py` 认 1/2/3 位、`quality/appeal.py`
只认 `chapter-%02d.md` 且**必须从第 1 章连续**。三套答案里最苛刻的那套决定
了体检能不能开口。

生产库里的那本 200 章完本正好踩在中间：根目录是 `chapter-21.md … chapter-200.md`，
前二十章早被合并进 `已成稿/不周山守村人-前二十章.md`（标题写作「第一章 忙完这阵」，
**没有 `#` 前缀**）。旧读法从第 1 章数、缺一章就整本放弃，于是体检对一本写完的
书说「目录里还没有 chapter-01.md 这类章节稿」——这不是判不了，是找错了地方。

三种真实摆法，这一层都要认：

1. `chapter-NN.md` 平铺在 workdir（连载流水线的写法，任意位数、任意起始章号）；
2. `chapter-001.md` / `chapter-1.md` 并存（手写稿与旧流水线，同章号只取一份）；
3. **合稿**：若干章被并进一个 md（`manuscript.md` / `已成稿/前二十章.md`），
   后面继续按章落文件。合稿只用来**补缺**，绝不覆盖已有章文件。

两条边界：
① 合稿必须有结构证据（≥2 个「第 N 章」小标题、从第 1 章起、正文总量过下限），
   否则大纲/章纲/设定这类文件会被当成正文判出「没给读者东西」——假阳性比漏判贵；
② 只补前 `limit` 章，且不读 `作废稿/`、`大纲/`、`设定/` 这些目录：写进存档里的
   旧稿不是这本书现在的样子。
"""

from __future__ import annotations

import re
from pathlib import Path

# 章文件名：任何位数都算（`chapter-1.md` / `chapter-01.md` / `chapter-001.md`）。
CHAPTER_NAME = re.compile(r"^chapter-(\d{1,4})\.(?:md|txt)$", re.I)

# 带 `#` 的小标题（编辑器里粘出来的样子）：`## 第3章 夜行`
HEADING_HASHED = re.compile(
    r"^#{1,6}\s*第([0-9零一二三四五六七八九十百千]{1,6})章[\s:：、.]*(.*)$")
# 不带 `#` 的小标题（作者手写与合稿的样子）：`第三章 回家以后`
# 要求「第」在行首、章号后紧跟分隔符或行尾，整行不超过 BARE_MAX 字——正文里
# 「第三章程序改了」这种句子因此不会被当成标题。
HEADING_BARE = re.compile(
    r"^第([0-9零一二三四五六七八九十百千]{1,6})章(?:[\s:：、.]+(\S.*))?$")
BARE_MAX = 24

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}

# 合稿的准入线：正文总量不到这个数，多半是章纲/大纲，不是稿子。
MERGED_MIN_CHARS = 1200
# 文件名里带这些词的不当合稿（`已成稿/前二十章阅读目录.md` 这类索引尤甚）。
SKIP_NAME_WORDS = ("目录", "说明", "大纲", "章纲", "设定", "协议", "圣经",
                   "进度", "清单", "笔记", "readme")
# 目录名里带这些词的一律不读：存档里的旧稿不是这本书现在的样子。
SKIP_DIR_WORDS = ("作废", "废弃", "大纲", "章纲", "设定", "参考", "备份",
                  "存档", "archive", "old")


def cn_number(text):
    """把「一」「十二」「二十一」解析成整数；解析不了返回 None。"""
    text = str(text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total, current = 0, 0
    for char in text:
        if char in _CN_DIGITS:
            current = _CN_DIGITS[char]
        elif char == "十":
            total += (current or 1) * 10
            current = 0
        elif char == "百":
            total += (current or 1) * 100
            current = 0
        elif char == "千":
            total += (current or 1) * 1000
            current = 0
        else:
            return None
    return total + current


def split_chapters(text, allow_bare=False):
    """按「第 N 章」小标题切章；没有标题就整体当一章。

    ``allow_bare`` 打开后才认**不带 `#`** 的小标题（合稿的写法）。默认关着：
    评审输入按带 `#` 的 markdown 标题切，放开会让正文里以「第 N 章」开头的
    句子变成新章。
    """
    lines = str(text or "").splitlines()
    chapters, current = [], None
    for raw in lines:
        line = raw.strip()
        match = HEADING_HASHED.match(line)
        if match is None and allow_bare and 0 < len(line) <= BARE_MAX:
            match = HEADING_BARE.match(line)
        if match:
            if current is not None:
                chapters.append(current)
            number = cn_number(match.group(1))
            current = {"chapter": number if number is not None else len(chapters) + 1,
                       "title": (match.group(2) or "").strip(), "lines": []}
            continue
        if current is not None:
            current["lines"].append(line)
    if current is not None:
        chapters.append(current)
    if not chapters:
        return [{"chapter": 1, "title": "", "text": str(text or "")}]
    out = []
    for index, item in enumerate(chapters, 1):
        number = item["chapter"] if item["chapter"] else index
        out.append({"chapter": number, "title": item["title"],
                    "text": "\n".join(item["lines"]).strip()})
    return out


def _read(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _chapter_files(root):
    """{章号: 路径}；同章号有多份时取 `chapter-01.md` 这种标准写法。"""
    found, rank = {}, {}
    try:
        entries = list(root.iterdir())
    except OSError:
        return {}
    for path in entries:
        match = CHAPTER_NAME.match(path.name)
        if not match or not path.is_file():
            continue
        number = int(match.group(1))
        if number < 1:
            continue
        # 标准两位写法优先，其次文件名短的，最后按名字定序——保证同一份磁盘
        # 内容每次挑到同一个文件（否则判据会随目录顺序漂）。
        key = (0 if path.name.lower() == "chapter-%02d.md" % number else 1,
               len(path.name), path.name.lower())
        if number not in found or key < rank[number]:
            found[number] = path
            rank[number] = key
    return found


def _merged_candidates(root):
    """合稿候选：`manuscript.md` + 根目录与一级子目录里的 md/txt（跳过索引与存档）。"""
    out, seen = [], set()

    def add(path):
        if not path.is_file():
            return
        try:
            key = path.resolve()
        except OSError:
            return
        if key in seen:
            return
        seen.add(key)
        out.append(path)

    for name in ("manuscript.md", "manuscript.txt"):
        add(root / name)
    dirs = [root]
    try:
        children = sorted(root.iterdir())
    except OSError:
        children = []
    for child in children:
        if not child.is_dir():
            continue
        if child.name.startswith((".", "__")):
            continue
        if any(word in child.name.lower() for word in SKIP_DIR_WORDS):
            continue
        dirs.append(child)
    for folder in dirs:
        try:
            files = sorted(folder.iterdir())
        except OSError:
            continue
        for path in files:
            if path.suffix.lower() not in (".md", ".txt"):
                continue
            # 章文件已经按章号读过了，别再把它们当合稿切一遍。
            if CHAPTER_NAME.match(path.name):
                continue
            if any(word in path.name.lower() for word in SKIP_NAME_WORDS):
                continue
            add(path)
    return out


def _rel(path, root):
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def discover(workdir, limit=6):
    """读前 ``limit`` 章。返回 ``(rows, info)``。

    ``rows``：`{"chapter", "text", "source", "from"}`，按章号升序，只含真读到
    正文的章；``from`` 是 ``"file"``（章节文件）或 ``"merged"``（合稿补缺）。
    ``info``：`file_start/file_end/file_count`（只看章节文件）、`found`、
    `missing`（前 limit 章里缺哪些号）、`sources`、`merged`（用了哪几份合稿）、
    `gaps`（章文件本身的号码断档）。
    """
    raw = str(workdir or "").strip()
    info = {"workdir": raw, "file_start": 0, "file_end": 0, "file_count": 0,
            "found": [], "missing": [], "sources": [], "merged": [], "gaps": []}
    # 空路径必须当成「没有工作目录」：`Path("")` 是当前目录（`.`），拿它去扫
    # 会把仓库自己的 md 当成稿子——2026-11-04 就靠这条护栏挡住了「没设 workdir
    # 的任务也被套上打猎文约束」的误判。
    if not raw or raw in (".", "./"):
        return [], info
    root = Path(raw)
    if not root.is_dir():
        return [], info
    limit = max(1, int(limit or 1))
    numbered = _chapter_files(root)
    if numbered:
        info["file_start"] = min(numbered)
        info["file_end"] = max(numbered)
        info["file_count"] = len(numbered)
        info["gaps"] = [n for n in range(info["file_start"], info["file_end"])
                        if n not in numbered][:20]
    picked = {}
    for number in sorted(numbered):
        if number > limit:
            break
        body = _read(numbered[number]).strip()
        if body:
            picked[number] = {"chapter": number, "text": body,
                              "source": numbered[number].name, "from": "file"}
    if len(picked) < limit:
        for path in _merged_candidates(root):
            if all(n in picked for n in range(1, limit + 1)):
                break
            text = _read(path)
            if len(text.strip()) < MERGED_MIN_CHARS:
                continue
            items = split_chapters(text, allow_bare=True)
            if len(items) < 2:
                continue
            numbers = [item["chapter"] for item in items]
            # 只认**从第 1 章起**的合稿：这才是「这本书的前几章」；从中间某章
            # 起头的文件，多半是某卷的草稿，拿它补前缀会判错对象。
            if min(numbers) != 1:
                continue
            used = []
            for item in items:
                number = item["chapter"]
                if number in picked or number > limit:
                    continue
                body = str(item.get("text") or "").strip()
                if not body:
                    continue
                picked[number] = {"chapter": number, "text": body,
                                  "source": _rel(path, root), "from": "merged"}
                used.append(number)
            if used:
                info["merged"].append({"file": _rel(path, root),
                                       "chapters": sorted(used)})
    rows = [picked[n] for n in sorted(picked)]
    info["found"] = [row["chapter"] for row in rows]
    if rows:
        info["missing"] = [n for n in range(1, max(info["found"]) + 1)
                           if n not in picked]
    seen = []
    for row in rows:
        if row["source"] not in seen:
            seen.append(row["source"])
    info["sources"] = seen
    return rows, info
