# -*- coding: utf-8 -*-
"""枚举7：作者壳侧栏真实点击 作品管理→小说，列全部书。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'fanqie')
b = Browser(prof)

def real_click(page, x, y):
    page.send("Input.dispatchMouseEvent",
              {"type": "mousePressed", "x": x, "y": y,
               "button": "left", "clickCount": 1}, timeout=8.0)
    page.send("Input.dispatchMouseEvent",
              {"type": "mouseReleased", "x": x, "y": y,
               "button": "left", "clickCount": 1}, timeout=8.0)

try:
    page = b.first_page()
    page.navigate('https://fanqienovel.com/main/writer/chapter-manage/7696359004347305022',
                  timeout=30)
    time.sleep(9)
    # 找侧栏「作品管理」中心坐标
    pos = page.call("""()=>{
      const els=[...document.querySelectorAll('span,div,li')]
        .filter(e=>e.getBoundingClientRect().width>0&&e.getBoundingClientRect().height>0);
      const el=els.find(e=>(e.innerText||'').trim()==='作品管理');
      if(!el)return{err:'nf'};
      const r=el.getBoundingClientRect();
      return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2),
              cls:String(el.className).slice(0,50)};
    }""")
    print('作品管理:', json.dumps(pos, ensure_ascii=False))
    if pos.get('x'):
        real_click(page, pos['x'], pos['y'])
        time.sleep(2)
        # 子菜单里的「小说」
        pos2 = page.call("""()=>{
          const els=[...document.querySelectorAll('span,div,li,a')]
            .filter(e=>{const r=e.getBoundingClientRect();
              return r.width>0&&r.height>0&&r.y>0&&r.y<900;});
          const cands=els.filter(e=>(e.innerText||'').trim()==='小说');
          if(!cands.length)return{err:'nf 小说'};
          const el=cands[0];
          const r=el.getBoundingClientRect();
          return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2),
                  n:cands.length};
        }""")
        print('小说:', json.dumps(pos2, ensure_ascii=False))
        if pos2.get('x'):
            real_click(page, pos2['x'], pos2['y'])
            time.sleep(8)
        info = page.call("""()=>{
          const out=[];
          const anchors=document.querySelectorAll('a[href]');
          for(let i=0;i<anchors.length;i++){
            const h=anchors[i].href||'';
            const t=(anchors[i].innerText||'').trim();
            if(t.length>1 && h.indexOf('chapter-manage')>=0){
              out.push({h:h.slice(0,90), t:t.slice(0,26)});}}
          const lines=(document.body.innerText||'').split(String.fromCharCode(10));
          const hits=lines.filter(function(l){
            return l.indexOf('嫡女')>=0||l.indexOf('头七')>=0||l.indexOf('边关')>=0||l.indexOf('重生之')>=0;}).slice(0,10);
          return {url:decodeURIComponent(location.href).slice(0,110),
                  links:out.slice(0,14), hits:hits};
        }""")
        print(json.dumps(info, ensure_ascii=False, indent=1)[:1800])
        io.open('tests/_out/fanqie_booklist6.json', 'w', encoding='utf-8').write(
            json.dumps(info, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
