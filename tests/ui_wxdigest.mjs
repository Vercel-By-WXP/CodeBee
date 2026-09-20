/* 群摘要蜜蜂坞核验：Edge headless + CDP（临时服务端口 18961）。
 * 1) 未读徽章：dock 显示在右下角、徽章计数 2；
 * 2) 点蜜蜂 → 面板开、摘要卡 2 张（群名/正文可见）、打开即 seen 清零（徽章消失+后端归零）；
 * 3) 齿轮 → 配置表单：启用勾选、间隔 30，改监控目录保存 → 后端生效（无弹回）；
 * 4) 立即扫描：无新内容时 toast 完成、后端 last_scan 落账；
 * 5) pet_state 喂食：digest.unseen/latest 透出（桌面蜜蜂的数据源）。 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 18961;
const CDP_PORT = 9391;
const SERVICE = "http://127.0.0.1:" + PORT;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// 长 sleep 后服务端关掉空闲 keep-alive，undici 复用陈旧 socket 会 UND_ERR_SOCKET：
// 失败换新连接重试两次（手动 Connection 头会被 undici 无视，只能重试）。
const jf = async (path, opts = {}) => {
  let lastErr = null;
  for (let a = 0; a < 3; a++) {
    try { return await fetch(SERVICE + path, opts); }
    catch (e) { lastErr = e; await sleep(300); }
  }
  throw lastErr;
};

const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 260)));
};

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), "tutti-wxdigest-"));
  const dataDir = join(tmp, "data");
  const watch = join(tmp, "wx-exports");
  mkdirSync(join(dataDir, "wxdigest"), { recursive: true });
  mkdirSync(watch, { recursive: true });

  // 预置导出文件（2 条，游标已消费到末尾 → 扫描不出新摘要，不触发真模型调用）
  const expTxt = "【产品群】\n2024-09-01 10:00:05 张三\n今天上线新版本\n\n2024/9/1 10:01 李四\n收到\n";
  writeFileSync(join(watch, "产品群.txt"), expTxt, "utf-8");
  const st = statSync(join(watch, "产品群.txt"));

  // jsonl 台账：一行一条记录（模块按行解析，整批数组落单行会被当脏行跳过）；
  // 追加式台账按时间正序落盘（末行=最新，pet_digest 尾窗读的语义）。
  const digests = [
    { id: "d0", group: "老友群", from_ts: "2024-08-31 20:00:00", to_ts: "2024-08-31 20:10:00",
      count: 5, text: "- 总览：周末聚餐定在周六", created_at: "2024-08-31 20:30:00",
      model: "m", provider: "p", source: "老友群.txt" },
    { id: "d1", group: "产品群", from_ts: "2024-09-01 10:00:05", to_ts: "2024-09-01 10:01:00",
      count: 2, text: "- 总览：发版顺利\n- 待办：李四盯支付", created_at: "2024-09-01 10:30:00",
      model: "m", provider: "p", source: "产品群.txt" },
  ];
  writeFileSync(join(dataDir, "wxdigest", "digests.jsonl"),
    digests.map((d) => JSON.stringify(d)).join("\n") + "\n", "utf-8");
  writeFileSync(join(dataDir, "wxdigest.json"), JSON.stringify({
    version: 1,
    config: { enabled: true, watch_dir: watch, interval_minutes: 30, max_chars: 12000 },
    cursors: { "产品群": { last_ts: "2024-09-01 10:01:00", sender: "李四",
                           hash: "seedhash", mtime: st.mtimeMs, size: st.size } },
    unseen: 2, last_scan: "", next_scan: "", last_error: "",
  }), "utf-8");

  let svc = null, edge = null, ws = null, svcLog = "";
  try {
    svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", String(PORT),
      "--no-browser", "--host", "127.0.0.1"], {
      env: { ...process.env, TUTTI_DATA: dataDir, PYTHONPATH: ROOT },
      cwd: ROOT,
    });
    svc.stdout.on("data", (d) => { svcLog += String(d); });
    svc.stderr.on("data", (d) => { svcLog += String(d); });
    let up = false, upErr = "";
    for (let i = 0; i < 40 && !up; i++) {
      await sleep(500);
      try {
        const r = await jf("/api/state");
        await r.text();   // 必须消费响应体，否则半死 socket 被复用 → UND_ERR_SOCKET
        up = r.ok;
      } catch (e) { upErr = (e && e.cause && e.cause.code) || e.message; }
    }
    check("临时服务启动", up, upErr);

    // 后端直查：view / pet_state 喂食（逐个端点打点定位）
    for (const p of ["/api/health", "/api/state", "/api/wxdigest", "/api/pet_state"]) {
      try {
        const r = await fetch(SERVICE + p);
        const t = await r.text();
        check("probe " + p, r.ok, r.status + " len=" + t.length);
      } catch (e) { check("probe " + p, false, (e && e.cause && e.cause.code) || e.message); }
    }
    const v0 = await (await jf("/api/wxdigest")).json();
    check("/api/wxdigest 透出 unseen=2 + 摘要 2 条", v0.unseen === 2 && v0.digests.length === 2,
      JSON.stringify({ unseen: v0.unseen, n: (v0.digests || []).length }));
    check("view 有游标群「产品群」", (v0.groups || []).some((g) => g.name === "产品群"), JSON.stringify(v0.groups));
    const ps = await (await jf("/api/pet_state")).json();
    check("/api/pet_state 喂食 digest（桌面蜜蜂数据源）", ps.digest && ps.digest.unseen === 2 &&
      ps.digest.latest && ps.digest.latest.group === "产品群", JSON.stringify(ps.digest));

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
    check("Edge headless 就绪", !!target);
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) pending.get(m.id)(m); };
    const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
    const evalJs = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 300));
      return r.result?.result?.value;
    };
    await send("Page.enable");
    await send("Runtime.evaluate", { expression: `window.alert=()=>true;window.confirm=()=>true;` });
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(5000);   // 蜜蜂坞 boot 错开首屏 2s + 首次刷新

    /* 1) dock 可见 + 右下角 + 徽章 2 */
    const s1 = await evalJs(`(() => {
      const dock = document.getElementById("bee-dock");
      const fab = document.getElementById("bee-fab");
      const badge = document.getElementById("bee-badge");
      if (!dock || !fab) return JSON.stringify({ missing: true });
      const r = fab.getBoundingClientRect();
      return JSON.stringify({
        hidden: dock.classList.contains("hidden"),
        nearBR: (innerWidth - r.right) < 60 && (innerHeight - r.bottom) < 60,
        badge: badge.classList.contains("hidden") ? "" : badge.textContent,
      });
    })()`);
    const d1 = JSON.parse(s1);
    check("dock 显示且在右下角", !d1.hidden && d1.nearBR, s1);
    check("未读徽章 = 2", d1.badge === "2", s1);

    /* 2) 点蜜蜂 → 面板开 + 摘要卡 + seen 清零 */
    await evalJs(`document.getElementById("bee-fab").click()`);
    await sleep(900);
    const s2 = await evalJs(`(() => {
      const panel = document.getElementById("bee-panel");
      const cards = [...document.querySelectorAll("#bee-list .bee-digest")];
      return JSON.stringify({
        open: !panel.classList.contains("hidden"),
        cards: cards.length,
        firstGroup: cards[0]?.querySelector(".bee-tag")?.textContent,
        hasText: !!cards[0]?.querySelector(".bee-text")?.textContent.includes("发版"),
        badgeGone: document.getElementById("bee-badge").classList.contains("hidden"),
        cfgHidden: document.getElementById("bee-cfg").classList.contains("hidden"),
      });
    })()`);
    const d2 = JSON.parse(s2);
    check("面板打开且摘要卡 2 张", d2.open && d2.cards === 2, s2);
    check("最新摘要在前（产品群/正文可见）", d2.firstGroup === "产品群" && d2.hasText, s2);
    check("打开即 seen：徽章消失", d2.badgeGone, s2);
    check("有数据时默认展示列表不落配置", d2.cfgHidden, s2);
    const v2 = await (await jf("/api/wxdigest")).json();
    check("后端 unseen 归零", v2.unseen === 0, String(v2.unseen));

    /* 3) 齿轮 → 配置表单 + 保存改监控目录（无弹回） */
    await evalJs(`document.getElementById("bee-cfg-btn").click()`);
    await sleep(200);
    const s3 = await evalJs(`(() => {
      const cfg = document.getElementById("bee-cfg");
      return JSON.stringify({
        shown: !cfg.classList.contains("hidden"),
        enabled: document.getElementById("bee-enabled").checked,
        interval: document.getElementById("bee-interval").value,
      });
    })()`);
    const d3 = JSON.parse(s3);
    check("配置表单：启用勾选 + 间隔 30", d3.shown && d3.enabled && d3.interval === "30", s3);
    const newDir = tmp.replace(/\\/g, "\\\\") + "\\\\wx-exports2";
    await evalJs(`(async () => {
      const inp = document.getElementById("bee-dir");
      inp.value = "${newDir}";
      inp.blur();
      document.getElementById("bee-save").click();
      await new Promise((r) => setTimeout(r, 800));
      return document.getElementById("bee-dir").value;
    })()`);
    const v3 = await (await jf("/api/wxdigest")).json();
    check("保存监控目录 → 后端生效（无弹回）", v3.config.watch_dir === join(tmp, "wx-exports2"),
      String(v3.config.watch_dir));

    /* 4) 立即扫描（游标已到末尾 → 暂无新内容，不触发真模型） */
    await evalJs(`document.getElementById("bee-scan-btn").click()`);
    await sleep(1200);
    const s4 = await evalJs(`JSON.stringify({
      toast: (document.getElementById("toast") || {}).textContent || "",
      scanDisabled: document.getElementById("bee-scan-btn").disabled,
    })`);
    const d4 = JSON.parse(s4);
    check("扫描完成 toast 且按钮恢复", !d4.scanDisabled, s4);
    const v4 = await (await jf("/api/wxdigest")).json();
    check("后端 last_scan 落账", !!v4.last_scan, String(v4.last_scan));
  } catch (e) {
    const cause = e && e.cause ? " cause=" + (e.cause.code || e.cause.message || e.cause) : "";
    let alive = "?";
    try { alive = String((await jf("/api/health")).status); } catch (e2) { alive = "DEAD:" + ((e2 && e2.cause && e2.cause.code) || e2.message); }
    check("执行无异常", false, ((e && e.message) || String(e)) + cause + " alive=" + alive + " svcLog=" + svcLog.slice(-1200));
  } finally {
    try { ws && ws.close(); } catch (e) { /* ignore */ }
    try { edge && edge.kill(); } catch (e) { /* ignore */ }
    try { svc && svc.kill(); } catch (e) { /* ignore */ }
    await sleep(600);
    try { rmSync(tmp, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }

  const fails = results.filter((r) => !r.ok);
  console.log(fails.length ? `\nFAIL：${fails.length}/${results.length} 项未过` : `\nALL PASS：${results.length} 项全过`);
  process.exit(fails.length ? 1 : 0);
}

main();
