/* 手机端新任务页样式体检探针（只读，不建任务）。
 * 用法：SERVICE=http://127.0.0.1:18961 node tests/_probe_mobile_style.mjs
 * 输出：手机视口截图 + 关键元素几何/样式 + 右缘命中元素排查。 */
import { writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { bootEdge } from "./_ui_boot.mjs";

const SERVICE = process.env.SERVICE || "http://127.0.0.1:18961";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const OUT = join(ROOT, "scratch");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await bootEdge({ width: 390, height: 844 });
try {
  console.log(browser.envInfo.line);
  await browser.send("Page.enable", {});
  // 真机几何：iPhone 13 级别 390x844 DPR3 + 触摸 + 移动 UA，媒体查询才按手机走
  await browser.send("Emulation.setDeviceMetricsOverride", {
    width: 390, height: 844, deviceScaleFactor: 3, mobile: true,
  });
  await browser.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
  await browser.send("Emulation.setEmitTouchEventsForMouse", { enabled: true, configuration: "mobile" });
  await browser.send("Page.navigate", { url: SERVICE + "/" });
  await sleep(5000);

  // 切到「任务」子页（等价点左栏任务轨），空数据下直接露 composer；跳过首启欢迎弹窗
  await browser.evalJs(`
    (function () {
      try { localStorage.setItem("subpage", "tasks"); } catch (e) {}
      if (typeof window.showSub === "function") { try { window.showSub("tasks"); } catch (e) {} }
      if (typeof window.welcomeClose === "function") { try { window.welcomeClose(); } catch (e) {} }
      const w = document.getElementById("welcome");
      if (w) w.classList.add("hidden");
      return true;
    })()
  `);
  await sleep(2500);

  const report = await browser.evalJs(`
    (function () {
      const pick = (sel) => {
        const el = document.querySelector(sel);
        if (!el) return null;
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        return { sel, x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
                 textAlign: cs.textAlign, margin: cs.margin, minH: cs.minHeight, fs: cs.fontSize,
                 display: cs.display, pos: cs.position };
      };
      const sels = [".hero", ".hero-sub", ".composer", ".cmp-box", "#f-goal", ".cmp-bar",
                    "#f-type-btn", "#f-expert", ".cmp-quick", "#f-advanced > summary", "main"];
      const out = { page: location.hash || "tasks", els: [], rightEdge: [] };
      for (const s of sels) { const e = pick(s); if (e) out.els.push(e); }
      // 右缘 30px 竖带命中元素（截图灰块排查）
      const seen = new Set();
      for (let y = 80; y < innerHeight - 40; y += 12) {
        const el = document.elementFromPoint(innerWidth - 8, y);
        if (!el) continue;
        const key = el.tagName + "." + (el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className) + "#" + el.id;
        if (!seen.has(key)) {
          seen.add(key);
          const r = el.getBoundingClientRect();
          const cs = getComputedStyle(el);
          out.rightEdge.push({ key, y, x: Math.round(r.x), w: Math.round(r.width), h: Math.round(r.height),
                               bg: cs.backgroundColor, radius: cs.borderRadius, text: (el.textContent || "").trim().slice(0, 30) });
        }
      }
      // 溢出视口宽度的元素（横向溢出排查）
      out.overflow = [];
      document.querySelectorAll("body *").forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && (r.right > innerWidth + 1 || r.left < -1)) {
          const cs = getComputedStyle(el);
          if (cs.position === "fixed" || cs.position === "absolute") {
            out.overflow.push({ tag: el.tagName, id: el.id, cls: String(el.className).slice(0, 40),
                                left: Math.round(r.left), right: Math.round(r.right), w: Math.round(r.width), pos: cs.position });
          }
        }
      });
      out.overflow = out.overflow.slice(0, 20);
      out.scrollW = document.documentElement.scrollWidth; out.innerW = innerWidth;
      out.heroSubText = (document.querySelector(".hero-sub") || {}).textContent || "";
      return JSON.stringify(out);
    })()
  `);
  console.log(report);

  const shot = await browser.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
  if (!shot || !shot.result || !shot.result.data) throw new Error("captureScreenshot 无数据");
  writeFileSync(join(OUT, "mobile-style-after.png"), Buffer.from(shot.result.data, "base64"));
  console.log("screenshot -> scratch/mobile-style-after.png");
} finally {
  await browser.close();
}
