# -*- coding: utf-8 -*-
"""七猫全链v3：重试开弹层→选卡→确认→表单页→填表→抓每个字段的填入结果（不提交）。"""
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

try:
    page = b.first_page()
    page.navigate('https://zuozhe.qimao.com/front/book-manage', timeout=30)
    time.sleep(6)
    ok_dialog = False
    for attempt in range(4):
        page.real_click_text('新建小说', "button,a,[role=button],[class*=btn]")
        for _ in range(12):
            if page.call("()=>!!document.querySelector('.client-select')"):
                ok_dialog = True
                break
            time.sleep(1)
        if ok_dialog:
            break
        print('第%d次尝试：弹层未开，重试' % (attempt + 1))
        time.sleep(2)
    print('弹层:', ok_dialog)
    if not ok_dialog:
        sys.exit(0)
    time.sleep(1)
    r = page.call("""()=>{const ds=[...document.querySelectorAll('.client-select')];
      const el=ds[ds.length-1];
      const sp=[...el.querySelectorAll('span.client-name')]
        .find(e=>(e.innerText||'').trim()==='七猫中文网');
      const card=sp&&sp.closest('.client-box');
      if(!card)return{err:'nf card'};
      const rc=card.getBoundingClientRect();
      return {ok:true,x:Math.round(rc.x+rc.width/2),y:Math.round(rc.y+rc.height/2)};}""")
    print('卡片:', json.dumps(r, ensure_ascii=False))
    real_click(page, r['x'], r['y'])
    time.sleep(1.5)
    rc2 = page.real_click_text('确认', "button,[class*=btn],a")
    print('确认:', json.dumps(rc2, ensure_ascii=False)[:80])
    for _ in range(10):
        if page.call("()=>!!document.querySelector(\"input[placeholder='请选择一级分类']\")"):
            break
        time.sleep(1)
    time.sleep(2)
    print('URL:', page.call("()=>location.href.slice(0,90)"))
    # 表单全景
    form = page.call("""()=>{const out={inputs:[],textareas:[],radios:[],tagTriggers:[]};
      for(const e of document.querySelectorAll('input')){
        const r=e.getBoundingClientRect();
        if(r.width<=0&&e.type!=='radio')continue;
        if(e.placeholder)out.inputs.push({ph:e.placeholder.slice(0,20),type:String(e.type)});
        if(e.type==='radio')out.radios.push({v:e.value,n:e.name||''});}
      for(const e of document.querySelectorAll('textarea')){
        const r=e.getBoundingClientRect();
        if(r.width<=0)continue;
        out.textareas.push({ph:(e.placeholder||'').slice(0,25)});}
      const lines=(document.body.innerText||'').split(String.fromCharCode(10));
      out.formLabels=lines.filter(function(l){
        return l.indexOf('作品类型')>=0||l.indexOf('标签')>=0||l.indexOf('分类')>=0
            ||l.indexOf('封面')>=0||l.indexOf('简介')>=0;}).slice(0,10);
      return out;}""")
    print(json.dumps(form, ensure_ascii=False, indent=1)[:1200])
    io.open('tests/_out/qimao_form_full.json', 'w', encoding='utf-8').write(
        json.dumps(form, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
