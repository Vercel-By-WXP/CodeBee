# -*- coding: utf-8 -*-
"""七猫入口决定版：real_click_text 点新建小说，轮询 URL 跳转/表单出现，成则 dump 全表单。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'qimao')
b = Browser(prof)
try:
    page = b.first_page()
    page.navigate('https://zuozhe.qimao.com/front/book-manage', timeout=30)
    time.sleep(8)
    r = page.real_click_text('新建小说', "button,a,[role=button],[class*=btn]")
    print('点击:', json.dumps(r, ensure_ascii=False)[:90])
    got = None
    for _ in range(15):
        time.sleep(1)
        u = page.call("()=>location.href")
        if 'information' in str(u):
            got = 'url'
            break
        if page.call("()=>!!document.querySelector(\"textarea[placeholder*='作品名称'],input[placeholder*='作品名称']\")"):
            got = 'field'
            break
    print('信号:', got, '| URL:', page.call("()=>location.href.slice(0,100)"))
    if got:
        time.sleep(2)
        form = page.call("""()=>{const out={fields:[],labels:[]};
          for(const e of document.querySelectorAll('input,textarea')){
            const r=e.getBoundingClientRect();
            if(r.width<=0&&r.height<=0)continue;
            out.fields.push({tag:e.tagName,ph:(e.placeholder||'').slice(0,30),
              x:Math.round(r.x),y:Math.round(r.y)});}
          const lines=(document.body.innerText||'').split(String.fromCharCode(10)).filter(l=>l.trim());
          out.labels=lines.filter(function(l){
            return l.indexOf('作品类型')>=0||l.indexOf('一级')>=0||l.indexOf('二级')>=0
                ||l.indexOf('标签')>=0||l.indexOf('封面')>=0||l.indexOf('频道')>=0
                ||l.indexOf('男')>=0||l.indexOf('女')>=0;}).slice(0,14);
          return out;}""")
        print(json.dumps(form, ensure_ascii=False, indent=1)[:1600])
        io.open('tests/_out/qimao_entry_final.json', 'w', encoding='utf-8').write(
            json.dumps(form, ensure_ascii=False, indent=1))
        shot = Path('tests/_out/shots')
        shot.mkdir(parents=True, exist_ok=True)
        page.screenshot(str(shot / 'qimao-form-real.png'))
finally:
    try:
        b.close()
    except Exception:
        pass
