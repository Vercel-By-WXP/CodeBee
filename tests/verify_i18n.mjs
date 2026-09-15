// Edge headless + CDP 验证 i18n：
//   1. 默认中文（data-lang="zh"）— 抽取关键中文文本
//   2. 切到英文（localStorage.orch.lang=en + applyI18n）— 验证全部变英
//   3. 切回中文 — 验证回落
const wsUrl = process.argv[2] || "ws://127.0.0.1:9222/devtools/page/";
const appUrl = process.argv[3] || "http://127.0.0.1:18801/";
const TOKEN = process.argv[4] || "";

let msgId = 0;
const pending = new Map();

function send(ws, method, params) {
  const id = ++msgId;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
}

(async () => {
  const targetsRes = await fetch("http://127.0.0.1:9222/json");
  const targets = await targetsRes.json();
  const target = targets.find((t) => t.type === "page") || targets[0];
  console.log("connected to", target.url);

  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const { resolve, reject } = pending.get(m.id);
      pending.delete(m.id);
      if (m.error) reject(new Error(m.error.message));
      else resolve(m.result);
    }
  };

  await send(ws, "Page.enable");
  await send(ws, "Runtime.enable");

  async function navigate(url) {
    await send(ws, "Page.navigate", { url });
    // 等待 load + 一点缓冲
    await new Promise((r) => setTimeout(r, 1500));
  }

  async function evalJs(expr) {
    const r = await send(ws, "Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) {
      console.error("EVAL EXC:", r.exceptionDetails.text);
      throw new Error("JS exception");
    }
    return r.result.value;
  }

  async function snapshot() {
    return await evalJs(`(() => {
      const grab = (sel) => Array.from(document.querySelectorAll(sel)).map(e => e.textContent.trim()).filter(Boolean);
      return {
        lang: document.documentElement.dataset.lang,
        title: document.title,
        // 侧栏关键文本
        brand: document.querySelector(".brand-name")?.textContent?.trim() || "",
        newTaskBtn: document.querySelector("#btn-new-task")?.textContent?.trim() || "",
        tasksLabel: document.querySelector(".side-label")?.textContent?.trim() || "",
        provSideText: document.querySelector("#prov-side-text")?.textContent?.trim() || "",
        // 设置入口标题（点进设置后才显示）
        setItems: Array.from(document.querySelectorAll(".set-item")).map(e => e.textContent.trim()),
        // 顶栏
        pageTitle: document.querySelector("#page-title")?.textContent?.trim() || "",
        conn: document.querySelector("#conn")?.textContent?.trim() || "",
        // 视图标题
        hero: document.querySelector(".hero")?.textContent?.trim() || "",
        heroSub: document.querySelector(".hero-sub")?.textContent?.trim() || "",
        // 外观页关键文本（仅切到该页后）
        appearanceTitles: Array.from(document.querySelectorAll("#sub-appearance h3")).map(e => e.textContent.trim()),
        langModeBtns: Array.from(document.querySelectorAll("#lang-mode .seg-btn")).map(e => ({ lang: e.dataset.lang, text: e.textContent.trim(), active: e.classList.contains("active") })),
      };
    })()`);
  }

  // Step 1: 初始进入，默认中文
  await navigate(appUrl + (TOKEN ? "?token=" + TOKEN : ""));
  console.log("\n=== Step 1: 默认中文 ===");
  const zhSnap = await snapshot();
  console.log(JSON.stringify(zhSnap, null, 2));

  // Step 2: 进设置 → 外观 → 切英文
  await evalJs(`document.querySelector("#btn-settings").click()`);
  await new Promise((r) => setTimeout(r, 600));
  // 切到 appearance 子页
  await evalJs(`document.querySelector('[data-sub="appearance"]').click()`);
  await new Promise((r) => setTimeout(r, 600));

  console.log("\n=== Step 2: 进入外观页（中文） ===");
  const zhAppearance = await snapshot();
  console.log("appearanceTitles:", zhAppearance.appearanceTitles);
  console.log("langModeBtns:", zhAppearance.langModeBtns);

  // 点 English
  await evalJs(`document.querySelector('#lang-mode [data-lang="en"]').click()`);
  await new Promise((r) => setTimeout(r, 1500));  // 重画

  console.log("\n=== Step 3: 切到英文 ===");
  const enSnap = await snapshot();
  console.log(JSON.stringify(enSnap, null, 2));

  // Step 4: 切回中文
  await evalJs(`document.querySelector('#lang-mode [data-lang="zh"]').click()`);
  await new Promise((r) => setTimeout(r, 1500));

  console.log("\n=== Step 4: 切回中文 ===");
  const zhBack = await snapshot();
  console.log(JSON.stringify(zhBack, null, 2));

  ws.close();
  process.exit(0);
})().catch((e) => { console.error("FAIL:", e); process.exit(1); });