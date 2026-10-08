/* 手机端全流程回归：Edge headless + CDP 设备模拟（390x844 DPR2 mobile + 真触摸事件）。
 * 背景：皮肤层 ≤860px 曾把抽屉 #sidebar z-index 压到 30、低于遮罩 #drawer-mask 的 55——
 * 抽屉整层被半透明遮罩盖住，抽屉里点什么都落在遮罩上（=关抽屉），主区同时被盖，
 * 手机端「无法操作」实案（2026-10-08）。本测试钉住整条链：
 *   初始收起 → 触摸开抽屉 → 抽屉可命中可点（elementFromPoint 不落遮罩）→
 *   点项自动收回 → 遮罩点击关闭 → 新建任务全链路 → 详情/各页切换 → 桌面恢复。
 * 图标 closest 命中路径由 ui_mobile.mjs 覆盖；本文件聚焦触摸全流程与 z 层级。
 * 用法：先起临时数据目录服务，再 SERVICE=http://127.0.0.1:<port> node tests/ui_mobile_flow.mjs */
import { writeFileSync, mkdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { bootEdge } from "./_ui_boot.mjs";

const SERVICE = process.env.SERVICE || "http://127.0.0.1:18933";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (cond ? "" : "　— " + String(detail).slice(0, 220)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  // 窗口起桌面尺寸：clearDeviceMetricsOverride 后要落回 ≥900px 验证桌面布局不回归
  const { ws, send, evalJs, envInfo, close } = await bootEdge({ width: 1440, height: 950 });
  console.log("  · " + envInfo.line);

  // 真触摸点按：CDP touchStart/touchEnd（浏览器合成 click），坐标=元素中心
  const tap = async (sel) => {
    const raw = await evalJs(`(()=>{const el=document.querySelector(${JSON.stringify(sel)});
      if(!el) return "null"; const r=el.getBoundingClientRect();
      return JSON.stringify({x:r.x+r.width/2, y:r.y+r.height/2, w:r.width, h:r.height});})()`);
    if (!raw || raw === "null") return false;
    const p = JSON.parse(raw);
    if (!(p.w > 0)) return false;
    await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: p.x, y: p.y }] });
    await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    return true;
  };
  const tapXY = async (x, y) => {
    await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
    await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  };
  const hitAt = (x, y) => evalJs(`(()=>{const el=document.elementFromPoint(${x},${y});
    if(!el) return "none"; return el.closest("#sidebar") ? "sidebar"
      : (el.id === "drawer-mask" || el.closest("#drawer-mask")) ? "mask" : "other";})()`);

  try {
    await send("Page.enable");
    // 先切手机视口再导航：与真机一致（首屏就是窄视口，init 判 innerWidth<900 收抽屉）
    await send("Emulation.setDeviceMetricsOverride",
      { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
    await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
    await send("Page.navigate", { url: SERVICE + "/" });
    await sleep(3500);
    // 首启引导会盖住触摸目标：记账关掉（引导自身由 ui_welcome.mjs 覆盖）
    await evalJs(`localStorage.setItem("orch.welcomed","1"); try { welcomeClose(); } catch (e) {} "ok"`);
    await sleep(300);

    const vw = JSON.parse(await evalJs(`JSON.stringify({w: innerWidth, h: innerHeight})`));
    check("手机视口生效（innerWidth=390）", vw.w === 390, JSON.stringify(vw));

    // ---- 1. 初始态：抽屉收起、遮罩隐藏、主区可命中
    check("初始抽屉收起（side-collapsed）",
      (await evalJs(`document.body.classList.contains("side-collapsed")`)) === true);
    check("初始遮罩不显示",
      (await evalJs(`getComputedStyle(document.getElementById("drawer-mask")).display`)) === "none",
      await evalJs(`getComputedStyle(document.getElementById("drawer-mask")).display`));
    check("主区中心可命中（不落遮罩）", (await hitAt(260, 400)) === "other", await hitAt(260, 400));
    check("导航轨可命中", (await hitAt(20, 300)) === "other", await hitAt(20, 300));

    // ---- 2. 触摸汉堡开抽屉 + 核心层级断言
    check("汉堡按钮可触摸", (await tap("#btn-menu")) === true);
    await sleep(400);
    check("触摸汉堡 → 抽屉打开",
      (await evalJs(`!document.body.classList.contains("side-collapsed")`)) === true);
    const zSide = Number(await evalJs(`getComputedStyle(document.getElementById("sidebar")).zIndex`));
    const zMask = Number(await evalJs(`getComputedStyle(document.getElementById("drawer-mask")).zIndex`));
    check("抽屉 z-index 高于遮罩（修复核心：60 > 55）", zSide > zMask, `sidebar=${zSide} mask=${zMask}`);
    check("抽屉区域命中抽屉本身（不落遮罩）", (await hitAt(150, 300)) === "sidebar", await hitAt(150, 300));

    // ---- 3. 触摸抽屉内任务树任务 → 点了有反应 + 抽屉自动收回
    //（快捷导航/侧栏底脚已被导航轨改造有意隐藏 b1f1116，抽屉内可点项=任务树。
    //  mob2 没跑过：预期 toast「还没跑过」——「点了有反馈」正是无法操作的反面；
    //  详情打开链路由第 6 步用有种盘 run 的 mob1 覆盖。）
    await evalJs(`(()=>{const d=document.querySelector('#side-tasks details.sdir'); if(d) d.open=true; "ok";})()`);
    await sleep(200);
    const mob2Hit = await evalJs(`(()=>{let el=document.querySelector('#side-tasks .stask[data-key="mob2"]')
        || Array.from(document.querySelectorAll("#side-tasks .stask")).find(x=>x.textContent.includes("第二任务"));
      if(!el) return "null"; const r=el.getBoundingClientRect();
      return JSON.stringify({x:r.x+r.width/2,y:r.y+r.height/2,vis:r.width>0&&r.y>0&&r.y<innerHeight});})()`);
    let mob2Feedback = false, mob2Note = "mob2Hit=" + mob2Hit;
    if (mob2Hit !== "null") {
      const p = JSON.parse(mob2Hit);
      if (p.vis) {
        await tapXY(p.x, p.y);
        await sleep(900);
        mob2Feedback = await evalJs(`(()=>{const tEl=document.getElementById("toast");
          return !!(tEl && tEl.textContent.includes("还没跑过"));})()`);
      }
    }
    check("点抽屉内未跑任务 → toast 反馈（点了有反应）", mob2Feedback === true, mob2Note);
    check("点抽屉内项后抽屉自动收回",
      (await evalJs(`document.body.classList.contains("side-collapsed")`)) === true);

    // ---- 4. 再开抽屉 → 触摸遮罩关闭
    await tap("#btn-menu"); await sleep(400);
    check("抽屉打开时遮罩显示",
      (await evalJs(`getComputedStyle(document.getElementById("drawer-mask")).display`)) === "block");
    await tapXY(300, 500); await sleep(400);
    check("触摸遮罩 → 抽屉关闭",
      (await evalJs(`document.body.classList.contains("side-collapsed")`)) === true);

    // ---- 5. 新建任务全链路（抽屉内点「新任务」→ 表单 → 发送）
    await tap("#btn-menu"); await sleep(400);
    await tap("#btn-new-task"); await sleep(600);
    check("点「新任务」→ 主区表单可见",
      (await evalJs(`!document.getElementById("sub-tasks").classList.contains("hidden")`)) === true);
    await evalJs(`(()=>{const g=document.getElementById("f-goal");
      g.value="手机端全流程验证任务——验证触摸建链"; g.dispatchEvent(new Event("input")); return "ok";})()`);
    check("目标输入框可填",
      (await evalJs(`document.getElementById("f-goal").value.length > 5`)) === true);
    const sendHit = await evalJs(`(()=>{const b=document.getElementById("btn-create");
      const r=b.getBoundingClientRect(); const el=document.elementFromPoint(r.x+r.width/2, r.y+r.height/2);
      return el && (el===b || b.contains(el)) ? "hit" : (el&&el.id)||"miss";})()`);
    check("发送按钮在视口内且可命中", sendHit === "hit", sendHit);
    await tap("#btn-create");
    await sleep(5000);   // 建任务走 API（临时数据目录 + 隔离 HOME，无真实副作用）
    check("新建任务出现在侧栏任务树",
      (await evalJs(`document.getElementById("side-tasks").textContent.includes("手机端全流程验证任务")`)) === true);

    // ---- 6. 触摸种盘任务 → 详情打开（任务行属性是 data-key；种盘任务在 workspace 文件夹分组里）
    await evalJs(`document.body.classList.remove("side-collapsed"); "ok"`);
    await sleep(400);
    const staskHit = await evalJs(`(()=>{let el=document.querySelector('#side-tasks .stask[data-key="mob1"]');
      if(!el){ el = Array.from(document.querySelectorAll("#side-tasks .stask")).find(x=>x.textContent.includes("手机端流程测试任务")); }
      if(!el) return "null";
      const d = el.closest("details"); if (d) d.open = true;   // 文件夹分组收起时先展开
      const r=el.getBoundingClientRect();
      return JSON.stringify({x:r.x+r.width/2,y:r.y+r.height/2,vis:r.width>0&&r.y>0&&r.y<innerHeight});})()`);
    let detailOpened = false, detailNote = "staskHit=" + staskHit;
    if (staskHit !== "null") {
      const p = JSON.parse(staskHit);
      if (p.vis) {
        await tapXY(p.x, p.y);
        await sleep(1500);
        detailOpened = await evalJs(`!!S.detailTaskKey || !!S.detailRunId`);
        detailNote += " detailTaskKey=" + (await evalJs(`S.detailTaskKey`));
      }
    }
    check("点任务项 → 详情打开", detailOpened === true, detailNote);

    // ---- 6b. 详情页手机布局：对话态吃满主区（chat-fill 32px 浮卡让位已收窄）、
    //          返回钮/信息弹层在视口内（须在详情还开着时断言，切页会 closeRun）
    const rdW = JSON.parse(await evalJs(`(()=>{
      const rd = document.getElementById("run-detail");
      const pane = document.querySelector("#run-detail .rd-pane:not(.hidden)");
      const back = document.getElementById("btn-back");
      const r1 = rd.getBoundingClientRect();
      const r2 = pane ? pane.getBoundingClientRect() : null;
      const r3 = back ? back.getBoundingClientRect() : null;
      return JSON.stringify({ rdW: Math.round(r1.width), paneW: r2 ? Math.round(r2.width) : 0,
        backOk: r3 ? (r3.width > 0 && r3.x >= 0 && r3.right <= innerWidth + 2) : false });
    })()`));
    check("对话态详情吃满主区（≥300px，不再被 32px 让位白吃）", rdW.rdW >= 300, JSON.stringify(rdW));
    check("详情 pane 同宽吃满", rdW.paneW >= 300, "paneW=" + rdW.paneW);
    check("返回按钮在视口内可点", rdW.backOk === true);
    await evalJs(`(()=>{const t=document.getElementById("rd-more-toggle"); if(t) t.click(); return "ok";})()`);
    await sleep(500);
    const popOk = await evalJs(`(()=>{const p=document.getElementById("rd-meta-popover");
      if(!p||p.classList.contains("hidden")) return false; const r=p.getBoundingClientRect();
      return r.width>0 && r.x>=0 && r.right<=innerWidth+2;})()`);
    check("任务信息弹层不出视口", popOk === true);
    await evalJs(`(()=>{const c=document.getElementById("rd-more-close"); if(c) c.click(); return "ok";})()`);
    await sleep(300);

    // ---- 7. 触摸导航轨切页（运行记录/自动化/任务）
    for (const page of ["runs", "automation", "tasks"]) {
      const ok = await tap(`.rail-btn[data-rail-page="${page}"]`);
      await sleep(600);
      const tab = await evalJs(`S.tab`);
      check(`导航轨触摸切「${page}」`, ok === true && tab === page, "S.tab=" + tab);
    }

    // ---- 8. 触摸导航轨设置入口（side-foot 已隐藏，rail 代理按钮 onclick 转发 btn-settings.click()）
    const setBtnSel = `.rail-btn[title="设置"], .rail-btn[data-i18n-title="设置"]`;
    const gearOk = await tap(setBtnSel);
    await sleep(800);
    check("触摸导航轨设置入口 → 设置模式", gearOk === true &&
      (await evalJs(`document.body.classList.contains("settings-mode")`)) === true);
    await tap(`.rail-btn[data-rail-page="tasks"]`); await sleep(400);

    // ---- 9. composer 发送区在手机视口内的关键 hit-test
    const cmpHit = await evalJs(`(()=>{const b=document.querySelector(".cmp-send .primary");
      if(!b){const c=document.querySelector(".cmp-box"); if(!c) return "no-cmp";
        const r=c.getBoundingClientRect(); return r.width>0&&r.height>0 ? "cmp-ok" : "cmp-hidden";}
      const r=b.getBoundingClientRect(); const el=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
      return el&&(el===b||b.contains(el))?"hit":"covered:"+((el&&(el.id||el.className))||"?");})()`);
    check("composer 发送区手机视口可命中", ["hit", "cmp-ok"].includes(cmpHit), cmpHit);

    // ---- 10. 恢复桌面视口 → 三列布局不回归
    await send("Emulation.clearDeviceMetricsOverride");
    await send("Emulation.setTouchEmulationEnabled", { enabled: false });
    await sleep(800);
    await evalJs(`document.body.classList.remove("side-collapsed"); "ok"`);
    await sleep(300);
    const cols = (await evalJs(`getComputedStyle(document.getElementById("app")).gridTemplateColumns`) || "").trim();
    check("桌面恢复三列布局（46px rail + 侧栏 + 主区）", cols.startsWith("46px "), cols);
    const sidePos = await evalJs(`getComputedStyle(document.getElementById("sidebar")).position`);
    check("桌面侧栏回静态列", sidePos === "static" || sidePos === "relative", sidePos);

    // 留一张手机视口截图供人工核对（断言不依赖它）
    await send("Emulation.setDeviceMetricsOverride",
      { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
    await sleep(600);
    await send("Page.captureScreenshot", { format: "png" }).then((r) => {
      mkdirSync(join(ROOT, ".ui-shots"), { recursive: true });
      writeFileSync(join(ROOT, ".ui-shots", "mobile-flow.png"), Buffer.from(r.result.data, "base64"));
    });
  } finally {
    await close();
  }

  const bad = results.filter((r) => !r.ok).length;
  console.log(`\n手机端全流程回归：${results.length - bad} 过 / ${bad} 挂`);
  process.exit(bad ? 1 : 0);
}

main().catch((e) => { console.error("测试崩溃：", e); process.exit(2); });
