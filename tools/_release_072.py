# -*- coding: utf-8 -*-
"""发版三件套 v0.1.72。"""
import io
import re

p = 'CHANGELOG.md'
s = io.open(p, encoding='utf-8').read()
v = """## v0.1.72（2026-09-24）

- 作品信息沿连载链继承：续写批次与首展示同账，发布绑定/已发章数整链贯通
- 侧栏体验二连修：右缘可拖宽（手柄热区 + 双击复原）、拖拽重复监听剔除
- 禅道/竞品调研文档沉淀

## v0.1.71（2026-09-23）"""
assert "## v0.1.71（2026-09-23）" in s, 'anchor'
s = s.replace("## v0.1.71（2026-09-23）", v, 1)
io.open(p, 'w', encoding='utf-8', newline='\n').write(s)

p = 'README.md'
s = io.open(p, encoding='utf-8').read()
m = re.search(r"(<!--\s*relnotes:start\s*-->).*?(<!--\s*relnotes:end\s*-->)", s, re.S)
assert m, 'relnotes missing'
new_notes = m.group(1) + """
### 最新版更新内容（v0.1.72）

- 作品信息沿连载链继承：续写批次同展示，发布绑定/已发章数整链同账
- 侧栏右缘可拖宽（双击复原）、拖拽重复监听剔除
""" + m.group(2)
s = s[:m.start()] + new_notes + s[m.end():]
io.open(p, 'w', encoding='utf-8', newline='\n').write(s)

p = 'package.json'
s = io.open(p, encoding='utf-8').read()
assert '"version": "0.1.71"' in s
io.open(p, 'w', encoding='utf-8', newline='\n').write(
    s.replace('"version": "0.1.71"', '"version": "0.1.72"'))
print('trio done -> 0.1.72')
