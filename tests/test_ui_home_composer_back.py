"""主页态进对话详情必须把 composer 搬回表单（对话「返回」后输入框不许消失）。

2026-10-22 实案：workbench 主页（page-home）把 #sub-tasks .cmp-box 搬进
#rd-input-slot（homeMountComposer）；从侧栏点任务行进对话走 showDetailInMain——
它刻意不走 switchTab（name==="runs" 且详情未开时会先 closeRun），却因此漏掉
「离开主页搬回 composer」的契约。对话里点「返回」（btn-back → closeRun）的
回任务页分支也不搬回：.cmp-box 困在隐藏的 page-home 里，#sub-tasks 只剩
问候语和类型 chips——输入框凭空消失。

沿用 tests/test_ui_side_arch_toggle.py 的做法：把实现切片出来，在 Node 里
用最小假 DOM 真跑一遍。
"""
from __future__ import annotations

import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / "app" / "ui" / "app.js"

HARNESS = textwrap.dedent(r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const source = fs.readFileSync(process.argv[1], 'utf8');

// 切片 1：composer 搬运函数对（homeMountComposer + homeUnmountComposer）
const m0 = source.indexOf('function homeMountComposer() {');
const m1 = source.indexOf('/* 暖色皮肤主页显隐', m0);
assert(m0 >= 0 && m1 > m0, 'homeMountComposer/homeUnmountComposer 必须还在 app.js 里');
const movers = source.slice(m0, m1);

// 切片 2：showDetailInMain（主视图进详情的公共入口，sideOpenRun/sideOpenTask/jumpToRun 都走它）
const d0 = source.indexOf('function showDetailInMain() {');
const d1 = source.indexOf('/* 重试/续写/建任务产生新 run 后', d0);
assert(d0 >= 0 && d1 > d0, 'showDetailInMain 必须还在 app.js 里');
const detail = source.slice(d0, d1);
assert(detail.includes('homeUnmountComposer()'),
  '主页态进详情必须自己搬回 composer（本函数不走 switchTab），否则对话「返回」后表单没输入框');

// 最小假 DOM：只支撑这两个切片真实跑通
function makeNode() {
  return {
    parent: null, children: [],
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild(c) {
      if (c.parent) { const i = c.parent.children.indexOf(c); if (i >= 0) c.parent.children.splice(i, 1); }
      c.parent = this; this.children.push(c);
    },
  };
}

function makeWorld() {
  const slot = makeNode(), form = makeNode(), main = makeNode();
  const box = makeNode();
  form.appendChild(box);   // 初始态：composer 住在 #sub-tasks .form
  const where = () => (box.parent === slot ? 'slot' : box.parent === form ? 'form' : 'orphan');
  const doc = {
    body: makeNode(),
    querySelector(sel) {
      if (sel === 'main') return main;
      if (sel === '#rd-input-slot .cmp-box') return where() === 'slot' ? box : null;
      if (sel === '#sub-tasks .cmp-box') return where() === 'form' ? box : null;
      if (sel === '#sub-tasks .form') return form;
      return null;
    },
    querySelectorAll() { return []; },
  };
  const $ = (id) => (id === 'rd-input-slot' ? slot : null);
  const api = new Function('document', '$', 'S',
    'syncRailState', 'syncInspBtn', 'renderPageCrumb', 'collapseDrawerIfMobile', 'syncInspectorVis', 'atlasLiveStop',
    movers + '\n' + detail + '\nreturn { homeMountComposer, homeUnmountComposer, showDetailInMain };')(
    doc, $, { tab: 'home' }, () => {}, () => {}, () => {}, () => {}, () => {}, () => {});
  return { api, where, slot, form };
}

// 1) 主页态复现（2026-10-22 实案路径）：mount 进主页槽后进详情，composer 必须已搬回表单
{
  const w = makeWorld();
  w.api.homeMountComposer();
  assert.equal(w.where(), 'slot', 'mount 后 composer 应在主页 #rd-input-slot');
  w.api.showDetailInMain();
  assert.equal(w.where(), 'form', '主页态进详情必须把 composer 搬回 #sub-tasks .form');
  assert.equal(w.slot.children.length, 0, '主页槽不得残留节点');
}

// 2) 非主页态（composer 本就在表单，经典布局/设置壳路径）：进详情必须是无扰 no-op
{
  const w = makeWorld();
  w.api.showDetailInMain();
  assert.equal(w.where(), 'form', '非主页态进详情不得挪动 composer');
}

// 3) mount 幂等：重复 mount 不产生第二份 composer，搬回后表单里也只有一份
{
  const w = makeWorld();
  w.api.homeMountComposer();
  w.api.homeMountComposer();
  assert.equal(w.where(), 'slot');
  assert.equal(w.slot.children.length, 1, '重复 mount 不得复制节点');
  w.api.showDetailInMain();
  assert.equal(w.where(), 'form');
  assert.equal(w.form.children.length, 1, '搬回后表单里必须恰好一份 composer');
}
""")


class HomeComposerBackTests(unittest.TestCase):
    def setUp(self):
        self.node = shutil.which("node")
        if not self.node:
            self.skipTest("Node.js is required to exercise the home composer movers")

    def test_entering_detail_from_home_returns_composer_to_form(self):
        subprocess.run(
            [self.node, "-e", HARNESS, str(APP_JS)],
            check=True, capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace",   # 断言消息含中文，GBK 默认解码会炸读线程
        )


if __name__ == "__main__":
    unittest.main()
