# -*- coding: utf-8 -*-
"""卷修复：删空的第一卷默认 → 第二卷重生归来改名第一卷重生归来 → 确定。"""
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
    # 删 第一卷：默认（空卷）——找含该文本的行内删除图标
    row = page.call("""()=>{
      const rows=[...document.querySelectorAll('[class*=modal] div,[class*=modal] li')];
      for(const r0 of rows){
        const t=(r0.innerText||'').trim();
        if(t.indexOf('第一卷')===0 && t.indexOf('默认')>=0 && t.length<20){
          const del=[...r0.querySelectorAll('svg,[class*=delete],[class*=icon]')];
          const r=r0.getBoundingClientRect();
          return {t:t, x:Math.round(r.right-18), y:Math.round(r.y+r.height/2)};}}
      return {err:'nf row'};}""")
    print('第一卷行:', json.dumps(row, ensure_ascii=False))
    if row.get('x'):
        page.send("Input.dispatchMouseEvent", {"type":"mousePressed","x":row["x"],"y":row["y"],"button":"left","clickCount":1}, timeout=8.0)
        page.send("Input.dispatchMouseEvent", {"type":"mouseReleased","x":row["x"],"y":row["y"],"button":"left","clickCount":1}, timeout=8.0)
        time.sleep(2)
        # 可能出现确认弹层
        page.real_click_text('确定', "button")
        time.sleep(1.5)
    st = page.call("""()=>{const t=(document.body.innerText||'');
      return {still_has_default:t.indexOf('第一卷：默认')>=0,
              has_reborn:t.indexOf('重生归来')>=0};}""")
    print('删除后:', json.dumps(st, ensure_ascii=False))
finally:
    try:
        b.close()
    except Exception:
        pass
