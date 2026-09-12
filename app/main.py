# -*- coding: utf-8 -*-
"""Tutti（多智能体编排台）：纯标准库 HTTP 服务（零依赖，Python 3.8+）。

启动：python app/main.py [端口]，默认 8765，自动打开浏览器。
"""
from __future__ import annotations

import argparse
import json
import re
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from core import catalog, flows, jobs, manager, registry, remote, settings, store
from core import paths

MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
        ".ico": "image/x-icon", ".json": "application/manifest+json; charset=utf-8",
        ".webmanifest": "application/manifest+json; charset=utf-8"}

PORT = 8765  # main() 启动时更新；/api/connect 组装扫码地址用


class Handler(BaseHTTPRequestHandler):
    server_version = "Tutti/1.0"

    # ------------------------------------------------------------ 基础
    def log_message(self, fmt, *args):
        pass  # 安静模式；异常仍会记录到 run 目录

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    # ------------------------------------------------------------ 远程访问
    def _forwarded_ip(self):
        """经代理进来的真实客户端 IP：X-Forwarded-For 首跳，缺省 CF-Connecting-IP。"""
        fw = self.headers.get("X-Forwarded-For") or ""
        if not fw:
            fw = self.headers.get("CF-Connecting-IP") or ""
        return remote.effective_ip(self.client_address[0], fw), fw

    def _authed(self):
        q = parse_qs(urlparse(self.path).query)
        ip, fw = self._forwarded_ip()
        return remote.request_authed(ip, fw,
                                     (q.get("token") or [""])[0],
                                     self.headers.get("X-Tutti-Token") or "")

    def _client_id(self):
        cid = (self.headers.get("X-Tutti-Client") or "").strip()
        if not cid:  # EventSource 带不了自定义头，身份从 query 兜底
            cid = (parse_qs(urlparse(self.path).query).get("client") or [""])[0].strip()
        if cid:
            return cid[:64]
        # 无头请求（curl/旧脚本）：真本机统一算"本机"，远程各自匿名且无持久身份
        ip, fw = self._forwarded_ip()
        return "local" if (ip in ("127.0.0.1", "::1") and not fw) else ""

    def _client_name(self):
        name = (self.headers.get("X-Tutti-Name") or "").strip()
        if not name:
            name = (parse_qs(urlparse(self.path).query).get("name") or [""])[0].strip()
        if name:
            try:
                name = unquote(name)  # 前端对非 ASCII 设备名做了 encodeURIComponent
            except Exception:
                pass
            return name[:24]
        return "本机" if self._client_id() == "local" else "其他设备"

    def _deny_control(self):
        ok, view = remote.acquire(self._client_id(), self._client_name())
        if ok:
            if view.get("mine"):
                store.bump_state()  # 空闲自动接管：让其他端立即看到
            return None
        return self._json(423, {
            "error": "「%s」正在控制，请先在右上角接管控制权" % view.get("holder", "其他设备"),
            "control": view})

    # ------------------------------------------------------------ 路由
    def do_GET(self):
        path = urlparse(self.path).path
        m = None
        # 静态页不设防（无敏感信息）：远程裸地址打开时由前端令牌门引导输入
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/api/"):
            if not self._authed():
                return self._json(401, {"error": "需要访问令牌（启动 Tutti 时控制台会显示）"})
            if path == "/api/state":
                return self._json(200, _state_payload(self._client_id()))
            if path == "/api/events":
                return self._api_events()
            if path == "/api/control":
                return self._json(200, {"control": remote.control_view(self._client_id())})
            if path == "/api/connect":
                # 供设置页「手机连接」弹框生成二维码；远程打开需令牌，天然受保护
                return self._json(200, {"urls": remote.build_connect_urls(PORT),
                                        "port": PORT})
            if path == "/api/models":
                from core import modelhub
                return self._json(200, {"providers": modelhub.provider_view(),
                                        "bindings": modelhub.bindings(),
                                        "catalog": modelhub.models_view(),
                                        "source_names": modelhub.source_names()})
            if path == "/api/models/sources":
                from core import modelhub
                return self._json(200, {"sources": modelhub.sources()})
            if path == "/api/flows":
                return self._json(200, {"flows": flows.list_flows()})
            if path == "/api/settings":
                return self._json(200, dict(settings.load(), **jobs.workers_info()))
            if path == "/api/orchestrator":
                from core import modelhub
                return self._json(200, {"orchestrator": modelhub.orchestrator_view()})
            if path == "/api/catalog":
                return self._json(200, {"catalog": manager.catalog_view(),
                                        "checking": manager.updates_checking()})
            if path == "/api/sessions":
                from core import sessions
                return self._json(200, {"sessions": sessions.scan(
                    force="force=1" in urlparse(self.path).query)})
            if path == "/api/catalog/reload":
                catalog.load(force=True)
                manager.detect_all(force=True)
                return self._json(200, {"ok": True, "catalog": manager.catalog_view()})
            if path == "/api/runs":
                return self._json(200, {"runs": store.list_runs()})
            m = re.match(r"^/api/runs/([^/]+)$", path)
            if m:
                run = store.get_run(m.group(1))
                return self._json(200, {"run": run}) if run else self._json(404, {"error": "not found"})
            m = re.match(r"^/api/runs/([^/]+)/report$", path)
            if m:
                run = store.get_run(m.group(1))
                if not run:
                    return self._json(404, {"error": "not found"})
                p = paths.RUNS_DIR / m.group(1) / "report.md"
                if not p.is_file():
                    return self._send(200, "（报告尚未生成）", "text/markdown; charset=utf-8")
                return self._send(200, p.read_text(encoding="utf-8", errors="replace"),
                                  "text/markdown; charset=utf-8")
            m = re.match(r"^/api/runs/([^/]+)/log$", path)
            if m:
                # step 由前端 encodeURIComponent 编码（"steps/x.log" → "steps%2Fx.log"），
                # 必须解码后再拼路径，否则永远找不到日志文件。read_step_log 内部已防目录穿越。
                rel = (parse_qs(urlparse(self.path).query).get("step") or [""])[0]
                rel = rel.replace("\\", "/").lstrip("/")
                run = store.get_run(m.group(1))
                if not run:
                    return self._json(404, {"error": "not found"})
                text = store.read_step_log(m.group(1), rel)
                return self._json(200, {"log": text})
            return self._json(404, {"error": "unknown api"})
        # 静态文件：单文件或 UI 子目录文件（如 icons/icon-192.png）；
        # _static 内的 parents 校验确保解析后仍在 UI_DIR 内，防穿越
        name = path.lstrip("/")
        if re.match(r"^[\w.-]+(/[\w.-]+)*$", name):
            return self._static(name)
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        m = None
        if not self._authed():
            return self._json(401, {"error": "需要访问令牌（启动 Tutti 时控制台会显示）"})
        if path == "/api/control":
            return self._api_control()
        if path == "/api/control/heartbeat":
            ok, view = remote.heartbeat(self._client_id())
            return self._json(200, {"ok": ok, "control": view})
        # 写操作需要控制权：空闲自动接管；他人持有时 423，由前端引导抢夺
        deny = self._deny_control()
        if deny:
            return deny
        if path == "/api/tasks":
            return self._api_create_task()
        m = re.match(r"^/api/tasks/([^/]+)/(archive|delete|retry)$", path)
        if m:
            if m.group(2) == "archive":
                body = self._body()
                ok, err = store.archive_task(m.group(1), bool(body.get("archived", True)))
            elif m.group(2) == "retry":
                ok, err, run = store.retry_task(m.group(1))
                if not ok:
                    return self._json(400, {"error": err})
                jobs.enqueue({"kind": "orchestration", "run_id": run["id"], "task_id": m.group(1)})
                return self._json(200, {"ok": True, "run_id": run["id"]})
            else:
                ok, err = store.delete_task(m.group(1))
            return self._json(400, {"error": err}) if not ok else self._json(200, {"ok": True})
        m = re.match(r"^/api/runs/([^/]+)/cancel$", path)
        if m:
            ok = jobs.cancel(m.group(1))
            return self._json(200, {"ok": ok})
        m = re.match(r"^/api/runs/([^/]+)/delete$", path)
        if m:
            ok, err = store.delete_run(m.group(1))
            return self._json(400, {"error": err}) if not ok else self._json(200, {"ok": True})
        if path == "/api/runs/delete":
            n, skipped, err = store.delete_runs(self._body().get("ids") or [])
            if err and not n:
                return self._json(400, {"error": err})
            return self._json(200, {"ok": True, "count": n, "skipped": skipped, "message": err})
        if path == "/api/runs/clear":
            n, skipped = store.clear_runs()
            return self._json(200, {"ok": True, "count": n, "skipped": skipped})
        if path == "/api/orchestration":
            return self._api_set_preference()
        if path == "/api/catalog/reset":
            catalog.reset_to_default()
            catalog.load(force=True)
            return self._json(200, {"ok": True})
        if path == "/api/catalog/reload":
            catalog.load(force=True)
            manager.detect_all(force=True)
            return self._json(200, {"ok": True})
        if path == "/api/catalog/check-updates":
            # 进入智能体目录页时前端自动调用；后台逐条查远端版本，立即返回
            force = bool(self._body().get("force"))
            n = manager.check_updates_async(force=force)
            return self._json(200, {"ok": True, "count": n,
                                    "checking": manager.updates_checking()})
        if path == "/api/models/provider/delete":
            from core import modelhub
            body = self._body()
            n, err = modelhub.providers_op([body.get("id") or ""], "delete")
            return self._json(400, {"error": err}) if err else self._json(200, {"ok": True, "count": n})
        if path == "/api/models/provider-op":
            from core import modelhub
            body = self._body()
            n, err = modelhub.providers_op(body.get("ids") or [], body.get("op") or "")
            return self._json(400, {"error": err}) if err else self._json(200, {"ok": True, "count": n})
        if path == "/api/models/provider":
            from core import modelhub
            err = modelhub.upsert_provider(self._body())
            return self._json(400, {"error": err}) if err else self._json(200, {"ok": True})
        if path == "/api/models/import":
            from core import modelhub
            body = self._body()
            ids = body.get("sources")
            res = modelhub.import_sources(ids if isinstance(ids, list) else None)
            if res["imported"]:
                modelhub.refresh_all_async()  # 导入后自动拉取各供应商可用模型
                res["message"] += "；正在后台获取模型列表…"
            return self._json(200, dict(res, ok=res["imported"] > 0))
        if path == "/api/models/import-ccswitch":
            from core import modelhub
            n, msg = modelhub.import_ccswitch()
            if n:
                modelhub.refresh_all_async()  # 导入后自动拉取各供应商可用模型
                msg += "；正在后台获取模型列表…"
            return self._json(200, {"ok": n > 0, "imported": n, "message": msg})
        if path == "/api/models/refresh":
            from core import modelhub
            n, err = modelhub.refresh_models(self._body().get("id") or "")
            return self._json(200, {"ok": bool(n), "count": n, "message": err})
        if path == "/api/models/refresh-all":
            from core import modelhub
            n = modelhub.refresh_all_async()
            return self._json(200, {"ok": True, "count": n,
                                    "message": "后台刷新 %d 个供应商…" % n})
        if path == "/api/models/model-op":
            from core import modelhub
            body = self._body()
            pid = body.get("provider_id") or ""
            op = body.get("op") or ""
            names = body.get("names")
            if names is not None:                     # 批量：names 数组
                n, err = modelhub.model_ops(pid, names, op)
                return self._json(400, {"error": err}) if err else self._json(
                    200, {"ok": True, "count": n})
            err = modelhub.model_op(pid, body.get("name") or "", op)
            return self._json(400, {"error": err}) if err else self._json(200, {"ok": True})
        if path == "/api/models/reorder":
            from core import modelhub
            body = self._body()
            err = modelhub.reorder_models(body.get("provider_id") or "",
                                          body.get("names") or [])
            return self._json(400, {"error": err}) if err else self._json(200, {"ok": True})
        if path == "/api/models/test-provider":
            from core import modelhub
            return self._json(200, modelhub.test_provider(self._body().get("id") or ""))
        if path == "/api/models/test-model":
            from core import modelhub
            body = self._body()
            return self._json(200, modelhub.test_model(body.get("provider_id") or "",
                                                       body.get("name") or ""))
        if path == "/api/models/binding":
            from core import modelhub
            body = self._body()
            b = modelhub.set_binding(body.get("agent_id") or "",
                                     provider_id=body.get("provider_id"),
                                     model=body.get("model"),
                                     models=body.get("models"),
                                     difficulty_routing=body.get("difficulty_routing"),
                                     chain=body.get("chain"))
            return self._json(200, {"ok": True, "binding": b})
        if path == "/api/flows":
            flow, err = flows.upsert_flow(self._body())
            if err:
                return self._json(400, {"error": err})
            return self._json(200, {"ok": True, "flow": flow})
        m = re.match(r"^/api/flows/([^/]+)/delete$", path)
        if m:
            err = flows.delete_flow(m.group(1))
            return self._json(400, {"error": err}) if err else self._json(200, {"ok": True})
        if path == "/api/settings":
            view, err = settings.save(self._body())
            if err:
                return self._json(400, {"error": err, "settings": view})
            n = jobs.configure(view["max_concurrent_jobs"])
            return self._json(200, {"ok": True, "settings": view, "workers": n})
        if path == "/api/orchestrator":
            from core import modelhub
            body = self._body()
            err = modelhub.set_orchestrator(body.get("provider_id"),
                                            model=body.get("model"),
                                            enabled=body.get("enabled"))
            return self._json(400, {"error": err}) if err else self._json(
                200, {"ok": True, "orchestrator": modelhub.orchestrator_view()})
        if path == "/api/orchestrator/test":
            from core import modelhub
            orch = modelhub.resolve_orchestrator()
            if not orch:
                return self._json(400, {"ok": False, "error": "编排者未启用或配置失效"})
            prov, model = orch
            res = modelhub.chat(prov["id"], model,
                                "请只回复两个字：收到", max_tokens=64, timeout=30)
            return self._json(200, dict(res, model=model,
                                        provider=prov.get("name", prov["id"])))
        m = re.match(r"^/api/catalog/([^/]+)/(install|upgrade|smoke)$", path)
        if m:
            entry = catalog.by_id(m.group(1))
            if not entry:
                return self._json(404, {"error": "catalog 中无此条目"})
            op = m.group(2)
            titles = {"install": "安装", "upgrade": "升级", "smoke": "冒烟测试"}
            run = store.create_run("mgmt", "%s %s" % (titles[op], entry.get("name", entry["id"])),
                                   entry_id=entry["id"], op=op)
            jobs.enqueue({"kind": "mgmt", "run_id": run["id"], "entry_id": entry["id"], "op": op})
            return self._json(200, {"run_id": run["id"]})
        m = re.match(r"^/api/catalog/([^/]+)/check-update$", path)
        if m:
            entry = catalog.by_id(m.group(1))
            if not entry:
                return self._json(404, {"error": "catalog 中无此条目"})
            return self._json(200, manager.check_update(entry))
        m = re.match(r"^/api/catalog/([^/]+)/model$", path)
        if m:
            entry = catalog.by_id(m.group(1))
            if not entry:
                return self._json(404, {"error": "catalog 中无此条目"})
            body = self._body()
            res = manager.write_model(entry, body.get("model"))
            return self._json(200 if res["ok"] else 400, res)
        return self._json(404, {"error": "unknown api"})

    # ------------------------------------------------------------ 业务
    def _api_control(self):
        body = self._body()
        action = body.get("action") or ""
        if action == "acquire":
            ok, view = remote.acquire(self._client_id(), self._client_name(),
                                      force=bool(body.get("force")))
            if not ok:
                return self._json(423, {"error": "「%s」正在控制" % view.get("holder", "其他设备"),
                                        "control": view})
            store.bump_state()  # 控制权交接：让其他端立即看到
            return self._json(200, {"ok": True, "control": view})
        if action == "release":
            view = remote.release(self._client_id())
            store.bump_state()
            return self._json(200, {"ok": True, "control": view})
        return self._json(400, {"error": "action 必须是 acquire 或 release"})

    def _api_events(self):
        """SSE 事件驱动：等 store 状态版本变化才构建/推送，空闲连接几乎零开销
        （此前每连接每 0.8s 盲构建全量 payload，多端并发会把 detect 的慢 IO
        放大成服务假死）。每 ~2s 醒一次顺带检查控制权变化。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")  # 防 nginx/隧道缓冲 SSE
        self.end_headers()
        cid = self._client_id()
        ver = store.state_version()
        sent_ctrl = None
        n = 0
        try:
            while True:
                ver = store.wait_state_change(ver, 2.0)
                ctrl = remote.control_view(cid)
                if ver == store.state_version() and ctrl == sent_ctrl:
                    continue  # 超时醒来且无变化
                payload = json.dumps(_state_payload(cid, ver), ensure_ascii=False)
                self.wfile.write(("data: " + payload + "\n\n").encode("utf-8"))
                self.wfile.flush()
                sent_ctrl = ctrl
                n += 1
                if n % 20 == 0:  # ~40s 一次注释行：探活兼防中间层断开空闲连接
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except Exception:
            pass  # 客户端断开是常态，线程随进程退出

    def _api_create_task(self):
        body = self._body()
        try:
            task = store.create_task(body)
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_task_status(task["id"], "queued")
        jobs.enqueue({"kind": "orchestration", "run_id": run["id"], "task_id": task["id"]})
        return self._json(200, {"task_id": task["id"], "run_id": run["id"]})

    def _api_set_preference(self):
        body = self._body()
        agent_id = body.get("agent_id")
        if not agent_id:
            return self._json(400, {"error": "agent_id 必填"})
        # 模型链已并入「CLI 绑定」（modelhub bindings），这里只管参与编排开关
        pref = registry.set_preference(agent_id, enabled=body.get("enabled"))
        return self._json(200, {"ok": True, "preference": pref})

    # ------------------------------------------------------------ 静态
    def _static(self, name):
        p = (paths.UI_DIR / name).resolve()
        if not p.is_file() or paths.UI_DIR.resolve() not in p.parents:
            return self._json(404, {"error": "not found"})
        suffix = p.suffix.lower()
        self._send(200, p.read_bytes(), MIME.get(suffix, "application/octet-stream"))


def _state_payload(client_id="", ver=None):
    agents = registry.effective_agents(catalog.load(), manager.detect_all())
    if ver is None:
        ver = store.state_version()
    return {
        "v": ver,
        "agents": agents,
        "tasks": store.list_tasks(30, archived=False),
        "archived_tasks": store.list_tasks(30, archived=True),
        "runs": store.list_runs(40),
        "control": remote.control_view(client_id),
    }


def main():
    parser = argparse.ArgumentParser(description="Tutti 多智能体编排台")
    parser.add_argument("--host", default="0.0.0.0",
                        help="监听地址；0.0.0.0 允许手机/局域网访问（默认），127.0.0.1 仅本机")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--trusted-proxy", action="store_true",
                        help="服务跑在 Cloudflare Tunnel/frp 等反代后面时开启："
                             "带转发头的回源请求必须带令牌，防止本机回源被当成 127.0.0.1 豁免")
    parser.add_argument("--public-url", default="",
                        help="固定公网地址（如 https://tutti.example.com），扫码弹框优先展示")
    parser.add_argument("--no-public-tunnel", action="store_true",
                        help="不自动开 Cloudflare 快速隧道（默认：检测到 cloudflared 就自动开，"
                             "获得随机 *.trycloudflare.com 公网地址，重启会变）")
    args = parser.parse_args()

    paths.ensure_dirs()
    catalog.load()
    store.load_all()
    from core import modelhub
    modelhub.migrate_orch_models()  # 旧「编排模型」偏好并入 CLI 绑定（幂等，带备份）
    modelhub.migrate_chains()       # 旧单供应商模型链升级为跨厂商 chain（幂等，带备份）
    jobs.start_worker()
    tok = remote.token()
    import atexit
    import os as _os
    remote.set_trusted_proxy(args.trusted_proxy or _os.environ.get("TUTTI_TRUST_PROXY") == "1",
                             args.public_url)

    global PORT
    PORT = args.port
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)

    def _announce_public(url):
        print("[Tutti] 公网     %s/?token=%s" % (url, tok))
        print("[Tutti]          ← 任何网络可访问；临时地址重启会变，手机重新扫码即可。"
              "\n[Tutti]          要固定域名：cloudflared tunnel login 后参考 README 公网章节。")
        store.bump_state()  # 扫码弹框下次打开即可拿到公网地址

    print("[Tutti] 本机     http://127.0.0.1:%d" % args.port)
    if remote.PUBLIC_URL:
        print("[Tutti] 公网     %s/?token=%s   ← 任何网络可访问（反代回源已强制校验令牌）"
              % (remote.PUBLIC_URL, tok))
    elif not args.no_public_tunnel and args.host != "127.0.0.1":
        if remote.has_local_creds():
            print("[Tutti] （检测到已有 Cloudflare 隧道凭据：临时隧道在此类机器上不可用，"
                  "请用 --public-url 配固定域名，或 start-public.bat）")
        elif remote.start_quick_tunnel(args.port, _announce_public):
            print("[Tutti] 正在建立 Cloudflare 快速隧道（公网地址几秒后打印；"
                  "若打不开说明当前网络不支持，可改用固定域名或 Tailscale）…")
        else:
            print("[Tutti] （未检测到 cloudflared，跳过公网隧道；"
                  "winget install Cloudflare.cloudflared 后重启即可获得公网地址）")
    atexit.register(remote.stop_quick_tunnel)
    if args.host != "127.0.0.1":
        lan = remote.lan_ip()
        if lan:
            print("[Tutti] 局域网   http://%s:%d/?token=%s   ← 手机同一 WiFi 直接打开" % (lan, args.port, tok))
        ts = remote.tailscale_ip()
        if ts:
            print("[Tutti] Tailscale http://%s:%d/?token=%s   ← 外网随时随地访问" % (ts, args.port, tok))
        elif not remote.PUBLIC_URL:
            print("[Tutti] （未检测到 Tailscale；安装后重启本服务即可获得外网地址）")
        print("[Tutti] 远程访问受令牌保护；手机打开一次带 token 的地址后会记住。")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:%d" % args.port)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[Tutti] 已退出")


if __name__ == "__main__":
    main()
