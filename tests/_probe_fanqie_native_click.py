# -*- coding: utf-8 -*-
"""对照实验：JS 原生 btn.click() vs CDP 真实鼠标——谁能触发创建请求。"""
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
# 换个书名避免重复书干扰判断
values = fq.values_create_book(dict(data, book_name='嫡女符嫂渡杀录'))
ov = json.load(io.open(r'C:/Users/HP/AppData/Roaming/CodeBee/publish/flows-fanqie.json',
                       encoding='utf-8'))
steps = ov['create_book']
sub_idx = next(i for i, s in enumerate(steps) if s.get('do') == 'submit')
steps = steps[:sub_idx]
steps = mgr._with_tag_steps(steps, fq.tag_groups(data), values, fq)

b = Browser(prof)
try:
    page = b.first_page()
    page.navigate('https://fanqienovel.com/main/writer/create', timeout=30)
    time.sleep(6)
    # 只装被动监听（不 fetch monkey-patch，排除监听本身的影响）
    page.call("""()=>{window.__n=0;
      const oo=XMLHttpRequest.prototype.open,os=XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.open=function(m,u){this.__u=u;return oo.apply(this,arguments);};
      XMLHttpRequest.prototype.send=function(){window.__n++;return os.apply(this,arguments);};}""")
    flow.run_flow(page, steps, values=values, config=fq.CONFIG,
                  auto_submit=True, dup_check=False,
                  shot=lambda n: None, log=lambda s: None)
    time.sleep(1)
    # A: CDP 真实鼠标
    r = page.call("""()=>{const b=[...document.querySelectorAll('button')]
      .find(e=>(e.innerText||'').includes('立即创建'));
      const rc=b.getBoundingClientRect();
      return {x:Math.round(rc.x+rc.width/2),y:Math.round(rc.y+rc.height/2)};}""")
    page.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": r['x'], "y": r['y'],
                                           "button": "left", "clickCount": 1}, timeout=8.0)
    page.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": r['x'], "y": r['y'],
                                           "button": "left", "clickCount": 1}, timeout=8.0)
    time.sleep(4)
    n1 = page.call("()=>window.__n")
    print('CDP 真实鼠标后请求数:', n1)
    # B: JS 原生 click
    page.call("""()=>{const b=[...document.querySelectorAll('button')]
      .find(e=>(e.innerText||'').includes('立即创建'));
      if(b)b.click();}""")
    time.sleep(4)
    n2 = page.call("()=>window.__n")
    print('JS 原生 click 后请求数:', n2)
    print('最终URL:', page.call("()=>location.href.slice(0,110)"))
    # 若有报错提示，抓走
    err = page.call("""()=>{const els=[...document.querySelectorAll('[class*=message],[class*=error],[class*=toast]')]
      .filter(e=>e.getBoundingClientRect().width>0)
      .map(e=>(e.innerText||'').trim()).filter(x=>x);
      return els.slice(0,4);}""")
    print('页面提示:', json.dumps(err, ensure_ascii=False))
finally:
    try:
        b.close()
    except Exception:
        pass
