# -*- coding: utf-8 -*-
"""枚举3：经作者壳侧栏（create 页同源）进 作品管理→小说，抓全部书卡。"""
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
    r = page.real_click_text('作品管理', "span,div,li,a")
    print('click 作品管理:', json.dumps(r, ensure_ascii=False)[:80])
    time.sleep(3)
    r2 = page.real_click_text('小说', "span,div,li,a", y_max=450)
    print('click 小说:', json.dumps(r2, ensure_ascii=False)[:80])
    time.sleep(8)
    info = page.call("""()=>{
      const links=[...document.querySelectorAll('a[href*=chapter-manage],a[href*=book-info],a[href*=book]')]
        .map(a=>({href:decodeURIComponent(a.href).slice(0,110),
                  text:(a.innerText||'').trim().slice(0,24)}))
        .filter(x=>x.text&&x.text.length>1);
      const txt=(document.body.innerText||'').split('\\n')
        .filter(l=>l.includes('嫡女')||l.includes('头七')||l.includes('重生')).slice(0,8);
      return {url:location.href.slice(0,110), links:links.slice(0,14), hits:txt};
    }""")
    print(json.dumps(info, ensure_ascii=False, indent=1)[:1600])
    io.open('tests/_out/fanqie_booklist4.json', 'w', encoding='utf-8').write(
        json.dumps(info, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
