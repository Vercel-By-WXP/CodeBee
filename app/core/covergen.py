# -*- coding: utf-8 -*-
"""封面图生成：调供应商图像 API 产出竖版封面插画，curl 直接落盘到运行目录。

接口形状：OpenAI 兼容 POST {base}/images/generations（Z.ai cogview 系列、
多数聚合网关都支持，默认返回图片 URL）。端点选取：编排者供应商的 openai 面
（显式协议或 wire_caps 实测出的另一协议面）优先，其余开了 openai 面的供应商
按优先级自动跟上——编排者走 anthropic 面不再挡封面。已知主机会补规范图像面
（智谱 anthropic 面 key 同源，图像只走 /api/paas/v4；实测 face 对图像回
HTTP 200+失败信封，规范面放前面省一轮空试）。错误信息带响应体真因（聚合
网关「无可用渠道」不再吞成光秃 HTTPError）。
图像模型：环境变量
CODEBEE_IMAGE_MODEL 优先，其次供应商模型清单里认得出的图像模型（关键词），
否则逐个试默认候选（cogview-3-flash → cogview-4），第一个 2xx 的胜出；
同一模型先试竖版尺寸再回落方图。
状态机与建书生成同款（running/done/failed 写任务 cover_gen 字段，
bump_state 推 SSE）。

安全边界：
- 图像 URL 仅接受 https，解析后 IP 命中私网/环回/链路本地一律拒绝
  （防 SSRF 打内网与云元数据）；禁用重定向（curl -L 不加）。
- Python 不经手图像字节：下载与落盘由 curl 子进程 -o 一步完成。
- 产物只落 paths.RUNS_DIR 受管路径（与 report.md 同款），不写任务工作目录。
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

_IMAGE_CANDIDATES = ("cogview-3-flash", "cogview-4")
# 模型清单里认得出的图像模型关键词（命中即列为候选，保持清单序）
_IMAGE_MODEL_KEYWORDS = ("cogview", "dall-e", "gpt-image", "flux", "kolors",
                         "seedream", "stable-diffusion", "hidream", "imagen")
_SIZES = ("768x1344", "1024x1024")   # 竖版优先，方图兜底


def _cover_prompt(task):
    """从任务与建书资料拼图像提示词：场景氛围向，不要文字（平台会自行压字）。"""
    bm = ((task.get("book_meta") or {}).get("fanqie") or {}).get("data") or {}
    if not isinstance(bm, dict):
        bm = {}
    title = bm.get("书名") or task.get("title") or ""
    genre = bm.get("类型") or bm.get("分类") or ""
    brief = (bm.get("一句话简介") or bm.get("简介") or task.get("goal") or "")
    return ("竖版小说封面插画，画面中不要出现任何文字。题材：%s %s。故事梗概：%s。"
            "商业网文封面质感：主体人物或核心场景突出，色彩浓郁有冲击力，"
            "构图上方留白便于后期压标题。" % (genre, title, str(brief)[:300]))


def _pick_key(prov):
    from . import modelhub
    keys = modelhub._chain_keys(prov) or []
    if keys and keys[0].get("key"):
        return keys[0]["key"]
    return prov.get("api_key") or ""


# 已知主机的规范图像面：wire_caps 记的 openai 面只保 chat 可用，图像端点在
# 各家规范路径上（真案：智谱 anthropic 面 key 同源可出图，但 wire_caps 面的
# /api/anthropic 路径对图像返回 HTTP 200+失败信封，必须走 /api/paas/v4）。
_CANON_IMAGE_FACE = {
    "open.bigmodel.cn": "https://open.bigmodel.cn/api/paas/v4",
}


def _openai_face(prov):
    """该供应商可用的 openai 面地址：显式 openai 协议用本体；其余只认 wire_caps
    里实测通过的 openai 面——两协议面的路径不同（Z.ai：/api/anthropic vs
    /api/paas/v4），靠猜必错，与绑定链「只挑实测 wire」同纪律。"""
    proto = str(prov.get("protocol") or "openai")
    base = str(prov.get("base_url") or "").rstrip("/")
    if proto == "openai" and base:
        return base
    cap = (prov.get("wire_caps") or {}).get("openai") or {}
    return str(cap.get("base") or "").rstrip("/")


def _image_bases(prov):
    """该供应商可试的图像端点（去重保序）：已知主机的规范图像面在前——
    实测 face 若恰好是 chat 代理路径（智谱 anthropic 面），对图像必是假 200；
    规范面直接出图省一轮空试。无规范表的其余主机仍用实测 face。"""
    from urllib.parse import urlparse
    face = _openai_face(prov)
    host = urlparse(face or str(prov.get("base_url") or "")).hostname or ""
    canon = _CANON_IMAGE_FACE.get(host) or ""
    bases = [b for b in (canon, face) if b]
    seen, out = set(), []
    for b in bases:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def _image_models(prov):
    """图像模型候选序：环境变量 CODEBEE_IMAGE_MODEL > 供应商模型清单里关键词
    命中的图像模型（保持清单序）> 默认候选 cogview（Z.ai 免费档先行）。"""
    models = []
    env_model = os.environ.get("CODEBEE_IMAGE_MODEL", "").strip()
    if env_model:
        models.append(env_model)
    for m in (prov.get("models") or []):
        name = str((m.get("name") if isinstance(m, dict) else m) or "").strip()
        low = name.lower()
        if name and any(k in low for k in _IMAGE_MODEL_KEYWORDS) and name not in models:
            models.append(name)
    models.extend(m for m in _IMAGE_CANDIDATES if m not in models)
    return models


def _candidates():
    """图像端点候选 [{label, bases, key, allow_private, prov}]：编排者供应商排
    最前，其余启用且配了密钥的供应商按列表优先级跟上；每家可试端点见
    _image_bases（实测 face + 已知主机规范面）；同一（端点组+密钥）只收一次。"""
    from . import modelhub
    orch = modelhub.resolve_orchestrator()
    first = [orch[0]] if orch else []
    first_ids = {p.get("id") for p in first}
    rest = [p for p in modelhub.providers() if p.get("id") not in first_ids]
    out, seen = [], set()
    for prov in first + rest:
        if not prov.get("enabled", True) or not prov.get("api_key"):
            continue
        bases = _image_bases(prov)
        key = _pick_key(prov)
        if not bases or not key:
            continue
        sig = (tuple(bases), key)
        if sig in seen:
            continue
        seen.add(sig)
        out.append({"label": prov.get("name") or prov.get("id") or "供应商",
                    "bases": bases, "key": key,
                    "allow_private": bool(prov.get("allow_private")),
                    "prov": prov})
    return out


def _safe_image_url(url):
    """SSRF 校验：仅 https；解析主机全部 IP，私网/环回/链路本地/保留段拒绝。"""
    import ipaddress
    from urllib.parse import urlparse
    u = urlparse(url)
    if u.scheme != "https":
        raise ValueError("仅允许 https 图像地址")
    host = u.hostname or ""
    if not host:
        raise ValueError("图像地址缺少主机")
    infos = socket.getaddrinfo(host, u.port or 443, proto=socket.IPPROTO_TCP)
    if not infos:
        raise ValueError("图像主机无法解析")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise ValueError("图像主机解析到受限地址，已拒绝")
    return url


def _curl_to(url, out_path):
    """curl 子进程下载落盘（Python 不经手图像字节；不加 -L 禁重定向）。"""
    from . import runner
    r = runner.run_process(
        argv=["curl", "-sS", "--max-time", "180",
              "--proto", "=https", "--fail",
              "-o", str(out_path), url],
        timeout=200)
    if not r["ok"]:
        raise RuntimeError((r.get("stderr") or r.get("stdout") or "下载失败")[:200])
    p = Path(out_path)
    if not p.is_file() or p.stat().st_size < 1024:
        raise RuntimeError("下载内容过小或为空")


def _post_images(url, key, body, allow_private, timeout):
    """图像接口专用 POST：非 2xx 也读响应体真因。_post_json_http 对 HTTPError
    直接返回 (0, None, repr)，聚合网关的「无可用渠道」会被吞成光秃
    HTTPError 503；复用同一套 SSRF 校验与出站 TLS 上下文。"""
    from . import modelhub, tlsctx
    _host, herr = modelhub._validate_host(url, allow_private)
    if _host is None:
        return 0, None, herr
    try:
        req = urllib.request.Request(url, method="POST",
                                     headers={"Authorization": "Bearer " + key,
                                              "Content-Type": "application/json"},
                                     data=json.dumps(body).encode("utf-8"))
        with modelhub._opener().open(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read(1024 * 1024).decode("utf-8", "replace")), ""
    except urllib.error.HTTPError as e:
        raw = e.read(65536).decode("utf-8", "replace")
        try:
            data = json.loads(raw)
        except Exception:
            data = None
        msg = ""
        if isinstance(data, dict):
            msg = str((data.get("error") or {}).get("message")
                      or data.get("message") or data.get("msg") or "")
        if not msg:
            msg = "HTTP %s %s" % (e.code, getattr(e, "reason", "") or "")
        return e.code, data, msg[:300]
    except Exception as e:
        return 0, None, tlsctx.humanize(repr(e)[:300])


def _call_images(base, key, model, prompt, size, allow_private):
    """POST /images/generations。返回 (data_item dict, 错误串)；data_item 含 url 或 b64_json。"""
    url = base.rstrip("/") + "/images/generations"
    status, data, err = _post_images(url, key,
                                     {"model": model, "prompt": prompt, "size": size},
                                     allow_private, 180)
    if status == 0:
        return None, err or "网络错误"
    if 200 <= status < 300:
        if isinstance(data, dict) and data.get("success") is False:
            # 智谱 anthropic 面对未知子路径回 HTTP 200+失败信封，按失败处理
            return None, str(data.get("msg") or "供应商返回失败信封")
        items = (data or {}).get("data") or []
        if items and isinstance(items[0], dict):
            return items[0], ""
        return None, "响应缺少 data[0]"
    return None, err or ("HTTP %s" % status)


def make_cover(run_id, task):
    """同步生成封面到运行目录。成功/失败返回 entry dict（写任务 cover_gen 用）。"""
    from . import builtin_agent, paths
    try:
        cands = _candidates()
        if not cands:
            raise RuntimeError(
                "没有可用的 openai 协议图像接口（扫了全部供应商的 openai 面，"
                "含适配测试实测出的）。可到模型管理页对网关跑一次适配测试补出 "
                "openai 面，或设环境变量 CODEBEE_IMAGE_MODEL 指定图像模型后重试")
        prompt = _cover_prompt(task)
        last_err = ""
        for cand in cands:
            for base in cand["bases"]:
                for model in _image_models(cand["prov"]):
                    for size in _SIZES:
                        item, err = _call_images(base, cand["key"], model,
                                                 prompt, size, cand["allow_private"])
                        if item is None:
                            last_err = err
                            continue
                        img_url = str(item.get("url") or "")
                        if not img_url:
                            last_err = "该供应商未返回图片 URL（仅内嵌数据），暂不支持"
                            continue
                        try:
                            safe_url = _safe_image_url(img_url)
                        except ValueError as e:
                            last_err = str(e)
                            continue
                        out_dir = paths.RUNS_DIR / str(run_id)
                        out_dir.mkdir(parents=True, exist_ok=True)
                        out = out_dir / "cover.png"
                        try:
                            _curl_to(safe_url, out)
                        except RuntimeError as e:
                            last_err = str(e)
                            continue
                        return {"status": "done", "file": "cover.png", "run_id": str(run_id),
                                "provider": cand["label"], "model": model, "size": size,
                                "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        raise RuntimeError(last_err or "图像接口无可用模型")
    except Exception as e:
        return {"status": "failed", "error": str(e)[:300],
                "at": time.strftime("%Y-%m-%d %H:%M:%S")}


def generate_async(run_id, task_id, task):
    """后台线程入口：生成封面并把终态写回任务 cover_gen（仿建书 generate_async）。"""
    from . import store
    if not store.get_task(task_id):
        return
    entry = make_cover(run_id, task)
    cur = store.get_task(task_id)
    if not cur:
        return
    prev = cur.get("cover_gen") or {}
    if prev.get("status") != "running":   # 期间被删/重置：丢弃结果
        return
    store.set_cover_gen(task_id, entry)


def start(task_id, run_id=None):
    """起后台封面生成线程。run_id 缺省用该任务最近一次 run。返回 (ok, err)。running 幂等拒绝。"""
    from . import store
    task = store.get_task(task_id)
    if not task:
        return False, "任务不存在"
    cur = task.get("cover_gen") or {}
    if cur.get("status") == "running":
        return True, ""
    rid = run_id
    if not rid:
        runs = store.task_runs(task_id)
        rid = (runs[-1].get("id") if runs else "")
    if not rid:
        return False, "没有可归属的运行记录（先跑一次任务再生成封面）"
    if not store.set_cover_gen(task_id, {"status": "running",
                                         "at": time.strftime("%Y-%m-%d %H:%M:%S")}):
        return False, "任务不存在"
    task = dict(task, cover_gen={"status": "running"})
    threading.Thread(target=generate_async, daemon=True,
                     name="cover-gen-%s" % task_id, args=(rid, task_id, task)).start()
    return True, ""
