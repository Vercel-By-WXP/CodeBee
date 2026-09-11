# -*- coding: utf-8 -*-
"""内置演示/测试智能体（mock）：不消耗任何配额即可跑通全流程。"""
from __future__ import annotations

import hashlib


def _seed(agent_id):
    return int(hashlib.sha256(agent_id.encode("utf-8")).hexdigest()[:6], 16)


def draft_manuscript(task, round_no):
    """生成一份示例稿件（mock 起草/修订直接落盘的内容）。"""
    title = task.get("title") or "未命名章节"
    goal = task.get("goal") or ""
    return (
        "# {title}\n\n"
        "这是第 {r} 轮 mock 草稿（演示用，不消耗模型配额）。\n\n"
        "## 任务目标\n{goal}\n\n"
        "## 正文\n"
        "夜色沿着山脊线缓慢退去，主角终于看清了石门上的刻痕——那不是文字，"
        "而是一段被反复涂抹又反复显影的名字。他伸手触碰的瞬间，耳边响起的"
        "却是自己昨夜说过的话。{extra}\n\n"
        "（本轮修订关注：{focus}）\n"
    ).format(
        title=title, r=round_no, goal=goal,
        extra="这一段在第 2 轮修订中补充了人物动机的伏笔。" if round_no >= 2 else "",
        focus="节奏与悬念铺排" if round_no == 1 else "人物弧光与首尾呼应",
    )


def implement_note(task):
    return "（mock 实现）已在目标目录写入 mock-impl.txt 作为变更占位。"


def critique(agent_id, round_no, dims, threshold):
    """确定性评分：第 1 轮略低于阈值，第 2 轮达标，用于验证发布门禁逻辑。"""
    s = _seed(agent_id)
    scores = {}
    for i, d in enumerate(dims):
        jitter = ((s >> i) % 5) * 0.1
        scores[d] = round(min(9.5, 4.8 + 1.2 * round_no + jitter), 1)
    passed = all(v >= threshold for v in scores.values())
    issues = []
    if round_no == 1:
        issues = [
            {"dim": dims[0], "severity": "major", "note": "开篇信息密度偏低，钩子出现太晚（mock 意见）"},
            {"dim": dims[-1], "severity": "minor", "note": "段落长短交替不够，读感偏平（mock 意见）"},
        ]
    return {
        "scores": scores,
        "issues": issues,
        "summary": "mock 总评：第 %d 轮稿件%s发布阈值" % (round_no, "已达到" if passed else "尚未达到"),
    }


def review(task, verify_passed):
    return {
        "pass": bool(verify_passed),
        "scores": {"正确性": 8.0 if verify_passed else 5.0, "可维护性": 8.0, "安全": 8.5},
        "issues": [] if verify_passed else [
            {"severity": "major", "title": "验证命令未通过", "detail": "请检查测试输出（mock 意见）"}],
        "summary": "mock 代码评审：%s" % ("通过" if verify_passed else "未通过"),
    }
