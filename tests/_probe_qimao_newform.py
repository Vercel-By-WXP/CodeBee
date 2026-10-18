# -*- coding: utf-8 -*-
"""七猫新表单页全结构：点新建小说直达表单后 dump 全部字段+下拉+标签区（不提交）。"""
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
    real_click(page, 1160, 99)
    time.sleep(4)
    print('URL:', page.call("()=>location.href.slice(0,100)"))
    form = page.call("""()=>{const out={fields:[],btns:[],labels:[]};
      for(const e of document.querySelectorAll('input,textarea')){
        const r=e.getBoundingClientRect();
        if(r.width<=0&&r.height<=0)continue;
        out.fields.push({tag:e.tagName,ph:(e.placeholder||'').slice(0,30),
          x:Math.round(r.x),y:Math.round(r.y),type:String(e.type||'')});}
      for(const e of document.querySelectorAll('button,[class*=btn]')){
        const r=e.getBoundingClientRect();
        if(r.width<=0)continue;
        const t=(e.innerText||'').trim();
        if(t&&t.length<=12)out.btns.push({t:t,x:Math.round(r.x),y:Math.round(r.y)});}
      const lines=(document.body.innerText||'').split(String.fromCharCode(10))
        .filter(l=>l.trim());
      out.labels=lines.filter(function(l){
        return l.indexOf('作品类型')>=0||l.indexOf('分类')>=0||l.indexOf('标签')>=0
            ||l.indexOf('封面')>=0||l.indexOf('简介')>=0||l.indexOf('笔名')>=0;}).slice(0,12);
      return out;}""")
    print(json.dumps(form, ensure_ascii=False, indent=1)[:1800])
    io.open('tests/_out/qimao_newform.json', 'w', encoding='utf-8').write(
        json.dumps(form, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
