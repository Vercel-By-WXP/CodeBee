"""侧栏「显示已归档」时钟开关必须照得出被移除文件夹的行为回归。

2026-10-21 实案：右键「移除该文件夹」＝整目录任务归档＋目录进 tutti.hiddenDirs；
renderSideTasks 的隐藏目录过滤是无条件的，时钟开关打开也照不回，而灰显行右键
「取消归档」是唯一会回写隐藏表的入口——死锁。沿用 tests/test_ui_side_dir_drag_sort.py
的做法：把 app.js 里的实现切片出来，在 Node 里用桩 localStorage 真跑一遍。
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

const start = source.indexOf('let _hiddenDirs = null;');
const end = source.indexOf('\nlet ctxItems = [];', start);
assert(start >= 0 && end > start, '隐藏目录实现必须还在 app.js 里');
const block = source.slice(start, end);

function makeApi(store, showArchived) {
  const localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };
  return new Function('localStorage', 'S', '$', 'renderSideTasks',
    block + '; return { sideHiddenDirs, hideSideDir, unhideSideDir, pruneHiddenSideDirs };')(
    localStorage, { sideSig: '' }, () => null, () => {});
}
// pruneHiddenSideDirs 读的是调用方传入的开关，包一层让用例写起来直白
function prune(api, dirs, showArchived) { api.pruneHiddenSideDirs(dirs, showArchived); return dirs.map((d) => d.dir); }
const dirs = (...names) => names.map((dir) => ({ dir, groups: [] }));
const HID = 'tutti.hiddenDirs';

// 1) 开关关着＝原行为：已移除目录不上侧栏
{
  const api = makeApi(new Map([[HID, JSON.stringify(['A'])]]));
  const list = dirs('A', 'B');
  assert.deepEqual(prune(api, list, false), ['B'], '开关关闭时已移除目录必须剪掉');
}

// 2) 开关打开＝找回入口：已移除目录照回（灰显行右键「取消归档」才会回写隐藏表）
{
  const api = makeApi(new Map([[HID, JSON.stringify(['A'])]]));
  const list = dirs('A', 'B');
  assert.deepEqual(prune(api, list, true), ['A', 'B'],
    '时钟开关打开时不得剪已移除目录，否则移除的文件夹永远找不回');
}

// 3) 回写链路：取消归档 unhideSideDir 后，开关关着也不再剪该目录（B 未捞回仍剪）
{
  const store = new Map([[HID, JSON.stringify(['A', 'B'])]]);
  const api = makeApi(store);
  api.unhideSideDir('A');
  assert.deepEqual(JSON.parse(store.get(HID)), ['B'], 'unhide 必须落盘');
  const list = dirs('A', 'B', 'C');
  assert.deepEqual(prune(api, list, false), ['A', 'C'], '捞回的 A 要在，仍在隐藏表的 B 照剪');
}

// 4) hideSideDir 幂等：重复移除不写重复项
{
  const store = new Map();
  const api = makeApi(store);
  api.hideSideDir('A');
  api.hideSideDir('A');
  assert.deepEqual(JSON.parse(store.get(HID)), ['A'], '重复 hide 不得产生重复条目');
}

// 5) localStorage 损坏时按空表兜底，不得抛错把侧栏渲染炸掉
{
  const api = makeApi(new Map([[HID, '{broken']]));
  assert.deepEqual([...api.sideHiddenDirs()], [], '坏 JSON 应回空表');
  const list = dirs('A');
  assert.deepEqual(prune(api, list, false), ['A']);
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
assert(block.includes('pruneHiddenSideDirs(dirs, S.showArchived)'),
  '隐藏目录过滤必须走 pruneHiddenSideDirs 且带开关，禁止退回无条件剪枝');
""")

DELETE_WIRING = textwrap.dedent(r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const source = fs.readFileSync(process.argv[1], 'utf8');

// 文件夹菜单：「删除该文件夹」必须紧挨「移除该文件夹」，别再长回两个越走越远的入口
const removeAt = source.indexOf('{ label: t("移除该文件夹"), danger: true, fn: () => removeSideDir(ids, dir) }');
assert(removeAt >= 0, '移除该文件夹菜单项必须还在');
assert(source.slice(removeAt, removeAt + 200)
  .includes('{ label: t("删除该文件夹"), danger: true, fn: () => deleteSideDir(ids, dir) }'),
  '移除旁边必须有删除该文件夹入口');

// deleteTask = 确认 + 前置提示；core 是执行段，不得自带确认（文件夹批删只确认一次）
const topEnd = /\r?\n\}\r?\n/g;   // 工作树可能是 CRLF，函数尾的列 0 “}” 用正则找
const dt = source.indexOf('async function deleteTask(');
assert(dt >= 0, 'deleteTask 必须还在');
assert(source.slice(dt, dt + 400).includes('await deleteTaskCore(id)'), '单删必须走 core');
const coreStart = source.indexOf('async function deleteTaskCore(');
assert(coreStart > dt, 'deleteTaskCore 必须存在');
topEnd.lastIndex = coreStart;
const core = source.slice(coreStart, topEnd.exec(source).index);
assert(!core.includes('uiConfirm'), 'core 不得自带确认');
assert(core.includes('return true'), 'core 必须回报成功');

// deleteSideDir：确认后逐个走 core；有失败必须保留目录，全成功才藏
const ds = source.indexOf('async function deleteSideDir(');
assert(ds > coreStart, 'deleteSideDir 必须存在');
topEnd.lastIndex = ds;
const block = source.slice(ds, topEnd.exec(source).index);
assert(block.includes('deleteTaskCore('), '批删必须复用单任务执行段');
const failedAt = block.indexOf('if (failed)');
const hidAt = block.indexOf('hideSideDir(dir)');
assert(failedAt >= 0 && hidAt >= 0 && failedAt < hidAt, '有失败要先收场，不得藏目录');
""")


class SideArchToggleTests(unittest.TestCase):
    def setUp(self):
        self.node = shutil.which("node")
        if not self.node:
            self.skipTest("Node.js is required to exercise the sidebar hidden-dir helper")

    def _run(self, script):
        return subprocess.run(
            [self.node, "-e", script, str(APP_JS)],
            check=True, capture_output=True, text=True, timeout=20,
        )

    def test_arch_toggle_shows_removed_dirs(self):
        self._run(HARNESS)

    def test_renderer_routes_pruning_through_the_toggle(self):
        self._run(RENDER_WIRING)

    def test_folder_delete_wiring(self):
        self._run(DELETE_WIRING)


if __name__ == "__main__":
    unittest.main()
