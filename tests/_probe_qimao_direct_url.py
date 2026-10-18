# -*- coding: utf-8 -*-
"""七猫直达表单页验证：information?client_id=1 能否绕过死掉的新建入口。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'qimao')
b = Browser(prof)
try:
    page = b.first_page()
    page.navigate('https://zuozhe.qimao.com/front/book-manage/information?client_id=1', timeout=30)
    time.sleep(8)
    st = page.call("""()=>{const out={url:location.href.slice(0,100),fields:[],lines:[]};
      for(const e of document.querySelectorAll('input,textarea')){
        const r=e.getBoundingClientRect();
        if(r.width<=0&&r.height<=0)continue;
        out.fields.push({tag:e.tagName,ph:(e.placeholder||'').slice(0,28),
          x:Math.round(r.x),y:Math.round(r.y)});}
      const ls=(document.body.innerText||'').split(String.fromCharCode(10)).filter(l=>l.trim());
      out.lines=ls.filter(function(l){
        return l.indexOf('作品')>=0||l.indexOf('分类')>=0||l.indexOf('频道')>=0
            ||l.indexOf('标签')>=0||l.indexOf('封面')>=0||l.indexOf('男')>=0
            ||l.indexOf('女')>=0||l.indexOf('创建')>=0;}).slice(0,15);
      return out;}""")
    print(json.dumps(st, ensure_ascii=False, indent=1)[:1600])
    io.open('tests/_out/qimao_direct_url.json', 'w', encoding='utf-8').write(
        json.dumps(st, ensure_ascii=False, indent=1))
    shot = Path('tests/_out/shots'); shot.mkdir(parents=True, exist_ok=True)
    page.screenshot(str(shot / 'qimao-direct-form.png'))
finally:
    try:
        b.close()
    except Exception:
        pass
