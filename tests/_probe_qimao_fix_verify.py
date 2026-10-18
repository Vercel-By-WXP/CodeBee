# -*- coding: utf-8 -*-
"""验证：修好的 click_match（真实鼠标派发）选 七猫中文网 卡片 → 确认 → 表单出现。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser
from core.publish.flow import _click_match_js

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'qimao')
b = Browser(prof)
try:
    page = b.first_page()
    page.navigate('https://zuozhe.qimao.com/front/book-manage', timeout=30)
    time.sleep(6)
    r0 = page.real_click_text('新建小说', "button,a,[role=button],[class*=btn]")
    print('新建小说:', json.dumps(r0, ensure_ascii=False)[:80])
    time.sleep(3)
    r = page.call(_click_match_js(), ["七猫中文网", "网站特色"], 400)
    print('click_match:', json.dumps(r, ensure_ascii=False)[:120])
    if (r or {}).get("x") is not None:
        page.send("Input.dispatchMouseEvent",
                  {"type": "mousePressed", "x": r["x"], "y": r["y"],
                   "button": "left", "clickCount": 1}, timeout=8.0)
        page.send("Input.dispatchMouseEvent",
                  {"type": "mouseReleased", "x": r["x"], "y": r["y"],
                   "button": "left", "clickCount": 1}, timeout=8.0)
        print('真实鼠标已派发 @', r["x"], r["y"])
    time.sleep(1.0)
    r2 = page.real_click_text('确认', "button,[class*=btn],a")
    print('确认:', json.dumps(r2, ensure_ascii=False)[:80])
    time.sleep(4)
    info = page.call("""()=>({url:location.href,
      ta:!!document.querySelector("textarea[placeholder*='作品名称']"),
      err:([...document.querySelectorAll('[class*=message],[class*=error]')]
        .map(e=>(e.innerText||'').trim()).filter(x=>x).slice(0,2)).join('|')})""")
    print('结果:', json.dumps(info, ensure_ascii=False))
    io.open('tests/_out/qimao_fix_verify.json', 'w', encoding='utf-8').write(
        json.dumps(info, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
