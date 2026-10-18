# -*- coding: utf-8 -*-
"""卷改名v2：精确找 重生归来 行内的编辑图标元素，真实点击其中心。"""
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
    # 找 重生归来 行：取含文本的最小元素，再在行内找两个图标容器
    info = page.call("""()=>{
      const els=[...document.querySelectorAll('[class*=modal] div,[class*=modal] li,[class*=modal] span')];
      const vis=e=>{const r=e.getBoundingClientRect();
        return r.width>0&&r.height>0;};
      const rows=els.filter(e=>vis(e)&&(e.innerText||'').trim()==='第二卷：重生归来');
      if(!rows.length)return{err:'nf row'};
      const row=rows[0];
      // 行内（或兄弟）找图标按钮
      const scope=row.parentElement||row;
      const icons=[...scope.querySelectorAll('button,[role=button],svg,[class*=icon],[class*=edit],[class*=del]')]
        .filter(vis)
        .map(e=>{const r=e.getBoundingClientRect();
          return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),
                  tag:e.tagName,cls:String(e.className).slice(0,40)};});
      const rr=row.getBoundingClientRect();
      return {rowX:Math.round(rr.x),rowR:Math.round(rr.right),rowY:Math.round(rr.y),
              icons:icons.slice(0,8)};}""")
    print('行与图标:', json.dumps(info, ensure_ascii=False)[:600])
    io.open('tests/_out/fanqie_vol_icons.json', 'w', encoding='utf-8').write(
        json.dumps(info, ensure_ascii=False, indent=1))
    shot = Path('tests/_out/shots'); shot.mkdir(parents=True, exist_ok=True)
    page.screenshot(str(shot / 'fanqie-vol-dialog2.png'))
finally:
    try:
        b.close()
    except Exception:
        pass
