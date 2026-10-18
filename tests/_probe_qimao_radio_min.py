# -*- coding: utf-8 -*-
"""最小实验：直达页→input.click() 选女生→时间线观察 checked 态是否被翻回。"""
import sys, time, json
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'qimao')
b = Browser(prof)
try:
    page = b.first_page()
    page.navigate('https://zuozhe.qimao.com/front/book-manage/information?client_id=1', timeout=30)
    time.sleep(8)
    snap = lambda: page.call(
        "()=>[...document.querySelectorAll('input[type=radio]')]"
        ".map(x=>({v:x.value,c:x.checked,"
        "vis:x.getBoundingClientRect().width>0,"
        "name:x.name||'',id:x.id||''}))")
    print('t0:', json.dumps(snap()))
    r = page.call(
        "()=>{const rs=[...document.querySelectorAll('input[type=radio]')]"
        ".filter(x=>x.value==='1'&&!x.disabled);"
        "if(!rs.length)return{ok:false};rs[0].click();return{ok:true,n:rs.length};}")
    print('click:', json.dumps(r))
    time.sleep(0.4)
    print('t+0.4s:', json.dumps(snap()))
    time.sleep(2)
    print('t+2.4s:', json.dumps(snap()))
    time.sleep(3)
    print('t+5.4s:', json.dumps(snap()))
finally:
    try:
        b.close()
    except Exception:
        pass
