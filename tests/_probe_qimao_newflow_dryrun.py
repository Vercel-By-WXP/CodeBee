# -*- coding: utf-8 -*-
"""新流程仪表化试填：直达URL→全套字段→tags→提交前停，dump 最终表单态。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser
from core.publish import flow
from core.publish import qimao as qm
from core.publish import manager as mgr

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'qimao')
meta_task = json.load(io.open(
    r'C:\Users\HP\AppData\Roaming\CodeBee\tasks\t-20261017-115114-8191.json',
    encoding='utf-8'))
data = (meta_task.get('book_meta') or {}).get('qimao', {}).get('data') or {}
values = qm.values_create_book(data)
steps = json.load(io.open(r'C:/Users/HP/AppData/Roaming/CodeBee/publish/flows-qimao.json',
                          encoding='utf-8'))['create_book']
sub_idx = next(i for i, s in enumerate(steps) if s.get('do') == 'submit')
steps = steps[:sub_idx]
steps = mgr._with_tag_steps(steps, qm.tag_groups(data), values, qm)

b = Browser(prof)
try:
    page = b.first_page()
    logs = []
    shot_dir = Path('tests/_out/shots'); shot_dir.mkdir(parents=True, exist_ok=True)
    n = -1
    try:
        n = flow.run_flow(page, steps, values=values, config=qm.CONFIG,
                          auto_submit=True, dup_check=False,
                          shot=lambda name: page.screenshot(str(shot_dir / (name + '.png'))),
                          log=lambda s: (logs.append(s), print('LOG', s)))
    except Exception as e:
        print('流程异常:', str(e)[:140])
    time.sleep(2)
    st = page.call("""()=>{const out={url:location.href.slice(0,90)};
      out.title=(document.querySelector("textarea[placeholder*='作品名称']")||{value:''}).value.slice(0,20);
      out.cat1=(document.querySelector("input[placeholder='请选择一级分类']")||{value:''}).value;
      out.cat2=(document.querySelector("input[placeholder='请选择二级分类']")||{value:''}).value;
      out.radios=[...document.querySelectorAll('input[type=radio]')].map(e=>({v:e.value,c:e.checked}));
      const btns=[...document.querySelectorAll('button,[class*=btn]')].filter(e=>
        (e.innerText||'').includes('确认创建'));
      if(btns.length){const b0=btns[btns.length-1];
        out.submit_dis=b0.disabled||String(b0.className).includes('dis');}
      const errs=[...document.querySelectorAll('[class*=error],[class*=tip]')]
        .filter(e=>e.getBoundingClientRect().width>0)
        .map(e=>(e.innerText||'').trim()).filter(x=>x&&x.length<40);
      out.errs=[...new Set(errs)].slice(0,6);
      const tagEls=[...document.querySelectorAll('[class*=tag]')].filter(e=>{
        const r=e.getBoundingClientRect();return r.width>0;})
        .map(e=>(e.innerText||'').trim().slice(0,12)).filter(x=>x);
      out.tags=[...new Set(tagEls)].slice(0,15);
      return out;}""")
    print('== 最终表单态 ==')
    print(json.dumps(st, ensure_ascii=False, indent=1)[:1200])
    io.open('tests/_out/qimao_dryrun.json', 'w', encoding='utf-8').write(
        json.dumps({'logs': logs, 'state': st}, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
