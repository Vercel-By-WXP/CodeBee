# -*- coding: utf-8 -*-
"""Handler 的发布/文件操作 API 方法簇。

从 main.py 拆出（单文件行数治理）；方法以 mixin 形式挂到 Handler，
保持 self 语义与测试对 main 模块属性的打桩语义；旧调用面由 main 再导出。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import pick_dialog


def _main():
    import main
    return main


class HandlerPublishApi(BaseHTTPRequestHandler):
    """发布/文件操作 API 的 mixin（方法由 Handler 继承）。"""

    # 宿主 Handler 提供的辅助（仅供 pyright 可见；运行时由 main.Handler MRO 解析）
    _send: Any
    _json: Any
    _body: Any
    _forwarded_ip: Any

    def _api_reveal(self, kind, res_id):
        """打开/返回资源所在目录：任务=工作目录，运行=记录目录（步骤日志、报告都在里面）。
        路径一律由服务端按 id 推导，不接受客户端传任意路径——本服务监听局域网，
        不能变成远程探测文件系统的口子。open=false 只回路径给前端复制。"""
        if kind == "tasks":
            task = _main().store.get_task(res_id)
            if not task:
                return self._json(404, {"error": "任务不存在"})
            p = Path(task.get("workdir") or "")
        else:
            run = _main().store.get_run(res_id)
            if not run:
                return self._json(404, {"error": "运行记录不存在"})
            p = _main().paths.RUNS_DIR / res_id
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
        task = _main().store.get_task(task_id)
        if not task:
            return self._json(404, {"error": "任务不存在"})
        if op == "git-merge":
            from core import contracts
            contract = contracts.get(task_id)
            if (contract and contract.get("approval_required")) or task.get("approval_required"):
                if not contracts.release_allowed(task_id):
                    return self._json(409, {"error": "任务需要先完成审批，才能合并产物",
                                            "contract": contract})
        if op == "git-merge":
            from core import operations
            operation_id = operations.begin(
                "git:%s:merge" % task_id,
                {"task_id": task_id, "branch": task.get("git_branch") or ""},
                task_id=task_id, metadata={"action": "merge"})
            remote_applied = False
            try:
                ok, err, info = gitmod.merge_task_branch(task.get("workdir"), task)
                if not ok:
                    operations.fail(operation_id, err, metadata={"action": "merge"})
                    return self._json(400, {"error": err})
                remote_applied = True
                _main().store.set_task_git_state(task_id, "merged")
                operations.confirm(operation_id, remote_receipt=(info or {}).get("commit") or "",
                                   metadata={"action": "merge"})
                return self._json(200, {"ok": True, "operation_id": operation_id,
                                        **(info or {})})
            except Exception as exc:
                # The remote merge may have happened before local state
                # persistence failed; keep the operation auditable and
                # prevent a blind retry from duplicating the merge.
                if remote_applied:
                    operations.mark_unknown(operation_id,
                                            "Git 合并可能已完成，但本地状态回写失败：%s" % exc,
                                            metadata={"action": "merge"})
                else:
                    operations.finish_exception(operation_id, exc)
                return self._json(500, {"error": "Git 合并收口失败：%s" % exc,
                                        "operation_id": operation_id})
        body = self._body()
        if not body.get("confirm"):
            return self._json(400, {"error": "丢弃任务分支不可恢复，需要 confirm=true 二次确认"})
        from core import operations
        operation_id = operations.begin(
            "git:%s:discard" % task_id, {"task_id": task_id}, task_id=task_id,
            metadata={"action": "discard"})
        remote_applied = False
        try:
            ok, err = gitmod.discard_task_branch(task.get("workdir"), task)
            if not ok:
                operations.fail(operation_id, err, metadata={"action": "discard"})
                return self._json(400, {"error": err})
            remote_applied = True
            _main().store.set_task_git_state(task_id, "discarded")
            operations.confirm(operation_id, metadata={"action": "discard"})
            return self._json(200, {"ok": True, "operation_id": operation_id})
        except Exception as exc:
            if remote_applied:
                operations.mark_unknown(operation_id,
                                        "Git 丢弃可能已完成，但本地状态回写失败：%s" % exc,
                                        metadata={"action": "discard"})
            else:
                operations.finish_exception(operation_id, exc)
            return self._json(500, {"error": "Git 丢弃收口失败：%s" % exc,
                                    "operation_id": operation_id})

    def _api_book_meta_generate(self, task_id):
        """作品信息一键生成（POST /api/tasks/<id>/book-meta，body: {platform}）。

        后台线程跑（编排者→作者CLI→模板的降级链可能数分钟），请求立即返回；
        前端靠任务 book_meta 状态（SSE 全量状态里带）轮进度。已有 running 时
        幂等拒绝，不重复起线程。生成权不限定首批（2026-09-30 实案：
        start_chapter>1 的独立任务被误拦，用户点首版也撞墙）——任何任务都能
        触发；但开书资料属于「这本书」，素材与落点统一取连载链根任务
        （都是用第一个），续写批次触发的生成整链可见、建书口径不分叉。"""
        from core import bookmeta
        task = _main().store.get_task(task_id)
        if not task:
            return self._json(404, {"error": "任务不存在"})
        body = self._body()
        if not isinstance(body, dict):
            body = {}
        platform = (body.get("platform") or "").strip()
        if platform not in bookmeta.PLATFORMS:
            return self._json(400, {"error": "platform 必须是 fanqie 或 qimao"})
        chain = _main().store.serial_chain_ids(task_id)
        target = (_main().store.get_task(chain[0]) if chain else None) or task
        target_id = str(target.get("id"))
        # 作品信息生成只读取已有素材，再把独立的 book_meta/Markdown 结果写回；
        # 它可以和任务主流程并行。此前这里复用了主任务运行闸门，导致用户在
        # 任务仍在产出章节时无法生成建书资料，前端只能误报“生成失败”。
        cur = ((target.get("book_meta") or {}).get(platform) or {})
        if cur.get("status") == "running":
            return self._json(200, {"ok": True, "already": True})
        if not _main().store.set_book_meta(target_id, platform,
                                   {"status": "running",
                                    "at": time.strftime("%Y-%m-%d %H:%M:%S")}):
            return self._json(404, {"error": "任务不存在"})
        author = bookmeta._resolve_author(target)
        threading.Thread(target=bookmeta.generate_async, daemon=True,
                         name="book-meta-%s" % platform,
                         args=(target_id, platform, author)).start()
        return self._json(200, {"ok": True, "started": True})

    def _api_book_meta_rename(self, task_id):
        """作品信息换名（POST /api/tasks/<id>/book-meta/rename，body: {platform}）。

        只换 book_name 一个字段：整套重生成会覆盖已核对过的分类/标签/简介，
        而用户的痛点只是书名与本站已有作品撞名。同步接口（单次轻模型调用），
        成功才写盘，模型失败时现有数据原样不动。排除名单=全站已用书名
        （books.json 登记 + 各任务 book_meta），从根上防「换出来的还是重名」。
        落点同生成：连载链根任务（2026-09-30 都是用第一个）。"""
        from core import bookmeta
        task = _main().store.get_task(task_id)
        if not task:
            return self._json(404, {"error": "任务不存在"})
        body = self._body()
        if not isinstance(body, dict):
            body = {}
        platform = (body.get("platform") or "").strip()
        if platform not in bookmeta.PLATFORMS:
            return self._json(400, {"error": "platform 必须是 fanqie 或 qimao"})
        chain = _main().store.serial_chain_ids(task_id)
        target = (_main().store.get_task(chain[0]) if chain else None) or task
        target_id = str(target.get("id"))
        if target.get("status") in ("queued", "running"):
            return self._json(400, {"error": "任务正在运行，请等本轮结束后再换名"})
        entry = ((target.get("book_meta") or {}).get(platform) or {})
        if entry.get("status") != "done" or not isinstance(entry.get("data"), dict):
            return self._json(400, {"error": "该平台作品信息尚未生成，没有书名可换"})
        old_name = str((entry["data"] or {}).get("book_name") or "").strip()
        if not old_name:
            return self._json(400, {"error": "现有记录没有书名可换"})
        new_name, err = bookmeta.rename_book(target, platform, old_name,
                                             bookmeta.used_book_names())
        if err:
            return self._json(400, {"error": err})
        data = dict(entry["data"])
        data["book_name"] = new_name
        payload = dict(entry)
        payload["data"] = data
        payload["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        payload["source"] = (str(entry.get("source") or "") +
                             "；换名：《%s》→《%s》" % (old_name, new_name)).lstrip("；")
        if not _main().store.set_book_meta(target_id, platform, payload):
            return self._json(404, {"error": "任务不存在"})
        # 归档 Markdown 同步（工作目录在场才写；source 只进归档不进 book_meta
        # 数据——数据里多的键会让 repair_existing 的幂等比较每次不等）
        try:
            fp = Path(target.get("workdir") or "") / bookmeta.PLATFORMS[platform]["file"]
            if fp.parent.is_dir():
                fp.write_text(bookmeta.render_markdown(
                    target, platform, dict(data, source=payload["source"])),
                    encoding="utf-8")
        except OSError:
            pass
        return self._json(200, {"ok": True, "old": old_name, "new": new_name})

    def _api_publish_platform_op(self, platform, op):
        """平台会话操作：connect 开浏览器等扫码 / disconnect 关 / probe 探测表单。"""
        from core.publish import manager as pub
        body = self._body() or {}
        account = str(body.get("account") or "").strip()
        if op == "connect":
            ok, err = pub.connect(platform, account)
        elif op == "disconnect":
            ok, err = pub.disconnect(platform, account), ""
        else:
            ok, err = pub.probe_form_async(platform, account)
        if not ok:
            return self._json(400, {"error": err or "操作失败"})
        return self._json(200, {"ok": True})

    def _api_publish_task_op(self, task_id, op):
        """发布动作：create-book（按作品信息建书）/ chapter（发一章）。

        章节文件必须在任务工作目录内（防穿越，同 /api/dir/file 口径）；
        建书、发章和定时发布均走无人值守提交；草稿使用独立接口。"""
        from pathlib import Path as _P
        from core.publish import manager as pub
        task = _main().store.get_task(task_id)
        if not task:
            return self._json(404, {"error": "任务不存在"})
        body = self._body() or {}
        platform = (body.get("platform") or "").strip()
        if platform not in ("fanqie", "qimao"):
            return self._json(400, {"error": "platform 必须是 fanqie 或 qimao"})
        # Publishing actions are unattended; draft-only uses publish-all's
        # separate as_draft option and never reaches this endpoint.
        auto_submit = True
        force = bool(body.get("force"))
        force_confirmed = bool(body.get("force_confirmed"))
        force_reason = str(body.get("force_reason") or "").strip()
        if op == "create-book":
            kwargs = ({"force": force, "force_confirmed": force_confirmed,
                       "force_reason": force_reason}
                      if force or force_confirmed or force_reason else {})
            ok, err = pub.create_book_async(task_id, platform, auto_submit,
                                            account=str(body.get("account") or "").strip(),
                                            **kwargs)
        elif op == "auto-publish":
            # 定时发布配置（P2.5）：enabled=false 也落（保留 time 供再开）；
            # 校验/归一在 auto.norm_auto_publish，语义见 auto.py 头注
            from core.publish import auto as pub_auto
            body["platform"] = platform
            # Scheduled publishing is unattended. Persist auto-submit so the
            # automation tick always resumes the release path.
            body["auto_submit"] = True
            if body.get("enabled"):
                ap, ap_err = pub_auto.norm_auto_publish(body)
                if not ap:
                    return self._json(400, {"error": ap_err})
            else:
                ap, ap_err = pub_auto.norm_auto_publish(body)
                if not ap:
                    return self._json(400, {"error": ap_err})
                ap["enabled"] = False
            if ap.get("enabled"):
                from core import contracts
                if not contracts.publish_allowed(task_id, unattended=True):
                    return self._json(409, {
                        "error": "任务需要先完成审批，才能启用自动发布",
                        "contract": contracts.get(task_id),
                    })
            ok, err = _main().store.set_auto_publish(task_id, ap), ""
            if ok:
                return self._json(200, {"ok": True, "auto_publish": ap})
        else:
            f = str(body.get("file") or "").strip()
            if not f:
                return self._json(400, {"error": "file 必填（章节文件路径）"})
            wd = task.get("workdir") or ""
            try:
                fp = _P(f) if _P(f).is_absolute() else _P(wd) / f
                fp = fp.resolve()
                fp.relative_to(_P(wd).resolve())
            except (OSError, ValueError):
                return self._json(400, {"error": "章节文件必须在任务工作目录内"})
            # Run one selected chapter through the automatic batch worker. It
            # performs a fresh platform reconciliation before submitting and
            # can reopen a saved browser profile if the service restarted or
            # CDP disconnected. Doing sync inline here made this one-chapter
            # UI action the only publishing path that required a manual retry.
            chapter_no, _title, _body, chapter_error = pub.read_chapter(str(fp))
            if chapter_error:
                return self._json(400, {"error": chapter_error})
            from core.publish import auto as pub_auto
            kwargs = ({"force": force, "force_confirmed": force_confirmed,
                       "force_reason": force_reason}
                      if force or force_confirmed or force_reason else {})
            if body.get("scheduled"):
                kwargs["scheduled"] = True
            # Every single-chapter request uses the automatic batch worker,
            # including legacy files whose chapter number appears only in the
            # document heading. This keeps platform reconciliation, saved
            # session recovery, retries, optional book preparation, and exact
            # file matching consistent with bulk and scheduled publishing.
            relative_file = fp.relative_to(_P(wd).resolve()).as_posix()
            ok, err = pub_auto.publish_pending_async(
                task_id, platform, auto_submit=True,
                only=[int(chapter_no)], auto_prepare_book=True,
                _only_file=relative_file, **kwargs)
        if not ok:
            payload = {"error": err or "操作失败"}
            if str(err or "").startswith("质量门禁拦截："):
                payload["quality_gate"] = {"status": "blocked", "message": err}
            return self._json(400, payload)
        return self._json(200, {"ok": True, "started": True})

    def _api_publish_register_book(self, task_id):
        """人工登记平台已有作品（建书流程没走通/手工建书后的补账口）。"""
        from core.publish import manager as pub
        if not _main().store.get_task(task_id):
            return self._json(404, {"error": "任务不存在"})
        body = self._body()
        if not isinstance(body, dict):
            body = {}
        ok, err = pub.register_book(task_id, (body.get("platform") or "").strip(),
                                    str(body.get("title") or ""),
                                     book_id=str(body.get("book_id") or ""),
                                     account=(str(body["account"]).strip()
                                              if "account" in body else None))
        if not ok:
            return self._json(400, {"error": err or "登记失败"})
        return self._json(200, {"ok": True})

    def _api_publish_unbind_book(self, task_id):
        """解除本地作品绑定（POST /api/publish/task/<id>/unbind-book，
        body: {platform}）。只删 books.json 登记，不碰平台线上作品；
        用于登记错链/书已废后清位重建——此前只能手工翻 books.json。"""
        from core.publish import manager as pub
        if not _main().store.get_task(task_id):
            return self._json(404, {"error": "任务不存在"})
        body = self._body() or {}
        ok, err = pub.unbind_book(task_id, (body.get("platform") or "").strip())
        if not ok:
            return self._json(400, {"error": err or "解绑失败"})
        return self._json(200, {"ok": True})

    def _api_publish_auto(self, task_id):
        """批量发布全部待发章节（publish/auto.py）。

        护栏（每日上限/连败退避/幂等/单飞）在 auto 层，每章发起前复查；
        auto_submit 默认走批量自动提交；失败时由发布链降级为定时发布并继续。
        as_draft=true 走「全部发草稿」：逐章存草稿不上线、不受质量闸拦、
        无需人工确认连跑到清空（2026-10-09）；草稿与直发互斥，草稿优先。"""
        from core.publish import auto as pub_auto
        if not _main().store.get_task(task_id):
            return self._json(404, {"error": "任务不存在"})
        body = self._body() or {}
        platform = (body.get("platform") or "").strip()
        if platform not in ("fanqie", "qimao"):
            return self._json(400, {"error": "platform 必须是 fanqie 或 qimao"})
        force = bool(body.get("force"))
        force_confirmed = bool(body.get("force_confirmed"))
        force_reason = str(body.get("force_reason") or "").strip()
        only = body.get("chapters")            # 批量选择发布：章号数组（可省=全部待发）
        kwargs = {"only": only if isinstance(only, list) else None,
                  "as_draft": bool(body.get("as_draft"))}
        if body.get("scheduled"):
            kwargs["scheduled"] = True
        if force or force_confirmed or force_reason:
            kwargs.update(force=force, force_confirmed=force_confirmed,
                          force_reason=force_reason)
        auto_submit = True
        ok, err = pub_auto.publish_pending_async(
            task_id, platform, auto_submit=auto_submit,
            auto_prepare_book=bool(body.get("auto_prepare_book", True)),
            **kwargs)
        if not ok:
            payload = {"error": err or "操作失败"}
            if str(err or "").startswith("质量门禁拦截："):
                payload["quality_gate"] = {"status": "blocked", "message": err}
            return self._json(400, payload)
        return self._json(200, {"ok": True, "started": True})

    def _api_task_side(self, task_id):
        """任务检查器（右缘停靠列）的轻量聚合端点。聚合逻辑在 store.task_side
        （可单测、单实例状态）；这里只做 404 转换。"""
        d = _main().store.task_side(task_id)
        return self._json(404, {"error": "任务不存在"}) if d is None else self._json(200, d)

    # ------------------------------------------------------ GIT 工作台（详情页版本页签）
    # 与 /api/git/info 不同：workdir 由任务 id 服务端推导，不接受客户端传任意路径，
    # 所以不必限本机——局域网端（手机）打开详情页同样能看变更、做日常 git 操作。

    def _api_git_wb(self, task_id):
        from core import gitmod
        task = _main().store.get_task(task_id)
        if not task:
            return self._json(404, {"error": "任务不存在"})
        d: dict = gitmod.workbench_status(str(task.get("workdir") or ""))
        d["isolation"] = {"state": task.get("git_state") or "",
                          "rev": task.get("git_rev") or "",
                          "branch": gitmod.branch_name(task_id)}
        d["task"] = {"id": task_id, "status": task.get("status") or "",
                     "title": task.get("title") or ""}
        return self._json(200, d)

    def _api_git_wb_diff(self, task_id):
        from core import gitmod
        task = _main().store.get_task(task_id)
        if not task:
            return self._json(404, {"error": "任务不存在"})
        qs = parse_qs(urlparse(self.path).query)
        d = gitmod.file_diff(str(task.get("workdir") or ""), (qs.get("path") or [""])[0])
        if d.get("error"):
            return self._json(400, {"error": d["error"]})
        return self._json(200, d)

    def _api_git_wb_op(self, task_id):
        from core import gitmod
        task = _main().store.get_task(task_id)
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

    def _api_pet_state(self):
        """桌面蜜蜂（app/pet.py 子进程）的喂食端点：任务近况最小集 + 相关设置。

        数据与 /api/runs 同源（latest_run_by_task 全量扫描，内存操作），但只裁
        出桌宠需要的字段；每任务一条（封顶 200），蜜蜂在进程内自行差分出
        「刚完工/刚出事」。设置随响应回传，蜜蜂轮询到 pet_enabled=false 自行
        退出——关闭桌宠不需要专门的信号通道。
        """
        global _BOOT_TS
        st = _main().settings.load()
        rows = []
        n_run = n_q = 0
        latest = _main().store.latest_run_by_task()
        for tid in sorted(latest.keys(), reverse=True)[:200]:
            run = latest[tid]
            task = _main().store.get_task(tid) or {}
            rs = str(run.get("status") or "")
            if rs == "running":
                n_run += 1
            elif rs == "queued":
                n_q += 1
            steps = run.get("steps") or []
            cur = next((s for s in steps if s.get("status") == "running"), None)
            rows.append({
                "id": tid,
                "title": str(task.get("title") or tid),
                "type": str(task.get("type") or ""),
                "run_status": rs,
                "steps_done": sum(1 for s in steps if s.get("status") == "done"),
                "steps_total": len(steps),
                "step_current": str((cur.get("summary") or cur.get("agent_label")
                                     or "") if cur else ""),
                "error": str(run.get("error") or "")[:160],
            })
        try:
            from core import wxdigest as _wxd   # 群摘要喂食：未读数+最新一条（失败兜底零值）
            dg = _wxd.pet_digest()
        except Exception:
            dg = {"unseen": 0, "latest": None}
        return self._json(200, {
            "ok": True, "ts": int(time.time()), "port": _main().PORT,
            # boot：本服务进程的启动时间戳。蜜蜂记住首次见到的值，值变了说明
            # 服务换了个进程（升级重启）——旧蜜蜂自行退场让新服务 spawn 新蜜蜂
            # （旧形象常驻的根因：同端口轮询永远正常，旧进程从不自离）。
            "boot": _main()._BOOT_TS,
            "settings": {"pet_enabled": bool(st.get("pet_enabled", False)),
                         "pet_mode": str(st.get("pet_mode") or "always"),
                         "pet_skin": str(st.get("pet_skin") or "plush")},
            "workers": {"running": n_run, "queued": n_q},
            "tasks": rows,
            "digest": dg,
        })

    def _api_pick_folder(self):
        """系统原生「选择文件夹」对话框（工作目录「选择…」/点输入框用）。仅限本机：
        对话框弹在服务所在机器上，远端触发等于替别人开窗。pick_dialog.ask_directory
        拉起子进程弹真窗口，选中绝对路径直接回给前端回填；用户取消回空 path。
        机器没有 tkinter 时 fallback=true，前端回落网页目录弹框，不失去选目录能力。"""
        ip, fw = self._forwarded_ip()
        if ip not in ("127.0.0.1", "::1") or fw:
            return self._json(403, {"error": "目录选择仅限本机使用，请手动输入路径"})
        body = self._body()
        if not _main()._PICK_LOCK.acquire(blocking=False):
            return self._json(200, {"path": "", "busy": True})
        try:
            path, err, fb = pick_dialog.ask_directory(str(body.get("initial") or ""),
                                                      str(body.get("title") or "选择文件夹"))
        finally:
            _main()._PICK_LOCK.release()
        if err:
            return self._json(200, {"path": "", "fallback": fb, "error": err})
        return self._json(200, {"path": path or ""})

    def _api_pick_file(self):
        """系统原生「选择文件」对话框（导入备份包用）。仅限本机，同 _api_pick_folder；
        没有 tkinter 时 fallback=true（本场景没有网页回落，前端提示手输路径）。"""
        ip, fw = self._forwarded_ip()
        if ip not in ("127.0.0.1", "::1") or fw:
            return self._json(403, {"error": "文件选择仅限本机使用，请手动输入路径"})
        body = self._body()
        if not _main()._PICK_LOCK.acquire(blocking=False):
            return self._json(200, {"path": "", "busy": True})
        try:
            path, err, fb = pick_dialog.ask_file(str(body.get("initial") or ""),
                                                 str(body.get("title") or "选择文件"))
        finally:
            _main()._PICK_LOCK.release()
        if err:
            return self._json(200, {"path": "", "fallback": fb, "error": err})
        return self._json(200, {"path": path or ""})

    def _api_dir_scan(self):
        """侧栏文件夹「查看文件」：递归列出该工作目录下的全部文件（左侧文件页树形展示）。

        只读；仅限本机请求（同 /api/browse 的信息泄露口径）。name 为相对路径
        （"/" 分隔，如 "docs/readme.md"），前端按目录层级渲染成树；跳过
        .git / node_modules 等噪音目录；不设文件数上限，全量列出。
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
        try:
            for cur, dirs, names in os.walk(str(p)):
                # os.walk 默认不跟随目录符号链接：环形目录不会死循环
                dirs[:] = [d for d in dirs if d not in _main()._SKIP_DIRS_SHARE]
                for n in names:
                    try:
                        st = (Path(cur) / n).stat()
                    except OSError:
                        continue   # 断链/权限：跳过单个文件
                    files.append({"name": (Path(cur) / n).relative_to(p).as_posix(),
                                  "size": st.st_size, "mtime": int(st.st_mtime)})
        except (PermissionError, OSError):
            return self._json(403, {"error": "无权限读取该目录"})
        files.sort(key=lambda f: (-f["mtime"], f["name"].lower()))
        return self._json(200, {"path": str(p), "files": files})

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
        ctype = _main().MIME.get(ext, "application/octet-stream")
        if ext in (".md", ".txt", ".log", ".csv", ".yml", ".yaml", ".json", ".ini", ".toml",
                   ".py", ".js", ".ts", ".html", ".css", ".svg"):
            ctype = "text/plain; charset=utf-8"
        if ctype.startswith("text/"):
            data = _main()._utf8_bytes(data)
            if re.fullmatch(r"chapter-\d{1,4}(?:-v\d+)?\.(?:md|txt)",
                            fp.name, re.I):
                from core import chaptersafe
                data = chaptersafe.strip_internal_blocks(
                    data.decode("utf-8", "replace")).encode("utf-8")
        # mtime 供前端「编辑保存」做冲突检测：文件被任务/外部改动后拒绝覆盖（409）
        return self._send(200, data, ctype,
                          {"X-Tutti-Mtime": str(int(fp.stat().st_mtime))})

    def _api_dir_save(self):
        """「查看文件」弹窗的编辑保存：{dir, name, content, mtime} → 覆写该文件。

        仅限本机（同 /api/dir/file 口径，写操作还过全局控制权门槛）；name 必须
        相对且解析后落在 dir 内（与读取端同一套防穿越判定）；内容按 UTF-8 落盘
        （读取端本就把文本按 UTF-8 下发，GBK 老文件保存一次即归一化为 UTF-8）。
        mtime 是打开预览时响应头 X-Tutti-Mtime 带回的旧值：不等 → 文件已被
        任务/外部改动过，回 409 拒绝覆盖，让用户重开预览确认后再改。
        """
        ip, fw = self._forwarded_ip()
        if ip not in ("127.0.0.1", "::1") or fw:
            return self._json(403, {"error": "文件保存仅限本机使用"})
        body = self._body()
        raw_dir = (body.get("dir") or "").strip()
        raw_name = (body.get("name") or "").strip()
        content = body.get("content")
        if not raw_dir or not raw_name:
            return self._json(400, {"error": "缺少 dir/name"})
        if not isinstance(content, str):
            return self._json(400, {"error": "content 必须是文本"})
        if len(content.encode("utf-8")) > 4 * 1024 * 1024:
            return self._json(400, {"error": "内容超过 4MB，请在编辑器里改大文件"})
        try:
            base = Path(raw_dir).expanduser().resolve()
        except Exception:
            return self._json(400, {"error": "非法路径"})
        if not base.is_dir():
            return self._json(404, {"error": "目录不存在: %s" % base})
        name = raw_name.replace("\\", "/")
        parts = name.split("/")
        if not name or not all(p and p != ".." for p in parts):
            return self._json(400, {"error": "非法文件名"})
        fp = (base / name).resolve()
        try:
            fp.relative_to(base)
        except ValueError:
            return self._json(403, {"error": "越界"})
        if fp.exists() and not fp.is_file():
            return self._json(400, {"error": "同名路径不是文件"})
        old_mtime = body.get("mtime")
        if fp.exists() and old_mtime is not None:
            try:
                if abs(int(fp.stat().st_mtime) - int(old_mtime)) > 1:
                    return self._json(409, {"error": "文件已被外部修改，请关闭弹窗重新打开确认后再编辑"})
            except OSError:
                pass
        try:
            if re.fullmatch(r"chapter-\d{1,4}(?:-v\d+)?\.(?:md|txt)",
                            fp.name, re.I):
                from core import chaptersafe
                content = chaptersafe.strip_internal_blocks(content)
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_bytes(content.encode("utf-8"))
        except OSError:
            return self._json(403, {"error": "无法写入（只读/被占用？）"})
        return self._json(200, {"ok": True, "size": fp.stat().st_size,
                                "mtime": int(fp.stat().st_mtime)})

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

    def _api_code_map(self):
        """代码认知图谱：GET=读状态/条目，POST=生成或部分刷新。

        地图落在工作目录的 .codebee/ 下，随仓库提交；只接受本机请求，避免把
        任意路径的结构索引暴露给局域网。POST 走全局鉴权与同源门槛。
        """
        ip, fw = self._forwarded_ip()
        if ip not in ("127.0.0.1", "::1") or fw:
            return self._json(403, {"error": "代码地图仅限本机使用"})
        from core import code_map
        if self.command == "POST":
            body = self._body() or {}
            raw = str(body.get("workdir") or "").strip()
            if not raw:
                return self._json(400, {"error": "缺少 workdir"})
            paths = body.get("paths") or None
            if isinstance(paths, str):
                paths = [x for x in re.split(r"[\s,]+", paths) if x]
            if paths is not None and not isinstance(paths, list):
                return self._json(400, {"error": "paths 必须是数组"})
            try:
                return self._json(200, code_map.build(raw, changed_paths=paths or None))
            except (OSError, ValueError) as exc:
                return self._json(400, {"error": str(exc)[:300]})
        qs = parse_qs(urlparse(self.path).query)
        raw = (qs.get("workdir") or [""])[0].strip()
        if not raw:
            return self._json(400, {"error": "缺少 workdir"})
        try:
            result = code_map.status(raw, deep=(qs.get("deep") or [""])[0] in ("1", "true", "yes"))
            query = (qs.get("query") or [""])[0].strip()
            prefix = (qs.get("path") or [""])[0].strip()
            if query or prefix:
                result["entries"] = code_map.read_entries(
                    raw, query=query, prefix=prefix,
                    limit=(qs.get("limit") or ["40"])[0])
        except (OSError, ValueError) as exc:
            return self._json(400, {"error": str(exc)[:300]})
        return self._json(200, result)
