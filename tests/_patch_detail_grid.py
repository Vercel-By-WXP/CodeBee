# -*- coding: utf-8 -*-
"""详情页双栏布局重排：rd-grid（主栏=蜂巢/日志/步骤/计划/成品/报告，
侧栏=运行控制卡片+指挥区）。幂等：已重排则跳过。"""
import io
import re
import sys

P = "app/ui/index.html"


def main():
    s = io.open(P, encoding="utf-8").read()
    if 'class="rd-grid"' in s:
        print("already")
        return 0
    # 截取 run-detail 面板整块
    m = re.search(r'(      <div id="run-detail" class="panel wide hidden">\n)(.*?)(\n      </div>\n)', s, re.S)
    if not m:
        print("anchor run-detail not found")
        return 1
    block = m.group(2)

    def grab(pattern, src):
        mm = re.search(pattern, src, re.S)
        return mm.group(0) if mm else ""

    hive = grab(r'        <div id="rd-hive".*?</div>\n        </div>', block) or \
           grab(r'        <div id="rd-hive".*?\n        </div>', block)
    logbox = grab(r'        <div id="rd-log".*?</div>', block)
    steps = grab(r'        <div id="rd-steps" class="steps"></div>', block)
    plan = grab(r'        <div id="rd-plan" class="hidden"></div>', block)
    direct = grab(r'        <div id="rd-direct".*?\n        </div>', block)
    bible = grab(r'        <div id="rd-bible".*?</div>', block)
    gitp = grab(r'        <div id="rd-git".*?</div>', block)
    files = grab(r'        <div id="rd-files" class="rd-files"></div>', block)
    meta = grab(r'        <div id="rd-meta" class="stats"></div>', block)
    report_h = grab(r'        <h3 data-i18n="报告" class="sec-title">报告</h3>', block)
    report = grab(r'        <div id="rd-report" class="report"></div>', block)
    need = {"hive": hive, "logbox": logbox, "steps": steps, "plan": plan,
            "direct": direct, "bible": bible, "gitp": gitp, "files": files,
            "meta": meta, "report": report}
    missing = [k for k, v in need.items() if not v]
    if missing:
        print("missing fragments:", missing)
        return 1

    head_keep = """        <div class="detail-head">
          <button id="btn-back" class="ghost" data-i18n="返回"><svg class="ico" aria-hidden="true"><use href="#i-arrow-left"></use></svg>返回</button>
          <h2 id="rd-title"></h2>
          <span id="rd-status" class="chip"></span>
        </div>"""

    def strip(i):
        return i[len("        "):] if i.startswith("        ") else i

    new_block = head_keep + """
        <div class="rd-grid">
          <div class="rd-main">
""" + "\n".join("  " + strip(x) for x in
                [hive, logbox, steps, plan, bible, gitp, files, report_h, report]) + """
          </div>
          <aside class="rd-side">
            <div class="rd-side-card">
""" + "  " + strip(meta) + """
              <div class="rd-actions">
                <button id="btn-pause" class="ghost hidden" data-i18n="暂停">暂停</button>
                <button data-i18n="取消运行" id="btn-cancel" class="danger hidden">取消运行</button>
                <button data-i18n="↻ 继续任务" id="btn-retry" class="primary hidden">↻ 继续任务</button>
                <button data-i18n="继续连载" id="btn-continue" class="ghost hidden">继续连载</button>
                <button data-i18n="基于此任务新建" id="btn-newfrom" class="ghost hidden">基于此任务新建</button>
                <button data-i18n="删除记录" id="btn-delete" class="danger hidden">删除记录</button>
              </div>
            </div>
""" + "\n".join("  " + strip(x) for x in [direct]) + """
          </aside>
        </div>"""
    s = s[:m.start(2)] + new_block + s[m.end(2):]
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("relaid out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
