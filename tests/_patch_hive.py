# -*- coding: utf-8 -*-
"""蜂巢工作台 JS 注入：等 app.js 语法健康后幂等注入（并行 agent 写入窗口规避）。

用法：python tests/_patch_hive.py
"""
import io
import subprocess
import sys
import time

P = "app/ui/app.js"

HIVE_BLOCK = '''
/* ---------------- 蜂巢工作台：每个智能体一格，点开即看实时输出 ---------------- */
let hiveTimer = null;                    // 卡片尾巴轮询表
const hiveTails = {};                    // "runid|rel" -> 最近一行输出缓存

function stopHiveTick() { if (hiveTimer) { clearInterval(hiveTimer); hiveTimer = null; } }

/* run.steps -> 蜂巢格：运行中的排前且蜜蜂摆动；终态灰显可回看。轮询只对活跃 run 起。 */
window.renderHive = function (run) {
  const box = $("rd-hive");
  if (!box) return;
  const steps = (run && run.steps) || [];
  if (!run || !steps.length) { box.classList.add("hidden"); stopHiveTick(); return; }
  box.classList.remove("hidden");
  const running = steps.filter((s) => s.status === "running");
  const rest = steps.filter((s) => s.status !== "running").slice(-10).reverse();
  const shown = running.concat(rest).slice(0, 14);
  const sub = $("rd-hive-sub");
  if (sub) sub.textContent = running.length
    ? t("在岗") + " " + running.length + " / " + steps.length : t("全部空闲");
  $("rd-hive-cells").innerHTML = shown.map((s) => {
    const st = s.status === "running" ? "running" : (s.status === "failed" ? "failed" : "done");
    const who = s.agent_label || s.agent || "";
    const title = [s.role, who, s.started_at ? t("开始于 ") + s.started_at : "",
                   (s.note ? "◆ " + s.note : ""), s.summary].filter(Boolean).join(" · ");
    const key = run.id + "|" + (s.log || "");
    return '<div class="hive-cell st-' + st + '" title="' + esc(title) + '" ' +
      'onclick="hiveOpenLog(\\'' + esc(run.id) + "', '" + esc(s.log || "") + '\\')">' +
      '<div class="hc-head">' +
      '<svg class="ico hc-bee" aria-hidden="true"><use href="#i-bee"></use></svg>' +
      '<span class="hc-role">' + esc(s.role || "") + "</span>" +
      '<span class="hc-who">' + esc(who) + "</span></div>" +
      '<div class="hc-tail" data-log="' + esc(s.log || "") + '">' +
      esc(hiveTails[key] || (s.summary || "")) + "</div>" +
      '<div class="hc-meta"><span>' +
      (s.duration_s != null ? s.duration_s + "s" : (s.started_at || "")) + "</span>" +
      (st === "running" ? '<span class="hc-live">● ' + t("工作中") + "</span>" : "") +
      "</div></div>";
  }).join("");
  const live = running.length && (run.status === "running" || run.status === "queued");
  if (live) { if (!hiveTimer) hiveTimer = setInterval(() => hiveTick(run.id), 2500); hiveTick(run.id); }
  else stopHiveTick();
};

/* 活跃步骤的实时尾巴：只拉 900 字符窗口，取最后一行非空输出 */
async function hiveTick(rid) {
  if (document.hidden || !rid) return;
  const box = $("rd-hive-cells");
  if (!box) return;
  const rels = Array.from(box.querySelectorAll(".hc-tail[data-log]"))
    .map((el) => el.dataset.log).filter(Boolean);
  for (const rel of rels) {
    try {
      const r = await api("/api/runs/" + encodeURIComponent(rid) +
        "/log?step=" + encodeURIComponent(rel) + "&tail=900");
      const lines = String(r.log || "").split("\\n").filter((l) => l.trim());
      const last = (lines.pop() || "").trim().slice(-160);
      const key = rid + "|" + rel;
      if (last && hiveTails[key] !== last) {
        hiveTails[key] = last;
        box.querySelectorAll(".hc-tail").forEach((el) => {
          if (el.dataset.log === rel) el.textContent = last;
        });
      }
    } catch (e) { /* 网络抖动保留上一帧 */ }
  }
}

/* 蜂巢格点击 → 打开该步骤实时日志（toggleLog 自带 2.5s 跟随 + 贴底滚动） */
window.hiveOpenLog = function (runId, rel) {
  if (!rel) { toast(t("该步骤无日志"), true); return; }
  toggleLog(runId, rel);
};

function stopLogLive()'''

ANCHOR = "\nfunction stopLogLive()"

CALL1_OLD = """  S.lastRun = run;
  renderDirector(run, active);"""
CALL1_NEW = """  S.lastRun = run;
  renderDirector(run, active);
  renderHive(run);"""
CALL2_OLD = """  renderDirector(activeRun || null, !!activeRun);"""
CALL2_NEW = """  renderDirector(activeRun || null, !!activeRun);
  renderHive(activeRun || latest);   // 终态回看最近一次运行的蜂巢"""
ATT_OLD = """    '<span class="att-chip">' + esc(a.name) + "<b onclick=\\"dirRemoveAtt(" + i + ")\\" title=\\"" +"""
ATT_NEW = """    '<span class="att-chip2">' + esc(a.name) + "<b onclick=\\"dirRemoveAtt(" + i + ")\\" title=\\"" +"""


def healthy():
    r = subprocess.run(["node", "--check", P], capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or "")[-200:]


def inject():
    s = io.open(P, encoding="utf-8").read()
    if "function renderHive" in s:
        return "already"
    if s.count(ANCHOR) != 1:
        return "anchor1:%d" % s.count(ANCHOR)
    s = s.replace(ANCHOR, HIVE_BLOCK, 1)
    if s.count(CALL1_OLD) == 1:
        s = s.replace(CALL1_OLD, CALL1_NEW, 1)
    if s.count(CALL2_OLD) == 1:
        s = s.replace(CALL2_OLD, CALL2_NEW, 1)
    if ATT_OLD in s:
        s = s.replace(ATT_OLD, ATT_NEW, 1)
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    return "done"


def main():
    for i in range(10):
        ok, err = healthy()
        if not ok:
            print("wait %d: %s" % (i, err.replace("\n", " ")[:120]))
            time.sleep(20)
            continue
        res = inject()
        if res in ("done", "already"):
            ok2, err2 = healthy()
            print(res, "syntax:", "OK" if ok2 else err2[:160])
            return 0 if ok2 else 1
        print("anchor miss %s, retry" % res)
        time.sleep(20)
    print("FAILED after retries")
    return 1


if __name__ == "__main__":
    sys.exit(main())
