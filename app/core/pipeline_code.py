# -*- coding: utf-8 -*-
"""pipeline.py 的 code 子模块。

从 pipeline.py 拆出（单文件行数治理）；模块级状态与辅助函数仍在 pipeline.py，全部经 _pl() 解析以保持测试对 pipeline 属性的打桩语义；旧调用面由 pipeline 再导出。
"""
from __future__ import annotations

import os


def _pl():
    from . import pipeline
    return pipeline


def _fast_path_flag(task, mode, difficulty, user_context_len):
    """低风险短链判定 + 席位覆写（Raven 四席借鉴）。

    启发式：非 plan_mode 且（fast 档，或 auto 档简单任务、无附件、
    背景不长）。席位 planning.fast_path：True=强制短链（用户显式
    plan_mode 仍最高优先）/False=禁用短链走全规划；缺省=启发式现状。"""
    fp = (not task.get("plan_mode") and (mode == "fast" or
          (mode == "auto" and difficulty == "easy"
           and not task.get("attachments")
           and user_context_len < 600)))
    try:
        seat = ((task.get("seats") or {}).get("planning") or {}).get("fast_path")
    except Exception:
        seat = None
    if seat is True:
        return not task.get("plan_mode")
    if seat is False:
        return False
    return fp


def _run_code(run, task, agents, ev, stats, mode):
    run_id = run["id"]
    workdir = task["workdir"]
    repairs = []       # 每轮 {round, kind, verify_pass, review_pass, issues}
    impl_sid = [""]    # §07 T1.1：最后一次实现的 CLI 会话 id（fix 轮复用）
    route = {}
    switched = False
    # 换将守卫：死链补位在 _run_step 内挑人，看不到换将循环的已知死者与已封 403
    # 上游，曾把「pi → claude-code」补位补回 pi 本人（同一条网关撞两次 403）。
    # 两个容器由下面的换将循环就地填，_run_one 每次调用现取。
    swap_guard = {"exclude": set(), "blocked": []}
    resume_ctx = _pl()._valid_resume(task, agents)

    # ---- 难度（影响模型选择）：用户指定 > 启发式 > 规划器判定
    difficulty = task.get("difficulty") or "auto"
    explicit = difficulty in ("easy", "hard")
    if mode == "fast":
        difficulty, explicit = "easy", True
    elif mode == "expert":
        difficulty, explicit = "hard", True
    if not explicit:
        difficulty = _pl().modelhub.classify_difficulty(task["goal"], task.get("verify_command"))

    # ---- 路由
    if resume_ctx is not None:
        impl = resume_ctx["agent"]
        route["implementer"] = resume_ctx["note"]
    elif mode == "manual":
        impl, _ = _pl()._pick_implementer(agents, task.get("implementer"))
    else:
        impl, route["implementer"] = _pl().router.pick(agents, "implement", "code", stats)
    if impl is None:
        _pl().store.update_run(run_id, expected_status="running", status="failed",
                         error="没有可用智能体", ended_at=_pl()._now())
        return

    # 项目记忆既给规划器，也给快速路径的实现者；不能因省掉独立规划而漏掉。
    user_context_len = len(task.get("context") or "")
    project_memory = _pl()._read_project_memory(
        workdir, query=task.get("goal") or task.get("title") or "")
    if project_memory:
        task = dict(task, context=(task.get("context") or "") + "\n\n" + project_memory)

    # 断点简报（B）：重试继承上一遍失败/中断 run 的现场，注入规划器与实现者
    # 共用的 __CONTEXT__——「走到哪、改了什么、还剩什么」不再从零重建上下文。
    _handoff = run.get("handoff") or {}
    _brief = _pl()._handoff_brief(_handoff)
    if _brief:
        task = dict(task, context=(task.get("context") or "") + "\n\n" + _brief)
    # 断点会话（A）：上一遍实现者的 CLI 会话 id，implement_all 里按人复用。
    _inh_session = run.get("inherit_session") or {}

    def _keep_sid(agent_row, agent_b, r):
        """记录最后一次实现的会话 id：run 内 fix 轮复用 + 落盘 run 供重试跨 run 续会话。"""
        sid = _pl()._resume_sid(agent_b, r.get("sid"))
        if sid:
            impl_sid[0] = sid
            try:
                _pl().store.update_run(run_id, impl_session={
                    "agent": agent_row.get("id"), "session": sid,
                    "workdir": workdir})
            except Exception:
                pass
        return sid

    # ---- 规划。低风险短任务直接把单步计划作为目标，省掉一次独立模型调用；
    # 附件或较长的用户背景仍先规划，避免快速路径漏读约束。
    fast_path = _fast_path_flag(task, mode, difficulty, user_context_len)
    planned = False
    if (task.get("plan_mode") or mode in ("auto", "expert")) and not fast_path:
        _pl()._wait_gate(run_id, ev)
        plan_step, plan_log = _pl().store.add_step(run_id, "plan", impl["id"], impl.get("label"),
                                             note=route.get("implementer", ""))
        plan = _pl()._planner_call(
            _pl().planner.make_code_plan, _pl()._steered_task(run_id, task),
            _pl().modelhub.bind_agent(impl, difficulty),
            _pl()._resume_workdir(resume_ctx, workdir), ev,
            resume=resume_ctx["session"] if resume_ctx else None,
            log_path=str(plan_log) if plan_log else None,
            deadline=_pl()._run_deadline(run_id), run_id=run_id)
        plan_step = plan_step or {}
        _pl()._ensure_budget(run_id)
        # 规划器判定优先于启发式（仅当用户未显式指定难度）
        if not explicit and plan.get("difficulty") in ("easy", "hard"):
            difficulty = plan["difficulty"]
        _pl().store.finish_step(run_id, plan_step["n"], "done",
                          summary="计划来源 %s（难度 %s）：%s" % (
                              plan["source"], difficulty,
                              "；".join(s["title"] for s in plan["steps"])[:160]),
                          duration_s=0.1 if impl.get("mode") == "mock" else None)
        planned = True
    elif fast_path:
        plan = {
            "source": "dynamic-fast-path",
            "difficulty": "easy",
            "steps": [{"title": "直接实现并自检", "detail": task["goal"]}],
            "workflow": {
                "review_required": not bool(task.get("verify_command")),
                "max_repair_rounds": 1,
                "allow_switch": False,
            },
        }
    else:
        plan = {"source": "manual", "steps": [{"title": "实现任务", "detail": task["goal"]}]}
    workflow = _pl().task_compile.code_workflow(
        task, difficulty, plan=plan, mode=mode, planned=planned)
    _pl().store.update_run(run_id, plan=plan, difficulty=difficulty, workflow=workflow)
    subtasks = plan["steps"]
    if not _pl()._human_intervention_gate(run_id, task, "decompose", ev):
        return
    # 计划落盘（planning-with-files）：磁盘上的计划与 spec/evidence 同居任务档案
    _pl()._write_task_plan(task, workdir, plan)

    # ---- 评审者（有会话延续时评审者仍用新鲜上下文，避免偏见）
    if not workflow["review_required"]:
        reviewer = None
        route["reviewer"] = workflow["reason"]
    elif mode in ("auto", "expert"):
        reviewer, route["reviewer"] = _pl().router.pick_reviewer(
            agents, impl, "code", stats, difficulty=difficulty)
    else:
        reviewer, route["reviewer"] = _pl()._pick_reviewer_legacy(agents, impl)
    _pl()._record_actual_route(run_id, task, agents, stats, impl, reviewer=reviewer,
                         implement_reason=route.get("implementer", ""),
                         review_reason=route.get("reviewer", ""),
                         direct=not workflow["review_required"])
    _pl().store.update_run(run_id, route=route)

    def implement_all(impl_agent, prefix_note):
        nonlocal reviewer
        # 会话延续只对原实现者有效；换将后新智能体没有该会话，必须丢弃。
        # 优先级：任务级 resume（用户显式指定）> 断点继承会话（A：跨 run 续跑，
        # 仅同一实现者 + 同一工作目录 + 该 CLI 支持续会话时复用）
        inh_sid = ""
        if (not resume_ctx and _inh_session.get("session")
                and _inh_session.get("agent") == impl_agent["id"]
                and _pl()._same_workdir(_inh_session.get("workdir") or "", workdir)
                and _pl()._resume_sid(impl_agent, _inh_session.get("session"))):
            inh_sid = _inh_session["session"]
        use_resume = (resume_ctx["session"]
                      if (resume_ctx and impl_agent["id"] == resume_ctx["agent"]["id"])
                      else (inh_sid or None))
        # 续会话时 CLI 要在会话所属项目目录下启动，否则定位不到会话
        step_wd = _pl()._resume_workdir(resume_ctx, workdir) if (resume_ctx and use_resume) else workdir

        def _run_one(agt):
            """用指定智能体跑全部子任务；返回 (ok, 最后一次 res)。"""
            agt_b = _pl().modelhub.bind_agent(agt, difficulty)
            att_imgs = _pl()._task_images(task, workdir)  # 图片附件供 codex 原生 -i 直读
            # §07 T3.1 FrugalGPT 级联（默认关）：easy 任务把链按 tier 升序重排，
            # 便宜模型先跑；质量闸门不过走既有 repair/换将轮，等效"贵模型兜底"。
            if difficulty == "easy":
                try:
                    from .settings_schema import get as ss_get, register_default_namespaces
                    register_default_namespaces()
                    if ss_get("cascade", "enabled"):
                        from . import capability
                        mh_data = _pl().modelhub._load()
                        agt_b = capability.cascade_reorder(
                            agt_b, capability.make_tier_lookup(_pl().modelhub.providers()),
                            providers=_pl().modelhub.providers(),
                            pricing=mh_data.get("pricing") or {},
                            difficulty=difficulty, task_type=task.get("type") or "code",
                            role="implement")
                except Exception:
                    pass
            try:
                execution_steps = _pl().task_graph.topological_order(subtasks)
            except _pl().task_graph.DependencyError as exc:
                _pl().store.update_run(run_id, expected_status="running", status="failed",
                                 error="任务依赖无效：%s" % exc, ended_at=_pl()._now())
                return False, {"ok": False, "error": "任务依赖无效：%s" % exc}
            original_indexes = {
                str(item.get("id") or item.get("title") or ""): n
                for n, item in enumerate(subtasks)
            }
            # A shared workspace must remain serial, but dependency-aware order
            # is still enforced here. Independent steps can be parallelized by
            # future isolated-worktree executors without changing this contract.
            for i, sub in enumerate(execution_steps):
                original_index = original_indexes.get(
                    str(sub.get("id") or sub.get("title") or ""), i)
                # 子任务进度注入（planning-with-files 的每轮注入计划头）：多子任务时
                # 让实现者知道全局位置与已完成项，防止长链目标漂移；单子任务零噪音
                prog = ""
                if len(subtasks) > 1:
                    done_titles = "、".join(
                        (s.get("title") or "")[:40] for s in subtasks[:i]) or "（无）"
                    prog = ("\n\n## 计划进度（第 %d/%d 项）\n已完成：%s。当前接着做下面这一项，"
                            "不要重做已完成的。" % (i + 1, len(subtasks), done_titles))
                prompt = _pl()._RUN_CONSTITUTION + _pl()._render_template_values(_pl().CODE_IMPL_PROMPT, {
                    "__GOAL__": task["goal"],
                    "__SUBTASK__": sub["detail"] if sub["detail"] else sub["title"],
                    "__FILES__": _pl()._files_hint(sub),
                    "__CONTEXT__": task.get("context") or "（无）",
                    "__VERIFY_HINT__": _pl()._verify_hint(task),
                }) + prog
                role = "implement" if len(subtasks) == 1 else "implement-%d/%d" % (i + 1, len(subtasks))
                _pl().mark_task_plan(workdir, original_index + 1, "run")   # 活计划：起跑即标进行中
                res = _pl()._run_step(run_id, role, agt_b, prompt, step_wd,
                                readonly=False, ev=ev,
                                note=prefix_note if i == 0 else "",
                                resume=use_resume, images=att_imgs,
                                require_tools=True, swap_guard=swap_guard)
                _keep_sid(agt, agt_b, res)
                # 断点继承会话失效兜底（A）：会话可能已被 CLI 清理或跨机不可续。
                # 认证/配额类死因换会话也没用，不重试；其余丢弃继承会话原样重跑一次。
                if (not res["ok"] and inh_sid and i == 0 and use_resume == inh_sid
                        and not (res.get("raw") or {}).get("cancelled")
                        and not _pl().runner._auth_error(res.get("error") or "")
                        and not _pl().runner._quota_error(res.get("error") or "")):
                    res = _pl()._run_step(run_id, role, agt_b, prompt, step_wd,
                                    readonly=False, ev=ev,
                                    note=((prefix_note + "；") if prefix_note else "")
                                    + "继承会话续跑失败，已丢弃旧会话全新执行",
                                    resume=None, images=att_imgs,
                                    require_tools=True, swap_guard=swap_guard)
                    _keep_sid(agt, agt_b, res)
                if agt.get("mode") == "mock" and res["ok"]:
                    try:
                        mock_path = os.path.abspath(os.path.join(workdir, "mock-impl.txt"))
                        if _pl()._inside(workdir, mock_path):
                            with open(mock_path, "a", encoding="utf-8") as f:
                                f.write("%s mock 实现：%s / %s\n" % (_pl()._now(), task["title"], sub["title"]))
                    except Exception:
                        pass
                if not res["ok"]:
                    _pl().mark_task_plan(workdir, original_index + 1, "fail")
                    return False, res
                _pl().mark_task_plan(workdir, original_index + 1, "done")
            return True, res

        ok, res = _run_one(impl_agent)
        if ok:
            return True
        if _pl().runner._auth_error(res.get("error") or ""):
            auth_err = ("实现步骤失败（上游拒绝认证；已停止自动换 CLI，避免隐式切换供应商）。"
                        "请检查当前绑定的 Key、账号权限和端点；需要认证降级时，"
                        "请在该 CLI 的绑定链中显式配置不同凭据。错误：%s"
                        % (res.get("error") or "")[:500])
            _pl().store.update_run(run_id, expected_status="running", status="failed",
                             error=auth_err, ended_at=_pl()._now())
            return False
        # 单路实现失败：Best-of-N 赛马兜底（借鉴 orca worktree 择优）——多路并行
        # 各自 worktree 隔离实现+验证，胜者 diff 回主工作区；失败回落走换将。
        # 会话延续在场时跳过（N 路共用同一 CLI 会话会互相践踏，与连载同理）
        if (mode == "auto" and impl_agent.get("mode") == "real"
                and max(1, min(3, int(task.get("best_of") or 1))) >= 2
                and resume_ctx is None and not inh_sid
                and _pl()._code_bestof(run, task, impl_agent, difficulty, ev)):
            return True
        # 实现步失败不立刻判死：2026-09-16 实测配额烧干时 5 连跑全在同一条 CLI 上
        # 失败收场，而健康的 opencode 一直在旁观望——跨 CLI 换将重试
        # （mode=manual 尊重用户指定，不换）。2026-09-22 续修：配额/限流类死亡
        # 是确定性秒死，允许继续走查候选名单（同上游让位异上游）；明确的 403
        # 权限拒绝只走不同上游，其余死因（超时等）仍只换一次。
        if mode == "auto" and impl_agent.get("mode") == "real":
            tried = {impl_agent["id"], "mock-a", "mock-b"}
            # 本场停用的 CLI 不进换将候选：账号级死错（欠费/凭据缺失）的
            # 补位只是把失败换个人重演（2026-10-15 案）
            try:
                from . import bench as _bench
                tried |= _bench.benched_ids(run_id)
            except Exception:
                pass
            dead_ids = {impl_agent["id"]}  # 实现步已失败的原实现者，属已知死候选
            notes = []
            dead_ups = []  # 配额死亡候选的上游：先异上游，无异上游时仍可捡回
            blocked_ups = swap_guard["blocked"]  # 403 上游拒绝：同 host 不再尝试
            swap_guard["exclude"] = tried  # 就地 add，补位挑人时看到的就是这一份
            # 原实现者若死于配额，其上游（网关/账号）也一并判入死池——否则第一棒
            # 换将不知道同网关候选已死，评审者重选更会把「与死者同上游」的候选
            # 继续当活口，评审撞同一条失效网关白烧一轮后误报「评审器故障」。
            if _pl().runner._quota_error(res.get("error") or ""):
                ups0 = _pl().router.agent_upstreams(
                    impl_agent["id"], difficulty=difficulty,
                    task_type="code", role="implement")
                if ups0:
                    dead_ups.append(ups0)
            if _pl()._has_forbidden_result(res):
                ups0 = _pl().router.agent_upstreams(
                    impl_agent["id"], difficulty=difficulty,
                    task_type="code", role="implement")
                blocked_ups.extend(_pl()._forbidden_upstream_sets(res, ups0))
            prev_id = impl_agent["id"]
            other = None
            while True:
                other, other_reason = _pl().router.pick_switch_candidate(
                    agents, "implement", "code", stats,
                    exclude=tried, dead_upstreams=dead_ups,
                    difficulty=difficulty, blocked_upstreams=blocked_ups,
                    failed_cli=next((agent for agent in agents
                                     if agent.get("id") == prev_id), impl_agent),
                    error=res)
                if other is None or other.get("mode") != "real":
                    other = None
                    break
                note = "实现步失败自动换将 %s → %s：%s。失败原因：%s" % (
                    prev_id, other["id"], other_reason,
                    (res.get("error") or "")[:200])
                ok2, res = _run_one(other)
                if ok2:
                    route_note = "；".join(notes + [note])
                    # 换将成功：预先选定的评审者可能在实现步走查时被证伪为死链
                    # （配额/403 上游死亡），且死者同上游（同网关）的候选同样不可信。
                    # 按「排除本轮已知死者 + 同上游候选」的池重选评审者，否则评审
                    # 撞同一条失效网关，白烧一轮后误报「评审器故障」。
                    if workflow["review_required"]:
                        reviewer, route["reviewer"] = _pl().router.pick_reviewer(
                            agents, other, "code", stats,
                            exclude=dead_ids,
                            dead_upstreams=dead_ups + blocked_ups,
                            difficulty=difficulty)
                    _pl().store.update_run(run_id, error="", route_note=route_note)
                    _pl()._record_actual_route(
                        run_id, task, agents, stats, other,
                        implementers=[other], reviewer=reviewer,
                        implement_reason=route_note,
                        review_reason=route.get("reviewer", ""),
                        direct=not workflow["review_required"])
                    return True
                notes.append(note)
                tried.add(other["id"])
                dead_ids.add(other["id"])
                prev_id = other["id"]
                ups = _pl().router.agent_upstreams(
                    other["id"], difficulty=difficulty,
                    task_type="code", role="implement")
                denied_upstreams = _pl()._forbidden_upstream_sets(res, ups)
                blocked_ups.extend(denied_upstreams)
                if _pl().runner._quota_error(res.get("error") or ""):
                    if ups:
                        dead_ups.append(ups)
                elif not denied_upstreams:
                    break  # 非配额死因：只换一次就收手
            tail_err = (res.get("error") or "")[:200]
            if notes:
                res_err = "；".join(notes) + "；换将后仍失败：%s" % tail_err
            elif other is None:
                reason = (other_reason or
                          ("无不同上游真实 CLI 可换将；相同 403 上游已跳过"
                           if blocked_ups else "无其他真实 CLI 可换将"))
                res_err = "实现步骤失败（%s）: %s" % (reason, tail_err)
            else:
                res_err = "实现步骤失败: %s" % tail_err
        else:
            res_err = "实现步骤失败: %s" % res.get("error")
        _pl().store.update_run(run_id, expected_status="running", status="failed",
                         error=res_err, ended_at=_pl()._now())
        return False

    def review_and_score():
        verify_pass, verify_ran = _pl()._run_verify(run_id, task, workdir, ev)
        if not workflow["review_required"]:
            return {
                "pass": verify_pass,
                "scores": {},
                "issues": ([] if verify_pass else [{
                    "severity": "high", "title": "验证命令未通过",
                    "detail": "动态短链省略模型评审，先修复确定性验证失败。",
                }]),
                "summary": "低风险短链：确定性验证%s，模型评审已省略。" %
                           ("通过" if verify_pass else "未通过"),
            }, verify_pass, verify_ran
        # gate 验证（借鉴 pi-subagents 的 gate:"npm test"）：verify 失败时先跳过
        # 模型评审直接进修复轮——评审此时只能复述「验证没过」，白烧一次调用。
        # 连续失败 >=2 轮后恢复评审参与诊断（模型能看出编译错误之外的病灶）。
        if not verify_pass and verify_ran:
            verify_fail_streak[0] += 1
            if verify_fail_streak[0] < 2:
                return {"pass": False, "scores": {},
                        "issues": [{"severity": "high", "title": "验证命令未通过",
                                    "detail": "验证命令 `%s` 未通过：先修复使验证转绿，无需等评审。"
                                              % task.get("verify_command")}],
                        "summary": "gate：验证未通过，跳过模型评审直接修复"}, verify_pass, verify_ran
        else:
            verify_fail_streak[0] = 0
        # 先让确定性验证落盘，再执行模型评审；终态判断会同时使用两份证据。
        review_json = _pl()._run_review(
            run_id, task, workdir, _pl().modelhub.bind_agent(reviewer, difficulty), ev,
            scope_files=_pl()._plan_scope_files(subtasks), agents=agents, impl=impl,
            stats=stats, difficulty=difficulty)
        _pl()._persist_review_record(run_id, task, impl, reviewer, review_json)
        return review_json, verify_pass, verify_ran

    verify_fail_streak = [0]   # gate：verify 连败计数（>=2 轮恢复评审参与诊断）

    attempt_note = route.get("implementer", "") if mode == "auto" else ""
    round_no = 0
    review_json, verify_pass, verify_ran = None, True, False
    while True:
        if round_no == 0:
            if not implement_all(impl, attempt_note):
                return
        elif switched:
            if not implement_all(impl, "换将后全量重实现"):
                return
        else:
            if not _pl()._human_intervention_gate(run_id, task, "repair", ev):
                return
            issues_txt = _pl()._format_issues(review_json, verify_pass, task.get("verify_command"))
            prompt = _pl()._render_template_values(_pl().CODE_FIX_PROMPT, {
                "__GOAL__": task["goal"],
                "__ISSUES__": issues_txt,
                "__VERIFY_HINT__": _pl()._verify_hint(task),
            })
            prompt = _pl().attachments.append_task_context(prompt, task)
            res = _pl()._run_step(run_id, "fix-r%d" % round_no, _pl().modelhub.bind_agent(impl, difficulty),
                            prompt, workdir, readonly=False, ev=ev, timeout=2400,
                            note="自动修复第 %d 轮" % round_no,
                            resume=resume_ctx["session"] if resume_ctx else impl_sid[0],
                            require_tools=True)
            _keep_sid(impl, _pl().modelhub.bind_agent(impl, difficulty), res)
            if (res.get("raw") or {}).get("timed_out"):
                # 自动修复没有换将/重试兜底；超时后继续验收会把未完成的修复
                # 当作正常轮次，让 run 继续显示 running，最终还可能误标 done。
                # 步骤本身仍保留 timeout 细节，这里将 run/task 收口为终态 timeout。
                error = (res.get("error") or "修复步骤超时")[:400]
                _pl().store.update_run(run_id, expected_status="running", status="timeout",
                                 ended_at=_pl()._now(), error="代码修复步骤超时：%s" % error)
                return
            if res["ok"]:
                _pl()._record_actual_route(
                    run_id, task, agents, stats, impl,
                    implementers=[impl], reviewer=reviewer,
                    implement_reason="修复轮由 %s 完成" %
                    (impl.get("label") or impl.get("id")),
                    review_reason=route.get("reviewer", ""),
                    direct=not workflow["review_required"])
            if impl.get("mode") == "mock" and res["ok"]:
                pass  # mock 不产生真实变更
        review_json, verify_pass, verify_ran = review_and_score()
        if not _pl()._human_intervention_gate(run_id, task, "review", ev):
            return
        if review_json.get("reviewer_error"):
            # 评审器故障（Key 失效/崩溃）：没有可修的质量问题，修复轮与换将
            # 都无意义（换将也走同一个失效网关）——以明确错误收口，原因可见。
            _why = (review_json.get("summary") or "评审器执行失败")[:300]
            _pl().store.update_run(run_id, expected_status="running", status="failed",
                             ended_at=_pl()._now(), error="评审器故障（评审未执行）：%s" % _why)
            return
        passed = verify_pass and bool(review_json.get("pass"))
        repairs.append({"round": round_no, "kind": "switch" if switched else (
            "initial" if round_no == 0 else "repair"),
            "verify_pass": verify_pass, "review_pass": bool(review_json.get("pass")),
            "passed": passed})
        if passed:
            break
        if round_no < workflow["max_repair_rounds"] and not switched:
            round_no += 1
            continue
        if mode == "auto" and workflow["allow_switch"] and not switched:
            ex = (impl["id"],)
            if impl.get("mode") == "real":
                ex += ("mock-a", "mock-b")  # 真实实现者失败时不降级到 mock
            other, other_reason = _pl().router.pick(agents, "implement", "code", stats, exclude=ex)
            if other is not None:
                note = "换将 %s → %s：%d 轮实现/修复后仍未通过。%s" % (
                    impl["id"], other["id"], round_no + 1, other_reason)
                _pl().store.update_run(run_id, error="")
                impl = other
                _pl()._record_actual_route(
                    run_id, task, agents, stats, impl,
                    implementers=[impl], reviewer=reviewer,
                    implement_reason=note,
                    review_reason=route.get("reviewer", ""),
                    direct=not workflow["review_required"])
                switched = True
                round_no += 1
                continue
        break

    overall_pass = verify_pass and bool(review_json.get("pass"))
    scores = review_json.get("scores") or {}
    overall_score = round(sum(scores.values()) / len(scores), 1) if scores else None
    review_status = review_json.get("review_status") or "ok"
    coverage = review_json.get("coverage") or {}
    finding_state = review_json.get("finding_state") or {}
    verifier = review_json.get("verifier") or {}
    verdict = {
        "type": "code", "engine": "code", "pass": overall_pass, "mode": mode,
        "verify_ran": verify_ran, "verify_pass": verify_pass,
        "review_pass": (bool(review_json.get("pass"))
                        if workflow["review_required"] else None),
        "review_skipped": not workflow["review_required"],
        "review_status": review_status,
        "review_degraded": bool(review_json.get("review_degraded")),
        "review_degradation_reasons": review_json.get("review_degradation_reasons") or [],
        "coverage": coverage,
        "finding_counts": finding_state.get("counts") or {},
        "verifier": verifier,
        "scores": scores, "overall_score": overall_score,
        "issues": review_json.get("issues") or [],
        "reviewer": reviewer["id"] if reviewer else "",
        "route": route, "repairs": repairs, "switched": switched,
        "workflow": workflow,
    }

    lines = [
        "# 代码任务报告：%s" % task["title"], "",
        "- 结论：**%s**" % ("✅ 通过" if overall_pass else "❌ 未通过"),
        "- 编排模式：%s　实现者：%s　评审者：%s" % (
            {"auto": "自动", "fast": "快速", "expert": "专家",
             "manual": "手动"}.get(mode, mode), impl.get("label"),
            reviewer.get("label") if reviewer else "按验证结果省略"),
        "- 动态步骤：规划 %s；实现 %d 步；验证 %s；评审 %d 人；最多修复 %d 轮；换将 %s" % (
            "独立执行" if workflow["planning"] == "llm" else "并入实现",
            workflow["implementation_steps"], "有" if workflow["verification"] else "无",
            workflow["reviewers"], workflow["max_repair_rounds"],
            "允许" if workflow["allow_switch"] else "关闭"),
        "- 验证命令：%s → %s" % (task.get("verify_command") or "（未配置）",
                                 "通过" if verify_pass else "未通过"),
        "- 综合评分：%s　修复/换将：%s" % (
            overall_score if overall_score is not None else "-",
            ("%d 轮修复" % (len(repairs) - 1)) if len(repairs) > 1 else "无") +
            ("　（已换将重实现）" if switched else ""),
        "- 评审覆盖：变更 %d / 纳入 %d / 策略忽略 %d / 未纳入 %d；分块 %s/%s；状态 %s" % (
            int(coverage.get("changed_files") or 0),
            len(coverage.get("reviewed_files") or []),
            len(coverage.get("ignored_files") or []),
            len(coverage.get("omitted_files") or []) +
            len(coverage.get("chunk_omitted_files") or []),
            coverage.get("chunks", "-"),
            int((coverage.get("chunks") or 0)) - int(coverage.get("failed_chunks") or 0),
            review_status),
        "- findings：新增 %d / 已解决 %d / 重开 %d / 已驳回 %d" % (
            int((finding_state.get("counts") or {}).get("new") or 0),
            int((finding_state.get("counts") or {}).get("resolved") or 0),
            int((finding_state.get("counts") or {}).get("reopened") or 0),
            int((finding_state.get("counts") or {}).get("dismissed") or 0)),
    ]
    if review_json.get("review_degradation_reasons"):
        lines.append("- ⚠ 评审降级：%s" % "；".join(
            review_json["review_degradation_reasons"][:6]))
    if verifier.get("status") not in (None, "", "skipped"):
        counts = verifier.get("counts") or {}
        lines.append("- 跨族复核：%s（%s）accept %d / reject %d / uncertain %d" % (
            verifier.get("verifier") or "-", verifier.get("status"),
            int(counts.get("accept") or 0), int(counts.get("reject") or 0),
            int(counts.get("uncertain") or 0)))
    if route:
        lines.append("")
        lines.append("## 路由依据")
        lines.extend("- %s：%s" % (k, v) for k, v in route.items() if v)
    from . import review_findings as _review_findings
    lines += ["", "## 评审问题", ""]
    shown = []
    for raw in (review_json.get("issues") or []):
        item = _review_findings.normalize(raw)
        if item is not None:
            shown.append(item)
    if shown:
        for item in shown:
            where = (" %s:%s" % (item["path"], item["line_start"] or "?")
                     if item["path"] else "")
            tags = [item["severity"]]
            if item.get("verification") == "rejected":
                tags.append("已驳回")
            if str(item.get("status") or "").upper() == "REOPENED":
                tags.append("复发")
            lines.append("- **[%s]**%s %s：%s" % (
                "/".join(tags), where, item["header"], item["detail"]))
    else:
        lines.append("（无）")
    resolved = finding_state.get("resolved") or []
    if resolved:
        lines += ["", "## 本轮已解决", ""]
        for item in resolved[:20]:
            lines.append("- %s:%s %s" % (
                item.get("path") or "?", item.get("line_start") or "?",
                item.get("header") or item.get("finding_id") or ""))
    lines += ["", "## 评审总评", "", review_json.get("summary", ""), ""]
    _pl().store.write_report(run_id, "\n".join(lines))
    _pl()._write_task_evidence(run_id, task, workdir, _pl()._evidence_lines_from_run(run_id, task))
    # Stop gate（planning-with-files）：收工时活计划若有未勾选项，摘要与 verdict
    # 如实点名——「计划没走完就报完成」属于静默降级（赛马/异常路径不回写勾选时
    # 此前无任何可见信号）。只保可见不改判：勾选是文件事实，质量判据仍是评审。
    try:
        gate_total, gate_left = _pl().plan_stopgate(workdir)
    except Exception:
        gate_total, gate_left = 0, []
    gate_note = ""
    if gate_left:
        verdict["plan_gate"] = "活计划 %d 项中 %d 项未勾选完成：%s" % (
            gate_total, len(gate_left),
            "、".join("#%d[%s]%s" % (n, mk, t) for n, mk, t in gate_left[:3]))
        gate_note = "；⚠ 活计划 %d/%d 项完成" % (gate_total - len(gate_left), gate_total)
    _pl().store.update_run(run_id, expected_status="running", status="done", verdict=verdict,
                     summary="代码任务%s（验证%s / 评审%s%s%s）" % (
                         "通过" if overall_pass else "未通过",
                         "通过" if verify_pass else "未通过",
                         ((("通过" if review_json.get("pass") else "未通过") +
                           ("" if review_status == "ok" else
                            "（%s）" % ("覆盖不完整" if review_status == "partial"
                                       else "已降级")))
                          if workflow["review_required"] else "按策略省略"),
                         "，%d 轮修复" % (len(repairs) - 1) if len(repairs) > 1 else "",
                         gate_note),
                     ended_at=_pl()._now())
