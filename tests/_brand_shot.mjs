// 品牌区截图验证：加载页面 → 截侧栏品牌区 + 整页 → dump 品牌文本
import { writeFileSync } from "fs";
const OUT1 = new URL("./_brand_area.png", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const OUT2 = new URL("./_full_page.png", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");

const targets = await (await fetch("http://127.0.0.1:9222/json")).json();
const target = targets.find((t) => t.type === "page" && t.url === "about:blank") || targets.find((t) => t.type === "page");
const ws = new WebSocket(target.webSocketDebuggerUrl);
let msgId = 0;
const pending = new Map();
const send = (method, params) => new Promise((resolve, reject) => {
  const id = ++msgId;
  pending.set(id, { resolve, reject });
  ws.send(JSON.stringify({ id, method, params }));
});
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) {
    const { resolve, reject } = pending.get(m.id);
    pending.delete(m.id);
    m.error ? reject(new Error(m.error.message)) : resolve(m.result);
  }
};
await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
await send("Page.enable");
await send("Runtime.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });
await send("Page.navigate", { url: "http://127.0.0.1:18801/" });
await new Promise((r) => setTimeout(r, 2500));

const brand = await send("Runtime.evaluate", { returnByValue: true, expression: `(() => {
  const b = document.querySelector(".brand");
  const r = b.getBoundingClientRect();
  const sym = document.querySelector("#i-logo");
  return { brandText: b.textContent.trim(), brandRect: {x:r.x,y:r.y,w:r.width,h:r.height},
           collapsed: document.body.classList.contains("side-collapsed"),
           hasPolygon: !!sym.querySelector("polygon"), hasWings: !!sym.querySelector("ellipse"),
           title: document.title, sub: document.querySelector(".brand .sub")?.textContent };
})()` });
console.log("BRAND:", JSON.stringify(brand.result.value, null, 2));

// 截图 1：侧栏顶部品牌区（2x 放大裁剪）
const s1 = await send("Page.captureScreenshot", { format: "png",
  clip: { x: 0, y: 0, width: 300, height: 130, scale: 2 } });
writeFileSync(OUT1, Buffer.from(s1.data, "base64"));
// 截图 2：整页
const s2 = await send("Page.captureScreenshot", { format: "png" });
writeFileSync(OUT2, Buffer.from(s2.data, "base64"));
console.log("screenshots saved:", OUT1, OUT2);
ws.close();
process.exit(0);