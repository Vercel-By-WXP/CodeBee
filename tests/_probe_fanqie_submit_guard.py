# -*- coding: utf-8 -*-
"""终极诊断：读「立即创建」按钮的 disabled 逻辑与表单状态快照。"""
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
ov = json.load(io.open(r'C:\Users\HP\AppData\Roaming\CodeBee\publish/flows-fanqie.json'.replace('/publish/', '/publish\\'), encoding='utf-8')) if False else json.load(io.open(r'C:/Users/HP/AppData/Roaming/CodeBee/publish/flows-fanqie.json', encoding='utf-8'))
steps = ov['create_book']
sub_idx = next(i for i, s in enumerate(steps) if s.get('do') == 'submit')
steps = steps[:sub_idx]
steps = mgr._with_tag_steps(steps, fq.tag_groups(data), values, fq)

b = Browser(prof)
try:
    page = b.first_page()
    flow.run_flow(page, steps, values=values, config=fq.CONFIG,
                  auto_submit=True, dup_check=False,
                  shot=lambda n: None, log=lambda s: None)
    time.sleep(1)
    st = page.call("""()=>{
      const btns=[...document.querySelectorAll('button')].filter(e=>
        (e.innerText||'').includes('立即创建'));
      const b0=btns[0];
      const info={n:btns.length};
      if(b0){
        info.disabled=b0.disabled;
        info.aria=b0.getAttribute('aria-disabled');
        info.cls=String(b0.className).slice(0,80);
        const r=b0.getBoundingClientRect();
        info.pos={x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)};
        const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
        info.hitAt=hit?(String(hit.tagName)+'/'+String(hit.className||'').slice(0,50)):null;
      }
      // 表单项的校验态：任何 error/red 类
      const errs=[...document.querySelectorAll('[class*=error],[class*=invalid],[class*=danger]')]
        .filter(e=>e.getBoundingClientRect().width>0)
        .map(e=>({cls:String(e.className).slice(0,50),t:(e.innerText||'').trim().slice(0,40)}))
        .filter(e=>e.t);
      info.errs=errs.slice(0,10);
      // 封面区
      const cover=[...document.querySelectorAll('div,span')]
        .filter(e=>{const t=(e.innerText||'').trim();return t==='选择封面'||t.includes('选择封面');})
        .slice(0,3).map(e=>{const r=e.getBoundingClientRect();
          return {t:(e.innerText||'').trim().slice(0,20),x:Math.round(r.x),y:Math.round(r.y),
                  w:Math.round(r.width),h:Math.round(r.height),cls:String(e.className).slice(0,40)};});
      info.cover=cover;
      return info;}""")
    print(json.dumps(st, ensure_ascii=False, indent=1)[:1200])
    io.open('tests/_out/fanqie_submit_guard.json', 'w', encoding='utf-8').write(
        json.dumps(st, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
