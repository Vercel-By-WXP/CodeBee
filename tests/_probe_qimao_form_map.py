# -*- coding: utf-8 -*-
"""七猫表单全字段校准：直达页 → 频道radio/分类下拉/标签/按钮 逐项摸底。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'qimao')
b = Browser(prof)
out = {}

def real_click(page, x, y):
    page.send("Input.dispatchMouseEvent",
              {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
              timeout=8.0)
    page.send("Input.dispatchMouseEvent",
              {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
              timeout=8.0)

try:
    page = b.first_page()
    page.navigate('https://zuozhe.qimao.com/front/book-manage/information?client_id=1', timeout=30)
    time.sleep(8)
    # 1) 表单行标签全景（每行 label 文本）
    rows = page.call("""()=>{const out=[];
      for(const it of document.querySelectorAll('[class*=form-item],[class*=form_row],[class*=row]')){
        const r=it.getBoundingClientRect();
        if(r.width<=0||r.height<=0)continue;
        const t=(it.innerText||'').trim();
        if(!t||t.length>60)continue;
        out.push({cls:String(it.className).slice(0,40),t:t.split(String.fromCharCode(10)).join('/').slice(0,50)});
      }
      return [...new Map(out.map(x=>[x.t,x])).values()].slice(0,15);}""")
    print('表单行:', json.dumps(rows, ensure_ascii=False, indent=1)[:900])
    out['rows'] = rows
    # 2) 频道区（y~301 的两个 input 是什么）
    ch = page.call("""()=>{const out=[];
      for(const e of document.querySelectorAll('input')){
        const r=e.getBoundingClientRect();
        if(r.y<270||r.y>330)continue;
        out.push({type:e.type,name:e.name||'',v:e.value||'',
          ph:e.placeholder||'',checked:e.checked,
          sib:(e.parentElement?e.parentElement.innerText.trim().slice(0,20):'')});}
      return out;}""")
    print('频道区:', json.dumps(ch, ensure_ascii=False))
    out['channel'] = ch
    # 3) 点开一级分类下拉，抓选项
    st1 = page.call("""()=>{const e=document.querySelector("input[placeholder='请选择一级分类']");
      const r=e.getBoundingClientRect();
      return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};}""")
    real_click(page, st1['x'], st1['y'])
    time.sleep(2.5)
    drop = page.call("""()=>{const out=[];
      const sels=['[class*=popper]','[class*=dropdown]','[class*=select-menu]','[class*=selectList]'];
      for(const sel of sels){
        for(const p of document.querySelectorAll(sel)){
          const r=p.getBoundingClientRect();
          if(r.width<=0||r.height<=0)continue;
          for(const li of p.querySelectorAll('li,span,[role=option],p')){
            const rr=li.getBoundingClientRect();
            if(rr.width<=0)continue;
            const t=(li.innerText||'').trim();
            if(t&&t.length<=10&&!out.some(x=>x.t===t))out.push({t:t,cls:String(li.className).slice(0,30)});}}}
      return out.slice(0,20);}""")
    print('一级分类选项:', json.dumps(drop, ensure_ascii=False)[:600])
    out['cat1'] = drop
    # 4) 提交按钮状态
    btn = page.call("""()=>{const out=[];
      for(const e of document.querySelectorAll('button,[class*=btn]')){
        const r=e.getBoundingClientRect();
        if(r.width<=0)continue;
        const t=(e.innerText||'').trim();
        if(t.includes('创建')||t.includes('确认')||t.includes('保存'))
          out.push({t:t,x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),
                    dis:e.disabled||String(e.className).includes('dis')});}
      return out.slice(0,5);}""")
    print('按钮:', json.dumps(btn, ensure_ascii=False))
    out['buttons'] = btn
    io.open('tests/_out/qimao_form_map.json', 'w', encoding='utf-8').write(
        json.dumps(out, ensure_ascii=False, indent=1))
    shot = Path('tests/_out/shots'); shot.mkdir(parents=True, exist_ok=True)
    page.screenshot(str(shot / 'qimao-cat1-open.png'))
finally:
    try:
        b.close()
    except Exception:
        pass
