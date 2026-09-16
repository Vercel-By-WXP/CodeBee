# -*- coding: utf-8 -*-
"""CodeBee（多智能体编排台）：纯标准库 HTTP 服务（零依赖，Python 3.8+）。

启动：python app/main.py [端口]，默认 8765，自动打开浏览器。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from core import automation, catalog, flows, jobs, manager, market, market_remote, registry, remote, settings, store
from core import paths
from core import health

MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
        ".ico": "image/x-icon", ".json": "application/manifest+json; charset=utf-8",
        ".webmanifest": "application/manifest+json; charset=utf-8"}


def _utf8_bytes(data):
    """文本预览出口的编码守卫：字节流非合法 UTF-8 时按 GBK 解码后重编码为
    UTF-8 再发。子代理在中文 Windows 上可能把工作区文件落成 GBK，直接透传
    会让声明 charset=utf-8 的浏览器预览整片乱码（与 pipeline 读防线同一问题
    的服务端出口面）。"""
    try:
        data.decode("utf-8")
        return data
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("gbk").encode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return data.decode("utf-8", "replace").encode("utf-8")

PORT = 8765  # main() 启动时更新；/api/connect 组装扫码地址用

# 侧栏「查看文件」/「目录浏览」跳过的噪音目录（与 store 的习惯一致）
_SKIP_DIRS_SHARE = {".git", "node_modules", "__pycache__", ".venv", "venv",
                    ".idea", ".vscode", "_attachments"}


class Handler(BaseHTTPRequestHandler):
    server_version = "CodeBee/1.0"

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
                                     self.headers.get("X-CodeBee-Token") or "")

    def _client_id(self):
        cid = (self.headers.get("X-CodeBee-Client") or "").strip()
        if not cid:  # EventSource 带不了自定义头，身份从 query 兜底
            cid = (parse_qs(urlparse(self.path).query).get("client") or [""])[0].strip()
        if cid:
            return cid[:64]
        # 无头请求（curl/旧脚本）：真本机统一算"本机"，远程各自匿名且无持久身份
        ip, fw = self._forwarded_ip()
        return "local" if (ip in ("127.0.0.1", "::1") and not fw) else ""

    def _client_name(self):
        name = (self.headers.get("X-CodeBee-Name") or "").strip()
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
                return self._json(401, {"error": "需要访问令牌（启动 CodeBee 时控制台会显示）"})
            if path == "/api/state":
                return self._json(200, _state_payload(self._client_id()))
            if path == "/api/browse":
                return self._api_browse()
            if path == "/api/dir/scan":
                # 侧栏文件夹「查看文件」：列出该工作目录的文件（跳过噪音目录，只列文件）
                return self._api_dir_scan()
            if path == "/api/dir/file":
                return self._api_dir_file()
            if path == "/api/git/info":
                return self._api_git_info()
            if path == "/api/events":
                return self._api_events()
            if path == "/api/control":
                return self._json(200, {"control": remote.control_view(self._client_id())})
            if path == "/api/health":
                from core import health
                return self._json(200, health.snapshot())
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
            if path == "/api/skills":
                from core import skills
                return self._json(200, skills.view())
            if path == "/api/settings":
                return self._json(200, dict(settings.load(), **jobs.workers_info()))
            if path == "/api/selfupdate":
                from core import selfupdate
                return self._json(200, selfupdate.check(
                    force=bool(parse_qs(urlparse(self.path).query).get("force"))))
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
            if path == "/api/usage":
                from core import usage
                q = parse_qs(urlparse(self.path).query)
                try:
                    days = max(0, min(3650, int((q.get("days") or ["30"])[0])))
                except ValueError:
                    days = 30
                return self._json(200, usage.summary(days=days))
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
            m = re.match(r"^/api/runs/([^/]+)/files$", path)
            if m:
                run = store.get_run(m.group(1))
                if not run:
                    return self._json(404, {"error": "not found"})
                wd, files = store.run_artifacts(m.group(1))
                # 任务一步都没跑出来过（如历次都在检出前失败）→ 工作目录里的
                # 文件变动是并行活动的噪音，不算这个任务的成品
                if files and not store.task_step_count(run.get("task_id") or ""):
                    files = []
                return self._json(200, {"workdir": wd, "files": files,
                                        "task_id": run.get("task_id") or ""})
            m = re.match(r"^/api/tasks/([^/]+)/side$", path)
            if m:
                # 任务检查器（右缘停靠列）专用：轻量聚合、可轮询，不带 diff 文本
                return self._api_task_side(m.group(1))
            m = re.match(r"^/api/tasks/([^/]+)/runs$", path)
            if m:
                # 任务级详情用：该任务全部 run（含 steps），不受前端 run 窗口限制
                if not store.get_task(m.group(1)):
                    return self._json(404, {"error": "not found"})
                return self._json(200, {"runs": store.task_runs(m.group(1))})
            m = re.match(r"^/api/tasks/([^/]+)/bible$", path)
            if m:
                # 故事圣经：查看（无令牌豁免走 _authed 已过；本机免令牌）
                fp, text, err = store.read_story_bible(m.group(1))
                if err:
                    return self._json(400, {"error": err})
                return self._json(200, {"path": fp, "text": text})
            m = re.match(r"^/api/tasks/([^/]+)/git$", path)
            if m:
                # GIT 工作台全貌（详情页「版本」页签）：仓库状态聚合 + 任务隔离态
                return self._api_git_wb(m.group(1))
            m = re.match(r"^/api/tasks/([^/]+)/git/diff$", path)
            if m:
                # 工作台单文件实时 diff（?path= 仓库内相对路径）
                return self._api_git_wb_diff(m.group(1))
            m = re.match(r"^/api/tasks/([^/]+)/continue-info$", path)
            if m:
                # 「继续连载」弹框数据：能否续、已写到第几章、默认续几章
                info = store.continue_info(m.group(1))
                if info is None:
                    return self._json(404, {"error": "not found"})
                return self._json(200, info)
            m = re.match(r"^/api/runs/([^/]+)/file$", path)
            if m:
                if not store.get_run(m.group(1)):
                    return self._json(404, {"error": "not found"})
                rel = (parse_qs(urlparse(self.path).query).get("name") or [""])[0]
                data, err = store.read_run_file(m.group(1), unquote(rel))
                if err:
                    return self._json(404, {"error": err})
                ext = Path(rel.lower()).suffix
                ctype = MIME.get(ext, "application/octet-stream")
                # 文本类扩展名直接在浏览器里看内容；未知二进制才触发下载
                if ext in (".md", ".txt", ".log", ".csv", ".yml", ".yaml", ".ini", ".toml"):
                    ctype = "text/plain; charset=utf-8"
                if ctype.startswith("text/"):
                    data = _utf8_bytes(data)
                return self._send(200, data, ctype)
            m = re.match(r"^/api/runs/([^/]+)/log$", path)
            if m:
                # step 由前端 encodeURIComponent 编码（"steps/x.log" → "steps%2Fx.log"），
                # 必须解码后再拼路径，否则永远找不到日志文件。read_step_log 内部已防目录穿越。
                q = parse_qs(urlparse(self.path).query)
                rel = (q.get("step") or [""])[0]
                rel = rel.replace("\\", "/").lstrip("/")
                # tail：蜂巢卡片实时尾巴轮询用小窗口；日志面板用默认（4000）。
                # 先取默认/解析再夹紧：此前 max(200, min(40000, 0)) 恒为 200，
                # 不带 tail 的日志抽屉一直只读到末尾 200 字节（2026-09-15 实测暴露）
                try:
                    tail = int((q.get("tail") or [""])[0] or 0) or paths.LOG_TAIL_CHARS
                except ValueError:
                    tail = paths.LOG_TAIL_CHARS
                tail = max(200, min(40000, tail))
                run = store.get_run(m.group(1))
                if not run:
                    return self._json(404, {"error": "not found"})
                # pretty=1：日志抽屉等人类视图，把 codex JSONL 事件流翻译成可读行；
                # 蜂巢卡片等机器视图可不带，只享受重复行折叠
                pretty = (q.get("pretty") or [""])[0] in ("1", "true")
                text = store.read_step_log(m.group(1), rel, tail=tail, pretty=pretty)
                # 带上步骤/运行状态：日志面板靠它区分「实时刷新中」和「已结束」，
                # 结束即停轮询（否则用户盯着不动的日志以为刷新坏了）
                st = next((s.get("status") or "" for s in (run.get("steps") or [])
                           if s.get("log") == rel), "")
                return self._json(200, {"log": text, "step_status": st,
                                        "run_status": run.get("status") or ""})
            if path == "/api/automation":
                return self._json(200, {"tasks": automation.list_tasks(),
                                        "templates": automation.templates()})
            m = re.match(r"^/api/automation/([^/]+)$", path)
            if m:
                t = automation.get_task(m.group(1))
                return self._json(200, {"task": t}) if t else self._json(404, {"error": "not found"})
            if path == "/api/market":
                return self._json(200, market.view())
            if path == "/api/market/remote":
                q = parse_qs(urlparse(self.path).query)
                return self._json(200, market_remote.view(
                    offset=(q.get("offset") or ["0"])[0],
                    limit=(q.get("limit") or [""])[0],
                    source=(q.get("source") or [""])[0],
                    q=(q.get("q") or [""])[0]))
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
            return self._json(401, {"error": "需要访问令牌（启动 CodeBee 时控制台会显示）"})
        if path == "/api/control":
            return self._api_control()
        if path == "/api/control/heartbeat":
            ok, view = remote.heartbeat(self._client_id())
            return self._json(200, {"ok": ok, "control": view})
        # 写操作需要控制权：空闲自动接管；他人持有时 423，由前端引导抢夺。
        # 例外（配置管理类操作全局生效，不被「哪台设备在操作」挡住，
        # 否则告警弹框里的按钮在多端场景会静默 423 失败）：
        if path in ("/api/health/op", "/api/models/provider-op", "/api/models/model-op",
                    "/api/models/test-provider", "/api/models/test-model",
                    "/api/models/probe-wire"):
            pass                                    # 落到下方各自路由
        else:
            deny = self._deny_control()
            if deny:
                return deny
        if path == "/api/tasks":
            return self._api_create_task()
        if path == "/api/health/op":
            # 供应商健康告警的手动操作（silence 静默 / reset 手动恢复）
            from core import health
            body = self._body()
            prov = (body.get("provider") or "").strip()
            if not prov:
                return self._json(400, {"error": "provider 必填"})
            op = body.get("op") or ""
            if op == "silence":
                ok, err = health.silence(prov, minutes=int(body.get("minutes") or 0))
            elif op == "reset":
                ok, err = health.reset(prov)
            else:
                return self._json(400, {"error": "op 必须是 silence 或 reset"})
            if not ok:
                return self._json(404, {"error": err})
            store.bump_state()
            return self._json(200, {"ok": True, "health": health.snapshot()})
        if path == "/api/attachments":
            return self._api_add_attachment()
        m = re.match(r"^/api/tasks/([^/]+)/(archive|delete|retry|rename|continue)$", path)
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
            elif m.group(2) == "continue":
                # 继续连载：在旧任务基础上新建任务（沿用目标/目录/评审设置，章节号衔接）
                ok, err, new_task = store.continue_task(
                    m.group(1), (self._body() or {}).get("chapters"))
                if not ok:
                    return self._json(400, {"error": err})
                run = store.create_run("orchestration", new_task["title"],
                                       task_id=new_task["id"])
                store.update_task_status(new_task["id"], "queued")
                jobs.enqueue({"kind": "orchestration",
                              "run_id": run["id"], "task_id": new_task["id"]})
                return self._json(200, {"ok": True, "task_id": new_task["id"],
                                        "run_id": run["id"]})
            elif m.group(2) == "rename":
                ok, err = store.rename_task(m.group(1), self._body().get("title"))
            else:
                ok, err = store.delete_task(m.group(1))
            return self._json(400, {"error": err}) if not ok else self._json(200, {"ok": True})
        m = re.match(r"^/api/tasks/([^/]+)/bible$", path)
        if m:
            # 故事圣经：编辑保存（运行中由 store 拒绝，防打碎前缀缓存）
            body = self._body()
            ok, err = store.write_story_bible(m.group(1), body.get("text") or "")
            return self._json(400, {"error": err}) if not ok else self._json(200, {"ok": True})
        m = re.match(r"^/api/tasks/([^/]+)/(git-merge|git-discard)$", path)
        if m:
            return self._api_git_verdict(m.group(1), m.group(2))
        m = re.match(r"^/api/tasks/([^/]+)/git$", path)
        if m:
            # GIT 工作台写操作：{action, path?, message?, branch?, confirm?}
            return self._api_git_wb_op(m.group(1))
        m = re.match(r"^/api/(tasks|runs)/([^/]+)/reveal$", path)
        if m:
            return self._api_reveal(m.group(1), m.group(2))
        m = re.match(r"^/api/runs/([^/]+)/messages$", path)
        if m:
            return self._api_add_message(m.group(1))
        m = re.match(r"^/api/runs/([^/]+)/messages/retract$", path)
        if m:
            return self._api_retract_message(m.group(1))
        m = re.match(r"^/api/runs/([^/]+)/pause$", path)
        if m:
            # 暂停/放行：标志位挂在下一个步骤开始前；取消不必先解除暂停
            body = self._body() or {}
            if not store.set_paused(m.group(1), bool(body.get("paused"))):
                return self._json(404, {"error": "not found"})
            return self._json(200, {"ok": True, "paused": bool(body.get("paused"))})
        m = re.match(r"^/api/runs/([^/]+)/cancel$", path)
        if m:
            # 先落「用户主动取消」标记再置事件：自动续跑必须尊重这个意图不得续上；
            # 顺序保证起跑方读到已置位事件时，标记一定已写入（排队取消的兜底依赖它）。
            store.update_run(m.group(1), cancelled_by_user=True)
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
        if path == "/api/selfupdate/apply":
            from core import selfupdate
            res = selfupdate.apply_upgrade()
            return self._json(400, res) if res.get("error") else self._json(200, dict(res, ok=True))
        if path == "/api/selfupdate/restart":
            from core import selfupdate
            global PORT
            if not selfupdate.relaunch(PORT):
                return self._json(400, {"error": "重启参数非法"})
            def _bye():
                time.sleep(0.8)
                selfupdate.self_quit()
            threading.Thread(target=_bye, daemon=True).start()
            return self._json(200, {"ok": True, "message": "服务正在重启，几秒后自动恢复"})
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
        if path == "/api/models/probe-wire":
            # 适配测试：实测供应商另一条 wire 协议（同密钥），结果存 wire_caps
            from core import modelhub
            caps, note = modelhub.probe_wire_caps(self._body().get("id") or "")
            return self._json(200, {"ok": True, "wire_caps": caps, "note": note})
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
        if path == "/api/skills/lesson-op":
            from core import skills
            body = self._body()
            err = skills.lesson_op(body.get("id") or "", body.get("op") or "")
            return self._json(400, {"error": err}) if err else self._json(200, {"ok": True})
        if path == "/api/skills/pack-op":
            from core import skills
            body = self._body()
            err = skills.pack_op(body.get("id") or "", body.get("op") or "")
            return self._json(400, {"error": err}) if err else self._json(200, {"ok": True})
        if path == "/api/skills/learn":
            from core import skills
            n = skills.learn_from_run((self._body().get("run_id") or ""))
            return self._json(200, {"ok": True, "learned": n})
        m = re.match(r"^/api/flows/([^/]+)/reset$", path)
        if m:
            err = flows.reset_flow(m.group(1))
            return self._json(400, {"error": err}) if err else self._json(200, {"ok": True})
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
        if path == "/api/settings/default-workdir":
            body = self._body()
            old = settings.default_workdir()
            view, err = settings.save({"default_workdir": body.get("path") or ""})
            if err:
                return self._json(400, {"error": err, "settings": view})
            new = settings.default_workdir()
            moved = skipped = 0
            if body.get("migrate") and old != new:
                try:
                    Path(new).mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    return self._json(400, {"error": "新默认路径不可创建: %s" % e})
                moved, skipped = store.migrate_task_workdirs(old, new)
            return self._json(200, {"ok": True, "settings": view, "moved": moved, "skipped": skipped})
        if path == "/api/orchestrator":
            from core import modelhub
            body = self._body()
            err = modelhub.set_orchestrator(body.get("provider_id"),
                                            model=body.get("model"),
                                            enabled=body.get("enabled"))
            return self._json(400, {"error": err}) if err else self._json(
                200, {"ok": True, "orchestrator": modelhub.orchestrator_view()})
        if path == "/api/orchestrator/test":
            from core import modelhub, usage
            orch = modelhub.resolve_orchestrator()
            if not orch:
                return self._json(400, {"ok": False, "error": "编排者未启用或配置失效"})
            prov, model = orch
            res = modelhub.chat(prov["id"], model,
                                "请只回复两个字：收到", max_tokens=64, timeout=30,
                                cache_ttl=86400)  # §07 T2.2：连通测试幂等，24h 精确缓存
            try:
                usage.record(source="test", role="orch-test", agent="orchestrator",
                             agent_label="编排者", tool="orchestrator", model=model,
                             provider=prov.get("name", prov.get("id", "")),
                             ok=bool(res.get("ok")), usage=res.get("usage"))
            except Exception:
                pass
            return self._json(200, dict(res, model=model,
                                        provider=prov.get("name", prov["id"])))
        m = re.match(r"^/api/catalog/([^/]+)/(install|upgrade|uninstall|smoke)$", path)
        if m:
            entry = catalog.by_id(m.group(1))
            if not entry:
                return self._json(404, {"error": "catalog 中无此条目"})
            op = m.group(2)
            titles = {"install": "安装", "upgrade": "升级", "uninstall": "卸载",
                      "smoke": "冒烟测试"}
            if op == "uninstall" and not catalog.uninstall_command(entry):
                return self._json(400, {
                    "error": "无法推导卸载命令：请在 data/catalog.json 的 \"%s\" 里配置 uninstall 字段"
                             % entry["id"]})
            run = store.create_run("mgmt", "%s %s" % (titles[op], entry.get("name", entry["id"])),
                                   entry_id=entry["id"], op=op)
            jobs.enqueue({"kind": "mgmt", "run_id": run["id"], "entry_id": entry["id"], "op": op})
            return self._json(200, {"run_id": run["id"]})
        m = re.match(r"^/api/catalog/([^/]+)/launch$", path)
        if m:
            # 一键打开（web 类起服务+开浏览器 / console 类新终端窗口）。
            # 即时返回不走任务队列；body 可传 {"open": false} 供测试免开浏览器
            entry = catalog.by_id(m.group(1))
            if not entry:
                return self._json(404, {"error": "catalog 中无此条目"})
            res = manager.launch(entry, open_browser=bool(self._body().get("open", True)))
            return self._json(200 if res.get("ok") else 400, res)
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
        if path == "/api/automation":
            try:
                t = automation.create(self._body())
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            return self._json(200, {"ok": True, "task": t})
        m = re.match(r"^/api/automation/([^/]+)$", path)
        if m:
            try:
                t = automation.update(m.group(1), self._body())
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            return self._json(200, {"ok": True, "task": t}) if t else self._json(404, {"error": "not found"})
        m = re.match(r"^/api/automation/([^/]+)/(toggle|run|delete)$", path)
        if m:
            tid, op = m.group(1), m.group(2)
            if op == "toggle":
                t = automation.set_enabled(tid, bool((self._body() or {}).get("enabled", True)))
                return self._json(200, {"ok": True, "task": t}) if t else self._json(404, {"error": "not found"})
            if op == "run":
                t, run_id = automation.run_now(tid)
                if t is None:
                    return self._json(404, {"error": "not found"})
                return self._json(200, {"ok": True, "task": t, "run_id": run_id or ""})
            ok = automation.delete(tid)
            return self._json(200, {"ok": True}) if ok else self._json(404, {"error": "not found"})
        # 外部目录路由必须在通配的 market/<id>/(install|remove) 之前——
        # 否则 "remote" 会被当成包名吞掉
        if path == "/api/market/remote/refresh":
            return self._json(200, market_remote.refresh((self._body() or {}).get("source")))
        m = re.match(r"^/api/market/remote/install$", path)
        if m:
            res, err = market_remote.install_remote((self._body() or {}).get("id") or "")
            return self._json(400, {"error": err}) if err else self._json(200, res)
        m = re.match(r"^/api/market/([^/]+)/(install|remove)$", path)
        if m:
            if m.group(2) == "install":
                res, err = market.install(m.group(1))
            else:
                err = market.remove(m.group(1))
                res = {"ok": True, "id": m.group(1)}
            return self._json(400, {"error": err}) if err else self._json(200, res)
        return self._json(404, {"error": "unknown api"})

    # ------------------------------------------------------------ 业务
    def _api_reveal(self, kind, res_id):
        """打开/返回资源所在目录：任务=工作目录，运行=记录目录（步骤日志、报告都在里面）。
        路径一律由服务端按 id 推导，不接受客户端传任意路径——本服务监听局域网，
        不能变成远程探测文件系统的口子。open=false 只回路径给前端复制。"""
        if kind == "tasks":
            task = store.get_task(res_id)
            if not task:
                return self._json(404, {"error": "任务不存在"})
            p = Path(task.get("workdir") or "")
        else:
            run = store.get_run(res_id)
            if not run:
                return self._json(404, {"error": "运行记录不存在"})
            p = paths.RUNS_DIR / res_id
        if not p.is_dir():
            return self._json(404, {"error": "目录不存在: %s" % p})
        if self._body().get("open"):
            try:
                if sys.platform == "win32":
                    subprocess.Popen(["explorer", str(p)])
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(p)])
                else:
                    subprocess.Popen(["xdg-open", str(p)])
            except Exception as e:
                return self._json(500, {"error": "无法打开目录: %s" % e})
        return self._json(200, {"ok": True, "path": str(p)})

    def _api_git_verdict(self, task_id, op):
        """任务分支裁决：git-merge 合并回原分支（采纳）/ git-discard 删除分支（否决）。

        丢弃不可恢复，必须显式 confirm=true；两边的守卫与冲突回滚都在
        gitmod 里（见 merge_task_branch / discard_task_branch）。
        """
        from core import gitmod
        task = store.get_task(task_id)
        if not task:
            return self._json(404, {"error": "任务不存在"})
        if op == "git-merge":
            ok, err, info = gitmod.merge_task_branch(task.get("workdir"), task)
            if not ok:
                return self._json(400, {"error": err})
            store.set_task_git_state(task_id, "merged")
            return self._json(200, {"ok": True, **(info or {})})
        body = self._body()
        if not body.get("confirm"):
            return self._json(400, {"error": "丢弃任务分支不可恢复，需要 confirm=true 二次确认"})
        ok, err = gitmod.discard_task_branch(task.get("workdir"), task)
        if not ok:
            return self._json(400, {"error": err})
        store.set_task_git_state(task_id, "discarded")
        return self._json(200, {"ok": True})

    def _api_task_side(self, task_id):
        """任务检查器（右缘停靠列）的轻量聚合端点。聚合逻辑在 store.task_side
        （可单测、单实例状态）；这里只做 404 转换。"""
        d = store.task_side(task_id)
        return self._json(404, {"error": "任务不存在"}) if d is None else self._json(200, d)

    # ------------------------------------------------------ GIT 工作台（详情页版本页签）
    # 与 /api/git/info 不同：workdir 由任务 id 服务端推导，不接受客户端传任意路径，
    # 所以不必限本机——局域网端（手机）打开详情页同样能看变更、做日常 git 操作。

    def _api_git_wb(self, task_id):
        from core import gitmod
        task = store.get_task(task_id)
        if not task:
            return self._json(404, {"error": "任务不存在"})
        d = gitmod.workbench_status(str(task.get("workdir") or ""))
        d["isolation"] = {"state": task.get("git_state") or "",
                          "rev": task.get("git_rev") or "",
                          "branch": gitmod.branch_name(task_id)}
        d["task"] = {"id": task_id, "status": task.get("status") or "",
                     "title": task.get("title") or ""}
        return self._json(200, d)

    def _api_git_wb_diff(self, task_id):
        from core import gitmod
        task = store.get_task(task_id)
        if not task:
            return self._json(404, {"error": "任务不存在"})
        qs = parse_qs(urlparse(self.path).query)
        d = gitmod.file_diff(str(task.get("workdir") or ""), (qs.get("path") or [""])[0])
        if d.get("error"):
            return self._json(400, {"error": d["error"]})
        return self._json(200, d)

    def _api_git_wb_op(self, task_id):
        from core import gitmod
        task = store.get_task(task_id)
        if not task:
            return self._json(404, {"error": "任务不存在"})
        if task.get("status") in ("queued", "running"):
            return self._json(409, {"error": "任务正在运行：智能体正在工作目录里产出，"
                                            "Git 写操作等任务结束再进行（只读查看不受限）"})
        body = self._body()
        action = str(body.get("action") or "")
        ok, err, data = gitmod.workbench_op(str(task.get("workdir") or ""), action, body)
        if not ok:
            return self._json(400, {"error": err})
        return self._json(200, {"ok": True, "action": action, **(data or {})})

    def _api_browse(self):
        """本机目录浏览（工作目录「选择…」弹框用）。仅限本机请求：目录枚举是信息
        泄露面，手机/局域网端不提供、继续手填。path 缺省=用户主目录；__drives__=盘符
        列表（Windows 从「此电脑」开始选）。只列目录不列文件，纯只读。"""
        # 本机判定按来源 IP，不能按 client_id：页面请求带 X-CodeBee-Client，
        # 本机页面的 client_id 也不是 "local"，会被误拒
        ip, fw = self._forwarded_ip()
        if ip not in ("127.0.0.1", "::1") or fw:
            return self._json(403, {"error": "目录选择仅限本机使用，请手动输入路径"})
        qs = parse_qs(urlparse(self.path).query)
        raw = (qs.get("path") or [""])[0].strip()
        if raw == "__drives__":
            drives = [c + ":\\" for c in "CDEFGHIJKLMNOPQRSTUVWXYZ"
                      if Path(c + ":\\").exists()]
            return self._json(200, {"path": "此电脑", "parent": "", "dirs": drives})
        try:
            p = Path(raw).expanduser() if raw else Path.home()
        except Exception:
            return self._json(400, {"error": "非法路径"})
        if not p.exists():
            return self._json(404, {"error": "目录不存在: %s" % p})
        if not p.is_dir():
            return self._json(400, {"error": "不是目录: %s" % p})
        try:
            dirs = sorted((d.name for d in p.iterdir() if d.is_dir()), key=str.lower)
        except PermissionError:
            dirs = []  # 无权限的目录按空目录处理，可继续选它本身
        parent = "" if p.parent == p else str(p.parent)
        return self._json(200, {"path": str(p), "parent": parent, "dirs": dirs})

    def _api_dir_scan(self):
        """侧栏文件夹「查看文件」：递归列出该工作目录下的全部文件（左侧文件页树形展示）。

        只读；仅限本机请求（同 /api/browse 的信息泄露口径）。name 为相对路径
        （"/" 分隔，如 "docs/readme.md"），前端按目录层级渲染成树；跳过
        .git / node_modules 等噪音目录；上限 2000 个文件（mtime 新→旧），
        超出截断并带 truncated 标记。
        """
        ip, fw = self._forwarded_ip()
        if ip not in ("127.0.0.1", "::1") or fw:
            return self._json(403, {"error": "文件浏览仅限本机使用"})
        qs = parse_qs(urlparse(self.path).query)
        raw = (qs.get("path") or [""])[0].strip()
        if not raw:
            return self._json(400, {"error": "缺少 path"})
        try:
            p = Path(raw).expanduser()
        except Exception:
            return self._json(400, {"error": "非法路径"})
        if not p.is_dir():
            return self._json(404, {"error": "目录不存在: %s" % p})
        files = []
        truncated = False
        try:
            for cur, dirs, names in os.walk(str(p)):
                # os.walk 默认不跟随目录符号链接：环形目录不会死循环
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS_SHARE]
                for n in names:
                    try:
                        st = (Path(cur) / n).stat()
                    except OSError:
                        continue   # 断链/权限：跳过单个文件
                    files.append({"name": (Path(cur) / n).relative_to(p).as_posix(),
                                  "size": st.st_size, "mtime": int(st.st_mtime)})
                    if len(files) >= 2000:
                        truncated = True
                        break
                if truncated:
                    break
        except (PermissionError, OSError):
            return self._json(403, {"error": "无权限读取该目录"})
        files.sort(key=lambda f: (-f["mtime"], f["name"].lower()))
        return self._json(200, {"path": str(p), "files": files, "truncated": truncated})

    def _api_dir_file(self):
        """侧栏「查看文件」里点文件 chip：返回该文件内容（浏览器新页打开/下载）。
        只限本机（同 browse 口径）；name 必须相对，解析后仍落在 dir 内，防穿越。"""
        ip, fw = self._forwarded_ip()
        if ip not in ("127.0.0.1", "::1") or fw:
            return self._json(403, {"error": "文件查看仅限本机使用"})
        qs = parse_qs(urlparse(self.path).query)
        raw_dir = (qs.get("dir") or [""])[0].strip()
        raw_name = (qs.get("name") or [""])[0].strip()
        if not raw_dir or not raw_name:
            return self._json(400, {"error": "缺少 dir/name"})
        try:
            base = Path(raw_dir).expanduser().resolve()
        except Exception:
            return self._json(400, {"error": "非法路径"})
        if not base.is_dir():
            return self._json(404, {"error": "目录不存在: %s" % base})
        name = unquote(raw_name).replace("\\", "/")
        parts = name.split("/")
        if not name or not all(p and p != ".." for p in parts):
            return self._json(400, {"error": "非法文件名"})
        fp = (base / name).resolve()
        # 嵌套路径（docs/x.md）也要放行：Windows 下 str(fp) 是反斜杠，
        # startswith(base+"/") 会把合法子目录文件误判越界，改用 relative_to 判定
        try:
            fp.relative_to(base)
        except ValueError:
            return self._json(403, {"error": "越界"})
        if not fp.is_file():
            return self._json(404, {"error": "文件不存在"})
        try:
            data = fp.read_bytes()
        except OSError:
            return self._json(403, {"error": "无法读取"})
        ext = Path(name.lower()).suffix
        ctype = MIME.get(ext, "application/octet-stream")
        if ext in (".md", ".txt", ".log", ".csv", ".yml", ".yaml", ".json", ".ini", ".toml",
                   ".py", ".js", ".ts", ".html", ".css", ".svg"):
            ctype = "text/plain; charset=utf-8"
        if ctype.startswith("text/"):
            data = _utf8_bytes(data)
        return self._send(200, data, ctype)

    def _api_git_info(self):
        """探测工作目录是否为 git 仓库，返回分支/标签/最近提交供「代码版本」下拉。
        只读（rev-parse / status / log），不改仓库。仅限本机请求（同目录浏览的口径）。"""
        ip, fw = self._forwarded_ip()
        if ip not in ("127.0.0.1", "::1") or fw:
            return self._json(403, {"error": "代码版本探测仅限本机使用"})
        from core import gitmod
        qs = parse_qs(urlparse(self.path).query)
        raw = (qs.get("workdir") or [""])[0].strip()
        if not raw:
            return self._json(200, {"repo": False})
        try:
            p = Path(raw).expanduser()
        except Exception:
            return self._json(400, {"error": "非法路径"})
        if not p.is_dir():
            return self._json(200, {"repo": False})
        try:
            return self._json(200, gitmod.repo_info(str(p)))
        except Exception as e:
            return self._json(200, {"repo": False, "error": str(e)[:200]})

    def _api_add_attachment(self):
        """上传一个任务附件（截图/文件）到待提交区：{name, data: base64} → 返回附件 id。
        创建任务时把 id 列表放进 payload.attachments，落盘到工作目录 _attachments/。"""
        from core import attachments
        # 防超大声明：普通附件 8MB（base64 约 11MB），Office 文档 24MB（约 32MB），
        # 门限取上限加余量；按扩展名的精确校验在 save_pending 里
        try:
            if int(self.headers.get("Content-Length") or 0) > 34 * 1024 * 1024:
                return self._json(413, {"error": "附件过大（普通 8MB / Word·Excel·PPT 24MB）"})
        except ValueError:
            return self._json(400, {"error": "非法 Content-Length"})
        body = self._body()
        try:
            meta = attachments.save_pending(body.get("name"), body.get("data"))
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        return self._json(200, {"ok": True, "attachment": meta})

    def _api_add_message(self, run_id):
        """运行中指挥：往该 run 的信箱追加一条用户指令（文字 + 附件）。
        附件走待提交区（前端先 POST /api/attachments 拿 id）→ commit 进 workdir，
        把落盘相对路径随消息入箱，供下一个步骤 drain 时注入。写接口已在 do_POST
        统一做过设备控制（_deny_control），此处不重复。"""
        run = store.get_run(run_id)
        if not run:
            return self._json(404, {"error": "not found"})
        body = self._body() or {}
        text = body.get("text") or ""
        att_ids = body.get("attachments") or []
        workdir = store.run_workdir(run_id)
        saved = []
        if att_ids and workdir:
            from core import attachments
            try:
                saved = attachments.commit_to_workdir(workdir, att_ids)
            except Exception:
                saved = []
        saved_paths = [a.get("path") for a in saved if a.get("path")]
        if not text.strip() and not saved_paths:
            return self._json(400, {"error": "消息为空"})
        msg = store.add_message(run_id, text, sender=self._client_name(),
                                attachments=saved_paths)
        if not msg:
            return self._json(400, {"error": "运行不存在或消息非法"})
        return self._json(200, {"ok": True, "message": msg})

    def _api_retract_message(self, run_id):
        """撤回一条尚未下达的指令（drain 前从信箱删除）。已送达的撤不回——
        那是审计事实。写接口已在 do_POST 统一做过设备控制。"""
        body = self._body() or {}
        ok, err = store.retract_message(run_id, body.get("id"))
        if not ok:
            return self._json(400, {"error": err})
        return self._json(200, {"ok": True})

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
        # 每个任务的最近一次运行（不受 runs 窗口限制）：侧栏靠它展示各任务真实近况
        "task_latest": store.latest_run_by_task(),
        # 每个任务的运行次数/步骤总数（全量）：侧栏「查看全部」的计数来源
        "task_stats": store.task_run_stats(),
        "runs": store.list_runs(40),
        "control": remote.control_view(client_id),
        # 供应商健康/告警（顶栏横幅数据源；有告警时 bump_state 会推给所有端）
        "health": health.snapshot(),
    }


class ThreadedServer(ThreadingHTTPServer):
    """Windows 下 SO_REUSEADDR 允许两个进程同时 LISTEN 同一端口（请求随机
    分发到其中一个——服务"时好时坏"的根源）。独占锁让第二个实例在这里
    干净失败，而不是静默双绑。正常重启不受影响（监听 socket 关闭不进
    TIME_WAIT；已建立连接的 TIME_WAIT 不阻止重新 LISTEN 同端口）。"""
    allow_reuse_address = False

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main():
    parser = argparse.ArgumentParser(description="CodeBee 多智能体编排台")
    parser.add_argument("--host", default="0.0.0.0",
                        help="监听地址；0.0.0.0 允许手机/局域网访问（默认），127.0.0.1 仅本机")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--trusted-proxy", action="store_true",
                        help="服务跑在 Cloudflare Tunnel/frp 等反代后面时开启："
                             "带转发头的回源请求必须带令牌，防止本机回源被当成 127.0.0.1 豁免")
    parser.add_argument("--public-url", default="",
                        help="固定公网地址（如 https://codebee.example.com），扫码弹框优先展示")
    parser.add_argument("--no-public-tunnel", action="store_true",
                        help="不自动开 Cloudflare 快速隧道（默认：检测到 cloudflared 就自动开，"
                             "获得随机 *.trycloudflare.com 公网地址，重启会变）")
    parser.add_argument("--wait-port", action="store_true",
                        help="自更新重启用：先等旧实例释放端口再启动（Windows 双 LISTEN 防护）")
    args = parser.parse_args()

    paths.ensure_dirs()
    from core import attachments
    n_pc = attachments.cleanup_stale()  # 待提交附件残留清理（崩溃/弃单不堆积）
    if n_pc:
        print("[CodeBee] 附件待提交区：清理过期残留 %d 个" % n_pc)
    catalog.load()
    store.load_all()
    health.init()  # 供应商健康/告警：恢复落盘状态 + 启动探针线程
    from core import modelhub
    modelhub.migrate_orch_models()  # 旧「编排模型」偏好并入 CLI 绑定（幂等，带备份）
    modelhub.migrate_chains()       # 旧单供应商模型链升级为跨厂商 chain（幂等，带备份）
    try:
        from core import settings_schema
        settings_schema.register_default_namespaces()  # budget/cascade/compaction 配置就绪（幂等）
    except Exception:
        pass
    from core import skills
    n_lc = skills.migrate_lesson_categories()  # 分类字段上线前的教训按关键词回填（幂等，带备份）
    if n_lc:
        print("[CodeBee] 经验库：%d 条历史教训已自动归类" % n_lc)
    from core import usage
    n_bf = usage.backfill_from_runs()  # 历史运行 token 回填台账（幂等，仅补缺失步骤）
    if n_bf:
        print("[CodeBee] 用量台账：已从历史运行回填 %d 条记录" % n_bf)
    # dsh-migration §1E：崩溃遗留的 running run 标记为 failed（在 jobs.resume_interrupted
    # 之前执行，否则续跑逻辑会把僵尸 run 当成正常中断接手）
    n_rc = store.recover_orphaned_runs()
    if n_rc:
        print("[CodeBee] 崩溃恢复：%d 个遗留运行标记为 failed（interrupted at startup）" % n_rc)
    try:
        from core import settings_schema
        settings_schema.register_default_namespaces()  # budget/cascade/compaction 配置就绪（幂等）
    except Exception:
        pass
    jobs.start_worker()
    n_resume = jobs.resume_interrupted()   # 启动恢复：服务被杀中断的连载任务自动续跑
    if n_resume:
        print("[CodeBee] 已自动恢复 %d 个中断的连载任务（断点续跑）" % n_resume)
    n_auto = automation.start()   # 自动化：加载定时任务并拉起调度线程（错过的一次性任务不补跑）
    if n_auto:
        print("[CodeBee] 自动化：%d 个定时任务已加载" % n_auto)
    tok = remote.token()
    import atexit
    import os as _os
    remote.set_trusted_proxy(args.trusted_proxy or _os.environ.get("TUTTI_TRUST_PROXY") == "1",
                             args.public_url)

    global PORT
    PORT = args.port
    if args.wait_port:
        from core import selfupdate
        selfupdate.wait_port_before_bind(args.port)
    try:
        httpd = ThreadedServer((args.host, args.port), Handler)
    except OSError as e:
        # Windows 的 SO_REUSEADDR 允许两个进程同时 LISTEN 同一端口（请求随机
        # 分发，表现为"时好时坏"）；加独占锁后双起在这里干净失败并指路。
        import os
        hint = ""
        if os.name == "nt":
            hint = ("（Windows 排查：netstat -ano | findstr :%d 找到 PID，"
                    "tasklist /FI \"PID eq <PID>\" 看是谁；旧进程杀掉或换 --port）"
                    % args.port)
        raise SystemExit("[CodeBee] 端口 %d 已被占用，无法启动：%s %s"
                         % (args.port, e, hint))

    def _announce_public(url):
        print("[CodeBee] 公网     %s/?token=%s" % (url, tok))
        print("[CodeBee]          ← 任何网络可访问；临时地址重启会变，手机重新扫码即可。"
              "\n[CodeBee]          要固定域名：cloudflared tunnel login 后参考 README 公网章节。")
        store.bump_state()  # 扫码弹框下次打开即可拿到公网地址

    print("[CodeBee] 本机     http://127.0.0.1:%d" % args.port)
    print("[CodeBee] 数据目录 %s" % paths.DATA_DIR)
    if remote.PUBLIC_URL:
        print("[CodeBee] 公网     %s/?token=%s   ← 任何网络可访问（反代回源已强制校验令牌）"
              % (remote.PUBLIC_URL, tok))
    elif not args.no_public_tunnel and args.host != "127.0.0.1":
        if remote.has_local_creds():
            print("[CodeBee] （检测到已有 Cloudflare 隧道凭据：临时隧道在此类机器上不可用，"
                  "请用 --public-url 配固定域名，或 start-public.bat）")
        elif remote.start_quick_tunnel(args.port, _announce_public):
            print("[CodeBee] 正在建立 Cloudflare 快速隧道（公网地址几秒后打印；"
                  "若打不开说明当前网络不支持，可改用固定域名或 Tailscale）…")
        else:
            print("[CodeBee] （未检测到 cloudflared，跳过公网隧道；"
                  "winget install Cloudflare.cloudflared 后重启即可获得公网地址）")
    atexit.register(remote.stop_quick_tunnel)
    if args.host != "127.0.0.1":
        lan = remote.lan_ip()
        if lan:
            print("[CodeBee] 局域网   http://%s:%d/?token=%s   ← 手机同一 WiFi 直接打开" % (lan, args.port, tok))
        ts = remote.tailscale_ip()
        if ts:
            print("[CodeBee] Tailscale http://%s:%d/?token=%s   ← 外网随时随地访问" % (ts, args.port, tok))
        elif not remote.PUBLIC_URL:
            print("[CodeBee] （未检测到 Tailscale；安装后重启本服务即可获得外网地址）")
        print("[CodeBee] 远程访问受令牌保护；手机打开一次带 token 的地址后会记住。")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:%d" % args.port)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[CodeBee] 已退出")


if __name__ == "__main__":
    main()
