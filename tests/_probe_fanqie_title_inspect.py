# -*- coding: utf-8 -*-
"""关键诊断：跑到提交前一步，读出 书名/主角/简介 输入框的真实值（不提交）。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser
from core.publish import flow
from core.publish import fanqie as fq
from core.publish import manager as mgr

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'fanqie')
meta_task = json.load(io.open(
    r'C:\Users\HP\AppData\Roaming\CodeBee\tasks\t-20261017-115114-8191.json',
    encoding='utf-8'))
data = (meta_task.get('book_meta') or {}).get('fanqie', {}).get('data') or {}
values = fq.values_create_book(dict(data, book_name=data.get('book_name') or '嫡女重生：我的符能渡人也能杀人'))
ov = json.load(io.open(r'C:\Users\HP\AppData\Roaming\CodeBee\publish\flows-fanqie.json',
                       encoding='utf-8'))
steps = ov['create_book']
# 截到 提交步 之前（含诊断截图），submit 及其后不跑
sub_idx = next(i for i, s in enumerate(steps) if s.get('do') == 'submit')
steps = steps[:sub_idx]
steps = mgr._with_tag_steps(steps, fq.tag_groups(data), values, fq)

logs = []
b = Browser(prof)
try:
    page = b.first_page()
    shot_dir = Path('tests/_out/shots')
    shot_dir.mkdir(parents=True, exist_ok=True)
    n = flow.run_flow(page, steps, values=values, config=fq.CONFIG,
                      auto_submit=True, dup_check=False,
                      shot=lambda name: page.screenshot(str(shot_dir / (name + '.png'))),
                      log=lambda s: (logs.append(s), print('LOG', s)))
    time.sleep(2)
    state = page.call("""()=>{
      const g=(sel)=>{const e=document.querySelector(sel);
        return e?String(e.value||'').slice(0,40):'(无元素)';};
      const radios=[...document.querySelectorAll('label.arco-radio')].map(e=>({
        t:(e.innerText||'').trim(), ck:String(e.className).includes('checked')}));
      return {title:g('input[placeholder=\"请输入作品名称\"]'),
              p1:g('input[placeholder=\"请输入主角名1\"]'),
              p2:g('input[placeholder=\"请输入主角名2\"]'),
              summary_len:(document.querySelector('textarea[placeholder*=\"请输入50-500字\"]')||{value:''}).value.length,
              radios:radios,
              btn:[...document.querySelectorAll('button')].filter(e=>(e.innerText||'').includes('立即创建'))
                .map(e=>({txt:(e.innerText||'').trim(), disabled:e.disabled,
                          cls:String(e.className).slice(0,60)}))};
    }""")
    print(json.dumps(state, ensure_ascii=False, indent=1)[:900])
    io.open('tests/_out/fanqie_form_state.json', 'w', encoding='utf-8').write(
        json.dumps(state, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
