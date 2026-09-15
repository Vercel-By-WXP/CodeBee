/* 一次性验收：皮肤卡片描述不再溢出卡片边界（全局 button nowrap 被 .skin-card 放开）。
 * 自含临时服务（端口 18799）。用法：node tests/_check_skin_desc.mjs */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import http from "node:http";

const getJson = (url, opts = {}) => new Promise((res, rej) => {
  const req = http.request(url, { method: opts.method || "GET" }, (r) => {
    let b = "";
    r.on("data", (c) => (b += c));
    r.on("end", () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } });
  });
  req.on("error", rej);
  req.end();
});

const PORT = 18799;
const SERVICE = "http://127.0.0.1:" + PORT;
const CDP_PORT = 9353;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const PASS = [], FAIL = [];
function check(name, cond, detail = "") {
  (cond ? PASS : FAIL).push(name);
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 300)));
}

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-skin-"));
  let edge = null, ws = null, svc = null;
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: join(tmp, "data"), PYTHONPATH: ROOT },
      cwd: ROOT, stdio: "ignore",
    });
    let up = false;
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try { up = (await getJson(SERVICE + "/api/state")).ok !== false; } catch (e) { /* wait */ }
    }
    check("临时服务启动", up);

    edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
      `--user-data-dir=${join(tmp, "p")}`, `--remote-debugging-port=${CDP_PORT}`,
      "--window-size=1440,1000", "about:blank"], { stdio: "ignore" });
    let target = null;
    for (let i = 0; i < 30 && !target; i++) {
      await sleep(500);
      try {
        const list = await getJson(`http://127.0.0.1:${CDP_PORT}/json/list`);
        target = list.find((t) => t.type === "page");
      } catch (e) { /* wait */ }
    }
    check("无头 Edge 启动", Boolean(target));
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

    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(4500);

    // 进设置 → 外观子页
    await js(`(() => {
      const btn = document.querySelector('.set-item[data-sub="appearance"]');
      if (btn) btn.click();
      return btn ? "ok" : "no-btn";
    })()`);
    await sleep(1200);

    const r = await js(`(() => {
      const cards = [...document.querySelectorAll("#skin-grid .skin-card")];
      if (!cards.length) return { n: 0 };
      return {
        n: cards.length,
        ws: getComputedStyle(cards[0]).whiteSpace,
        servedCssNew: true,
        cards: cards.map((c) => {
          const d = c.querySelector(".skin-desc");
          const cr = c.getBoundingClientRect(), dr = d ? d.getBoundingClientRect() : null;
          return {
            name: (c.querySelector(".skin-name") || {}).textContent || "",
            cardWs: getComputedStyle(c).whiteSpace,
            descWs: d ? getComputedStyle(d).whiteSpace : "",
            overflowX: c.scrollWidth - c.clientWidth,
            descOutside: dr ? +(dr.right - cr.right).toFixed(1) : null,
            descLines: d ? Math.round(d.getBoundingClientRect().height / (11.5 * 1.5)) : 0,
          };
        }),
      };
    })()`);
    check("皮肤卡片已渲染 6 张", r.n === 6, JSON.stringify(r).slice(0, 120));
    check(".skin-card white-space 已放开为 normal", r.ws === "normal", "got=" + r.ws);
    for (const c of (r.cards || [])) {
      check(`「${c.name}」描述不出卡片右缘（超出=${c.descOutside}px, 横向滚动=${c.overflowX}px, 行数=${c.descLines}）`,
        c.descOutside <= 0.5 && c.overflowX <= 1,
        JSON.stringify(c));
    }
  } finally {
    try { ws && ws.close(); } catch (e) {}
    try { edge && edge.kill(); } catch (e) {}
    try { svc && svc.kill(); } catch (e) {}
    await sleep(400);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) {}
  }
  console.log(`\n结果：${PASS.length} 通过，${FAIL.length} 失败`);
  process.exit(FAIL.length ? 1 : 0);
}
main();
