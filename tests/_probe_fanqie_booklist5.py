# -*- coding: utf-8 -*-
"""枚举4：SPA 合成点击进 作品管理→小说，抓全部书卡链接与名称。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'fanqie')
b = Browser(prof)
JS_CLICK = ("(s)=>{const els=[...document.querySelectorAll('span,div,li,a')]"
            ".filter(e=>e.getBoundingClientRect().width>0);"
            "const a=els.find(e=>(e.innerText||'').trim()===s);"
            "if(!a)return{err:'nf '+s}; a.click(); return{ok:true};}")
JS_LIST = ("()=>{const out=[];"
           "const anchors=document.querySelectorAll('a[href]');"
           "for(let i=0;i<anchors.length;i++){"
           "const h=anchors[i].href||'';"
           "const t=(anchors[i].innerText||'').trim();"
           "if(t.length>0 && (h.indexOf('chapter-manage')>=0 || h.indexOf('book-info')>=0)){"
           "out.push({h:h.slice(0,100), t:t.slice(0,24)});}}"
           "const lines=(document.body.innerText||'').split(String.fromCharCode(10));"
           "const hits=lines.filter(function(l){"
           "return l.indexOf('嫡女')>=0||l.indexOf('头七')>=0||l.indexOf('重生之')>=0;}).slice(0,8);"
           "return {url:location.href.slice(0,110), links:out.slice(0,14), hits:hits};}")
try:
    page = b.first_page()
    page.navigate('https://fanqienovel.com/main/writer/create', timeout=30)
    time.sleep(8)
    print('click 作品管理:', json.dumps(page.call(JS_CLICK, '作品管理'), ensure_ascii=False))
    time.sleep(2.5)
    print('click 小说:', json.dumps(page.call(JS_CLICK, '小说'), ensure_ascii=False))
    time.sleep(8)
    info = page.call(JS_LIST)
    print(json.dumps(info, ensure_ascii=False, indent=1)[:1600])
    io.open('tests/_out/fanqie_booklist5.json', 'w', encoding='utf-8').write(
        json.dumps(info, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
