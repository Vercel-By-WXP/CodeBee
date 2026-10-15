"""Reviewed rank-scan packages and isolated serial-book handoff."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

PACKAGE_FILE = "selection-package.json"
TEXT_FIELDS = (
    "title", "platform", "audience", "genre", "premise", "mechanism",
    "differentiation", "protagonist", "opening_event", "long_term_engine",
    "story_bible",
)
CONTRACT = """
扫榜报告用于立项，不是过签预测。先比较 2-3 个候选，再保留 1-3 个有具体事件的方案。
逐项区分：平台明确意见 / 可定位的正文问题 / 平台适配假设 / 未验证项。
书名、分类不能证明剧情机制饱和或签约偏好；无正文证据就标 E 与待核实，不伪造精读。
评估首个事件的独特机制、主角主动选择与现实代价、读者回报、前 3 章因果和长篇可写性。
打磨要改变事件、选择或后果；换职业/朝代/书名和修辞替换不算机制创新。
末尾必须且只能有一个 ```json 代码块，作为标准连载输入（不是示例占位符）：
{"schema_version":1,"candidates":[{"id":"c1","title":"实际书名（同时作为新文件夹名）",
"platform":"目标平台和频道","audience":"具体读者与阅读承诺","genre":"题材",
"premise":"一句话主线","mechanism":"可复述的核心机制、限制与代价",
"differentiation":"相对同类作品的事件/选择差异及证据，未知项明确标注",
"protagonist":"欲望、主动选择、人物关系、损失与成长",
"opening_event":"首个具体事件、选择、后果及读者回报",
"first_three_chapters":["第1章目标/阻碍/行动/结果","第2章因果推进与回报","第3章阶段结果与后续期待"],
"milestones":{"20000":"前2万字试写评估节点","30000":"前3万字首单元闭环节点"},
"long_term_engine":"后续三单元如何变化、升级、回收伏笔，避免重复机制",
"story_bible":"可直接注入连载的世界规则、人物关系、时间线、叙事视角与文风、伏笔台账",
"risks":["证据缺口/原创性风险/平台要求待核实项及验证办法"]}]}
2万/3万字是内部试写检查点，不能宣称为通用平台政策；有用户要求时按其要求规划。
所有字段填实质方案；不在本任务写正文，也不调用建任务工具自动开书。
"""
REVIEW = """
审核立项包而非小说成稿。仅有榜单书名不能推断机制饱和；核查来源与 A-E 分级。
按交付约束检查 JSON 与方案一致，指出具体事件、主角选择、后果、差异兑现及长篇重复风险。
缺乏证据就如实标待核实；不能把假设包装为编辑意见。内部评分不能映射过签概率。
存在未解决的设定/切入点/因果或交接缺陷必须记 major，不得用平均分掩盖。
修订后复核上轮问题是否改变了事件和后果，不能只换说法。
"""


def folder_title(value):
    """Reject unsafe names instead of silently changing the book title."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("书名不能为空")
    title = value.strip()
    if (len(title) > 80 or title in (".", "..") or title[-1] in " ."
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', title)
            or title.split(".")[0].upper() in
            {"CON", "PRN", "AUX", "NUL", *("COM%d" % n for n in range(1, 10)),
             *("LPT%d" % n for n in range(1, 10))}):
        raise ValueError("书名不能用作文件夹名：请去掉路径、特殊字符或保留设备名")
    return title


def validate_package(data):
    errors = []
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return ["立项包 schema_version 必须为 1"]
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 3:
        return ["立项包需要 1-3 个候选"]
    ids, titles = set(), set()
    for i, candidate in enumerate(candidates, 1):
        if not isinstance(candidate, dict):
            errors.append("候选%d必须为对象" % i)
            continue
        cid = candidate.get("id")
        if not isinstance(cid, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,32}", cid) or cid in ids:
            errors.append("候选%d需要唯一 id" % i)
        else:
            ids.add(cid)
        for field in TEXT_FIELDS:
            value = candidate.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > 16000:
                errors.append("候选%d缺少有效 %s" % (i, field))
        try:
            title = folder_title(candidate.get("title"))
            if title.casefold() in titles:
                errors.append("候选书名重复")
            titles.add(title.casefold())
        except ValueError as exc:
            errors.append(str(exc))
        chapters = candidate.get("first_three_chapters")
        if (not isinstance(chapters, list) or len(chapters) != 3
                or not all(isinstance(x, str) and x.strip() for x in chapters)):
            errors.append("候选%d缺少前三章因果规划" % i)
        milestones = candidate.get("milestones")
        if not isinstance(milestones, dict) or not all(
                isinstance(milestones.get(k), str) and milestones[k].strip()
                for k in ("20000", "30000")):
            errors.append("候选%d缺少2万/3万字检查点" % i)
        risks = candidate.get("risks")
        if not isinstance(risks, list) or not risks or not all(
                isinstance(x, str) and x.strip() for x in risks):
            errors.append("候选%d缺少风险与待核实项" % i)
    return errors


def parse_report(text):
    blocks = re.findall(r"```json\s*\n(.*?)\n```", text, re.S | re.I)
    if len(blocks) != 1:
        return None, ["报告末尾需要一个标准立项 JSON 代码块"]
    try:
        data = json.loads(blocks[0])
    except (ValueError, TypeError):
        return None, ["立项 JSON 无法解析"]
    return data, validate_package(data)


def finalize(run_id, task, text, passed, run_dir):
    """Freeze exactly the reviewed text; later launches never trust mutable workdir files."""
    data, errors = parse_report(text)
    if passed and not errors:
        data = dict(data, source={"task_id": task["id"], "run_id": run_id,
                                 "report_sha256": hashlib.sha256(text.encode()).hexdigest()})
        target = Path(run_dir) / PACKAGE_FILE
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        # The report itself stays beside its reviewed package, independent of later runs.
        (Path(run_dir) / "selection-report.md").write_text(text, encoding="utf-8")
    return {"selection_ready": bool(passed and not errors), "selection_errors": errors}

