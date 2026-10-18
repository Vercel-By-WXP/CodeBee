# -*- coding: utf-8 -*-
"""七猫弹层全景：新建小说后所有可见弹层容器的结构。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'qimao')
b = Browser(prof)
JS = ("()=>{const out=[];"
      "for(const dlg of document.querySelectorAll('[class*=dialog],[class*=modal],[class*=wrapper]')){"
      "const r=dlg.getBoundingClientRect();"
      "if(r.width<=0||r.height<=0)continue;"
      "const txt=(dlg.innerText||'').trim();"
      "if(!txt||txt.length>500)continue;"
      "const t=txt.split(String.fromCharCode(10)).join('/');"
      "out.push({cls:String(dlg.className).slice(0,60),n:dlg.querySelectorAll('*').length,"
      "t:t.slice(0,130)});}"
      "return out.slice(0,10);}")
try:
    page = b.first_page()
    page.navigate('https://zuozhe.qimao.com/front/book-manage', timeout=30)
    time.sleep(6)
    page.real_click_text('新建小说', "button,a,[role=button],[class*=btn]")
    time.sleep(4)
    st = page.call(JS)
    print(json.dumps(st, ensure_ascii=False, indent=1)[:1200])
    io.open('tests/_out/qimao_dialog_generic.json', 'w', encoding='utf-8').write(
        json.dumps(st, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
