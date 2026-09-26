/* 智能体目录「一键升级全部」验收：
 * 1) 目录页头部有「一键升级全部」按钮
 * 2) 点击后：已安装且配了升级命令的条目批量起 mgmt run（就地面板跟踪）
 * 3) 运行中再点：toast 报「正忙于其他管理操作」（去重闸在 UI 的回声）
 * 4) 跑完后再点：能起新 run（幂等重跑）
 * 5) 只配 install 没配 upgrade 的条目从不进批量（否则起必败 run）
 * 自含临时服务（端口 18963）：TUTTI_DATA + USERPROFILE 双重指到临时目录；
 * TUTTI_TEST_NO_DEFAULT_CATALOG=1 只加载种盘条目——默认合并会把真实 CLI
 * 条目带进来，一键升级会把真机的 npm/winget 升级真跑一遍（2026-09-26 实弹案）。
 * 用法：node tests/ui_catalog_upgrade_all.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18963;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9382;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find(() => true);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const PASS = [], FAIL = [];
function check(name, cond, detail = "") {
  (cond ? PASS : FAIL).push(name);
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 220)));
}

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-upall-ui-"));
  const dataDir = join(tmp, "data");
  const fakeHome = join(tmp, "home");
  mkdirSync(dataDir, { recursive: true });
  mkdirSync(join(fakeHome, "faketools"), { recursive: true });
  for (const f of ["up-a.exe", "up-b.exe"])
    writeFileSync(join(fakeHome, "faketools", f), "fake");
  // up-a：已安装 + 配了慢升级命令（约 4s）→ 应被批量受理并就地跟踪
  // up-b：已安装但只配 install 没配 upgrade → 不收（收了只会起必败 run）
  writeFileSync(join(dataDir, "catalog.json"), JSON.stringify([
    { id: "up-a", name: "升级甲", note: "测试条目 A", cli_group: "installed",
      detect: { exe: "~/faketools/up-a.exe" },
      install: "ping -n 1 127.0.0.1", upgrade: "ping -n 4 127.0.0.1",
      default_enabled: false },
    { id: "up-b", name: "升级乙", note: "测试条目 B", cli_group: "installed",
      detect: { exe: "~/faketools/up-b.exe" },
      install: "ping -n 1 127.0.0.1",
      default_enabled: false },
  ]));

  let edge = null, ws = null, svc = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: dataDir, USERPROFILE: fakeHome, PYTHONPATH: ROOT,
             TUTTI_TEST_NO_DEFAULT_CATALOG: "1" },
      cwd: ROOT, stdio: "ignore",
    });
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* wait */ }
    }
    check("临时服务启动（端口 " + PORT + "，home=临时目录，无默认 catalog）", up);

    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "p")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1440,1000", "about:blank"], { stdio: "ignore" });
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* wait */ }
    }
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params }));
    });
    const js = async (expr) => (await send("Runtime.evaluate",
      { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;

    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);
    await js(`switchTab("agents"); "ok"`);
    let pre = null;
    for (let i = 0; i < 40; i++) {
      pre = await js(`(() => {
        const names = [...document.querySelectorAll("#ag-installed .card .head .name")].map(x => x.textContent.trim());
        const btn = document.getElementById("btn-catalog-upgrade-all");
        return { names, btnThere: !!btn, btnVisible: !!(btn && btn.offsetParent) };
      })()`);
      if (["升级甲", "升级乙"].every((n) => (pre.names || []).includes(n))) break;
      await sleep(1000);
    }
    check("目录：测试条目已安装渲染（升级甲/升级乙）",
      ["升级甲", "升级乙"].every((n) => (pre.names || []).includes(n)), JSON.stringify(pre.names));
    check("目录：「一键升级全部」按钮存在且可见", pre.btnThere && pre.btnVisible, JSON.stringify(pre));

    const panelOf = (name) => `(() => {
      const card = [...document.querySelectorAll("#ag-installed .card")]
        .find(c => c.querySelector(".head .name").textContent.trim() === "${name}");
      if (!card) return null;
      const p = card.querySelector(".mgmt-panel");
      return p ? { cls: p.className, title: (p.querySelector(".mgmt-title") || {}).textContent || "" } : null;
    })()`;

    // 第一次点击：批量受理 up-a
    await js(`document.getElementById("btn-catalog-upgrade-all").click(); "clicked"`);
    await sleep(1800);
    const toast1 = await js(`(document.getElementById("toast") || {}).textContent || ""`);
    check("一键升级：toast 报告批量受理数量", /已开始升级 1 个智能体/.test(toast1 || ""), toast1);
    const pa = await js(panelOf("升级甲"));
    check("一键升级：升级甲出现升级面板", !!pa && /升级/.test(pa.title || ""), JSON.stringify(pa));
    const pb = await js(panelOf("升级乙"));
    check("一键升级：升级乙（无 upgrade 字段）不进批量", !pb, JSON.stringify(pb));

    // 运行中再点：busy 回声
    await js(`document.getElementById("btn-catalog-upgrade-all").click(); "clicked2"`);
    await sleep(1500);
    const toast2 = await js(`(document.getElementById("toast") || {}).textContent || ""`);
    check("一键升级：运行中再点提示忙于其他操作",
      (toast2 || "").indexOf("正忙于其他管理操作") >= 0, toast2);

    // 等第一次跑完（约 4s 命令 + 轮询），面板翻终态
    let doneSeen = false;
    for (let i = 0; i < 30; i++) {
      const p = await js(panelOf("升级甲"));
      if (p && /done/.test(p.cls)) { doneSeen = true; break; }
      await sleep(1000);
    }
    check("一键升级：升级甲面板翻完成态", doneSeen, JSON.stringify(await js(panelOf("升级甲"))));

    // 跑完后第三次点击：起新 run
    await js(`document.getElementById("btn-catalog-upgrade-all").click(); "clicked3"`);
    await sleep(1800);
    const toast3 = await js(`(document.getElementById("toast") || {}).textContent || ""`);
    check("一键升级：跑完后再点能起新 run", /已开始升级 1 个智能体/.test(toast3 || ""), toast3);
    let done2 = false;
    for (let i = 0; i < 30; i++) {
      const p = await js(panelOf("升级甲"));
      if (p && /done/.test(p.cls)) { done2 = true; break; }
      await sleep(1000);
    }
    check("一键升级：第二次批量也跑完", done2, "");

    // 英文界面：按钮文案走 t() 字典
    const enBtn = await js(`setLang("en"); applyI18n();
      document.getElementById("btn-catalog-upgrade-all").textContent.trim()`);
    check("i18n：英文界面按钮文案", enBtn === "Upgrade all", enBtn);
    await js(`setLang("zh"); applyI18n(); "zh-back"`);

    ws.close();
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    try { svc && svc.kill(); } catch (e) { /* ignore */ }
    await sleep(700);
    if (edge?.pid) {
      try { spawn("taskkill", ["/F", "/T", "/PID", String(edge.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    }
    if (svc?.pid) {
      try { spawn("taskkill", ["/F", "/T", "/PID", String(svc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    }
    await sleep(500);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  console.log("\n===== 一键升级全部验收：%d 通过 / %d 失败 =====", PASS.length, FAIL.length);
  if (FAIL.length) { console.log("失败项：", FAIL); process.exit(1); }
  process.exit(0);
}

main();
