/* 定向 i18n 核验（夹在并发 agent 抢用 9222 时的替代）：连自己的 CDP 端口，验证
 * 1) 默认中文：文件夹行显示目录末段名，无主运行文件夹显示「其他」；
 * 2) 切英文后重绘：「其他」→ Other（t() 字典命中），目录名不翻译（保持末段名）；
 * 3) 图标+文字混合按钮（#btn-set-back）切语言后文字仍在图标右侧（setElementText 修复）。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
const APP = process.env.APP || "http://127.0.0.1:18798/";
const CDP = Number(process.env.CDP || 9244);
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const profile = mkdtempSync(tmpdir() + "/i18nchk-");
const proc = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run", "--disable-sync",
  `--user-data-dir=${profile}`, `--remote-debugging-port=${CDP}`, "--window-size=1400,950", "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const check = (n, c, d = "") => { results.push(!!c); console.log((c ? "  ✓ " : "  ✗ ") + n + (c ? "" : "　— " + String(d).slice(0, 200))); };
let target = null;
for (let i = 0; i < 30 && !target; i++) {
  await sleep(500);
  try { const l = await fetch(`http://127.0.0.1:${CDP}/json/list`).then((r) => r.json());
    target = l.find((t) => t.type === "page" && (t.url === "about:blank" || /18798/.test(t.url))); } catch (e) {}
}
check("Edge 启动（CDP " + CDP + "）", !!target);
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
let seq = 0; const pending = new Map();
const send = (m, p = {}) => new Promise((res, rej) => { const id = ++seq;
  const timer = setTimeout(() => { pending.delete(id); rej(new Error("CDP 超时 " + m)); }, 20000);
  pending.set(id, (mm) => { clearTimeout(timer); res(mm); }); ws.send(JSON.stringify({ id, method: m, params: p })); });
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
const ev = async (x) => { const r = await send("Runtime.evaluate", { expression: x, returnByValue: true, awaitPromise: true });
  const ex = r.result?.exceptionDetails; if (ex) throw new Error((ex.exception?.description || ex.text || "").slice(0, 200)); return r.result?.result?.value; };
await send("Page.enable");
await send("Page.navigate", { url: APP });
await sleep(4000);
for (let i = 0; i < 20; i++) { const n = await ev(`document.querySelectorAll("#side-tasks .stask").length`); if (n > 0) break; await sleep(500); }

/* 1) 默认中文：t("其他") 与目录名 */
const zh = JSON.parse(await ev(`(() => {
  const labels = [...document.querySelectorAll("#side-tasks details.sdir summary .t")].map(x => x.textContent.trim());
  return JSON.stringify({ lang: document.documentElement.dataset.lang, other: t("其他"), labels });
})()`));
check("默认中文 lang=zh", zh.lang === "zh", JSON.stringify(zh));
check("中文 t(其他)=其他", zh.other === "其他", zh.other);
check("文件夹行显示目录末段名（无斜杠）", zh.labels.every((l) => !/[\\/]/.test(l)), JSON.stringify(zh.labels));

/* 2) 切英文后重绘：其他→Other，目录名不变 */
const en = JSON.parse(await ev(`(() => {
  localStorage.setItem("orch.lang", "en");
  document.documentElement.dataset.lang = "en";
  if (typeof applyI18n === "function") applyI18n();
  S.sideSig = null; renderSideTasks();
  const labels = [...document.querySelectorAll("#side-tasks details.sdir summary .t")].map(x => x.textContent.trim());
  return JSON.stringify({ other: t("其他"), labels });
})()`));
check("英文 t(其他)=Other", en.other === "Other", en.other);
check("英文下目录名仍不翻译（proj-* 保持）", en.labels.some((l) => /proj-/.test(l)), JSON.stringify(en.labels));

/* 3) 图标+文字按钮切语言后文字仍在图标右侧（setElementText 落点修复） */
await ev(`localStorage.setItem("orch.lang","zh"); document.documentElement.dataset.lang="zh"; if(typeof applyI18n==="function") applyI18n(); "ok"`);
await ev(`document.getElementById("btn-settings").click(); "ok"`);
await sleep(900);
const back = JSON.parse(await ev(`(() => {
  const b = document.getElementById("btn-set-back");
  const svg = b.querySelector("svg.ico").getBoundingClientRect();
  const tn = [...b.childNodes].find(n => n.nodeType === 3 && n.textContent.trim());
  const rg = document.createRange(); rg.selectNodeContents(tn);
  const t = rg.getBoundingClientRect();
  return JSON.stringify({ text: tn.textContent.trim(), textRightOfIcon: t.left >= svg.right });
})()`));
check("「返回」文字在图标右侧（未被塞到 svg 前）", back.textRightOfIcon, JSON.stringify(back));

ws.close(); proc.kill();
try { spawn("taskkill", ["/F", "/T", "/PID", String(proc.pid)], { stdio: "ignore" }); } catch (e) {}
await sleep(500);
try { rmSync(profile, { recursive: true, force: true }); } catch (e) {}
const bad = results.filter((x) => !x).length;
console.log("\n===== 定向 i18n 核验：%d 通过 / %d 失败 =====", results.length - bad, bad);
if (bad) process.exit(1);
