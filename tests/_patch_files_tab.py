# -*- coding: utf-8 -*-
"""成品文件搬进检查器 TAB：详情页主栏 rd-files 撤除，loadArtifacts 渲染到
insp-pane-files；applyInspectorTab 支持 files。幂等。"""
import io
import subprocess
import sys
import time

HTML = "app/ui/index.html"
JS = "app/ui/app.js"


def node_ok():
    r = subprocess.run(["node", "--check", JS], capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or "")[-160:]


def patch_html():
    s = io.open(HTML, encoding="utf-8").read()
    changed = []
    # 1) 建 files pane（若无）
    if 'id="insp-pane-files"' not in s:
        anchor = '''        <div id="insp-steps" class="insp-steps"></div>
      </section>'''
        assert s.count(anchor) == 1, "progress pane anchor"
        s = s.replace(anchor, anchor + '''
      <!-- 成品分区：工作目录新产出文件 + 面板内预览（主栏不再重复展示） -->
      <section class="insp-pane hidden" id="insp-pane-files">
        <div id="insp-files-body" class="insp-files"></div>
      </section>''', 1)
        changed.append("pane")
    # 2) 主栏 rd-files 撤除
    for frag in ('            <div id="rd-files" class="rd-files"></div>\n',
                 '        <div id="rd-files" class="rd-files"></div>\n',
                 '<div id="rd-files" class="rd-files"></div>\n'):
        if frag in s:
            s = s.replace(frag, "", 1)
            changed.append("rd-files removed")
            break
    io.open(HTML, "w", encoding="utf-8", newline="\n").write(s)
    return changed


def patch_js():
    s = io.open(JS, encoding="utf-8").read()
    changed = []
    old_box = '''async function loadArtifacts(runId) {
  const box = $("rd-files");'''
    new_box = '''async function loadArtifacts(runId) {
  // 成品文件渲染进检查器「成品文件」TAB（主栏聚焦步骤/日志/报告，不再重复展示）
  const box = $("insp-files-body");'''
    if s.count(old_box) == 1:
        s = s.replace(old_box, new_box, 1)
        changed.append("loadArtifacts target")
    old_tabs = '''  for (const name of ["git", "progress"]) {
    const pane = $("insp-pane-" + name);'''
    new_tabs = '''  for (const name of ["git", "progress", "files"]) {
    const pane = $("insp-pane-" + name);'''
    if s.count(old_tabs) == 1:
        s = s.replace(old_tabs, new_tabs, 1)
        changed.append("applyInspectorTab files")
    io.open(JS, "w", encoding="utf-8", newline="\n").write(s)
    return changed


def main():
    ch = patch_html()
    ok, err = node_ok()
    ch2 = patch_js()
    ok2, err2 = node_ok()
    print("html:", ch, "| js:", ch2, "| syntax:", ok and ok2, (err + err2)[:120])
    return 0 if (ok and ok2) else 1


if __name__ == "__main__":
    sys.exit(main())
