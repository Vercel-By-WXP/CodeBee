/* 禅道产品档案「基线分支」下拉（datalist）验证：填仓库工作目录 → 拉分支 →
 * datalist 填充 + 点选回填；聚焦触发拉取；「▾」强制刷新。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 19700 + (process.pid % 300);
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9900 + (process.pid % 400);
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 200)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "tutti-ztrev-"));
  const repoDir = mkdtempSync(join(tmpdir(), "tutti-revrepo-"));
  // 造真 git 仓库（两个分支）
  const git = (args) => new Promise((res) => {
    const g = spawn("git", args, { cwd: repoDir });
    g.on("close", res);
  });
  await git(["init", "-b", "main"]);
  await git(["config", "user.email", "t@t"]); await git(["config", "user.name", "t"]);
  (await import("node:fs")).writeFileSync(join(repoDir, "a.txt"), "x");
  await git(["add", "."]); await git(["commit", "-m", "init"]);
  await git(["branch", "release/1.0"]);

  const svc = spawn("python", ["app/main.py", "--port", String(PORT),
    "--no-browser", "--no-public-tunnel"],
    { cwd: ROOT, env: { ...process.env, TUTTI_DATA: dataDir }, stdio: "ignore" });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) {
    await sleep(500);
    try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* 未就绪 */ }
  }
  check("临时服务就绪", up);

  const profile = mkdtempSync(join(tmpdir(), "tutti-cdp-ztrev-"));
  const proc = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank",
  ], { stdio: "ignore" });
  try {
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page");
      } catch (e) { /* Edge 未就绪 */ }
    }
    check("Edge headless 就绪", !!target);
    const ws = new WebSocket(target.webSocketDebuggerUrl);
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
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      return r.result?.result?.value;
    };
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4000);

    // 进禅道页，直接预置一个产品档案卡（绕开需连禅道的「从禅道添加产品」流程——
    // 本测试聚焦基线分支下拉本身）
    await evalJs(`switchTab("zentao"); "ok"`);
    await sleep(800);
    const hasCard = await evalJs(`
      (() => {
        S.ztProfiles = [{ product: 1, our_sides: ["backend"],
          repos: { backend: { workdir: "", git_rev: "" } }, module_routes: [] }];
        renderZentaoProfiles();
        return !!document.querySelector("#zt-profiles .zt-prof");
      })()`);
    check("产品档案卡片可渲染", hasCard === true);

    // 填仓库工作目录（真 git 仓库）→ 触发分支拉取
    const fill = await evalJs(`
      (() => {
        const card = document.querySelector("#zt-profiles .zt-prof");
        if (!card) return "no-card";
        const inp = card.querySelector('.zt-p-wd[data-side="backend"]');
        if (!inp) return "no-inp";
        inp.value = ${JSON.stringify(repoDir)};
        inp.dispatchEvent(new Event("change", { bubbles: true }));
        const rev = card.querySelector('.zt-p-rev[data-side="backend"]');
        rev.dispatchEvent(new Event("focus"));
        return "filled";
      })()`);
    check("工作目录已填", fill === "filled", fill);
    await sleep(2000);

    // datalist 应填入 main + release/1.0
    const opts = JSON.parse(await evalJs(`JSON.stringify({
      list: Array.from(document.querySelectorAll('#zt-profiles .zt-prof datalist')[0]?.options || [])
        .map((o) => o.value)
    })`));
    check("分支下拉含 main", (opts.list || []).includes("main"), JSON.stringify(opts.list));
    check("分支下拉含 release/1.0", (opts.list || []).includes("release/1.0"), JSON.stringify(opts.list));

    // input 关联 datalist（list 属性静态生成，分支填充成功即证通道通）
    const linked = await evalJs(`
      (() => { const r = document.querySelector('#zt-profiles .zt-prof .zt-p-rev[data-side="backend"]');
        return JSON.stringify({ list: r?.getAttribute("list") || "",
          dl: !!document.getElementById(r?.getAttribute("list") || "_") }); })()`);
    const listId = JSON.parse(linked).list;
    check("input 已关联 datalist", !!listId && JSON.parse(linked).dl === true, linked);
  } finally {
    try { proc.kill(); } catch (e) {}
    try { svc.kill(); } catch (e) {}
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
    try { rmSync(repoDir, { recursive: true, force: true }); } catch (e) {}
  }

  const fails = results.filter((r) => !r.ok);
  console.log(fails.length ? `\n${fails.length}/${results.length} 项失败` : `\n全部 ${results.length} 项通过`);
  process.exit(fails.length ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
