# -*- coding: utf-8 -*-
"""七猫校准：表单页（information?client_id=1）的 分类下拉/标签弹层 结构探针（只开不提交）。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'qimao')
b = Browser(prof)
out = {}
try:
    page = b.first_page()
    page.navigate('https://zuozhe.qimao.com/front/book-manage', timeout=30)
    time.sleep(6)
    page.real_click_text('新建小说', "button,a,[role=button],[class*=btn]")
    time.sleep(3)
    r = page.call("""()=>{const ds=[...document.querySelectorAll('.client-select')];
      const el=ds[ds.length-1];
      if(!el)return{err:'nf dialog'};
      const sp=[...el.querySelectorAll('span.client-name')]
        .find(e=>(e.innerText||'').trim()==='七猫中文网');
      const card=sp&&sp.closest('.client-box');
      if(!card)return{err:'nf card'};
      const rc=card.getBoundingClientRect();
      return {ok:true,x:Math.round(rc.x+rc.width/2),y:Math.round(rc.y+rc.height/2)};}""")
    print('卡片定位:', json.dumps(r, ensure_ascii=False))
    if r.get('ok'):
        page.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": r["x"], "y": r["y"],
                                               "button": "left", "clickCount": 1}, timeout=8.0)
        page.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": r["x"], "y": r["y"],
                                               "button": "left", "clickCount": 1}, timeout=8.0)
        time.sleep(1.2)
        page.real_click_text('确认', "button,[class*=btn],a")
        time.sleep(5)
    # 表单页：点开 一级分类 下拉，抓 popper 结构
    st1 = page.call("""()=>{
      const inp=document.querySelector("input[placeholder='请选择一级分类']");
      if(!inp)return{err:'nf 一级分类输入框', url:location.href};
      const r=inp.getBoundingClientRect();
      return {ok:true,x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};}""")
    print('一级分类框:', json.dumps(st1, ensure_ascii=False))
    if st1.get('ok'):
        page.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": st1["x"], "y": st1["y"],
                                               "button": "left", "clickCount": 1}, timeout=8.0)
        page.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": st1["x"], "y": st1["y"],
                                               "button": "left", "clickCount": 1}, timeout=8.0)
        time.sleep(2.5)
        drop = page.call("""()=>{
          const pops=[...document.querySelectorAll('[class*=popper],[class*=dropdown],[class*=select-menu]')]
            .filter(e=>e.getBoundingClientRect().height>0);
          const items=[];
          for(const p of pops){
            for(const li of p.querySelectorAll('li,span,[role=option]')){
              const t=(li.innerText||'').trim();
              if(t&&t.length<=12)items.push({cls:String(li.className).slice(0,40),t:t.slice(0,12)});}}
          const uniq=[];const seen=new Set();
          for(const it of items){if(!seen.has(it.t)){seen.add(it.t);uniq.push(it);}}
          return {poppers:pops.length, items:uniq.slice(0,25)};}""")
        print('下拉选项:', json.dumps(drop, ensure_ascii=False)[:800])
        out['category_dropdown'] = drop
        # 关掉下拉（按 ESC 语义：点页面空白处）
        page.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 30, "y": 500,
                                               "button": "left", "clickCount": 1}, timeout=8.0)
        page.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": 30, "y": 500,
                                               "button": "left", "clickCount": 1}, timeout=8.0)
        time.sleep(1)
    # 找 作品标签 触发器（新增的标签选择入口）
    tag = page.call("""()=>{
      const els=[...document.querySelectorAll('div,span,button,[class*=tag],[class*=label]')]
        .filter(e=>e.getBoundingClientRect().width>0);
      const hits=els.filter(e=>{const t=(e.innerText||'').trim();
        return t.includes('作品标签')||t.includes('添加标签')||t.includes('选择标签');})
        .map(e=>({t:(e.innerText||'').trim().slice(0,20),cls:String(e.className).slice(0,50)}));
      return hits.slice(0,8);}""")
    print('标签触发器:', json.dumps(tag, ensure_ascii=False)[:400])
    out['tag_triggers'] = tag
    io.open('tests/_out/qimao_form_fields.json', 'w', encoding='utf-8').write(
        json.dumps(out, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
