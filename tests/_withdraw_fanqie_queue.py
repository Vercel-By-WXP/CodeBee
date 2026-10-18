# -*- coding: utf-8 -*-
"""撤番茄定时队列 v2：朴素 JS 风格，逐章删除+确认。"""
import sys, time, json
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

FIND = ("function(){"
        "var rows=document.querySelectorAll('div,li,tr');"
        "for(var i=0;i<rows.length;i++){"
        "var r0=rows[i];"
        "var rr=r0.getBoundingClientRect();"
        "if(rr.width<=200||rr.height<20||rr.height>80||rr.y<300||rr.y>700)continue;"
        "var t=r0.innerText||'';"
        "if(t.indexOf('待发布')<0)continue;"
        "var icons=r0.querySelectorAll('svg');"
        "if(!icons.length)continue;"
        "var ic=icons[icons.length-1];"
        "var r=ic.getBoundingClientRect();"
        "if(r.x<1100)continue;"
        "return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),"
        "t:t.split(String.fromCharCode(10))[0].slice(0,14)};}"
        "return {done:true};}")

COUNT = ("function(){var t=document.body.innerText||'';"
         "var m=t.match(/待发布/g);"
         "return m?m.length:0;}")

try:
    page = b.first_page()
    page.navigate('https://fanqienovel.com/main/writer/chapter-manage/7697858830406798398',
                  timeout=30)
    time.sleep(9)
    for round_ in range(12):
        pos = page.call(FIND)
        if pos.get('done'):
            print('队列已清空')
            break
        print('删:', json.dumps(pos, ensure_ascii=False))
        real_click(page, pos['x'], pos['y'])
        time.sleep(1.5)
        page.real_click_text('确定', "button")
        time.sleep(2.5)
    print('终态 待发布数:', page.call(COUNT))
finally:
    try:
        b.close()
    except Exception:
        pass
