# -*- coding: utf-8 -*-
"""七猫入口终局：点 新建小说 → 轮询任意可见弹层/页面变化 → dump 新站点选择形态。"""
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
    time.sleep(8)
    real_click(page, 1160, 99)   # 新建小说（1123,92 中心略偏右）
    time.sleep(2)
    # 抓任何出现的新可见弹层
    st = page.call("""()=>{const out=[];
      for(const e of document.querySelectorAll('[class*=dialog],[class*=modal],[class*=pop],[class*=drawer]')){
        const r=e.getBoundingClientRect();
        const s=getComputedStyle(e);
        if(r.width<=0||r.height<=0||s.display==='none'||s.visibility==='hidden')continue;
        const txt=(e.innerText||'').trim();
        if(!txt)continue;
        out.push({cls:String(e.className).slice(0,70),
                  txt:txt.split(String.fromCharCode(10)).join('/').slice(0,150)});}
      return {url:location.href.slice(0,90), dialogs:out.slice(0,6)};}""")
    print(json.dumps(st, ensure_ascii=False, indent=1)[:1500])
    shot = Path('tests/_out/shots')
    shot.mkdir(parents=True, exist_ok=True)
    page.screenshot(str(shot / 'qimao-after-create-click.png'))
    io.open('tests/_out/qimao_after_create_click.json', 'w', encoding='utf-8').write(
        json.dumps(st, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
