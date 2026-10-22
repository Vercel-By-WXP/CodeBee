"""「待你处理」支持单条忽略：按运行指纹记账，同一运行状态变化后重新出现。

活动模型是纯函数，直接 require activity.js 在 Node 里跑；接线部分沿用
tests/test_ui_side_arch_toggle.py 的做法，对 app.js 源码切片做静态断言。
"""
from __future__ import annotations

import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


UI = Path(__file__).resolve().parents[1] / "app" / "ui"
ACTIVITY_JS = UI / "activity.js"
APP_JS = UI / "app.js"
I18N_JS = UI / "i18n.js"

HARNESS = textwrap.dedent(r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const model = require(process.argv[1]);

const state = {
  tasks: [
    { id: 't1', title: '神瞳鉴宝：31-40章修订复评' },
    { id: 't2', title: '一头猎物，三笔账' },
  ],
  task_latest: {
    t1: { id: 'r1', task_id: 't1', status: 'failed', ended_at: '2026-10-22 09:00:00' },
    t2: { id: 'r2', task_id: 't2', status: 'done', ended_at: '2026-10-22 08:00:00' },
  },
  task_attention: { t2: { approval_pending: true } },
  runs: [],
};
const now = new Date('2026-10-22T12:00:00');

const base = model.build(state, {}, now);
assert.equal(base.pending, 2, '失败与待审批都必须计入待处理');
const failed = base.rows.find((r) => r.id === 'r1');
const approval = base.rows.find((r) => r.id === 'r2');
assert(failed && failed.needs && failed.reasons.includes('执行失败'));
assert(approval && approval.needs && approval.reasons.includes('待审批'));

// 1) 忽略失败运行：从待处理与活动列表消失
const one = model.build(state, {}, now, { r1: failed.fingerprint });
assert.equal(one.pending, 1, '忽略后待处理计数必须下降');
assert(!one.rows.some((r) => r.id === 'r1'), '被忽略的失败运行不得继续出现在活动列表');
assert(one.rows.find((r) => r.id === 'r2').needs, '只能忽略点中的那一条');

// 2) 忽略待审批：不再要求处理，但已完成记录仍留在活动列表
const two = model.build(state, {}, now, { r2: approval.fingerprint });
assert.equal(two.pending, 1);
const kept = two.rows.find((r) => r.id === 'r2');
assert(kept && !kept.needs, '忽略后不得再算待处理');
assert(kept.group !== '需要你处理', '忽略的条目必须离开需要你处理分组');

// 3) 同一运行状态变化（指纹变化）＝重新提醒，避免忽略掉新故障
const rerunState = {
  ...state,
  task_latest: {
    ...state.task_latest,
    t1: { ...state.task_latest.t1, ended_at: '2026-10-22 11:00:00' },
  },
};
const revived = model.build(rerunState, {}, now, { r1: failed.fingerprint });
assert.equal(revived.pending, 2, '新的一次失败必须重新计入待处理');
assert(revived.rows.find((r) => r.id === 'r1').needs);

// 4) 模型必须暴露 ignore 给状态侧栏调用
assert.equal(typeof model.ignore, 'function', '活动模型必须暴露 ignore');

// 5) 接线：忽略按钮挂在待处理卡上，且不触发整卡打开详情
const app = fs.readFileSync(process.argv[2], 'utf8');
const i18n = fs.readFileSync(process.argv[3], 'utf8');
const btn = app.indexOf('class="pending-ignore"');
assert(btn >= 0, '待处理卡必须有忽略按钮');
const seg = app.slice(btn, btn + 320);
assert(seg.includes('ignorePendingRun('), '忽略按钮必须接到忽略处理函数');
assert(seg.includes('event.stopPropagation()'), '点忽略不得顺带打开运行详情');
const fn = app.indexOf('function ignorePendingRun(');
assert(fn >= 0, '忽略处理函数必须存在');
assert(app.slice(fn, fn + 240).includes('window.activityModel.ignore'),
  '忽略必须走活动模型，侧栏徽章与计数才能同步');
assert(i18n.includes('"忽略": "Dismiss"'), '忽略需要英文翻译');
""")


class ActivityIgnoreTests(unittest.TestCase):
    def setUp(self):
        self.node = shutil.which("node")
        if not self.node:
            self.skipTest("Node.js is required to exercise the activity model")

    def test_ignore_dismisses_exact_run_state(self):
        proc = subprocess.run(
            [self.node, "-e", HARNESS, str(ACTIVITY_JS), str(APP_JS), str(I18N_JS)],
            check=False, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


if __name__ == "__main__":
    unittest.main()
