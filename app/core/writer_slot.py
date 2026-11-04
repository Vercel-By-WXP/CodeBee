# -*- coding: utf-8 -*-
"""写手槽位（writer slot）：连载小说的「写手资产」——版本化提示词纪律 + 模型绑定
提示 + 迭代台账 + 挑战者影子对账（Raven Evolver 借鉴二期）。

三件事：

1. **写手版本**：写手的「纪律提示词 + 模型绑定提示」是可版本化资产
   （data/writer_slot.json）。当前启用版本的纪律块经 skills.block_for 注入
   起草提示词的 __SKILLS__ 槽（只对 novel/serial_novel 生效）；「迭代」=
   新增版本 + 启用切换 + 台账对比，历史版本永不删。
2. **迭代台账**：每次连载 run 收尾记一行（写手版本、章数、过线数、综合分、
   发布结论）。版本好不好看台账，不看出码速度。
3. **挑战者影子**（同题旁路）：启用后，连载每章按采样率把**同一份起草提示词**
   发给挑战者模型（modelhub.generate_once），后台线程跑——不阻塞、不改生产稿、
   失败不连累生产链。产出（可选裁判打分）进 data/writer_shadow.jsonl，
   报告和生产章稿分数对账：赢了才值得人工拍板换将。

一切读盘失败都折叠成空/默认：台账坏了只损失台账，影子炸了只损失影子。
"""
from __future__ import annotations

import copy
import json
import re
import threading
import time
from pathlib import Path

from . import paths

_FILE = paths.DATA_DIR / "writer_slot.json"
_SHADOW_JSONL = paths.DATA_DIR / "writer_shadow.jsonl"
_SHADOW_DIR = paths.DATA_DIR / "writer_shadow"

_LEDGER_CAP = 200          # 台账条数上限（新的在前，老的滚出）
_SHADOW_KEEP = 4000        # 影子 jsonl 行数上限（轮转保留后半）
_SHADOW_MAX_BYTES = 8 * 1024 * 1024
_NOVEL_TYPES = ("novel", "serial_novel")

_LOCK = threading.Lock()

# 种子 v1：从历史事故沉淀的通识纪律（体例混用 / 指纹 4 连用 / 跨章矛盾）。
# 题材专属硬要求走 quality/constraints-<genre>.json，不在这里重复。
SEED_V1_PROMPT = (
    "1. 体例唯一：全篇统一一种引号体例（以本书已发布章节为准，不得混用）；"
    "数字、省略号、破折号跟随已发布章节；每章一个文体，不混排。\n"
    "2. 收束轮换：章末收束句式逐章轮换（动作收尾／对白收尾／悬念收尾／景物收尾），"
    "同一句式不得连用两章；同一比喻全章至多出现一次。\n"
    "3. 禁模板腔：不写「落笔入册」「不知过了多久」一类万能句；不用排比凑字；"
    "每章开头三段内必须发生新事件或新信息，不做静态铺陈开章。\n"
    "4. 事实对账：人名、称谓、时间线、物件与前情提要逐一对齐，与已发布章节矛盾"
    "视为事故；引用前文时宁可含糊，不可写死错细节。\n"
    "5. 每章至少一处小获得或小反转；连续两章零进展即视为跑偏，主动并线。\n"
    "6. 本纪律与题材约束、平台事实块、改稿单冲突时，以后者为准。"
)


def _default():
    return {
        "active_version": "",            # 空 = 未启用（不注入，生产链行为不变）
        "versions": {
            "v1": {
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "notes": "种子版：历史事故（体例混用/收束指纹/跨章矛盾）沉淀的通识纪律",
                "prompt": SEED_V1_PROMPT,
                "binding_hint": {},
            },
        },
        "ledger": [],
        "shadow": {"enabled": False, "provider_id": "", "model": "",
                   "sample_rate": 0.0, "judge_provider_id": "", "judge_model": ""},
    }


def _load():
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = _default()
    if not isinstance(data, dict):
        data = _default()
    base = _default()
    for k, v in base.items():
        data.setdefault(k, copy.deepcopy(v))
    if not isinstance(data.get("versions"), dict) or not data["versions"]:
        data["versions"] = base["versions"]
    return data


def _save(data):
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_FILE)


def state():
    """API 视图：整份配置的深拷贝（无密钥，可直接下发）。"""
    with _LOCK:
        return copy.deepcopy(_load())


def active_version():
    with _LOCK:
        return str(_load().get("active_version") or "")


def next_version(data):
    nums = [int(m.group(1)) for m in
            (re.match(r"v(\d+)$", k) for k in data["versions"]) if m]
    return "v%d" % ((max(nums) + 1) if nums else 1)


def add_version(prompt, notes="", binding_hint=None):
    prompt = str(prompt or "").strip()
    if not prompt:
        return False, "prompt 不能为空"
    with _LOCK:
        data = _load()
        key = next_version(data)
        data["versions"][key] = {
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "notes": str(notes or ""),
            "prompt": prompt,
            "binding_hint": dict(binding_hint or {}),
        }
        _save(data)
    return True, key


def set_active(version):
    with _LOCK:
        data = _load()
        if version and version not in data["versions"]:
            return False, "版本 %s 不存在" % version
        data["active_version"] = str(version or "")
        _save(data)
    return True, ""


def set_binding_hint(version, binding_hint):
    with _LOCK:
        data = _load()
        ver = data["versions"].get(version)
        if ver is None:
            return False, "版本 %s 不存在" % version
        clean = {k: str(binding_hint.get(k) or "").strip()
                 for k in ("agent_id", "provider_id", "model")}
        ver["binding_hint"] = clean
        _save(data)
    return True, ""


def apply_binding(version=None):
    """把某版本的绑定提示应用到 data/models.json（写手模型换将的落锤动作）。

    只在有完整 agent_id 时执行；provider/model 缺省位不动那部分配置。
    """
    with _LOCK:
        data = _load()
    v = version or data.get("active_version") or ""
    ver = data["versions"].get(v)
    if not ver:
        return {"ok": False, "error": "版本 %s 不存在" % v}
    hint = ver.get("binding_hint") or {}
    if not hint.get("agent_id"):
        return {"ok": False, "error": "版本 %s 没有 binding_hint.agent_id，无处可应用" % v}
    from . import modelhub  # 惰性：modelhub 链路大，别在模块加载期拖进来
    b = modelhub.set_binding(hint["agent_id"],
                             provider_id=hint.get("provider_id") or None,
                             model=hint.get("model") or None)
    return {"ok": True, "version": v, "binding": {
        "agent_id": hint["agent_id"],
        "provider_id": (b or {}).get("provider_id") or "",
        "model": (b or {}).get("model") or "",
        "chain": (b or {}).get("chain") or []}}


# ------------------------------------------------------------------ 注入块

def block_for(task):
    """写手纪律块：只对小说任务注入；未启用版本 / 版本无正文 → 空串。"""
    ttype = (task or {}).get("type") or ""
    if ttype not in _NOVEL_TYPES:
        return ""
    with _LOCK:
        data = _load()
    v = str(data.get("active_version") or "")
    if not v:
        return ""
    prompt = str((data["versions"].get(v) or {}).get("prompt") or "").strip()
    if not prompt:
        return ""
    return ("### 【写手纪律 %s（本产品写手资产；与题材约束、平台事实、改稿单"
            "冲突时以后者为准）】\n%s" % (v, prompt))


# ------------------------------------------------------------------ 迭代台账

def record_run(run, task, verdict):
    """连载 run 收尾记一行台账。字段缺就留空，绝不抛错（调用方无守卫压力）。"""
    try:
        scores = (verdict or {}).get("chapter_scores") or []
        with _LOCK:
            data = _load()
            entry = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "run_id": (run or {}).get("id") or "",
                "task_id": (task or {}).get("id") or "",
                "title": (task or {}).get("title") or "",
                "version": str((run or {}).get("writer_version")
                               or data.get("active_version") or ""),
                "chapters": len(scores),
                "passed_chapters": sum(1 for c in scores if c.get("passed")),
                "overall": (verdict or {}).get("overall"),
                "publishable": (verdict or {}).get("publishable"),
                "quality_status": (verdict or {}).get("quality_status") or "",
                "review_degraded": (verdict or {}).get("review_degraded"),
                "total_words": (verdict or {}).get("total_words"),
            }
            data["ledger"].insert(0, entry)
            del data["ledger"][_LEDGER_CAP:]
            _save(data)
    except Exception:
        pass


# ------------------------------------------------------------------ 挑战者影子

def get_shadow():
    with _LOCK:
        return copy.deepcopy(_load().get("shadow") or {})


def set_shadow(**patch):
    allowed = ("enabled", "provider_id", "model", "sample_rate",
               "judge_provider_id", "judge_model")
    with _LOCK:
        data = _load()
        sh = data.setdefault("shadow", {})
        for k in allowed:
            if k in patch and patch[k] is not None:
                sh[k] = patch[k]
        try:
            sh["sample_rate"] = max(0.0, min(1.0, float(sh.get("sample_rate") or 0)))
        except (TypeError, ValueError):
            sh["sample_rate"] = 0.0
        sh["enabled"] = bool(sh.get("enabled"))
        _save(data)
        return copy.deepcopy(sh)


def maybe_shadow(run_id, task, chapter, prompt, workdir, champion_text=""):
    """起草收口处调用：按采样率决定要不要让挑战者同题试写。命中就开后台线程，
    返回 True；不命中/未启用返回 False。任何异常都吞掉——影子不能碰生产。"""
    try:
        cfg = get_shadow()
        if not cfg.get("enabled"):
            return False
        rate = float(cfg.get("sample_rate") or 0)
        if rate <= 0:
            return False
        import random
        if random.random() >= rate:
            return False
        wpc = 2400
        try:
            serial = (task or {}).get("serial") or {}
            wpc = int(serial.get("words_per_chapter") or 2400)
        except Exception:
            pass
        threading.Thread(
            target=_shadow_worker,
            args=(str(run_id or ""), str((task or {}).get("id") or ""),
                  int(chapter or 0), str(prompt or ""), str(workdir or ""),
                  str(champion_text or ""), wpc),
            daemon=True, name="writer-shadow").start()
        return True
    except Exception:
        return False


def _cjk(text):
    return len(re.findall(r"[\u4e00-\u9fff]", text or ""))


def _judge_parse(text):
    """裁判回复里抠 JSON：{"score": 8.2, "note": "..."}。抠不到返回 (None, "")。"""
    m = re.search(r"\{[^{}]*\}", text or "", re.S)
    if not m:
        return None, ""
    try:
        d = json.loads(m.group(0))
        return float(d.get("score")), str(d.get("note") or "")[:200]
    except Exception:
        return None, ""


def _shadow_worker(run_id, task_id, chapter, prompt, workdir, champion_text, wpc):
    cfg = get_shadow()
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "run_id": run_id, "task_id": task_id, "chapter": chapter,
        "version": active_version(),
        "provider_id": cfg.get("provider_id") or "",
        "model": cfg.get("model") or "",
    }

    def done(extra):
        entry.update(extra)
        try:
            _shadow_append(entry)
        except Exception:
            pass

    if not entry["provider_id"] or not entry["model"]:
        done({"ok": False, "error": "挑战者 provider/model 未配置"})
        return
    from . import modelhub  # 惰性
    max_tokens = max(4096, min(16384, int(wpc * 2.5)))
    res = modelhub.generate_once(entry["provider_id"], entry["model"], prompt,
                                 max_tokens=max_tokens, timeout_s=900)
    if not res.get("ok"):
        done({"ok": False, "error": str(res.get("error") or "")[:300],
              "latency_ms": res.get("latency_ms")})
        return
    text = str(res.get("text") or "").strip()
    words = _cjk(text)
    extra = {"ok": bool(text), "error": "" if text else "挑战者返回空正文",
             "words": words, "latency_ms": res.get("latency_ms")}
    if text:
        # 确定性信号：逐字重复（查重闸同源）；文件落盘供人工细读
        try:
            from . import novel_quality
            extra["dup"] = bool(novel_quality.duplicate_notes(text))
        except Exception:
            pass
        try:
            _SHADOW_DIR.mkdir(parents=True, exist_ok=True)
            fp = _SHADOW_DIR / ("%s-ch%03d-%s.md" % (
                run_id or "run", chapter or 0,
                time.strftime("%Y%m%d%H%M%S")))
            fp.write_text(text, encoding="utf-8")
            extra["file"] = fp.name
        except OSError:
            pass
        jp = cfg.get("judge_provider_id") or ""
        jm = cfg.get("judge_model") or ""
        if jp and jm:
            jres = modelhub.generate_once(
                jp, jm,
                "你是网文评审。按节奏/代入感/文笔/钩子四维给下面这章正文打分"
                "（各 0-10 分）。只输出 JSON：{\"score\": 四维均分, \"note\": "
                "\"一句话依据\"}。\n---\n%s" % text[:8000],
                max_tokens=256, timeout_s=120)
            if jres.get("ok"):
                score, note = _judge_parse(jres.get("text"))
                if score is not None:
                    extra["judge_score"] = round(score, 1)
                    extra["judge_note"] = note
                    extra["judge_model"] = jm
    done(extra)


def _shadow_append(entry):
    _SHADOW_JSONL.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    try:
        if (_SHADOW_JSONL.exists()
                and _SHADOW_JSONL.stat().st_size > _SHADOW_MAX_BYTES):
            lines = _SHADOW_JSONL.read_text(encoding="utf-8").splitlines(True)
            _SHADOW_JSONL.write_text("".join(lines[-_SHADOW_KEEP // 2:]),
                                     encoding="utf-8")
    except OSError:
        pass
    with _SHADOW_JSONL.open("a", encoding="utf-8") as f:
        f.write(line)


def read_shadow(limit=500):
    try:
        lines = _SHADOW_JSONL.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def shadow_report(days=30):
    """影子对账：挑战者裁判分 vs 生产章稿均分（同 run 同章）。分数不可比时
    只报确定性信号。读不到 run 的章分就标 unresolved。"""
    import time as _t
    cut = _t.time() - days * 86400
    rows = [r for r in read_shadow(limit=2000)
            if _ts_unix(r.get("ts")) >= cut]
    rep = {"total": len(rows), "ok": 0, "failed": 0, "judged": 0,
           "wins": 0, "losses": 0, "ties": 0, "unresolved": 0,
           "mean_judge": None, "dup_count": 0, "rows": []}
    js, champ = [], []
    for r in rows:
        if r.get("ok"):
            rep["ok"] += 1
        else:
            rep["failed"] += 1
        if r.get("dup"):
            rep["dup_count"] += 1
        score = r.get("judge_score")
        if isinstance(score, (int, float)):
            rep["judged"] += 1
            js.append(float(score))
        cm = _champion_mean(r.get("run_id"), r.get("chapter"))
        if cm is None or not isinstance(score, (int, float)):
            rep["unresolved"] += 1
        else:
            champ.append(cm)
            if score > cm + 0.2:
                rep["wins"] += 1
            elif score < cm - 0.2:
                rep["losses"] += 1
            else:
                rep["ties"] += 1
        rep["rows"].append({k: r.get(k) for k in (
            "ts", "run_id", "task_id", "chapter", "version", "model",
            "ok", "error", "words", "dup", "judge_score", "file")})
    if js:
        rep["mean_judge"] = round(sum(js) / len(js), 2)
    rep["mean_champion"] = (round(sum(champ) / len(champ), 2)
                            if champ else None)
    return rep


def _ts_unix(ts):
    try:
        return time.mktime(time.strptime(str(ts), "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0.0


def _champion_mean(run_id, chapter):
    """生产章稿的同章均分：从 run.json 的 chapter_scores 里取。"""
    if not run_id:
        return None
    try:
        from . import store  # 惰性：store 大，别在模块加载期拖进来
        run = store.get_run(str(run_id)) or {}
        for c in run.get("chapter_scores") or []:
            if int(c.get("chapter") or -1) == int(chapter or -2):
                means = c.get("means") or {}
                vals = [float(v) for v in means.values() if isinstance(v, (int, float))]
                return round(sum(vals) / len(vals), 2) if vals else None
    except Exception:
        pass
    return None
