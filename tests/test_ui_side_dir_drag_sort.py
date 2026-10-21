"""侧栏项目「置顶块 + 活跃度尾」的行为回归。

沿用 tests/test_ui_health_alert_actions.py 的做法：把 app.js 里的实现切片出来，在 Node 里
用桩 localStorage 真跑一遍，而不是只断言源码里出现了某个字符串。
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

const start = source.indexOf('const SIDE_PIN_KEY = "orch.sidePinned";');
const end = source.indexOf('\n/* 侧栏「任务」树：', start);
assert(start >= 0 && end > start, '侧栏置顶实现必须还在 app.js 里');
const block = source.slice(start, end);

function makeApi(store) {
  const localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };
  return new Function('localStorage', 'ORPHAN_DIR', 'S', '$', 'renderSideTasks',
    block + `; return { sortSideDirs, setSidePin, applySideDrop, sideDropAccepted,
      loadSidePins, sidePinnedSet };`)(
      localStorage, 'ORPHAN', { sideSig: 'stale' }, () => null, () => {});
}
const dirs = (...specs) => specs.map(([dir, time]) => ({ dir, time, groups: [] }));
const orderOf = (list) => list.map((d) => d.dir);
const pins = (api) => api.loadSidePins();
const PIN = 'orch.sidePinned';

// 1) 没置顶过＝改造前的原样：按最近活动倒序，「其他」恒垫底
{
  const api = makeApi(new Map());
  assert.deepEqual(pins(api), []);
  const list = dirs(['A', '2026-10-01'], ['ORPHAN', '2026-10-09'], ['B', '2026-10-05']);
  api.sortSideDirs(list, 'ORPHAN');
  assert.deepEqual(orderOf(list), ['B', 'A', 'ORPHAN']);
}

// 2) 置顶块恒在上并按用户顺序，未置顶的仍按活跃度自动浮沉，「其他」仍垫底
{
  const api = makeApi(new Map([[PIN, JSON.stringify(['C', 'A'])]]));
  const list = dirs(['A', '2026-10-01'], ['B', '2026-10-02'], ['C', '2026-10-03'],
                    ['NEW1', '2026-10-08'], ['NEW2', '2026-10-04'], ['ORPHAN', '2026-10-09']);
  api.sortSideDirs(list, 'ORPHAN');
  assert.deepEqual(orderOf(list), ['C', 'A', 'NEW1', 'NEW2', 'B', 'ORPHAN']);
}

// 3) 置顶落盘：菜单置顶插到块首，取消置顶只摘自己那一条
{
  const api = makeApi(new Map([[PIN, JSON.stringify(['A', 'B'])]]));
  api.setSidePin('NEW', true);
  assert.deepEqual(pins(api), ['NEW', 'A', 'B']);
  api.setSidePin('A', false);
  assert.deepEqual(pins(api), ['NEW', 'B']);
  api.setSidePin('A', true);
  assert.deepEqual(pins(api), ['A', 'NEW', 'B']);
}

// 4) 落点合法性：落在置顶行上、或拖的就是置顶行，才接受；两边都不在块里没有可表达的顺序
{
  const api = makeApi(new Map([[PIN, JSON.stringify(['A', 'B'])]]));
  assert.equal(api.sideDropAccepted('C', 'A'), true,   // 拖进块
    '拖到置顶行上应接受');
  assert.equal(api.sideDropAccepted('A', 'C'), true,   // 拖出块＝取消置顶
    '把置顶项拖到非置顶行应接受');
  assert.equal(api.sideDropAccepted('A', 'B'), true,   // 块内改序
    '块内改序应接受');
  assert.equal(api.sideDropAccepted('C', 'D'), false,  // 纯活跃度区
    '两个非置顶行之间不该接受');
  // 自己拖到自己身上必须判无效：否则 applySideDrop 会把它从块里摘掉再找不到落点＝静默取消置顶
  assert.equal(api.sideDropAccepted('A', 'A'), false, '同项落点不该接受');
  assert.equal(api.sideDropAccepted('', 'A'), false, '没有载荷时不该接受');
}

// 5) 落点语义：插到目标行之前；拖到非置顶行＝从块里摘掉
{
  const api = makeApi(new Map([[PIN, JSON.stringify(['A', 'B'])]]));
  api.applySideDrop('C', 'B');
  assert.deepEqual(pins(api), ['A', 'C', 'B'], 'C 应插在 B 之前');
  api.applySideDrop('A', 'ZZZ');
  assert.deepEqual(pins(api), ['C', 'B'], 'A 拖出块后应取消置顶');
}

// 6) 僵尸路径：目录整体删掉后不该把名字一直留在置顶表里（sortSideDirs 只按 rank 生效，
//    不会凭空造行，但菜单置顶/落点会持续累积，所以靠可见性收敛）
{
  const api = makeApi(new Map([[PIN, JSON.stringify(['GONE', 'A'])]]));
  const list = dirs(['A', '2026-10-01'], ['B', '2026-10-02']);
  api.sortSideDirs(list, 'ORPHAN');
  assert.deepEqual(orderOf(list), ['A', 'B'], '已消失的置顶项不得影响其余排序');
}

// 7) 置顶表里的重复项取第一次出现，不被后面的覆盖
{
  const api = makeApi(new Map([[PIN, JSON.stringify(['B', 'A', 'B'])]]));
  const list = dirs(['A', '2026-10-01'], ['B', '2026-10-02']);
  api.sortSideDirs(list, 'ORPHAN');
  assert.deepEqual(orderOf(list), ['B', 'A']);
}
""")

RENDER_WIRING = textwrap.dedent(r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('function renderSideTasks() {');
const end = source.indexOf('\n/* 主视图打开详情面板的公共部分', start);
assert(start >= 0 && end > start, 'renderSideTasks 必须还在 app.js 里');
const block = source.slice(start, end);
assert(block.includes('sortSideDirs(dirs, ORPHAN)'), '渲染必须走统一排序入口');
assert(!/dirs\.sort\(/.test(block), '不得再就地 dirs.sort()，否则会绕过置顶块');
assert(block.includes('(isPinned ? "#i-pin" : "#i-folder")'), '置顶行必须换成图钉图标');
assert(block.includes('canDrag ? \' draggable="true"\'' + " : "), 'summary 必须按条件可拖');
assert(block.includes('const canDrag = !isOrphan'), '「其他」不得出现在可拖集合');
assert(block.includes('class="sdir\' + (isPinned ? " pinned" : "")'), '置顶行要打 pinned 类');
""")

# 落点重绘会把源节点摘出文档树，之后的 dragend 冒泡不到 #side-tasks 的委托监听上，
# 所以 drop 必须自己清掉 S.sideDragDir——否则残留的载荷会让一次无关的拖放触发幽灵改序。
DROP_PAYLOAD = textwrap.dedent(r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('sideBox.addEventListener("drop"');
assert(start >= 0, '侧栏 drop 监听必须还在');
const block = source.slice(start, source.indexOf('});', start) + 3);
const cleared = block.indexOf('S.sideDragDir = ""');
const applied = block.indexOf('applySideDrop(');
assert(cleared >= 0, 'drop 必须清掉 S.sideDragDir：' + block);
assert(applied >= 0 && cleared < applied, '必须在触发改序（重绘）之前清载荷');
""")


class SideDirPinSortTests(unittest.TestCase):
    def setUp(self):
        self.node = shutil.which("node")
        if not self.node:
            self.skipTest("Node.js is required to exercise the sidebar sort helper")

    def _run(self, script):
        return subprocess.run(
            [self.node, "-e", script, str(APP_JS)],
            check=True, capture_output=True, text=True, timeout=20,
        )

    def test_pin_sort_and_drop_semantics(self):
        self._run(HARNESS)

    def test_renderer_uses_the_shared_sort_entry(self):
        self._run(RENDER_WIRING)

    def test_drop_clears_the_drag_payload_before_repainting(self):
        self._run(DROP_PAYLOAD)


if __name__ == "__main__":
    unittest.main()
