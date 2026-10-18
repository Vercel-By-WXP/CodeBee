# -*- coding: utf-8 -*-
"""七猫校准实验：选频道(女生)→开一级分类→抓选项（验证频道 gating）。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'qimao')
b = Browser(prof)

def real_click(page, x, y):
    page.send("Input.dispatchMouseEvent",
              {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
              timeout=8.0)
    page.send("Input.dispatchMouseEvent",
              {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
              timeout=8.0)

DROP_JS = """()=>{const out=[];
  const sels=['[class*=popper]','[class*=dropdown]','[class*=select-menu]'];
  for(const sel of sels){
    for(const p of document.querySelectorAll(sel)){
      const r=p.getBoundingClientRect();
      if(r.width<=0||r.height<=0)continue;
      for(const li of p.querySelectorAll('li,span,[role=option],p')){
        const rr=li.getBoundingClientRect();
        if(rr.width<=0)continue;
        const t=(li.innerText||'').trim();
        if(t&&t.length<=10&&!out.some(x=>x.t===t))out.push({t:t,cls:String(li.className).slice(0,30)});}}}
  return out.slice(0,25);}"""

try:
    page = b.first_page()
    page.navigate('https://zuozhe.qimao.com/front/book-manage/information?client_id=1', timeout=30)
    time.sleep(8)
    # 女生 label 定位（radio value=1 的可点元素）
    pos = page.call("""()=>{const radios=[...document.querySelectorAll('input[type=radio]')];
      const f=radios.find(e=>e.value==='1');
      if(!f)return{err:'nf'};
      const label=f.closest('label')||f.parentElement;
      const r=label.getBoundingClientRect();
      return{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),
             t:(label.innerText||'').trim().slice(0,20)};}""")
    print('女生radio:', json.dumps(pos, ensure_ascii=False))
    if pos.get('x'):
        real_click(page, pos['x'], pos['y'])
        time.sleep(1.5)
        ck = page.call("""()=>{const radios=[...document.querySelectorAll('input[type=radio]')];
          return radios.map(e=>({v:e.value,checked:e.checked}));}""")
        print('选中态:', json.dumps(ck))
        # 开一级分类
        st1 = page.call("""()=>{const e=document.querySelector("input[placeholder='请选择一级分类']");
          const r=e.getBoundingClientRect();
          return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};}""")
        real_click(page, st1['x'], st1['y'])
        time.sleep(3)
        drop = page.call(DROP_JS)
        print('选频道后一级分类选项:', json.dumps(drop, ensure_ascii=False)[:800])
        io.open('tests/_out/qimao_channel_first.json', 'w', encoding='utf-8').write(
            json.dumps({'radios': ck, 'cat1': drop}, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
