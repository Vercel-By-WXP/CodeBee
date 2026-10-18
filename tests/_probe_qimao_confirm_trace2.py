# -*- coding: utf-8 -*-
"""七猫确认链路v2：新建小说→等弹层→选卡→确认→抓跳转（贴近服务流程节奏）。"""
import sys, time, json
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'qimao')
b = Browser(prof)
try:
    page = b.first_page()
    page.navigate('https://zuozhe.qimao.com/front/book-manage', timeout=30)
    time.sleep(6)
    r0 = page.real_click_text('新建小说', "button,a,[role=button],[class*=btn]")
    print('新建小说:', json.dumps(r0, ensure_ascii=False)[:70])
    # 等弹层（最多 15s，轮询）
    got = False
    for _ in range(15):
        st = page.call("()=>!!document.querySelector('.client-select')")
        if st:
            got = True
            break
        time.sleep(1)
    print('弹层出现:', got)
    if got:
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
        if r.get('ok'):
            page.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": r["x"], "y": r["y"],
                                                   "button": "left", "clickCount": 1}, timeout=8.0)
            page.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": r["x"], "y": r["y"],
                                                   "button": "left", "clickCount": 1}, timeout=8.0)
            time.sleep(1.5)
            rc2 = page.real_click_text('确认', "button,[class*=btn],a")
            print('确认:', json.dumps(rc2, ensure_ascii=False)[:80])
            time.sleep(6)
            print('URL:', page.call("()=>location.href.slice(0,90)"))
            print('有一级分类框:', page.call(
                "()=>!!document.querySelector(\"input[placeholder='请选择一级分类']\")"))
finally:
    try:
        b.close()
    except Exception:
        pass
