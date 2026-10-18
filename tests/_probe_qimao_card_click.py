# -*- coding: utf-8 -*-
"""诊断3：七猫站点卡片（七猫中文网）的容器结构与正确点击目标（不提交表单）。"""
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
    # 抓 七猫中文网 span 的祖先链
    anc = page.call("""()=>{
      const spans=[...document.querySelectorAll('span.client-name')]
        .filter(e=>(e.innerText||'').trim()==='七猫中文网');
      if(!spans.length)return{none:true};
      const sp=spans[0]; const chain=[];
      let e=sp;
      for(let i=0;i<5&&e;i++){
        const r=e.getBoundingClientRect();
        chain.push({tag:e.tagName,cls:String(e.className).slice(0,70),
          x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)});
        e=e.parentElement;
      }
      return {chain};
    }""")
    print('祖先链:', json.dumps(anc, ensure_ascii=False, indent=1)[:700])
    out['chain'] = anc
    # 找卡片容器类名
    card_cls = None
    for c in (anc.get('chain') or []):
        cl = c.get('cls', '')
        if 'client' in cl and 'name' not in cl and 'desc' not in cl:
            card_cls = cl.split(' ')[0]
            break
    print('卡片容器类:', card_cls)
    # 用容器中心做真实点击
    r = page.call("""(cls)=>{
      const spans=[...document.querySelectorAll('span.client-name')]
        .filter(e=>(e.innerText||'').trim()==='七猫中文网');
      const sp=spans[0]; if(!sp)return{ok:false,err:'nf'};
      const card=sp.closest('.'+cls)||sp.parentElement;
      card.scrollIntoView({block:'center'});
      const rc=card.getBoundingClientRect();
      return {ok:true,x:Math.round(rc.x+rc.width/2),y:Math.round(rc.y+rc.height/2),
        cardCls:String(card.className)};
    }""", card_cls or 'client-item')
    print('点击目标:', json.dumps(r, ensure_ascii=False))
    if (r or {}).get('ok'):
        m = page.real_click_xy = None
        # 用 CDP 真实鼠标派发（同 real_click_text 的派发通道）
        page.send('Input.dispatchMouseEvent', {'type': 'mousePressed', 'x': r['x'], 'y': r['y'],
                                               'button': 'left', 'clickCount': 1})
        page.send('Input.dispatchMouseEvent', {'type': 'mouseReleased', 'x': r['x'], 'y': r['y'],
                                               'button': 'left', 'clickCount': 1})
        time.sleep(1.5)
        sel = page.call("""()=>{
          const spans=[...document.querySelectorAll('span.client-name')]
            .filter(e=>(e.innerText||'').trim()==='七猫中文网');
          const sp=spans[0]; if(!sp)return{none:true};
          let e=sp; 
          for(let i=0;i<4&&e;i++){
            const c=String(e.className);
            if(/checked|active|selected|cur/.test(c))return{sel:true,cls:c};
            e=e.parentElement;
          }
          return {sel:false, cls:String((sp.closest('[class*=client]')||{}).className||'')};
        }""")
        print('选中态:', json.dumps(sel, ensure_ascii=False))
        out['sel'] = sel
        # 确认 → 看是否跳表单
        r2 = page.real_click_text('确认', "button,[class*=btn],a")
        time.sleep(4)
        info = page.call("""()=>({url:location.href,
          ta:!!document.querySelector("textarea[placeholder*='作品名称']")})""")
        print('确认后:', json.dumps(info, ensure_ascii=False))
        out['after'] = info
    io.open('tests/_out/qimao_card_click.json', 'w', encoding='utf-8').write(
        json.dumps(out, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
