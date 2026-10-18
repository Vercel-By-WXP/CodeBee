# -*- coding: utf-8 -*-
"""卷改名：第二卷：重生归来 → 第一卷：重生归来 → 确定。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'fanqie')
b = Browser(prof)
try:
    page = b.first_page()
    page.navigate('https://fanqienovel.com/main/writer/chapter-manage/7697858830406798398',
                  timeout=30)
    time.sleep(9)
    page.real_click_text('编辑分卷', "button,a,span,div,[class*=btn]")
    time.sleep(3)
    # 点 重生归来 行的编辑（✏）图标——在删除图标左侧约 22px
    row = page.call("""()=>{
      const rows=[...document.querySelectorAll('[class*=modal] div,[class*=modal] li')];
      for(const r0 of rows){
        const t=(r0.innerText||'').trim();
        if(t.indexOf('重生归来')>=0 && t.length<16){
          const r=r0.getBoundingClientRect();
          return {t:t, xEd:Math.round(r.right-40), xDel:Math.round(r.right-18),
                  y:Math.round(r.y+r.height/2)};}}
      return {err:'nf row'};}""")
    print('行:', json.dumps(row, ensure_ascii=False))
    if row.get('xEd'):
        page.send("Input.dispatchMouseEvent", {"type":"mousePressed","x":row["xEd"],"y":row["y"],"button":"left","clickCount":1}, timeout=8.0)
        page.send("Input.dispatchMouseEvent", {"type":"mouseReleased","x":row["xEd"],"y":row["y"],"button":"left","clickCount":1}, timeout=8.0)
        time.sleep(1.5)
        # 找出现的内容为 重生归来 的输入框，改值
        st = page.call("""()=>{
          const inp=[...document.querySelectorAll('.byte-modal input,[class*=modal] input')]
            .filter(e=>e.getBoundingClientRect().width>0 && (e.value||'').includes('重生归来'));
          if(!inp.length)return{err:'nf input'};
          const native=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
          native.call(inp[0],'第一卷：重生归来');
          inp[0].dispatchEvent(new Event('input',{bubbles:true}));
          inp[0].dispatchEvent(new Event('change',{bubbles:true}));
          return {ok:true};}""")
        print('改名:', json.dumps(st, ensure_ascii=False))
        time.sleep(1)
        r2 = page.real_click_text('确定', "button")
        print('确定:', json.dumps(r2, ensure_ascii=False)[:70])
        time.sleep(2)
        fin = page.call("""()=>{const t=(document.body.innerText||'');
          return {has_v1:t.indexOf('第一卷：重生归来')>=0,
                  has_v2:t.indexOf('第二卷：重生归来')>=0};}""")
        print('终态:', json.dumps(fin, ensure_ascii=False))
finally:
    try:
        b.close()
    except Exception:
        pass
