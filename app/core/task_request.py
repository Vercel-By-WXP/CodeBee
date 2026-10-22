"""Recognize explicit task creation commands from user text, without a model."""
from __future__ import annotations

import re

from . import flows
from .intent_contract import trusted_context


_LEAD = re.compile(
    r"^(?:(?:好的|好|那就|那么|那|接下来|现在|请你|请|能帮我|可以帮我|"
    r"帮我|给我|为我|替我|麻烦你|麻烦|帮忙|直接|单独|另外|再|我想要|我想|我要|我需要|"
    r"在\s*codebee\s*(?:里|中|内))[，,\s]*)*", re.I)
_ACTION = r"(?:另起|再建|再开|另开|新开|新建|创建|建立|发起|开设|开|建)"
_BEFORE_LOCATIONS = tuple(re.compile(
    r"^在\s*(?P<directory>.+)" + boundary + r"(?=" + _ACTION + r")")
    for boundary in (r"\s+(?:目录|工作区)?(?:下|里|中)\s*",
                     r"(?:目录|工作区)?(?:下|里|中)\s*", r"\s+"))
_DIRECTIVE = re.compile(
    r"^" + _ACTION + r"\s*(?:(?:一个|一项|个|项)\s*)?(?:新(?:的)?\s*)?"
    r"(?:codebee\s*(?:的\s*)?(?:新(?:的)?\s*)?)?"
    r"(?P<kind>[^，,。；;：:\r\n?？]{0,40}?)\s*任务(?P<rest>[\s\S]*)$", re.I)
_QUOTED = r'''(?:"[^"\r\n]+"|'[^'\r\n]+'|“[^”\r\n]+”|‘[^’\r\n]+’|`[^`\r\n]+`)'''
_ABSOLUTE = r"(?:[A-Za-z]:[\\/]|/|\\\\)"
_AFTER_LOCATIONS = tuple(re.compile(
    r"^(?:(?:在|到)\s*|(?:工作目录|工作区|目录)(?:设为|为|是)?\s*[:：]?\s*)"
    r"(?P<directory>" + _QUOTED + r"|" + _ABSOLUTE + r"[^，,。；;\r\n]+?)"
    + boundary + r"\s*(?P<rest>[\s\S]*)$")
    for boundary in (
        r"\s+(?:目录|工作区)?(?:下|里|中)",
        r"\s*(?:(?:目录|工作区)?(?:下|里|中)(?=\s|[，,；;\r\n]|开始|先|写|做|生成|制作|整理|修复|完成|$)"
        r"|(?=[，,；;\r\n]|$))(?![^，,。；;\r\n]*[\\/])"))
_TYPES = {"连载小说": "serial_novel", "连载写作": "serial_novel", "连载": "serial_novel",
          "小说": "novel", "短篇故事": "short_story", "短篇": "short_story",
          "短故事": "short_story", "短篇小说": "short_story",
          "代码开发": "code", "代码": "code", "开发": "code",
          "代码修复": "code", "文档": "doc", "说明书": "doc", "文章": "article", "调研": "research",
          "研究": "research", "PPT": "presentation", "翻译": "translation",
          "短剧": "short_drama", "短剧剧本": "short_drama", "微短剧": "short_drama"}
_GOALS = {"serial_novel": "连载写作", "novel": "小说写作", "short_story": "短篇写作",
          "code": "代码开发",
          "doc": "文档写作", "article": "文章写作", "short_drama": "短剧剧本创作",
          "direct": "独立执行用户目标"}
_WORK_LEAD = re.compile(r"^(?:(?:来|用来|用于|目标是|任务是|帮我|请|开始|先|做|完成)\s*)*")
_PRIMARY = (
    ("code", r"^(?:写代码|代码|开发|修复|重构|编程|实现.*?(?:接口|功能))"),
    ("research", r"^(?:调研|研究|分析)"),
    (None, r"^(?:写|撰写|创作|起草)[^，,。；;\r\n]*?(连载小说|连载|小说|文章|公众号|头条|文档|说明书)"),
)
_INFER = (
    ("short_drama", r"短剧|微短剧|短剧本|分集剧本|网剧本"),
    ("serial_novel", r"连载|逐章写|(?:前|先写|首批)\s*[0-9一二两三四五六七八九十百]+\s*章"),
    ("translation", r"翻译|译成"),
    ("presentation", r"PPT|演示文稿|幻灯片"),
    ("research", r"调研|竞品分析|市场研究"),
    ("article", r"写.*(?:文章|公众号|头条)"),
    ("novel", r"写.*小说"),
    ("doc", r"写.*(?:文档|说明书)"),
    ("code", r"修复|重构|开发|编程|写代码|实现.*(?:接口|功能)"),
)
_META = re.compile(r"^(?:的|类|是|怎么|如何|为什么|失败|报错|吗|么|呢|按钮|功能|是否|[?？])")
_NO_EXECUTION = re.compile(
    r"(?:只是|仅作|仅供)(?:一个)?(?:举例|示例|例子)|"
    r"(?:不要|别|暂不|先不|不必|无需|不用|禁止)\s*(?:立即|现在|马上)?\s*(?:执行|启动|起跑|运行)")
_CANCEL = re.compile(
    r"(?:不要|别|暂不|先不|不必|无需|不用|禁止)\s*(?:真的|真正|实际|再|立即|去)?\s*"
    r"(?:新建|创建|建立|发起|新开|开)(?:(?P<target>[^，,。；;\r\n]*?)任务|(?=\s*(?:[，,。；;！!]|$)))", re.I)
_CANCEL_TARGET = re.compile(
    r"^(?:(?:一个|一项|个|项|这个|这项|该|上述|本次|本|此|当前|任何|新的?|codebee(?:的)?)\s*)*", re.I)
_NUMBER = r"[0-9零〇一二两三四五六七八九十百千]+"
_CHAPTERS = re.compile(r"(?:前|(?:先|开始)?写\s*前?|首批\s*|共\s*)(" + _NUMBER + r")\s*章")
_WORDS = re.compile(r"每章\s*(?:目标|大约|约)?\s*(" + _NUMBER + r")\s*字")
_TOTAL_WORDS = re.compile(r"([0-9]+(?:\.[0-9]+)?|" + _NUMBER + r")\s*万(?:字|来字)|"
                          r"(?:共|全文|全篇|总|目标|大约|约)\s*(" + _NUMBER + r")\s*字")
_EPISODES = re.compile(r"(?:共|做|写|制作|生成)?\s*(" + _NUMBER + r")\s*集")
_EPISODE_SECONDS = re.compile(r"每集\s*(?:约|大约|目标)?\s*(" + _NUMBER + r")\s*(?:秒|s)(?!\w)", re.I)


def _number(text):
    if text.isascii() and text.isdigit():
        return int(text)
    digits = dict(zip("零〇一二两三四五六七八九", (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
    total = digit = 0
    for char in text:
        if char in "十百千":
            total += (digit or 1) * {"十": 10, "百": 100, "千": 1000}[char]
            digit = 0
        elif char in digits:
            digit = digits[char]
        else:
            return None
    return total + digit


def parse(text, *, workdir=""):
    """Return a task payload for an explicit product task creation command.

    Quoted examples, attachment previews, questions and ordinary writing
    requests keep their existing conversation route. Writing requirements such
    as target platforms reach drafting and review without the dispatch command.
    Directory existence and execution policy are validated at task creation.
    """
    if not isinstance(text, str):
        return None
    text = trusted_context(text).strip()
    if not text or len(text) > 16000:
        return None
    command = _LEAD.sub("", text, count=1)
    directory = None
    location = next((m for pattern in _BEFORE_LOCATIONS if (m := pattern.match(command))), None)
    if location:
        directory = location["directory"]
        command = command[location.end():]
    match = _DIRECTIVE.fullmatch(command)
    if not match:
        return None
    rest = match["rest"].lstrip(" \t\r\n，,。；;：:")
    kind = re.sub(r"新(?:的)?$", "", match["kind"].strip()).strip()
    if _META.match(rest) or _NO_EXECUTION.search(rest):
        return None
    for cancel in _CANCEL.finditer(rest):
        target = _CANCEL_TARGET.sub("", (cancel["target"] or "").strip()).strip()
        if target.lower() in ("", kind.lower()):
            return None
    location = next((m for pattern in _AFTER_LOCATIONS if (m := pattern.fullmatch(rest))), None)
    if location:
        # Conflicting workspace selections need the conversation route.
        if directory:
            return None
        directory = location["directory"]
        rest = location["rest"].lstrip(" \t\r\n，,。；;：:")
    if directory:
        directory = directory.strip().strip('"\'“”‘’`').strip()
        if directory in ("这", "这里", "当前", "这个", "该", "本", "当前目录", "当前工作区"):
            directory = None
        elif not re.match(r"^" + _ABSOLUTE, directory):
            return None
    aliases = {key.lower(): value for key, value in _TYPES.items()}
    registered = {}
    for flow in flows.list_flows():
        registered[flow["id"]] = flow
        for label in (flow["id"], flow["name"]):
            aliases.setdefault(label.lower(), flow["id"])
    task_type = aliases.get(kind.lower())
    if task_type is None:
        work = _WORK_LEAD.sub("", rest)
        for primary_type, pattern in _PRIMARY:
            primary = re.search(pattern, work, re.I)
            if primary:
                task_type = primary_type or _TYPES.get(primary[1], "article")
                break
    if task_type is None:
        task_type = next((ftype for ftype, pattern in _INFER
                          if re.search(pattern, kind + " " + rest, re.I)), "direct")
    # The child executes the work; handing it "create another task" as its
    # goal could recursively invoke the built-in task creation tool.
    goal = ("在%s下开展" % directory if directory else "") + _GOALS.get(
        task_type, registered.get(task_type, {}).get("name", task_type))
    if kind and kind.lower() not in aliases:
        goal += "（%s）" % kind
    if rest:
        goal += "：" + rest
    elif task_type == "direct":
        goal += "：请向用户询问这个新任务的具体目标。"
    payload = {"type": task_type, "goal": goal,
               "workdir": directory or workdir}
    if payload["type"] == "serial_novel":
        serial = dict(registered.get(task_type, {}).get("serial") or
                      {"chapters": 8, "words_per_chapter": 2500})
        for key, pattern in (("chapters", _CHAPTERS), ("words_per_chapter", _WORDS)):
            number = pattern.search(rest)
            if number:
                count = _number(number[1])
                if count:
                    serial[key] = count
        payload["serial"] = serial
    elif payload["type"] == "short_drama":
        drama = {}
        for key, pattern in (("episodes", _EPISODES),
                             ("seconds_per_episode", _EPISODE_SECONDS)):
            number = pattern.search(rest)
            if number:
                count = _number(number[1])
                if count:
                    drama[key] = count
        if drama:
            payload["drama"] = drama
    elif payload["type"] == "short_story":
        # 目标字数：目标文本「1.2 万字/一万二千字/共 9000 字」优先，缺失沿用流程默认
        story = dict(registered.get(task_type, {}).get("story") or {"words": 12000})
        number = _TOTAL_WORDS.search(rest)
        if number:
            raw = number[1] or number[2]
            if number[1] and "." in raw:
                story["words"] = int(float(raw) * 10000)
            else:
                count = _number(raw)
                if count:
                    story["words"] = count * 10000 if number[1] else count
        payload["story"] = story
    return payload
