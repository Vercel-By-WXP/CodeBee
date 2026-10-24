# -*- coding: utf-8 -*-
"""视觉兜底点击：文本/选择器定位链全灭时的最后救援。

OpenCUA「截图→模型给坐标→真实点击」思路的极简落地，借用现有编排里已接
好的视觉模型，不引入它的模型/数据集。触发面：flow.run_flow 里 click_real /
submit 步骤的重试全部耗尽、即将抛 FlowError 时——正常路径零调用零成本。

救援链：CDP 截图 → 视觉模型定位目标（图片像素坐标+置信度）→ 按
devicePixelRatio 换算回 CSS 视口坐标 → Input.dispatchMouseEvent 真实点击
（与 real_click_text 同一管道）。

纪律（与「假成功零容忍」口径对齐）：
- 模型侧任何失败（没配视觉模型/超时/答非所问/坐标出界/置信度低）一律
  放行原 FlowError，绝不把「没点上」包装成成功；点击是否真生效由流程
  后续 expect/verify 步骤把门，本模块不越权判成功；
- 只在步骤断定失败后触发，不会在正常重试中间插队抢点；
- 点击后取证：落点元素是谁（elementFromPoint）记进步骤日志，复盘时与
  发布截图对账。

设置（settings.json，设置页暂无 UI）：
- vision_fallback_enabled：一键停（默认开；无可用视觉模型时本模块自动
  等于关闭，不产生任何请求）；
- vision_fallback_model："provider_id/model_name" 钉死用哪个视觉模型；
  留空 = 从启用供应商的启用模型里按名字启发式挑，最多 3 个候选轮询。
"""
from __future__ import annotations

import base64
import json
import re

# 置信度地板：低于它宁可失败也不乱点。视觉模型普遍偏自信，0.55 只挡
# 明显含糊的答案，不做激进过滤（真挡错的代价是兜底永不生效）。
_CONF_MIN = 0.55
_MAX_CANDS = 3
# 视觉能力名字启发（按「最常见的多模态命名」圈定；宁可漏选走原失败
# 路径，不可误选纯文本模型硬吃图）。
_VISION_HINTS = ("4.5v", "4.6v", "glm-4v", "-vl", "vl-", "vision",
                 "gemini", "gpt-4o", "gpt-5", "claude", "qvq", "omni")

_PROMPT_TMPL = (
    "这是一张网页截图。请在图中找到可点击的按钮或元素，其可见文字与以下"
    "任一目标最接近：%s。只考虑当前可见、未被遮挡的元素。"
    "若找到，严格只输出一行 JSON，不要任何多余文字或代码块标记："
    '{"found": true, "text": "元素上可见的文字", "x": 123, "y": 456, '
    '"confidence": 0.9}。'
    "其中 x/y 是该元素中心点在截图中的像素坐标，原点为截图左上角，x 向右"
    " y 向下，confidence 取 0 到 1。找不到匹配元素时输出 "
    '{"found": false, "confidence": 0.2}。')


def _looks_vision(name):
    n = str(name or "").lower()
    return any(h in n for h in _VISION_HINTS)


def _candidates():
    """返回 [(provider_id, model_name)]；开关关/一个都没有时返回 []。"""
    from .. import settings
    try:
        s = settings.load()
    except Exception:
        return []
    if not s.get("vision_fallback_enabled", True):
        return []
    pin = str(s.get("vision_fallback_model") or "").strip()
    if pin:
        pid, _, model = pin.partition("/")
        pid, model = pid.strip(), model.strip()
        return [(pid, model)] if pid and model else []
    from .. import modelhub
    try:
        provs = modelhub.providers()
    except Exception:
        return []
    out = []
    for p in provs:
        if not p.get("enabled", True):
            continue
        # 无 KEY 的供应商 generate_once 只会白拿一个「无可用 API KEY」，
        # 提前跳过省一轮轮询
        if not (p.get("keys") or p.get("api_key")):
            continue
        for m in p.get("models") or []:
            if (m.get("enabled", True) and not m.get("hidden")
                    and _looks_vision(m.get("name"))):
                out.append((p.get("id") or "", m.get("name") or ""))
                if len(out) >= _MAX_CANDS:
                    return out
    return out


def _parse_reply(text):
    """从模型回复里抠定位 JSON；解析/校验不过关一律返回 None（放行原错误）。

    只认扁平 JSON（schema 无嵌套），容忍前后废话与 markdown 代码块。"""
    if not text:
        return None
    m = re.search(r'\{[^{}]*"found"[^{}]*\}', str(text), re.S)
    if not m:
        return None
    try:
        j = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(j, dict) or not j.get("found"):
        return None
    try:
        x, y = float(j["x"]), float(j["y"])
        conf = float(j.get("confidence") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    if x < 0 or y < 0 or conf < _CONF_MIN:
        return None
    return {"x": x, "y": y, "conf": conf,
            "text": str(j.get("text") or "")[:40]}


def rescue_click(page, targets, note=None):
    """兜底点击。targets：目标文字列表（主文案+等价文案，用于提示词）。

    成功（已派发点击）返回 {ok, via, text, x, y, conf, provider, model}；
    任何环节不行都返回 None，调用方继续抛原错误。外层兜底 try 是契约的
    一部分：救援代码自身的任何意外也不许顶掉流程的原错误。"""
    try:
        return _rescue_impl(page, targets, note)
    except Exception:
        return None


def _rescue_impl(page, targets, note):
    cands = _candidates()
    if not cands:
        return None
    try:
        png = page.screenshot_bytes()
        dpr = float(page.evaluate("window.devicePixelRatio") or 1.0)
        vw = int(page.evaluate("window.innerWidth") or 0)
        vh = int(page.evaluate("window.innerHeight") or 0)
    except Exception:
        return None
    if not png or dpr <= 0:
        return None
    desc = "／".join(str(t) for t in targets if str(t).strip())[:80]
    if not desc:
        return None
    img_b64 = base64.b64encode(png).decode("ascii")
    from .. import modelhub
    last_err = ""
    for pid, model in cands:
        # timeout 60s：慢速视觉通道（实测有 40s+ 档）45s 会白瞎一次救援；
        # 最坏 3 候选×60s 也远小于发布步预算。max_tokens 1024 给思考型
        # 模型留 headroom，512 会被思考烧穿回空文误判失败。
        r = modelhub.generate_once(pid, model, _PROMPT_TMPL % desc,
                                   max_tokens=1024, timeout_s=60,
                                   image_b64=img_b64)
        if not r.get("ok"):
            last_err = "%s/%s：%s" % (pid, model, str(r.get("error") or "")[:60])
            continue
        loc = _parse_reply(r.get("text"))
        if not loc:
            last_err = "%s/%s：回复里没有可用坐标" % (pid, model)
            continue
        # 截图像素 → CSS 视口坐标（Input.dispatchMouseEvent 吃的是 CSS 坐标；
        # Windows 显示缩放 125%/150% 时 DPR≠1，不换算会系统性打偏）
        css_x = int(round(loc["x"] / dpr))
        css_y = int(round(loc["y"] / dpr))
        if vw and vh:
            css_x = max(6, min(vw - 6, css_x))
            css_y = max(6, min(vh - 6, css_y))
        try:
            page.send("Input.dispatchMouseEvent",
                      {"type": "mousePressed", "x": css_x, "y": css_y,
                       "button": "left", "clickCount": 1}, timeout=8.0)
            page.send("Input.dispatchMouseEvent",
                      {"type": "mouseReleased", "x": css_x, "y": css_y,
                       "button": "left", "clickCount": 1}, timeout=8.0)
        except Exception:
            return None
        # 命中现场取证（只记录不把门）：落点元素是谁，供复盘对账
        hit = ""
        try:
            hit = str(page.call(
                "(x,y)=>{const e=document.elementFromPoint(x,y);"
                "return e?e.tagName+':'+((e.innerText||'').trim().slice(0,16)):'';}",
                css_x, css_y, timeout=6.0) or "")
        except Exception:
            pass
        if note:
            try:
                note("视觉兜底命中「%s」→(%d,%d) 置信度%.2f 模型%s/%s 落点%s"
                     % (loc["text"] or desc, css_x, css_y, loc["conf"],
                        pid, model, hit[:40]))
            except Exception:
                pass
        return {"ok": True, "via": "vision", "text": loc["text"],
                "x": css_x, "y": css_y, "conf": loc["conf"],
                "provider": pid, "model": model}
    if note and last_err:
        try:
            note("视觉兜底未命中（%s）" % last_err)
        except Exception:
            pass
    return None
