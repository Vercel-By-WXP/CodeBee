# -*- coding: utf-8 -*-
"""七猫入口排障：elementFromPoint 查遮挡 + JS el.click() 派发 + 长轮询。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'qimao')
b = Browser(prof)
try:
    page = b.first_page()
    page.navigate('https://zuozhe.qimao.com/front/book-manage', timeout=30)
    time.sleep(10)
    st = page.call("""()=>{const els=[...document.querySelectorAll('a,button')]
      .filter(e=>(e.innerText||'').trim()==='新建小说');
      if(!els.length)return{err:'nf'};
      const e=els[0];
      const r=e.getBoundingClientRect();
      const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
      return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),
              hitTag:hit?hit.tagName:null,
              hitCls:hit?String(hit.className||'').slice(0,50):null,
              same:hit===e};}""")
    print('落点检查:', json.dumps(st, ensure_ascii=False))
    r2 = page.call("""()=>{const els=[...document.querySelectorAll('a,button')]
      .filter(e=>(e.innerText||'').trim()==='新建小说');
      if(!els.length)return{err:'nf'};
      els[0].click();
      return {ok:true};}""")
    print('JS click:', json.dumps(r2, ensure_ascii=False))
    got = None
    for _ in range(15):
        time.sleep(1)
        u = str(page.call("()=>location.href"))
        if 'information' in u:
            got = 'url'
            break
        if page.call("()=>!!document.querySelector(\"textarea[placeholder*='作品名称'],input[placeholder*='作品名称']\")"):
            got = 'field'
            break
    print('信号:', got, '| URL:', page.call("()=>location.href.slice(0,100)"))
    if got:
        time.sleep(2)
        form = page.call("""()=>{const out={fields:[]};
          for(const e of document.querySelectorAll('input,textarea')){
            const r=e.getBoundingClientRect();
            if(r.width<=0&&r.height<=0)continue;
            out.fields.push({tag:e.tagName,ph:(e.placeholder||'').slice(0,30),
              x:Math.round(r.x),y:Math.round(r.y)});}
          return out;}""")
        print(json.dumps(form, ensure_ascii=False, indent=1)[:1200])
        shot = Path('tests/_out/shots')
        shot.mkdir(parents=True, exist_ok=True)
        page.screenshot(str(shot / 'qimao-form-js-click.png'))
finally:
    try:
        b.close()
    except Exception:
        pass
