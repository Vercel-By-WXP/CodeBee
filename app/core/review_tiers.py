# -*- coding: utf-8 -*-
"""评审强度分档：哪个任务值得几双眼睛。

**先说清楚这张表不是「全面加强」，而是「把眼睛挪到会赔钱的地方」。**

动手前的实测基线（`task_compile.content_workflow`，auto 模式、默认难度）：

| 类型 | 席 | 轮 | 备注 |
|---|---|---|---|
| code / direct / defect_retro | 1 | 1 | 有验证命令时连模型评审都不开 |
| email / weekly_report / translation | 1 | 1 | 轻量 |
| article / video_script / doc / speech / presentation / resume / handdraw | **1** | 2 | 非 deep 类型不给大纲，也就顺手只给 1 席 |
| novel / research / tech_proposal / short_drama / rank_scan | 2 | 2~3 | deep 类型 |
| **short_story** | **1** | 2 | **不在 deep 类型里——最容易被平台拒稿的品类只有一双眼睛** |
| **bid_doc** | **1** | 2 | 同上；废标是硬损失 |

所以「21 个流程跑同一套强度」这个判断是错的，实际早就分了。真正的问题是另外两条：

1. **`agents.seats: 3` 是个死字段**——小说家/短篇手/连载家的定义里写着「三席」，
   而 `content_workflow` 的上限硬编码 `min(2, ...)`、`pick_critics` 的
   `AUTO_CRITIC_LIMIT = 2`。界面上承诺了三席，跑起来永远两席（短篇还只有一席）。
2. **分档依据不在数据里**——想知道「为什么这篇是两席那篇是一席」只能去读 8248 行的
   pipeline。没有可查的表，就没法审计，也没法按业务调。

**分档依据不是「重要程度」，是两条能落地的问题**：

1. **谁来判断它不合格？** 有确定性验证的（代码跑测试、产物能解析），验证命令本身
   就是判官，模型评审只在没有验证时兜底；没有验证的，才轮得到模型。
2. **判错了谁买单？** 平台会拒、客户会退回的成稿（小说/短篇/连载/标书），多一双
   眼睛的钱从返工里省得回来；内部周报判宽一点没人受伤。

**五档**：

| 档 | 席 | 轮 | 大纲 | 谁在这个档 |
|---|---|---|---|---|
| `verify` | 1 | 1 | 否 | 有确定性验证，模型评审只在缺验证时兜底 |
| `single` | 1 | 1 | 否 | 轻量内容：改起来便宜、错了不致命 |
| `lean` | 1 | 2 | 否 | 中等内容：单席但给修订回炉（现状 article 等的口径） |
| `dual` | 2 | 2 | 是 | 需要交叉判断（`quality_gate.MIN_REVIEWERS` 的底线档） |
| `panel` | 3 | 2 | 是 | 平台会拒的成稿：第三席是「读者视角」，见 `seat_brief()` |

**第三席为什么不是「再来一个人」**：三份同提示词、同维度的输出，信息增量远低于
50%，但成本是实打实的 +50%。所以第三席挂的是不同的活——它照常给全维度分（聚合
口径不变，零风险），但额外必须回答一个前两席结构上看不见的问题：
**「读者会在哪一段放下这本书」**。平台拒稿的真实理由多半是这一句，而按 rubric
挑毛病的人永远挑不出来。

**连载的特殊处理**：逐章放行时成本乘章数，三席逐章会是 +50%；而「整本读下来像不像
能签约」是终局判断。所以 `serial_novel` 逐章保持 2 席，终局（全局）评审 3 席，
见 `global_seats()`。

**可覆盖**：`data/agents/<flow>.json` 写 `"seats": 2` 可给某个流程降/升档；
`data/flows.json` 写 `review_tier` 可直接指定档位。配置优先于内置表。
"""
from __future__ import annotations

import threading

_LOCK = threading.RLock()

# ---- 档位定义（id -> 参数）
TIERS = {
    "verify": {"seats": 1, "rounds": 1, "outline": False,
               "label": "单席·验证兜底"},
    "single": {"seats": 1, "rounds": 1, "outline": False,
               "label": "单席·速评"},
    "lean": {"seats": 1, "rounds": 2, "outline": False,
             "label": "单席·可回炉"},
    "dual": {"seats": 2, "rounds": 2, "outline": True,
             "label": "双席·交叉"},
    "panel": {"seats": 3, "rounds": 2, "outline": True,
              "label": "三席·含读者席"},
}

# ---- 流程 -> (档, 为什么)。why 会原样进 /api/review-tiers 与报告，
# 写的是「凭什么」，不是「重要」。改档必须同时改这句，否则说不清。
_BUILTIN = {
    # 有确定性验证：判官是测试命令，不是模型
    "code": ("verify", "改完跑 `verify_command`；有验证就不需要模型再读一遍 diff"),
    "direct": ("verify", "一次交代一次产出，没有可比对的质量维度"),
    "defect_retro": ("verify", "复盘报告的证据是缺陷数据本身，不是文笔"),

    # 轻量内容：改起来便宜，判宽了没人受伤
    "email": ("single", "几百字、发错能重发；两席交叉买不到判断"),
    "weekly_report": ("single", "周报的读者是同事，不是审核方"),
    "translation": ("single", "忠实度可逐句核对，不需要模型投票"),

    # 中等内容：需要修订回炉，但单席够用（保持现状口径，不升档）
    "article": ("lean", "选题与结构作者自己最清楚；单席 + 回炉比双席首轮便宜"),
    "video_script": ("lean", "前三秒靠手感，第二双眼睛的一致性不值一次全稿调用"),
    "doc": ("lean", "内部文档，错了改一版就好"),
    "speech": ("lean", "感染力只有讲的人自己知道；评审主要用来抓空话"),
    "presentation": ("lean", "页面密度靠实际投影看，模型两席看法高度重合"),
    "resume": ("lean", "岗位匹配由招聘方判断，模型只做一致性核对"),
    "handdraw_video": ("lean", "分镜可画性由真实制作环节兜底"),

    # 双席/三席：判错了要返工，平台会拒
    "novel": ("panel", "平台按签约标准判，拒一次损失整本；读者席专找弃文点"),
    "short_story": ("panel",
                    "原来只有一席；短篇的生死在结尾兑现，按维度打分看不见「读者被骗」"),
    "serial_novel": ("dual", "逐章二席（成本乘章数），终局全局评审升三席，见 global_seats()"),
    "research": ("dual", "论据可靠性需要第二个人反查"),
    "tech_proposal": ("dual", "风险识别是「找没想到的」，一个人找不全"),
    "short_drama": ("dual", "集尾钩子决定追看，需第二人核对连续性"),
    "bid_doc": ("dual", "原来只有一席；废标是硬损失（保证金/资格），单席不该扛"),
    "rank_scan": ("dual", "选错方向要重写整本；门槛 8.0 已由轮次追加兜住"),
}

# 内置表里没登记的新流程怎么判：不知道有没有验证命令的一律按 lean（保守），
# 宁可多一轮也不要把没人看过的稿子放出去。
_FALLBACK = ("lean", "未登记评审档的流程按「单席可回炉」处理（保守：不降轮）")


def tier_of(task):
    """任务 -> 档位参数。

    优先级：`data/flows.json` 的 `review_tier` > `data/agents/<flow>.json` 的
    `seats`（显式席数，反向映射到最接近的档） > 内置表 > 保守兜底。

    轮数的取值规则见函数尾部：轻量档强制 1 轮，其余档尊重流程/用户写的轮数
    （档位管席数，不管「该打回几轮」——那是门槛问题，不是成本问题）。
    """
    task = task if isinstance(task, dict) else {}
    flow = str(task.get("type") or "doc").strip()
    tier_id, why = _BUILTIN.get(flow, _FALLBACK)
    source = "builtin"

    tier_id, why, source = _apply_overrides(flow, tier_id, why, source)

    spec = dict(TIERS.get(tier_id) or TIERS["lean"])
    spec["tier"] = tier_id if tier_id in TIERS else "lean"
    spec["tier_label"] = spec.get("label") or spec["tier"]
    spec["why"] = why
    spec["source"] = source
    spec["flow"] = flow

    # 轮数：轻量档强制 1 轮（省的是「再评一遍」的往返）；其余档尊重流程/用户
    # 写的轮数——`rank_scan` 写 3 轮是有原因的（高门槛，需要多轮打回），档位
    # 不该把它压成 2 轮。用户写更小的数（`rounds: 1`）照样生效。
    try:
        asked = int(task.get("rounds") or 0)
    except (TypeError, ValueError):
        asked = 0
    if spec["tier"] in ("verify", "single"):
        spec["rounds"] = 1
    elif asked > 0:
        spec["rounds"] = asked
    return spec


def global_seats(task):
    """终局（全局）评审要几席。

    连载逐章放行时席数乘章数，三席逐章是 +50%；而「整本读下来像不像能签约」
    只需要判断一次，第三席放这里最划算。非连载返回逐章席数（没有终局这一说）。
    """
    flow = str((task or {}).get("type") or "")
    spec = tier_of(task)
    if flow == "serial_novel":
        return max(int(TIERS["panel"]["seats"]), int(spec["seats"]))
    return int(spec["seats"])


def _apply_overrides(flow, tier_id, why, source):
    """读两处配置覆盖：flows.json 的 review_tier / agents 的 seats。"""
    tier = ""
    try:
        from . import flows as flows_mod
        row = flows_mod.get_flow(flow) or {}
        tier = str(row.get("review_tier") or "").strip()
    except Exception:
        pass
    if tier in TIERS:
        return tier, "用户在流程设置里指定「%s」" % TIERS[tier]["label"], "flow"
    try:
        seats = _agent_seats(flow)
    except Exception:
        seats = None
    if seats:
        # 席数反查档位：1 席映射到 single 而不是 verify——用户显式写 seats=1
        # 是在说「一个人够了」，不是在说「不用评审」。
        pick = "panel" if seats >= 3 else ("dual" if seats == 2 else "single")
        return pick, "专家配置指定 %d 席评审" % seats, "agent"
    return tier_id, why, source


def _agent_seats(flow):
    """只认用户在 `data/agents/<flow>.json` 里显式写的 seats。

    **内置表里的 `seats: 3` 刻意不参与分档**：那是专家卡片的叙事字段（「这个
    专家需要三席」），让 `serial_novel` 的叙事「三席」直接决定逐章席数，会把
    8 章 × 3 席 × 2 轮 = 48 次调用强加上去。档位的单一来源是这张表 + 用户配置，
    不是别人顺手写的展示字段。
    """
    from . import paths
    try:
        raw_path = paths.DATA_DIR / "agents" / ("%s.json" % flow)
        if not raw_path.is_file():
            return None
        import json as _json
        raw = _json.loads(raw_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    seats = raw.get("seats")
    if isinstance(seats, dict):        # 兼容旧写法 {"review": 3}
        seats = seats.get("review")
    try:
        return max(1, min(3, int(seats)))
    except (TypeError, ValueError):
        return None


def seat_brief(index):
    """第 index 席（0 起）的额外指令；前两席返回空串（= 现状行为）。

    只在三席档才会用到第 3 席。文字刻意压得很短：这段会挂进每一次评审调用，
    写得越啰嗦越容易被模型当成「又一段背景」忽略。
    """
    if int(index or 0) < 2:
        return ""
    return (
        "## 你这一席的额外任务（第三席·读者视角）\n"
        "前两席会按评分维度挑毛病，你除了照常打分，还必须回答一个他们不会回答的问题：\n"
        "**读者会在哪一段放下这本书？** 请给出具体位置（章节/段落原句开头）与"
        "放弃的原因（读不懂 / 没兴趣知道后面 / 觉得被骗 / 节奏拖）。\n"
        "如果通篇都抓得住人，就在 summary 里直说「未发现弃文点」，不要为了交差硬找。\n"
    )


def table():
    """全量分档表（给接口/文档/对照表用）。"""
    try:
        from . import flows as flows_mod
        flows = flows_mod.list_flows()
    except Exception:
        flows = []
    rows = []
    for flow in flows:
        fid = str(flow.get("id") or "").strip()
        if not fid:
            continue
        task = {"type": fid, "rounds": flow.get("rounds"),
                "threshold": flow.get("threshold")}
        spec = tier_of(task)
        rows.append({
            "flow": fid,
            "name": str(flow.get("name") or fid),
            "engine": str(flow.get("engine") or ""),
            "tier": spec["tier"],
            "tier_label": spec["label"],
            "seats": spec["seats"],
            "rounds": spec["rounds"],
            "outline": spec["outline"],
            "global_seats": global_seats(task),
            "threshold": flow.get("threshold"),
            "why": spec["why"],
            "source": spec["source"],
        })
    order = {"verify": 0, "single": 1, "lean": 2, "dual": 3, "panel": 4}
    rows.sort(key=lambda r: (order.get(r["tier"], 9), r["flow"]))
    return rows


def estimate(rows=None):
    """按「评审调用次数」估成本与收益（token 的稳定代理指标）。

    为什么用调用次数而不是 token：单席的输入是同一份稿件，输出长度由 rubric
    维度数决定，两者都随流程固定；席数 × 轮数就是真实成本，且可复算、不依赖
    模型计费口径。返回的行直接可以贴进对照表。
    """
    rows = rows if rows is not None else table()
    try:
        from . import flows as flows_mod
        flow_by_id = {str(f.get("id")): f for f in flows_mod.list_flows()}
    except Exception:
        flow_by_id = {}
    out = []
    for row in rows:
        fid = row["flow"]
        flow = flow_by_id.get(fid) or {}
        serial = flow.get("serial") or {}
        try:
            chapters = int(serial.get("chapters") or 0)
        except (TypeError, ValueError):
            chapters = 0
        if chapters > 0:
            # 连载：逐章 seats×rounds + 终局 global_seats
            worst = chapters * row["seats"] * row["rounds"] + row["global_seats"]
            best = chapters * row["seats"] + row["global_seats"]
        else:
            worst = row["seats"] * row["rounds"]
            best = row["seats"]
        out.append(dict(row, chapters=chapters, review_calls=worst,
                        review_calls_best=best))
    return out


def baseline(rows=None):
    """换算成「相对双席基准」的百分比，用于对照表。

    `vs_dual` 用最坏情形（轮次跑满）算，`vs_dual_best` 用「首轮即过线」算
    ——后者才是常态（`if publishable: break`）。两个都给，是因为只看最坏
    情形会把单席档的省钱效果看没了，只看最好又会低估最贵的 path。
    """
    rows = rows or estimate()
    base_seats, base_rounds = TIERS["dual"]["seats"], TIERS["dual"]["rounds"]
    scaled = []
    for row in rows:
        chapters = int(row.get("chapters") or 0)
        if chapters > 0:
            base_worst = chapters * base_seats * base_rounds + base_seats
            base_best = chapters * base_seats + base_seats
        else:
            base_worst = base_seats * base_rounds
            base_best = base_seats
        worst = round(100.0 * row["review_calls"] / base_worst, 1) if base_worst else 0.0
        best = (round(100.0 * row["review_calls_best"] / base_best, 1)
                if base_best else 0.0)
        scaled.append(dict(row, vs_dual=worst, vs_dual_best=best))
    return {"base": TIERS["dual"]["label"], "rows": scaled}


def describe_all():
    """一行一条的人读版本（CLI / 日志用）。"""
    return ["%-14s %-16s 逐章 %d 席 × %d 轮 / 终局 %d 席  %s"
            % (r["flow"], r["tier_label"], r["seats"], r["rounds"],
               r["global_seats"], r["why"])
            for r in table()]
