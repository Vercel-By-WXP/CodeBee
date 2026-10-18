# -*- coding: utf-8 -*-
"""枚举6：create 页侧栏点 工作台，抓最近作品列表。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'fanqie')
b = Browser(prof)
try:
    page = b.first_page()
    page.navigate('https://fanqienovel.com/main/writer/create', timeout=30)
    time.sleep(8)
    r = page.real_click_text('工作台', "span,div,li,a", y_max=500)
    print('click 工作台:', json.dumps(r, ensure_ascii=False)[:90])
    time.sleep(8)
    info = page.call("""()=>{const out=[];
      const anchors=document.querySelectorAll('a[href]');
      for(let i=0;i<anchors.length;i++){
        const h=anchors[i].href||'';
        const t=(anchors[i].innerText||'').trim();
        if(t.length>1 && (h.indexOf('chapter-manage')>=0 || h.indexOf('book-info')>=0 || h.indexOf('book')>=0)){
          out.push({h:h.slice(0,100), t:t.slice(0,26)});}}
      const lines=(document.body.innerText||'').split(String.fromCharCode(10));
      const hits=lines.filter(function(l){
        return l.indexOf('嫡女')>=0||l.indexOf('头七')>=0||l.indexOf('边关')>=0||l.indexOf('重生之')>=0;}).slice(0,10);
      return {url:location.href.slice(0,110), links:out.slice(0,14), hits:hits};}""")
    print(json.dumps(info, ensure_ascii=False, indent=1)[:1500])
    io.open('tests/_out/fanqie_workbench.json', 'w', encoding='utf-8').write(
        json.dumps(info, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
