# -*- coding: utf-8 -*-
"""卷改名v3：真实点击编辑图标 → 输入框改名 → 确定 → 验证。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'fanqie')
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
    page.navigate('https://fanqienovel.com/main/writer/chapter-manage/7697858830406798398',
                  timeout=30)
    time.sleep(9)
    page.real_click_text('编辑分卷', "button,a,span,div,[class*=btn]")
    time.sleep(3)
    real_click(page, 912, 324)   # 重生归来 行的编辑图标
    time.sleep(1.5)
    st = page.call("""()=>{
      const inp=[...document.querySelectorAll('input')]
        .filter(e=>e.getBoundingClientRect().width>0 && (e.value||'').indexOf('重生归来')>=0);
      return {n:inp.length, v:inp.length?inp[0].value:''};}""")
    print('改名输入框:', json.dumps(st, ensure_ascii=False))
    if st.get('n'):
        page.call("""()=>{
          const inp=[...document.querySelectorAll('input')]
            .find(e=>e.getBoundingClientRect().width>0 && (e.value||'').indexOf('重生归来')>=0);
          const native=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
          native.call(inp,'第一卷：重生归来');
          inp.dispatchEvent(new Event('input',{bubbles:true}));
          inp.dispatchEvent(new Event('change',{bubbles:true}));}""")
        time.sleep(0.8)
        real_click(page, 882, 556)   # 确定 按钮位置（弹层底部橙色）
        time.sleep(2)
        fin = page.call("""()=>{const t=(document.body.innerText||'');
          return {v1:t.indexOf('第一卷：重生归来')>=0,
                  v2:t.indexOf('第二卷：重生归来')>=0,
                  stillDialog:!!document.querySelector('.byte-modal')};}""")
        print('改名后:', json.dumps(fin, ensure_ascii=False))
        if fin.get('stillDialog'):
            # 可能弹了二次确认或名字校验，截图看
            shot = Path('tests/_out/shots'); shot.mkdir(parents=True, exist_ok=True)
            page.screenshot(str(shot / 'fanqie-rename-after.png'))
            # 再点一次确定
            page.real_click_text('确定', "button")
            time.sleep(2)
            fin2 = page.call("""()=>{const t=(document.body.innerText||'');
              return {v1:t.indexOf('第一卷：重生归来')>=0,
                      v2:t.indexOf('第二卷：重生归来')>=0};}""")
            print('二次确定后:', json.dumps(fin2, ensure_ascii=False))
finally:
    try:
        b.close()
    except Exception:
        pass
