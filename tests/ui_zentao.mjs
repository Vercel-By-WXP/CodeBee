/* 禅道·产品档案 UI 验证：自起临时服务（TUTTI_DATA 隔离 + 种子 zentao.json v2）+ Edge headless。
 * 覆盖：设置导航「禅道」入口、档案卡渲染（产品/负责人/模块路由）、修复记录的
 * 排查徽章与任务链接、保存配置落库（含档案结构与脱敏）、增删产品/路由、
 * 立即扫描对不可达地址优雅报错。
 * 结束清理浏览器/服务进程、临时目录。 */
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18951;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = Number(process.env.TUTTI_TEST_CDP || 9377);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 240)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-uizentao-"));
  const dataDir = join(tmp, "data");
  mkdirSync(dataDir, { recursive: true });
  // 种子：产品档案 + 一条带排查结论的认领（v2 形状）
  writeFileSync(join(dataDir, "zentao.json"), JSON.stringify({
    version: 2,
    config: { base_url: "http://127.0.0.1:18949", account: "coder", password: "secret",
      product_profiles: [{ product: 7, assigned_to: "coder", severity_cap: 0,
        our_sides: ["backend"],
        repos: { backend: { workdir: "", git_rev: "main", verify_command: "" },
                 frontend: { workdir: "", git_rev: "", verify_command: "" } },
        repo_hints: { backend: "Spring Boot 服务", frontend: "" },
        owners: { backend: "be1", frontend: "fe1", not_ours: "" },
        module_routes: [{ module: 99, side: "backend", account: "" }] }],
      auto_resolve: true, auto_merge: true, triage_ai: true,
      poll_enabled: false, interval_hours: 2 },
    claims: { "501": { bug_id: 501, product: 7, title: "种子 Bug：登录 500",
        triage: { side: "backend", reason: "模块 #99 路由规则", by: "rule", account: "" },
        tasks: [{ side: "backend", task_id: "t-seed-1", run_id: "r-seed-1", state: "fixing" }],
        state: "fixing", note: "", attempts: 0, claimed_at: "2026-09-19 08:00:00" } },
    last_scan: "2026-09-19 08:00:00", next_scan: "", last_error: "",
  }, null, 1), "utf-8");

  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"),
    "--port", String(PORT), "--no-browser", "--host", "127.0.0.1"], {
    cwd: ROOT, stdio: "ignore",
    env: Object.assign({}, process.env, { TUTTI_DATA: dataDir, PYTHONPATH: ROOT }),
  });
  let edge = null, ws = null;
  try {
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).status === 200; } catch (e) { /* wait */ }
    }
    check("临时服务启动", up);
    if (!up) throw new Error("service not up");

    edge = spawn(EDGE, [
      "--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "profile")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1400,950", "about:blank",
    ], { stdio: "ignore" });

    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless 启动并开放 CDP", !!target);
    if (!target) throw new Error("no CDP target");

    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) pending.get(msg.id)(msg);
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq;
      pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });
    const evalJs = async (expr, awaitPromise = false) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise });
      return r.result?.result?.value;
    };
    const waitFor = async (expr, ms = 20000) => {
      const wrapped = typeof expr === "function" ? "(" + expr.toString() + ")()" : expr;
      for (let t = 0; t < ms; t += 400) {
        if (await evalJs(wrapped, true)) return true;
        await sleep(400);
      }
      return false;
    };

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(2500);
    await evalJs(`poll(); "ok"`);
    await waitFor(`typeof S === "object" && S !== null && S.state !== null`);
    await evalJs(`if (typeof welcomeClose === "function" &&
      !document.getElementById("welcome").classList.contains("hidden")) welcomeClose(); "ok"`);
    await sleep(300);

    // ① 设置导航入口 + 进子页
    check("① 设置导航有「禅道」入口",
      (await evalJs(`!!document.querySelector(".set-item[data-sub='zentao']")`)) === true);
    await evalJs(`switchTab("zentao"); "ok"`);
    const pageOn = await waitFor(
      `!document.getElementById("sub-zentao").classList.contains("hidden")`, 8000);
    check("① 点击进入禅道子页", pageOn === true);

    // ② 产品档案卡 + 修复记录渲染
    await waitFor(`typeof S.zentao === "object" && S.zentao !== null && (S.zentao.config||{}).product_profiles`);
    const prof = await evalJs(`(() => {
      const card = document.querySelector("#zt-profiles .zt-prof");
      if (!card) return null;
      return JSON.stringify({
        count: document.querySelectorAll("#zt-profiles .zt-prof").length,
        product: card.querySelector(".zt-p-product").value,
        rev: card.querySelector('.zt-p-rev[data-side="backend"]').value,
        fe: card.querySelector('.zt-p-owner[data-side="frontend"]').value,
        routes: card.querySelectorAll(".zt-mr-row").length,
        routeModule: (card.querySelector(".zt-mr-module") || {}).value,
        hint: card.querySelector('.zt-p-hint[data-side="backend"]').value
      });
    })()`, true);
    const pf = JSON.parse(prof || "{}");
    check("② 档案卡渲染（产品/分支/负责人/路由/提示）",
      pf.count === 1 && pf.product === "7" && pf.rev === "main" && pf.fe === "fe1" &&
      pf.routes === 1 && pf.routeModule === "99" && pf.hint === "Spring Boot 服务", prof);
    const pwPlaceholder = await evalJs(`document.getElementById("zt-password").placeholder`);
    check("② 已存密码不回显", String(pwPlaceholder || "").indexOf("已保存") >= 0, pwPlaceholder);
    const claim = await waitFor(`(() => {
      const t = document.getElementById("zentao-claims").textContent;
      return t.indexOf("种子 Bug") >= 0 && t.indexOf("修复中") >= 0 && t.indexOf("排查：后端问题") >= 0;
    })()`, 8000);
    check("② 修复记录含排查徽章与状态", claim === true);

    // ③ 档案卡编辑：加路由（填模块 88→前端）+ 改全局开关 → 保存落库
    await evalJs(`ztMrAdd(0);
      const rows = document.querySelectorAll("#zt-profiles .zt-prof")[0].querySelectorAll(".zt-mr-row");
      const last = rows[rows.length - 1];
      last.querySelector(".zt-mr-module").value = "88";
      last.querySelector(".zt-mr-side").value = "frontend";
      last.querySelector(".zt-mr-account").value = "fe2"; saveZentao(); "ok"`);
    const saved = await waitFor(`api("/api/zentao").then(v => {
      const p = (v.config.product_profiles || [])[0] || {};
      return p.product === 7 && (p.module_routes || []).length === 2 &&
        JSON.stringify(p.owners) === JSON.stringify({ backend: "be1", frontend: "fe1", not_ours: "" }) &&
        v.config.has_password === true && v.config.password === "" &&
        v.config.triage_ai === true;
    })`, 10000);
    check("③ 保存后档案结构落库（路由×2 + 负责人 + 脱敏）", saved === true);

    // ④ 加第二个产品 → 保存 → 后端 2 份档案；删除一份 → 回到 1
    await evalJs(`ztProfAdd();
      const cards = document.querySelectorAll("#zt-profiles .zt-prof");
      cards[cards.length - 1].querySelector(".zt-p-product").value = "8";
      saveZentao(); "ok"`);
    const two = await waitFor(`api("/api/zentao").then(v =>
      (v.config.product_profiles || []).length === 2 &&
      v.config.product_profiles[1].product === 8)`, 10000);
    check("④ 添加产品并保存（档案×2）", two === true);
    await evalJs(`ztProfDel(1); saveZentao(); "ok"`);
    const one = await waitFor(`api("/api/zentao").then(v =>
      (v.config.product_profiles || []).length === 1)`, 10000);
    check("④ 删除产品并保存（回到×1）", one === true);

    // ⑤ 立即扫描对不可达地址优雅报错
    await evalJs(`document.getElementById("zt-base-url").value = "http://127.0.0.1:9";
      document.getElementById("zt-password").value = ""; saveZentao(); "ok"`);
    await sleep(600);
    await evalJs(`scanZentao(); "ok"`);
    const errShown = await waitFor(
      `document.getElementById("zentao-status").textContent.indexOf("最近错误") >= 0`, 15000);
    check("⑤ 扫描不可达地址 → 最近错误上屏", errShown === true);

    console.log(results.every((r) => r.ok) ? "\n全部通过" : "\n存在失败项");
  } finally {
    try { if (ws) ws.close(); } catch (e) { /* ignore */ }
    try { if (edge) edge.kill(); } catch (e) { /* ignore */ }
    try { svc.kill(); } catch (e) { /* ignore */ }
    await sleep(800);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
  if (!results.every((r) => r.ok)) process.exit(1);
}

main().catch((e) => { console.error(e); process.exit(1); });
