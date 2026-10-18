# -*- coding: utf-8 -*-
"""七猫弹层 HTML 全文 dump（弹层开了但 .client-name 全空，看真实结构）。"""
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
    for attempt in range(4):
        page.real_click_text('新建小说', "button,a,[role=button],[class*=btn]")
        for _ in range(12):
            if page.call("()=>!!document.querySelector('.client-select')"):
                break
            time.sleep(1)
        if page.call("()=>!!document.querySelector('.client-select')"):
            break
        time.sleep(2)
    time.sleep(2)
    html = page.call("""()=>{const el=document.querySelector('.client-select');
      return el?el.outerHTML.slice(0,3000):'(gone)';}""")
    print(str(html)[:2800])
    io.open('tests/_out/qimao_dialog_html.txt', 'w', encoding='utf-8').write(str(html))
finally:
    try:
        b.close()
    except Exception:
        pass
