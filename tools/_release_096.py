# -*- coding: utf-8 -*-
"""发版三件套 v0.1.96（3D 蜂巢工作台视图 + 沿用累积修复）。"""
import io
import re

p = 'CHANGELOG.md'
s = io.open(p, encoding='utf-8').read()
v = """## v0.1.96（2026-10-08）

- **3D 交互式蜂巢工作台视图**：任务流程以三维场景呈现（交互旋转/缩放/拖拽），步骤节点与连线直观可见
- 沿用累积修复与调优（含禅道套接字瞬态、编排执行加固等）

## v0.1.95（2026-10-07）"""
assert "## v0.1.95（2026-10-07）" in s, 'anchor'
s = s.replace("## v0.1.95（2026-10-07）", v, 1)
io.open(p, 'w', encoding='utf-8', newline='\n').write(s)

p = 'README.md'
s = io.open(p, encoding='utf-8').read()
m = re.search(r"(<!--\s*relnotes:start\s*-->).*?(<!--\s*relnotes:end\s*-->)", s, re.S)
assert m, 'relnotes missing'
new_notes = m.group(1) + """
### 最新版更新内容（v0.1.96）

- 新增 3D 交互式蜂巢工作台视图：任务流程三维可视化（旋转/缩放/拖拽交互）
- 连带修复与调优一批
""" + m.group(2)
s = s[:m.start()] + new_notes + s[m.end():]
io.open(p, 'w', encoding='utf-8', newline='\n').write(s)

p = 'package.json'
s = io.open(p, encoding='utf-8').read()
assert '"version": "0.1.95"' in s
io.open(p, 'w', encoding='utf-8', newline='\n').write(
    s.replace('"version": "0.1.95"', '"version": "0.1.96"'))
print('trio done -> 0.1.96')
