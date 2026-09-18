/* 手机视口基础可用性探测：390×844（触控模拟）逐页查横向溢出 + 抽屉开合 +
 * 任务列表/运行详情可达 + 关键按钮可点尺寸。基础可用口径：页面无横向滚动、
 * 关键入口几何可见。一次性诊断脚本。
 * 跑法：node tests/_probe_mobile.mjs   （服务端口 18866 / CDP 9377，防并行撞车） */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE_PORT = 18866;
const CDP_PORT = 9377;
const SERVICE = `http://127.0.0.1:${SERVICE_PORT}`;
const RUN_ID = "r-20990101-000000-0003";
const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-mobile-"));
  const runsDir = join(dataDir, "runs", RUN_ID);
  mkdirSync(join(runsDir, "steps"), { recursive: true });
  const workdir = join(dataDir, "book");
  mkdirSync(workdir, { recursive: true });
  mkdirSync(join(dataDir, "tasks"), { recursive: true });
  writeFileSync(join(dataDir, "tasks", "task-a.json"), JSON.stringify({
    id: "task-a", title: "手机可用性探测任务", type: "novel", goal: "造数",
    workdir, status: "done", archived: false,
    created_at: "2099-01-01 00:00:00", mode: "manual",
  }, null, 2));
  writeFileSync(join(runsDir, "run.json"), JSON.stringify({
    id: RUN_ID, kind: "orchestration", title: "手机可用性探测任务",
    task_id: "task-a", status: "done",
    steps: [{ n: 1, role: "draft-c1", agent: "mock-a", agent_label: "A", status: "done",
              started_at: "00:00:01", log: "steps/01.log", summary: "完成", cost_usd: 0, tokens: 0 }],
    messages: [], created_at: "2099-01-01 00:00:00", started_at: "00:00:01",
    ended_at: "00:00:20", cost_usd: 0, tokens: 0, error: "", verdict: null, summary: "",
  }, null, 2));
  writeFileSync(join(runsDir, "steps", "01.log"), "输出行\n", "utf-8");

  const srv = spawn("python", ["app/main.py", "--port", String(SERVICE_PORT)], {
    cwd: ROOT, stdio: "ignore", env: { ...process.env, TUTTI_DATA: dataDir },
  });
  const edge = spawn(EDGE_CANDIDATES[0], [
    "--headless=new", "--disable-gpu", "--no-first-run", "--force-device-scale-factor=1",
    `--user-data-dir=${mkdtempSync(join(tmpdir(), "tutti-cdp-"))}`,
    `--remote-debugging-port=${CDP_PORT}`,
    "--blink-settings=prefersReducedMotion=false",
    "--window-size=420,900", "about:blank",
  ], { stdio: "ignore" });
  const profDir = edge.spawnargs.find((a) => a.startsWith("--user-data-dir=")).slice(17);
  try {
    let up = false;
    for (let i = 0; i < 60 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(`${SERVICE}/api/state`)).ok; } catch (e) {}
    }
    if (!up) throw new Error("service not up");
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        target = (await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json())
          .find((t) => t.type === "page");
      } catch (e) {}
    }
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });
    await send("Runtime.enable");
    await send("Page.enable");
    // iPhone 12/13/14 逻辑视口（TUTTI_TEST_VW 可换 360 等小屏）+ 触控/无悬停（手机指纹）
    const vwW = Number(process.env.TUTTI_TEST_VW || 390);
    await send("Emulation.setDeviceMetricsOverride",
      { width: vwW, height: 844, deviceScaleFactor: 3, mobile: true });
    await send("Emulation.setTouchEmulationEnabled",
      { enabled: true, maxTouchPoints: 5 });
    await send("Emulation.setEmulatedMedia", { features: [
      { name: "hover", value: "none" }, { name: "pointer", value: "coarse" },
    ]});
    await send("Page.navigate", { url: SERVICE });
    await sleep(3000);
    const evalJson = async (expr) => {
      const r = await send("Runtime.evaluate",
        { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.result && r.result.exceptionDetails) return { __err: r.result.exceptionDetails.text };
      return r.result ? r.result.result.value : undefined;
    };
    await evalJson(`(async () => {
      try { await api("/api/control", { method: "POST", body: JSON.stringify({ action: "acquire" }) }); } catch (e) {}
      try { if (typeof welcomeClose === "function") welcomeClose(); } catch (e) {}
      const w = document.querySelector("#welcome, .welcome, #guide-mask, #token-gate");
      if (w && w.remove) w.remove();
      return 1;
    })()`);
    await sleep(500);

    // 横向溢出体检：页面级 scrollWidth + 突出视口的元素（跳过 fixed 弹层内滚动容器）
    const overflowOf = `(() => {
      const vw = document.documentElement.clientWidth;
      const bad = [];
      for (const el of document.querySelectorAll("body *")) {
        const cs = getComputedStyle(el);
        if (cs.display === "none" || cs.visibility === "hidden") continue;
        const r = el.getBoundingClientRect();
        if (r.width > 40 && r.right > vw + 2) {
          bad.push(el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") +
            (typeof el.className === "string" && el.className
              ? "." + el.className.trim().split(/\\s+/).slice(0, 2).join(".") : "") +
            " w=" + Math.round(r.width) + " right=" + Math.round(r.right));
          if (bad.length >= 4) break;
        }
      }
      return { vw, scrollW: document.documentElement.scrollWidth, bad };
    })()`;

    // 基础可用验收：主视图（任务/新建）+ 各设置子页 + 运行详情
    const tabs = ["tasks", "runs", "automation", "usage", "agents", "models",
                  "bindings", "skills", "market", "orch", "appearance", "about"];
    let fails = 0;
    for (const tb of tabs) {
      await evalJson(`switchTab(${JSON.stringify(tb)}); 1`);
      await sleep(450);
      const o = await evalJson(overflowOf);
      const over = o.scrollW > o.vw + 2;
      if (over) fails++;
      console.log(`[${over ? "FAIL" : "ok  "}] tab=${tb} vw=${o.vw} scrollW=${o.scrollW}` +
        (o.bad.length ? " 溢出元素: " + o.bad.join(" | ") : ""));
    }

    // 主视图：退出 settings-mode（返回 CodeBee）后新建入口 + 侧栏抽屉开合
    await evalJson(`document.getElementById("btn-set-back") && document.getElementById("btn-set-back").click(); 1`);
    await sleep(400);
    const home = await evalJson(`(() => {
      const btn = document.getElementById("btn-new-task");
      const menu = document.getElementById("btn-menu");
      const cmp = document.querySelector("#sub-tasks");
      const br = btn && btn.getBoundingClientRect();
      const mr = menu && menu.getBoundingClientRect();
      return {
        newTaskBtn: !!br && br.width > 0 && br.height >= 24 ? Math.round(br.height) : 0,
        menuBtn: !!mr && mr.width > 0 && mr.height >= 24 ? Math.round(mr.height) : 0,
        composer: cmp && !cmp.classList.contains("hidden"),
        sidePos: getComputedStyle(document.getElementById("sidebar")).position,
        sideW: Math.round(document.getElementById("sidebar").getBoundingClientRect().width),
      };
    })()`);
    console.log(`[info] 主视图: 新建按钮高=${home.newTaskBtn} 菜单钮高=${home.menuBtn} ` +
      `composer可见=${home.composer} sidebar pos=${home.sidePos} w=${home.sideW}`);

    // 抽屉开合：点汉堡 → 侧栏滑入 → 点遮罩收起
    await evalJson(`document.getElementById("btn-menu").click(); 1`);
    await sleep(450);
    const drawerOpen = await evalJson(`(() => {
      const s = document.getElementById("sidebar");
      const mask = document.getElementById("drawer-mask");
      const br = document.getElementById("btn-new-task").getBoundingClientRect();
      return { w: Math.round(s.getBoundingClientRect().width),
               visible: s.getBoundingClientRect().right > 0,
               newTaskH: Math.round(br.height), newTaskW: Math.round(br.width),
               maskShown: mask ? !mask.classList.contains("hidden") &&
                 getComputedStyle(mask).display !== "none" : false };
    })()`);
    console.log(`[info] 抽屉开: 宽=${drawerOpen.w} 可见=${drawerOpen.visible} ` +
      `新建钮=${drawerOpen.newTaskW}x${drawerOpen.newTaskH} 遮罩=${drawerOpen.maskShown}`);
    await evalJson(`document.getElementById("drawer-mask") && document.getElementById("drawer-mask").click(); 1`);
    await sleep(450);
    const drawerClosed = await evalJson(
      `Math.round(document.getElementById("sidebar").getBoundingClientRect().right)`);
    console.log(`[info] 抽屉收: 侧栏右缘=${drawerClosed}（<=0 即收起）`);

    // 运行详情：sideOpenRun 真实路径 + 步骤/结果两张卡可看
    await evalJson(`sideOpenRun(${JSON.stringify(RUN_ID)}); 1`);
    await sleep(1200);
    for (const tb of ["steps", "result"]) {
      await evalJson(`(() => { const b = document.querySelector('#rd-tabs .rd-tab[data-tab="${tb}"]');
        if (b && !b.classList.contains("hidden")) b.click(); return 1; })()`);
      await sleep(500);
      const o = await evalJson(overflowOf);
      const det = await evalJson(`(() => {
        const d = document.getElementById("run-detail");
        const rail = document.getElementById("rd-rail");
        return { shown: d ? !d.classList.contains("hidden") : false,
                 railW: rail ? Math.round(rail.getBoundingClientRect().width) : 0 };
      })()`);
      const over = o.scrollW > o.vw + 2;
      if (over) fails++;
      console.log(`[${over ? "FAIL" : "ok  "}] 详情·${tb} 卡可见=${det.shown} 宽=${det.railW} ` +
        `scrollW=${o.scrollW}/${o.vw}` + (o.bad.length ? " 溢出: " + o.bad.join(" | ") : ""));
    }
    console.log(fails === 0 ? "MOBILE-BASIC: PASS" : `MOBILE-BASIC: ${fails} 处横向溢出`);
  } finally {
    try { spawn("taskkill", ["/F", "/T", "/PID", String(edge.pid)], { stdio: "ignore" }); } catch (e) {}
    try { spawn("taskkill", ["/F", "/T", "/PID", String(srv.pid)], { stdio: "ignore" }); } catch (e) {}
    await sleep(500);
    try { rmSync(profDir, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
  }
}
main().catch((e) => { console.error(e); process.exit(1); });
