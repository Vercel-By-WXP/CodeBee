/* 禅道 Bug 自动修复设置页 UI 验证：自起临时服务（TUTTI_DATA 隔离 + 种子 zentao.json）+ Edge headless。
 * 覆盖：设置导航出现「禅道」入口、进页回填表单、保存配置落库（脱敏）、
 * 表单回读、立即扫描对不可达地址优雅报错（last_error 可见）、认领卡片渲染。
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
  // 种子：一条认领记录 + 已配好的连接（密码脱敏验证用）
  writeFileSync(join(dataDir, "zentao.json"), JSON.stringify({
    version: 1,
    config: { base_url: "http://127.0.0.1:18949", account: "coder", password: "secret",
              products: [7], assigned_to: "coder", severity_cap: 0, workdir: "",
              git_rev: "", verify_command: "", auto_resolve: true, auto_merge: true,
              poll_enabled: false, interval_hours: 2 },
    claims: { "501": { bug_id: 501, product: "7", title: "种子 Bug：登录 500",
                       task_id: "t-seed-1", run_id: "r-seed-1", state: "fixing",
                       note: "", attempts: 0, claimed_at: "2026-09-19 08:00:00" } },
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
    // 首启欢迎引导若弹出则先关掉（真实路径）
    await evalJs(`if (typeof welcomeClose === "function" &&
      !document.getElementById("welcome").classList.contains("hidden")) welcomeClose(); "ok"`);
    await sleep(300);

    // ① 设置导航出现「禅道」入口；点击进子页
    const navBtn = await evalJs(
      `!!document.querySelector(".set-item[data-sub='zentao']")`);
    check("① 设置导航有「禅道」入口", navBtn === true);
    await evalJs(`switchTab("zentao"); "ok"`);
    await sleep(600);
    const pageOn = await evalJs(`JSON.stringify({
      sub: !document.getElementById("sub-zentao").classList.contains("hidden"),
      title: document.getElementById("page-title").textContent
    })`, true);
    const pn = JSON.parse(pageOn || "{}");
    check("① 点击进入禅道子页", pn.sub === true, pageOn);
    check("① 页标题切到禅道", String(pn.title || "").indexOf("禅道") >= 0, pageOn);

    // ② 进页回填：种子配置与认领卡可见
    await waitFor(`typeof S.zentao === "object" && S.zentao !== null`);
    const filled = JSON.parse(await evalJs(`JSON.stringify({
      url: document.getElementById("zt-base-url").value,
      acct: document.getElementById("zt-account").value,
      prods: document.getElementById("zt-products").value,
      claimCard: document.getElementById("zentao-claims").textContent.indexOf("种子 Bug") >= 0,
      stateTag: document.getElementById("zentao-claims").textContent.indexOf("修复中") >= 0
    })`, true));
    check("② 表单回填地址/账号/产品", filled.url === "http://127.0.0.1:18949" &&
      filled.acct === "coder" && filled.prods === "7", JSON.stringify(filled));
    check("② 种子认领卡渲染（含状态徽章）", filled.claimCard && filled.stateTag, JSON.stringify(filled));
    const pwPlaceholder = await evalJs(
      `document.getElementById("zt-password").placeholder`);
    check("② 已存密码时输入框留空不回显", pwPlaceholder.indexOf("已保存") >= 0, pwPlaceholder);

    // ③ 保存配置：改产品/开轮询/设密码 → 后端落库且脱敏
    await evalJs(`document.getElementById("zt-products").value = "1,2";
      document.getElementById("zt-password").value = "newpw";
      document.getElementById("zt-poll").checked = true;
      document.getElementById("zt-interval").value = "3"; saveZentao(); "ok"`);
    const saved = await waitFor(`api("/api/zentao").then(v =>
      JSON.stringify(v.config.products)==="[1,2]" && v.config.poll_enabled===true &&
      v.config.interval_hours===3 && v.config.has_password===true && v.config.password==="")`, 8000);
    check("③ 保存后后端配置落库且 password 脱敏", saved === true);
    const pollOn = await waitFor(
      `document.getElementById("zentao-status").textContent.indexOf("定时扫描已开启") >= 0`, 8000);
    check("③ 状态行显示定时扫描已开启", pollOn === true);

    // ④ 立即扫描对不可达地址优雅报错（连接拒绝 → last_error 上屏）
    await evalJs(`document.getElementById("zt-base-url").value = "http://127.0.0.1:9";
      document.getElementById("zt-password").value = ""; saveZentao(); "ok"`);
    await sleep(600);
    await evalJs(`scanZentao(); "ok"`);
    const errShown = await waitFor(
      `document.getElementById("zentao-status").textContent.indexOf("最近错误") >= 0`, 15000);
    check("④ 扫描不可达地址 → 最近错误上屏", errShown === true);

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
