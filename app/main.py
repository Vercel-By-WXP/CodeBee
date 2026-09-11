# -*- coding: utf-8 -*-
"""Tutti（多智能体编排台）：纯标准库 HTTP 服务（零依赖，Python 3.8+）。

启动：python app/main.py [端口]，默认 8765，自动打开浏览器。
"""
from __future__ import annotations

import argparse
import json
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from core import catalog, jobs, manager, registry, store
from core import paths

MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
        ".ico": "image/x-icon"}


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

    # ------------------------------------------------------------ 路由
    def do_GET(self):
        path = urlparse(self.path).path
        m = None
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/api/"):
            if path == "/api/state":
                return self._api_state()
            if path == "/api/models":
                from core import modelhub
                return self._json(200, {"providers": modelhub.provider_view(),
                                        "bindings": modelhub.bindings(),
                                        "catalog": modelhub.models_view()})
            if path == "/api/catalog":
                return self._json(200, {"catalog": manager.catalog_view()})
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
                q = urlparse(self.path).query
                rel = (q.split("step=")[-1] if "step=" in q else "").replace("/", "\\")
                run = store.get_run(m.group(1))
                if not run:
                    return self._json(404, {"error": "not found"})
                rel_norm = rel.replace("\\", "/")
                text = store.read_step_log(m.group(1), rel_norm)
                return self._json(200, {"log": text})
            return self._json(404, {"error": "unknown api"})
        # 静态文件
        name = path.lstrip("/")
        if re.match(r"^[\w.-]+$", name):
            return self._static(name)
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        m = None
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
        m = re.match(r"^/api/models/provider/delete$", path)
        if m:
            from core import modelhub
            body = self._body()
            modelhub.delete_provider(body.get("id") or "")
            return self._json(200, {"ok": True})
        if path == "/api/models/provider":
            from core import modelhub
            err = modelhub.upsert_provider(self._body())
            return self._json(400, {"error": err}) if err else self._json(200, {"ok": True})
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
            err = modelhub.model_op(body.get("provider_id") or "",
                                    body.get("name") or "", body.get("op") or "")
            return self._json(400, {"error": err}) if err else self._json(200, {"ok": True})
        if path == "/api/models/binding":
            from core import modelhub
            body = self._body()
            b = modelhub.set_binding(body.get("agent_id") or "",
                                     provider_id=body.get("provider_id"),
                                     model=body.get("model"),
                                     difficulty_routing=body.get("difficulty_routing"))
            return self._json(200, {"ok": True, "binding": b})
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
    def _api_state(self):
        agents = registry.effective_agents(catalog.load(), manager.detect_all())
        return self._json(200, {
            "agents": agents,
            "tasks": store.list_tasks(30, archived=False),
            "archived_tasks": store.list_tasks(30, archived=True),
            "runs": store.list_runs(40),
        })

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
        pref = registry.set_preference(agent_id, enabled=body.get("enabled"),
                                       model=body.get("model"))
        return self._json(200, {"ok": True, "preference": pref})

    # ------------------------------------------------------------ 静态
    def _static(self, name):
        p = (paths.UI_DIR / name).resolve()
        if not p.is_file() or paths.UI_DIR.resolve() not in p.parents:
            return self._json(404, {"error": "not found"})
        suffix = p.suffix.lower()
        self._send(200, p.read_bytes(), MIME.get(suffix, "application/octet-stream"))


def main():
    parser = argparse.ArgumentParser(description="Tutti 多智能体编排台")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    paths.ensure_dirs()
    catalog.load()
    store.load_all()
    jobs.start_worker()

    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = "http://127.0.0.1:%d" % args.port
    print("[Tutti] http://127.0.0.1:%d  （Ctrl+C 退出）" % args.port)
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[Tutti] 已退出")


if __name__ == "__main__":
    main()
