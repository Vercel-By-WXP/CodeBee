# -*- coding: utf-8 -*-
"""七猫确认链路追踪：选卡→确认→抓请求与跳转。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'qimao')
b = Browser(prof)
try:
    page = b.first_page()
    page.navigate('https://zuozhe.qimao.com/front/book-manage', timeout=30)
    time.sleep(6)
    # 被动 XHR 监听
    page.call("""()=>{window.__resp=[];
      const oo=XMLHttpRequest.prototype.open,os=XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.open=function(m,u){this.__u=u;return oo.apply(this,arguments);};
      XMLHttpRequest.prototype.send=function(){this.addEventListener('load',()=>{
        try{window.__resp.push({u:String(this.__u||'').slice(0,120),s:this.status,
          b:String(this.responseText).slice(0,300)});}catch(e){}});return os.apply(this,arguments);};}""")
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
    print('卡片:', json.dumps(r, ensure_ascii=False))
    if r.get('ok'):
        page.send("Input.dispatchMouseEvent", {"type":"mousePressed","x":r["x"],"y":r["y"],"button":"left","clickCount":1}, timeout=8.0)
        page.send("Input.dispatchMouseEvent", {"type":"mouseReleased","x":r["x"],"y":r["y"],"button":"left","clickCount":1}, timeout=8.0)
        time.sleep(1.5)
        # 选中态验证
        sel = page.call("""()=>{const sp=[...document.querySelectorAll('span.client-name')]
          .find(e=>(e.innerText||'').trim()==='七猫中文网');
          if(!sp)return{err:'nf'};
          let e=sp;
          for(let i=0;i<4&&e;i++){
            const c=String(e.className);
            if(/checked|active|selected|cur/.test(c))return{sel:true,cls:c};
            e=e.parentElement;}
          const card=sp.closest('.client-box');
          return {sel:false,cardCls:String(card?card.className:'')};}""")
        print('选中态:', json.dumps(sel, ensure_ascii=False))
        rc2 = page.real_click_text('确认', "button,[class*=btn],a")
        print('确认点击:', json.dumps(rc2, ensure_ascii=False)[:90])
        time.sleep(5)
        print('URL:', page.call("()=>location.href.slice(0,90)"))
        print('有一级分类框:', page.call("()=>!!document.querySelector(\"input[placeholder='请选择一级分类']\")"))
        resp = page.call("()=>window.__resp||[]")
        print('== 请求 ==')
        for x in (resp or [])[:6]:
            print(json.dumps(x, ensure_ascii=False)[:280])
finally:
    try:
        b.close()
    except Exception:
        pass
