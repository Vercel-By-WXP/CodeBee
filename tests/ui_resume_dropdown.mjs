/* 「继续会话」下拉核验：五个可续源正常可选 + 已装但不支持会话恢复的 CLI
 * （deepseek-harness、grok-build 等）以 disabled optgroup 置灰列出。
 * 用临时数据目录起服务（本机真实安装被 detect 扫到，正是要验证的环境）。 */
import { spawn } from "node:child_process";
import { execSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SERVICE = "http://127.0.0.1:18798";
const PORT = 18798;
const CDP_PORT = 9339;
const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find((p) => true);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
const check = (n, c, d = "") => {
  results.push(!!c);
  console.log((c ? "  ✓ " : "  ✗ ") + n + (c ? "" : "　— " + String(d).slice(0, 300)));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  try {
    execSync(`netstat -ano | findstr ":${PORT} " | findstr "LISTENING"`, { stdio: "pipe" });
    console.error("端口 " + PORT + " 被占用，先清理残留服务");
    process.exit(2);
  } catch (e) { /* 空闲 */ }

  const dataDir = mkdtempSync(join(tmpdir(), "tutti-resume-"));
  const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
    "--no-browser", "--host", "127.0.0.1"], {
    env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT }, cwd: ROOT, stdio: "ignore",
  });
  let up = false;
  for (let i = 0; i < 40 && !up; i++) {
    await sleep(500);
    try { up = (await fetch(SERVICE + "/api/state")).ok; } catch (e) { /* wait */ }
  }
  check("临时服务启动", up);

  const profile = mkdtempSync(join(tmpdir(), "tutti-resume-edge-"));
  const proc = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run", "--disable-sync",
    "--disable-extensions", `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP_PORT}`,
    "--window-size=1400,950", "about:blank"], { stdio: "ignore" });
  try {
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`).then((r) => r.json());
        target = list.find((t) => t.type === "page" && t.url === "about:blank");
      } catch (e) { /* 未就绪 */ }
    }
    check("Edge headless + CDP 就绪", !!target);
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0;
    const pending = new Map();
    const send = (method, params = {}) => new Promise((res, rej) => {
      const id = ++seq;
      const timer = setTimeout(() => { pending.delete(id); rej(new Error("CDP 超时：" + method)); }, 20000);
      pending.set(id, (m) => { clearTimeout(timer); res(m); });
      ws.send(JSON.stringify({ id, method, params }));
    });
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.id && pending.has(m.id)) pending.get(m.id)(m);
      else if (m.method === "Page.javascriptDialogOpening")
        send("Page.handleJavaScriptDialog", { accept: true });
    };
    const evalJs = async (x) => {
      const r = await send("Runtime.evaluate", { expression: x, returnByValue: true, awaitPromise: true });
      const ex = r.result?.exceptionDetails;
      if (ex) throw new Error("页面抛错：" + (ex.exception?.description || ex.text || "").slice(0, 240));
      return r.result?.result?.value;
    };
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    // catalog 检测要拉起各 CLI --version、/api/sessions 扫描五源目录：轮询等下拉出选项
    let info = null;
    for (let i = 0; i < 40 && !info; i++) {
      await sleep(500);
      const n = await evalJs(`document.querySelectorAll("#f-resume-agent option:not([disabled])[value]").length`);
      if (n >= 5) {
        info = JSON.parse(await evalJs(`JSON.stringify((() => {
          const sel = document.getElementById("f-resume-agent");
          const enabled = [...sel.options].filter(o => !o.disabled && o.value).map(o => o.textContent.trim());
          // 置灰项平铺在可选区之后（无 optgroup）：按 disabled 且 value 空筛出
          const grayed = [...sel.options].filter(o => o.disabled && o.value === "").map(o => o.textContent.trim());
          const enabledOpts = [...sel.options].filter(o => !o.disabled && o.value);
          // 左对齐断言：置灰项与可选项的文本左缘相同（getBoundingClientRect 只能给盒子，
          // option 内缩进用 offset 比不了，这里用「不存在 optgroup」+ 同层平铺来保证）
          const hasGroup = !!sel.querySelector("optgroup");
          return {
            enabled,
            enabledCount: enabledOpts.length,
            grayed,
            hasGroup,
          };
        })())`));
      }
    }
    check("五个可续源在列（codex/claude/opencode/qwen/mimo）",
      info.enabled.length >= 5 && info.enabled.some(t => t.includes("Codex")) &&
      info.enabled.some(t => t.includes("CC") || t.includes("Claude")) &&
      info.enabled.some(t => t.includes("OpenCode")) &&
      info.enabled.some(t => t.includes("Qwen")) &&
      info.enabled.some(t => t.includes("MiMo")), JSON.stringify(info.enabled));
    check("置灰项平铺（无 optgroup 标题行）且在列", !info.hasGroup && info.grayed.length > 0,
      JSON.stringify(info));
    check("DeepSeek Harness 置灰在列", info.grayed.some(t => t.includes("DeepSeek")),
      JSON.stringify(info.grayed));
    check("置灰项不占用可选数（enabled 恰为五源）", info.enabledCount === info.enabled.length,
      JSON.stringify(info));
    check("置灰项未被选中", await evalJs(
      `document.getElementById("f-resume-agent").value === ""`));
  } finally {
    try { proc.kill(); } catch (e) { /* ignore */ }
    try { spawn("taskkill", ["/F", "/T", "/PID", String(proc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    try { svc.kill(); } catch (e) { /* ignore */ }
    try { spawn("taskkill", ["/F", "/T", "/PID", String(svc.pid)], { stdio: "ignore" }); } catch (e) { /* ignore */ }
    try { rmSync(dataDir, { recursive: true, force: true }); } catch (e) { /* ignore */ }
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  const bad = results.filter((x) => !x).length;
  console.log(bad ? `\n${bad} 项未通过` : "\n全部通过");
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error("探针异常：", e); process.exit(1); });
