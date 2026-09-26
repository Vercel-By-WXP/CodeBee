# -*- coding: utf-8 -*-
"""模型评测基准台（promptfoo / deepeval 借鉴）：固定样题集 × 候选模型 × 固定
rubric → 实测能力榜。与 evaluation.py（供应商连通性，24h TTL）互补：这里评
的是「任务做得好不好」。

- 样题内置定版（写作 / 修 bug / 摘要，BUILTIN_SAMPLES）：同一道题所有模型
  都吃一模一样的输入，分数才可比；
- 修 bug 题带客观验证：候选返回的代码在子进程里跑断言（runner.run_process），
  主观评审之外给一道客观闸——谎报修好了会被直接戳穿；
- 评审 = 同一 rubric 的 LLM 裁判（编排者供应商）；裁判与候选同供应商时如实
  标「同族评审」，绝不伪装成独立评审；
- 每条真实调用入用量台账（source=bench）：花费硬顶/五维聚合照常记账；
  cost_usd 记 0——价格表（CCSwitch 导入）单位口径未核实，宁可少记不错记；
- 结果落 data/eval_bench.json：每 (供应商, 模型, 样题) 留最新一版 + 运行
  历史尾巴，逐条落盘（跑到一半崩了也有有效数据）。
"""
from __future__ import annotations

import json
import re
import threading
import time

# L2：可 import L0（paths/usage）与 L1（modelhub）；verify 子进程走 runner（L2 同层）
from . import paths

_LOCK = threading.RLock()
_RUN = None          # 运行中态：{"total","done","current","cancel","started_ts"}；None = 空闲

# ---------------------------------------------------------------- 内置样题集

_BUGGY_CODE = '''def sliding_window_max(nums, k):
    out = []
    for i in range(len(nums) - k + 1):
        out.append(max(nums[i:i + k]))
    return out'''

_WRITING_SPEC = ("题材：都市职场。写一章小说开篇，300 字左右：主角林晚是广告公司客户执行，"
                 "今天甲方把需求改到第八版，她还必须笑着说「没问题」。要求有画面感、有人物、"
                 "结尾留钩子。只输出正文，不要标题、不要解释。")

_MEETING_TEXT = (
    "周三产品会，人齐。老张先说上周数据：新增掉了一成半，原因排查到渠道 H 的假量，"
    "先暂停投放，财务那边的结算单先压着。然后是客服侧：工单积压四百多，主要是新版导出"
    "功能崩，用户骂得凶，建议这周内出补丁并群发安抚券。设计部提了三套新首页，定不下，"
    "要老板拍板，老板没来。运营说双十一的活动页下周必须启动，缺文案两个人手。最后散会前"
    "补充：下周一重开复盘会，必须带数据来。")

BUILTIN_SAMPLES = [
    {"id": "writing", "name": "章节写作",
     "requirement": "按给定题材与人设写 300 字左右小说开篇，有画面感、有人物、结尾留钩子",
     "dims": ["情节", "人物", "文笔", "吸引力"],
     "prompt": _WRITING_SPEC},
    {"id": "bugfix", "name": "小 bug 修复",
     "requirement": "修复滑动窗口最大值函数的边界缺陷，使全部断言通过，并附一句修复说明",
     "dims": ["正确性", "代码质量", "解释清晰"],
     "prompt": (
         "下面这个 Python 函数有边界缺陷：\n\n```python\n" + _BUGGY_CODE + "\n```\n\n"
         "预期行为：返回长度 k 的滑动窗口最大值列表（长度 len(nums)-k+1）；"
         "k <= 0 或 k > len(nums) 时返回 []，绝不能崩溃或输出错值。\n"
         "请只输出修复后的完整函数代码（```python 代码块），代码块后用一句话说明改了什么。"),
     "verify": True},
    {"id": "summary", "name": "结构化摘要",
     "requirement": "把一段流水账会议记录提炼成 3 条要点 + 待办清单，不丢关键数字与责任归属",
     "dims": ["准确性", "结构", "简洁"],
     "prompt": ("把下面的会议记录提炼成「3 条要点」和「待办清单」两节，保留关键数字与责任归属，"
                "每节各不超过 5 行：\n\n" + _MEETING_TEXT)},
]
_SAMPLE_IDS = {s["id"] for s in BUILTIN_SAMPLES}

_MAX_CANDIDATES = 8          # 一次评测的候选上限：控成本（每候选 = 样题数 × 2 次调用）
_MAX_CAND_TEXT = 4000        # 喂给裁判的作答截断


def samples():
    """给 UI 的样题清单（不带完整 prompt，页面展示用）。"""
    return [{"id": s["id"], "name": s["name"], "requirement": s["requirement"],
             "dims": s["dims"], "verify": bool(s.get("verify"))}
            for s in BUILTIN_SAMPLES]


# ---------------------------------------------------------------- 存储

def _path():
    """兼容旧引用（单测/探针断言用）；读写实际下沉 benchstore（L0 存储层，
    dispatch 同层合法取实测分软信号）。"""
    from . import benchstore
    return benchstore._path()


def _read():
    from . import benchstore
    return benchstore.read_doc()


def _write(data):
    from . import benchstore
    benchstore.write_doc(data)


def _persist_result(row):
    with _LOCK:
        data = _read()
        results = data.get("results") if isinstance(data.get("results"), list) else []
        results = [r for r in results
                   if not (isinstance(r, dict) and r.get("sample_id") == row["sample_id"]
                           and r.get("provider_id") == row["provider_id"]
                           and r.get("model") == row["model"])]
        results.append(row)
        data["results"] = results[-400:]     # 封顶：8 候选 × 3 样题 × 多轮历史也够用
        _write(data)


# ---------------------------------------------------------------- 评审

_JUDGE_TMPL = (
    "你是严格的作品评审员。请对下面这份「{name}」的作答，按维度各打 0-10 分"
    "（可带一位小数）：{dims}。\n\n题目要求：\n{requirement}\n\n"
    "候选作答（可能被截断）：\n{answer}\n\n{verify_note}\n"
    "只输出一个 JSON 对象，不要输出任何其他文字：\n"
    '{{"dims": {{"维度名": 分数}}, "overall": 0-10 的综合分, '
    '"comment": "一句话评语"}}')


def _judge_prompt(sample, answer, verify_ok, verify_detail):
    if sample.get("verify"):
        verify_note = ("客观验证结果：%s%s" % (
            "通过（代码在本地断言全部跑通）" if verify_ok
            else "未通过——代码没有跑对，正确性维度必须给低分",
            ("（%s）" % verify_detail) if verify_detail and not verify_ok else ""))
    else:
        verify_note = "客观验证：本题无客观验证。"
    return _JUDGE_TMPL.format(name=sample["name"], dims="、".join(sample["dims"]),
                              requirement=sample["requirement"],
                              answer=str(answer or "")[:_MAX_CAND_TEXT],
                              verify_note=verify_note)


def _parse_judge_json(text):
    """从裁判输出里抠出分数 JSON。兼容代码围栏与前后废话；解析失败返回 None。"""
    if not isinstance(text, str) or "{" not in text or "}" not in text:
        return None
    try:
        blob = text[text.index("{"): text.rindex("}") + 1]
        data = json.loads(blob)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    dims = {}
    for k, v in (data.get("dims") or {}).items():
        try:
            dims[str(k)] = max(0.0, min(10.0, float(v)))
        except (TypeError, ValueError):
            continue
    if not dims:
        return None
    overall = None
    try:
        overall = float(data.get("overall"))
    except (TypeError, ValueError):
        overall = None
    if overall is None:
        overall = sum(dims.values()) / len(dims)
    return {"dims": dims,
            "overall": max(0.0, min(10.0, overall)),
            "comment": str(data.get("comment") or "")[:200]}


# ---------------------------------------------------------------- 客观验证（修 bug 题）

def _extract_code(text):
    """取第一个 ```python 代码块；没有围栏就取包含目标函数定义的裸代码段。"""
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", str(text or ""), re.S)
    if m:
        return m.group(1)
    return str(text or "")


def _verify_bugfix(answer):
    """修 bug 题客观验证：候选代码 + 断言写成临时脚本真跑一遍。

    返回 (ok, detail)。断言覆盖正常路径与三个边界（k=1 / k>len / k<=0），
    「会算正常值但没修边界」的答案过不了。任何异常（写盘/起进程）都算未通过
    并带原因——验证层故障不能伪装成通过。
    """
    import tempfile, os   # 局部导入：仅在 verify 时需要
    code = _extract_code(answer)
    if "def sliding_window_max" not in code:
        return False, "未找到 sliding_window_max 函数定义"
    harness = code + "\n\n" + "\n".join([
        "assert sliding_window_max([1, 3, 2, 5], 2) == [3, 3, 5]",
        "assert sliding_window_max([4, 4, 4], 1) == [4, 4, 4]",
        "assert sliding_window_max([1, 2, 3], 5) == []",
        "assert sliding_window_max([1, 2], 0) == []",
        "assert sliding_window_max([1, 2], -1) == []",
        'print("PASS")',
    ]) + "\n"
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix="cb-bench-", suffix=".py")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(harness)
        from . import runner
        r = runner.run_process(argv=["python", "-X", "utf8", tmp], timeout=25)
        out = str(r.get("stdout") or "")
        if r.get("ok") and "PASS" in out:
            return True, ""
        tail = (out.strip().splitlines() or [""])[-1][:120]
        return False, tail or ("exit=%s" % r.get("exit_code"))
    except Exception as e:
        return False, "验证层异常：%s" % e
    finally:
        try:
            if tmp and os.path.isfile(tmp):
                os.unlink(tmp)
        except OSError:
            pass


# ---------------------------------------------------------------- 运行链

def _record_usage(run_id, role, sample_id, prov_id, model, ok, dur_ms, usage_d, error=""):
    """评测调用入台账（source=bench）；cost_usd 记 0（价格表单位未核实）。"""
    try:
        from . import usage
        usage.record(source="bench", run_id=run_id, task_id="", task_type="bench",
                     role=role, step=0, agent=str(prov_id)[:40],
                     agent_label=str(prov_id)[:60], tool="evalbench",
                     model=str(model)[:80], provider=str(prov_id)[:60],
                     ok=bool(ok), duration_s=round((dur_ms or 0) / 1000.0, 1),
                     cost_usd=0.0, usage=usage_d or {})
    except Exception:
        pass


def _run_bench(run_id, candidates, sample_ids, judge_cfg):
    """同步跑完一轮评测（start 的线程体；单测直接调它）。

    逐条产出、逐条落盘：候选失败 / 裁判失败 / 解析失败都如实成行，
    绝不让一次坏调用毁掉整轮。
    """
    global _RUN
    sample_by_id = {s["id"]: s for s in BUILTIN_SAMPLES}
    for cand in candidates:
        prov_id, model = cand.get("provider_id") or "", cand.get("model") or ""
        if not prov_id or not model:
            continue
        for sid in sample_ids:
            if _RUN and _RUN.get("cancel"):
                break
            sample = sample_by_id[sid]
            with _LOCK:
                if _RUN:
                    _RUN["current"] = "%s × %s" % (model, sample["name"])
            gen = _gen(prov_id, model, sample["prompt"], 2048)
            _record_usage(run_id, sid, sid, prov_id, model, gen.get("ok"),
                          gen.get("latency_ms"), gen.get("usage"), gen.get("error") or "")
            if not gen.get("ok"):
                _persist_result({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                 "sample_id": sid, "provider_id": prov_id, "model": model,
                                 "ok": False, "scored": False, "verify_ok": None,
                                 "error": str(gen.get("error") or "生成失败")[:200],
                                 "overall": None, "scores": {}, "comment": "",
                                 "cost_usd": 0.0, "duration_s": round(gen.get("latency_ms", 0) / 1000.0, 1),
                                 "same_family": False})
                with _LOCK:
                    if _RUN:
                        _RUN["done"] += 1
                continue
            answer = gen.get("text") or ""
            verify_ok, verify_detail = (None, "")
            if sample.get("verify"):
                verify_ok, verify_detail = _verify_bugfix(answer)
            jp = _gen(judge_cfg["provider_id"], judge_cfg["model"],
                      _judge_prompt(sample, answer, verify_ok, verify_detail), 800)
            _record_usage(run_id, "judge", sid, judge_cfg["provider_id"],
                          judge_cfg["model"], jp.get("ok"), jp.get("latency_ms"),
                          jp.get("usage"), jp.get("error") or "")
            parsed = _parse_judge_json(jp.get("text") or "") if jp.get("ok") else None
            row = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "sample_id": sid, "provider_id": prov_id, "model": model,
                   "ok": True, "scored": parsed is not None,
                   "verify_ok": verify_ok,
                   "error": "" if jp.get("ok") else str(jp.get("error") or "评审调用失败")[:200],
                   "overall": parsed["overall"] if parsed else None,
                   "scores": parsed["dims"] if parsed else {},
                   "comment": parsed["comment"] if parsed else "",
                   "cost_usd": 0.0,
                   "duration_s": round((gen.get("latency_ms", 0) + jp.get("latency_ms", 0)) / 1000.0, 1),
                   "same_family": prov_id == judge_cfg.get("provider_id")}
            _persist_result(row)
            with _LOCK:
                if _RUN:
                    _RUN["done"] += 1
    with _LOCK:
        if _RUN and _RUN.get("cancel"):
            _append_run_log(run_id, judge_cfg, cancelled=True)
        else:
            _append_run_log(run_id, judge_cfg)
        _RUN = None


def _gen(prov_id, model, prompt, max_tokens):
    """thin wrapper：便于单测统一 mock 一处。"""
    from . import modelhub
    try:
        return modelhub.generate_once(prov_id, model, prompt, max_tokens=max_tokens)
    except Exception as e:                      # 防御：generate_once 承诺不抛，这里兜底
        return {"ok": False, "text": "", "usage": None, "error": str(e)}


def _append_run_log(run_id, judge_cfg, cancelled=False):
    with _LOCK:
        data = _read()
        runs = data.get("runs") if isinstance(data.get("runs"), list) else []
        runs.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "run_id": run_id,
                     "judge": "%s · %s" % (judge_cfg.get("provider_id") or "",
                                           judge_cfg.get("model") or ""),
                     "cancelled": bool(cancelled)})
        data["runs"] = runs[-20:]
        _write(data)


def start(candidates, sample_ids=None):
    """发起一轮评测。返回 (运行描述, 错误)。裁判 = 编排者供应商（必须就绪）。"""
    global _RUN
    cands = []
    for c in (candidates or [])[:_MAX_CANDIDATES]:
        if isinstance(c, dict) and c.get("provider_id") and c.get("model"):
            cands.append({"provider_id": str(c["provider_id"]).strip(),
                          "model": str(c["model"]).strip()})
    if not cands:
        return None, "请先勾选至少一个候选模型"
    ids = [s for s in (sample_ids or list(_SAMPLE_IDS)) if s in _SAMPLE_IDS] or \
          [s["id"] for s in BUILTIN_SAMPLES]
    from . import modelhub
    orch = modelhub.orchestrator_view()
    if not orch.get("ready"):
        return None, "评审需要编排者供应商：请先在「编排设置」配置并启用"
    judge_cfg = {"provider_id": orch.get("provider_id") or "",
                 "model": orch.get("model") or orch.get("provider_model") or ""}
    with _LOCK:
        if _RUN:
            return None, "已有一轮评测在跑（%d/%d）" % (_RUN.get("done", 0), _RUN.get("total", 0))
        _RUN = {"total": len(cands) * len(ids), "done": 0, "current": "",
                "cancel": False, "started_ts": time.time()}
    run_id = "bench-" + time.strftime("%Y%m%d-%H%M%S")
    total = len(cands) * len(ids)
    threading.Thread(target=_run_bench, daemon=True, name=run_id,
                     args=(run_id, cands, ids, judge_cfg)).start()
    return {"run_id": run_id, "total": total}, None


def cancel():
    """请求停止在跑的一轮（当前这一步做完即停，已产出的结果保留）。"""
    with _LOCK:
        if _RUN:
            _RUN["cancel"] = True
            return True
    return False


def state():
    """给 UI 的全量视图：运行态 + 裁判 + 榜单 + 样题清单。"""
    from . import modelhub
    orch = modelhub.orchestrator_view()
    with _LOCK:
        run = dict(_RUN) if _RUN else None
    prov_names = {}
    try:
        prov_names = {p.get("id"): p.get("name") for p in modelhub.providers()}
    except Exception:
        pass
    data = _read()
    results = [r for r in (data.get("results") or []) if isinstance(r, dict)]
    board = _leaderboard(results, prov_names)
    return {"running": bool(run),
            "progress": run,
            "judge": {"provider_id": orch.get("provider_id") or "",
                      "model": orch.get("model") or "",
                      "ready": bool(orch.get("ready"))},
            "samples": samples(),
            "leaderboard": board,
            "last_runs": (data.get("runs") or [])[-5:]}


def _leaderboard(results, prov_names):
    """按 (供应商, 模型) 聚合：综合分均值、分维均值、客观验证通过数。"""
    by_model = {}
    for r in results:
        key = (r.get("provider_id") or "", r.get("model") or "")
        by_model.setdefault(key, []).append(r)
    rows = []
    for (prov_id, model), items in by_model.items():
        scored = [r for r in items if r.get("scored") and r.get("overall") is not None]
        failed = [r for r in items if not r.get("ok")]
        dims, dim_n = {}, {}
        for r in scored:
            for d, v in (r.get("scores") or {}).items():
                dims[d] = dims.get(d, 0.0) + float(v)
                dim_n[d] = dim_n.get(d, 0) + 1
        verified = [r for r in items if r.get("verify_ok") is not None]
        rows.append({
            "provider_id": prov_id, "model": model,
            "provider_name": prov_names.get(prov_id) or prov_id,
            "overall": round(sum(r["overall"] for r in scored) / len(scored), 2) if scored else None,
            "samples_n": len(items), "scored_n": len(scored),
            "failed_n": len(failed),
            "scores": {d: round(v / dim_n[d], 1) for d, v in dims.items() if dim_n[d]},
            "verify_pass": sum(1 for r in verified if r.get("verify_ok")),
            "verify_total": len(verified),
            "same_family": any(r.get("same_family") for r in scored),
            "last_ts": max((r.get("ts") or "") for r in items),
        })
    rows.sort(key=lambda x: (x["overall"] is None, -(x["overall"] or 0)))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows
