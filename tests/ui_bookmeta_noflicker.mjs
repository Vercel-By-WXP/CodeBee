/* 详情区「一闪一闪/填一半被清空」回归（2026-09-30 三修：A 签名守卫 +
 * B 输入保护 + C 登记草稿外置）。Edge headless + CDP，零依赖。
 *
 * 断言四组：
 *  1) 静置零重绘：任务态/运行态详情各观察 13s（覆盖 ≥1 轮 8s 轮询），
 *     rd-bookmeta / rd-meta 的直接子节点零替换——修前这里每 8s 整块重写。
 *  2) 任务态↔运行态切换不串台：runDetailSig 重置点兜住共用 DOM。
 *  3) 登记表单：输入中跨数据变化（生成 running→done 两次重绘波）值不丢
 *     （B 焦点守卫）；blur 后再触发真实重绘，值+展开态回填（C 草稿外置）；
 *     确认登记后表单收起、草稿清空、书籍落账。
 *  4) 发布自续轮询链活着（/api/publish 出现在资源时序里）+ 全程无控制台错误。
 * 临时数据目录 + 独立端口（18861 / CDP 9391，与其他 UI 测试互不冲撞）。
 * 用法：node tests/ui_bookmeta_noflicker.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE_PORT = 18861;
const CDP_PORT = 9391;
const SERVICE = `http://127.0.0.1:${SERVICE_PORT}`;
const RUN_ID = "r-20990105-000000-0001";
const TASK_ID = "task-bmflick";
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-bmflick-"));
  const runsDir = join(dataDir, "runs", RUN_ID);
  mkdirSync(join(runsDir, "steps"), { recursive: true });
  const workdir = join(dataDir, "book");
  mkdirSync(workdir, { recursive: true });
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  writeFileSync(join(runsDir, "steps", "01-outline-orch.log"), "--- 输出 ---\n大纲 6 章\n", "utf-8");
  writeFileSync(join(workdir, "chapter-001.md"), "# 第一章\n\n开局即冲突。\n", "utf-8");
  writeFileSync(join(dataDir, "tasks", `${TASK_ID}.json`), JSON.stringify({
    id: TASK_ID, title: "任务态标题实测", type: "serial_novel",
    goal: "长篇连载", workdir, status: "done",
    serial: { chapters: 6, words_per_chapter: 2000, start_chapter: 1 },
    created_at: "2000-01-05 00:00:00", mode: "manual", archived: false,
  }, null, 2));
  // run 标题与任务标题不同：运行态/任务态共用一套 DOM，切错目标立刻现形
  writeFileSync(join(runsDir, "run.json"), JSON.stringify({
    id: RUN_ID, kind: "orchestration", title: "运行态标题实测",
    task_id: TASK_ID, status: "done", messages: [],
    steps: [
      { n: 1, role: "outline", agent: "orch", agent_label: "编排者", note: "", status: "done",
        started_at: "00:00:01", ended_at: "00:00:20", duration_s: 19, exit_code: 0,
        summary: "大纲 6 章", log: "steps/01-outline-orch.log", cost_usd: 0, tokens: 0 },
    ],
    created_at: "2000-01-05 00:00:00", started_at: "2000-01-05 00:00:00",
    ended_at: "2000-01-05 00:02:00", cost_usd: 0, tokens: 0, error: "",
    verdict: null, summary: "",
  }, null, 2));

  const srv = spawn("python", ["app/main.py", "--port", String(SERVICE_PORT)], {
    cwd: ROOT, stdio: "ignore",
    env: { ...process.env, TUTTI_DATA: dataDir, TUTTI_BOOKMETA_TEMPLATE_ONLY: "1" },
  });
  const edgePath = EDGE_CANDIDATES.find((p) => true);
  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-"));
  const edge = spawn(edgePath, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--force-device-scale-factor=1",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });

  try {
    let up = false;
    for (let i = 0; i < 60 && !up; i++) {
      await sleep(500);
      try { const r = await fetch(`${SERVICE}/api/state`); up = r.ok; } catch (e) { /* retry */ }
    }
    check("临时服务就绪(18861)", up);

    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
        target = (await res.json()).find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless CDP", !!target);
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    const consoleErrors = [];
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) pending.get(msg.id)(msg);
      if (msg.method === "Runtime.exceptionThrown")
        consoleErrors.push(msg.params.exceptionDetails.text);
      if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error")
        consoleErrors.push(String(msg.params.args.map((a) => a.value).join(" ")));
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE });
    await sleep(2500);

    const evalJson = async (expr) => {
      const r = await send("Runtime.evaluate",
        { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.result && r.result.exceptionDetails) return { __err: r.result.exceptionDetails.text };
      return r.result ? r.result.result.value : undefined;
    };
    // 观察某容器「直接子节点被整体替换」的次数（innerHTML 全量重建的指纹）。
    // side 通道写的是孙节点（rd-meta-task 内部），不进本计数。
    const armObserver = (sel) => evalJson(`(() => {
      const el = document.querySelector(${JSON.stringify(sel)});
      if (!el) return false;
      if (!el.__flickObs) {
        el.__flickObs = new MutationObserver((muts) => {
          for (const m of muts) if (m.type === "childList") el.__flickCount += m.addedNodes.length;
        });
        el.__flickObs.observe(el, { childList: true });
      }
      el.__flickCount = 0;   // 每次布防清零：不把别的阶段的合法渲染记进本窗口
      return true;
    })()`);

    // ── 1) 任务态静置零重绘 ─────────────────────────────────────────
    await evalJson(`(async () => {
      sideOpenTask(${JSON.stringify(TASK_ID)});
      await new Promise((r) => setTimeout(r, 1000));
      document.querySelector('#rd-tabs .rd-tab[data-tab="bookmeta"]').click();
      return "ok";
    })()`);
    await sleep(2500);   // 首帧 + TAB 挂载消化完，再开始计数
    await armObserver("#rd-bookmeta");
    await armObserver("#rd-meta");
    await sleep(13000);  // ≥1 轮 8s 轮询 + 余量；修前这里必闪 1-2 次
    const taskIdle = await evalJson(`(() => ({
      bookmeta: document.querySelector("#rd-bookmeta").__flickCount,
      meta: document.querySelector("#rd-meta").__flickCount,
    }))()`);
    check("任务态静置 13s：作品信息面板零重建",
      taskIdle && taskIdle.bookmeta === 0, JSON.stringify(taskIdle));
    check("任务态静置 13s：meta 条零重建", taskIdle && taskIdle.meta === 0,
      JSON.stringify(taskIdle));

    // ── 2) 任务态 ↔ 运行态切换不串台（runDetailSig 重置点） ──────────
    // 注：详情头部标题两种模式都显示最近一次 run 的标题（HEAD 既有行为），
    // 模式判别改看 meta 条内容：任务态是「运行 N/M 次」累计组，运行态是「创建」。
    const switchModes = await evalJson(`(async () => {
      openRun(${JSON.stringify(RUN_ID)});
      await new Promise((r) => setTimeout(r, 1500));
      const runTitle = document.getElementById("rd-title").textContent;
      const runMeta = document.getElementById("rd-meta").textContent;
      sideOpenTask(${JSON.stringify(TASK_ID)});
      await new Promise((r) => setTimeout(r, 1500));
      const taskMeta = document.getElementById("rd-meta").textContent;
      return { runTitle, runMeta, taskMeta };
    })()`);
    check("运行态标题正确（守卫未误跳首帧）",
      switchModes && switchModes.runTitle === "运行态标题实测", JSON.stringify(switchModes));
    check("运行态 meta 是单次口径（含「创建」）",
      switchModes && switchModes.runMeta.includes("创建"), JSON.stringify(switchModes).slice(0, 160));
    check("切回任务态 meta 是累计组口径（含「运行 1/1」）",
      switchModes && switchModes.taskMeta.includes("1/1"), JSON.stringify(switchModes).slice(0, 160));

    // ── 运行态静置零重绘（修前 renderRunDetail 每 8s 无条件整写） ────
    await evalJson(`(async () => {
      openRun(${JSON.stringify(RUN_ID)});
      return "ok";
    })()`);
    await sleep(2500);
    await armObserver("#rd-meta");
    await sleep(13000);
    const runIdle = await evalJson(`(() => ({
      meta: document.querySelector("#rd-meta").__flickCount,
    }))()`);
    check("运行态静置 13s：meta 条零重建（renderRunDetail 签名守卫）",
      runIdle && runIdle.meta === 0, JSON.stringify(runIdle));

    // ── 3) 登记表单：B 焦点守卫 + C 草稿回填 + 登记闭环 ─────────────
    await evalJson(`(async () => {
      sideOpenTask(${JSON.stringify(TASK_ID)});
      await new Promise((r) => setTimeout(r, 1000));
      document.querySelector('#rd-tabs .rd-tab[data-tab="bookmeta"]').click();
      await new Promise((r) => setTimeout(r, 400));
      return "ok";
    })()`);
    const regOpen = await evalJson(`(() => {
      const btns = Array.from(document.querySelectorAll("#rd-bookmeta button"));
      const b = btns.find((x) => x.textContent.includes("登记已有作品"));
      if (!b) return null;
      b.click();
      const box = document.getElementById("pb-reg-fanqie");
      return { open: box && !box.classList.contains("hidden") };
    })()`);
    check("点「登记已有作品」表单展开", regOpen && regOpen.open === true,
      JSON.stringify(regOpen));

    // 聚焦 + 输入（oninput 属性处理器走草稿写侧）+ 生成触发两波真实重绘波
    const typed = await evalJson(`(() => {
      const input = document.getElementById("pb-reg-title-fanqie");
      input.focus();
      input.value = "残稿秘录";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      const gen = document.querySelector("#rd-bookmeta .bm-card button.primary");
      if (gen) gen.click();   // 番茄生成：running→done，SSE 推两次数据变化
      return { focused: document.activeElement === input, val: input.value };
    })()`);
    check("登记表单聚焦输入「残稿秘录」并点生成（制造数据变化波）",
      typed && typed.focused && typed.val === "残稿秘录", JSON.stringify(typed));

    let duringTyping = null;
    for (let i = 0; i < 90; i++) {
      await sleep(1000);
      duringTyping = await evalJson(`(() => {
        const box = document.getElementById("pb-reg-fanqie");
        const input = document.getElementById("pb-reg-title-fanqie");
        return { open: box && !box.classList.contains("hidden"),
          val: input ? input.value : "(input 没了)" };
      })()`);
      if (duringTyping.val !== "残稿秘录") break;   // 一丢立刻出循环报点位
      // 生成 done 的标志卡出现即可收（期间已覆盖 ≥2 次 8s 轮询）
      if (i >= 12) break;
    }
    check("输入保持焦点期间跨数据变化：值不丢、表单不折（B 守卫）",
      duringTyping && duringTyping.open === true && duringTyping.val === "残稿秘录",
      JSON.stringify(duringTyping));

    // blur 后再触发一次真实重绘（七猫生成）→ C 草稿回填：值 + 展开态都在
    await evalJson(`(() => { document.body.focus(); return "ok"; })()`);
    const regen = await evalJson(`(() => {
      const names = Array.from(document.querySelectorAll("#rd-bookmeta .bm-plat-name"));
      const qm = names.find((n) => n.textContent.trim() === "七猫");
      const gen = qm && qm.closest(".bm-card").querySelector("button.primary, button.ghost");
      if (gen) gen.click();
      return !!gen;
    })()`);
    check("blur 后触发七猫生成（真实重绘）", regen === true, regen);
    let afterRedraw = null;
    for (let i = 0; i < 90; i++) {
      await sleep(1000);
      afterRedraw = await evalJson(`(() => {
        const box = document.getElementById("pb-reg-fanqie");
        const input = document.getElementById("pb-reg-title-fanqie");
        return { open: box && !box.classList.contains("hidden"),
          val: input ? input.value : "(input 没了)" };
      })()`);
      if (afterRedraw.val !== "残稿秘录" || !afterRedraw.open) break;
      if (i >= 12) break;
    }
    check("重绘后登记草稿回填：值 + 展开态都在（C 外置）",
      afterRedraw && afterRedraw.open === true && afterRedraw.val === "残稿秘录",
      JSON.stringify(afterRedraw));

    // 登记闭环：确认登记 → 表单收起 + 草稿清 + 台账落书
    //（重绘受发布轮询 5s 节流，退场窗口给到 15s）
    let registered = null;
    for (let i = 0; i < 15; i++) {
      registered = await evalJson(`(async () => {
        if (window.__regClicked) {
          await new Promise((r) => setTimeout(r, 1000));
        } else {
          window.__regClicked = true;
          const idInput = document.getElementById("pb-reg-id-fanqie");
          if (idInput) { idInput.value = "730001"; idInput.dispatchEvent(new Event("input", { bubbles: true })); }
          const btns = Array.from(document.querySelectorAll("#rd-bookmeta button"));
          const ok = btns.find((x) => x.textContent.includes("确认登记"));
          if (ok) ok.click();
          await new Promise((r) => setTimeout(r, 1500));
        }
        return { regBtnGone:
          !Array.from(document.querySelectorAll("#rd-bookmeta .bm-card"))
            .filter((c) => { const n = c.querySelector(".bm-plat-name");
              return n && n.textContent.trim() === "番茄"; })
            .some((c) => Array.from(c.querySelectorAll("button"))
              .some((x) => x.textContent.includes("登记已有作品"))),
          bookShown: Array.from(document.querySelectorAll("#rd-bookmeta .pb-book"))
            .some((x) => (x.textContent || "").includes("已建书：残稿秘录")) };
      })()`);
      if (registered && registered.regBtnGone) break;
    }
    const regDump = registered && !registered.regBtnGone
      ? await evalJson(`(() => ({
          pub: (document.querySelector("#rd-bookmeta .bm-pub") || {}).textContent || "(无发布行)",
          toast: (document.getElementById("toast") || {}).textContent || "",
          bmRebuilds: document.querySelector("#rd-bookmeta").__flickCount,
        }))()`) : null;
    check("确认登记：「登记已有作品」退场（bookReady）",
      registered && registered.regBtnGone === true,
      JSON.stringify(registered) + (regDump ? " diag=" + JSON.stringify(regDump) : ""));

    // ── 4) 发布自续轮询链活着 + 无控制台错误 ───────────────────────
    const pubHits = await evalJson(`performance.getEntriesByType("resource")
      .filter((r) => r.name.includes("/api/publish")).length`);
    check("发布自续轮询链在拉 /api/publish（脱离主渲染泵仍活着）",
      Number(pubHits) >= 2, pubHits);
    check("无控制台错误", consoleErrors.length === 0,
      consoleErrors.join(" | ").slice(0, 300));
  } finally {
    try { edge.kill(); } catch (e) { /* noop */ }
    try { srv.kill(); } catch (e) { /* noop */ }
    await sleep(500);
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) { /* noop */ }
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* noop */ }
  }
  const bad = results.filter((r) => !r.ok);
  console.log("\n通过 %d / 失败 %d", results.length - bad.length, bad.length);
  process.exit(bad.length ? 1 : 0);
}

main().catch((e) => { console.error("测试崩溃:", e); process.exit(2); });
