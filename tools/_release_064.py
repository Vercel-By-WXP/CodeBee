# -*- coding: utf-8 -*-
"""发版三件套 v0.1.64。"""
import io
import re

p = 'CHANGELOG.md'
s = io.open(p, encoding='utf-8').read()
v = """## v0.1.64（2026-09-24）

- 禅道看图排查自动降级链：Bug 截图进排查与修复链，编排模型不会看图就自动换将并落标记，不再甩人工处理
- 排查读图守卫：读不到图留人工判断，不盲判定论（#27754 案）
- runner 瞬态错误热回退修复

## v0.1.63（2026-09-24）"""
assert "## v0.1.63（2026-09-24）" in s, 'anchor'
s = s.replace("## v0.1.63（2026-09-24）", v, 1)
io.open(p, 'w', encoding='utf-8', newline='\n').write(s)

p = 'README.md'
s = io.open(p, encoding='utf-8').read()
m = re.search(r"(<!--\s*relnotes:start\s*-->).*?(<!--\s*relnotes:end\s*-->)", s, re.S)
assert m, 'relnotes missing'
new_notes = m.group(1) + """
### 最新版更新内容（v0.1.64）

- 禅道 Bug 截图自动进排查/修复链：模型不会看图就自动换将，读不到留人工不盲判
- runner 瞬态错误热回退修复
""" + m.group(2)
s = s[:m.start()] + new_notes + s[m.end():]
io.open(p, 'w', encoding='utf-8', newline='\n').write(s)

p = 'package.json'
s = io.open(p, encoding='utf-8').read()
assert '"version": "0.1.63"' in s
io.open(p, 'w', encoding='utf-8', newline='\n').write(
    s.replace('"version": "0.1.63"', '"version": "0.1.64"'))
print('trio done -> 0.1.64')
