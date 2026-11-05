# -*- coding: utf-8 -*-
"""约束数据文件守测：`data/novel/constraints-*.json` 是数据，坏了不会报错，只会静默失效。

为什么单独守
------------
`constraints._load_file()` 对坏文件一律返回 None（这是对的：一份坏台账不该拖垮提示词），
但代价是**约束悄悄不生效**——作者以为「这次带着驳回教训开工」，实际提示词里一个字都没有。
`test_novel_constraints.py` 用的是临时目录 fixture，测的是行为，看不到仓库里这几份真数据。

守四件事：

1. 每份文件都能被 `_load_file` 接受（字段齐、constraints 非空）。
2. 拿它自己的 detect 词造一个任务，能渲染出非空块（题材投票投得回来）。
3. 渲染出来的条数不少于 3 条（`MAX_BLOCK_CHARS` 预算内不该只剩一两条）。
4. 不串味：A 的 detect 词只引出 A 的平台原话，绝不出现 B 的原话。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from core.quality import constraints

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "novel"


def _files():
    return sorted(DATA_DIR.glob("constraints-*.json"))


class ConstraintDataFilesTest(unittest.TestCase):
    def setUp(self):
        if not DATA_DIR.is_dir() or not _files():
            self.skipTest("仓库里没有 constraints-*.json")
        self._patch = mock.patch.object(constraints, "_dir", return_value=DATA_DIR)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def _task_for(self, data):
        words = [w for w in (data.get("detect") or []) if w][:4]
        return {"type": "novel", "title": "".join(words), "goal": "", "context": ""}

    def test_every_file_loads(self):
        for path in _files():
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            self.assertTrue(constraints._load_file(path) is not None, "%s 被跳过" % path.name)
            self.assertTrue(str(raw.get("genre") or "").strip(), "%s 缺 genre" % path.name)
            self.assertTrue(raw.get("constraints"), "%s 没有约束条目" % path.name)

    def test_every_file_renders_its_own_block(self):
        for path in _files():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            text = constraints.block_for(self._task_for(data))
            self.assertTrue(text, "%s 渲染出空块" % path.name)
            quote = str((data.get("source") or {}).get("quote") or "").strip()
            if quote:
                self.assertIn(quote[:12], text, "%s 块里没有平台原话" % path.name)

    def test_block_keeps_at_least_three_items(self):
        for path in _files():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            text = constraints.block_for(self._task_for(data))
            items = text.count("\n   依据：")
            self.assertGreaterEqual(items, 3, "%s 只渲染出 %d 条" % (path.name, items))

    def test_genres_do_not_leak_into_each_other(self):
        loaded = []
        for path in _files():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            loaded.append((path.name, data, constraints.block_for(self._task_for(data))))
        for name, data, text in loaded:
            for other_name, other, _ in loaded:
                if other_name == name:
                    continue
                other_quote = str((other.get("source") or {}).get("quote") or "").strip()
                if len(other_quote) < 8:
                    continue
                self.assertNotIn(other_quote[:12], text,
                                 "%s 的块里出现了 %s 的原话" % (name, other_name))


if __name__ == "__main__":
    unittest.main()
