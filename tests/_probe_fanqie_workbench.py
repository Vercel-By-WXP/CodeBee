# -*- coding: utf-8 -*-
"""枚举2：从工作台进入作品管理，抓全部书卡（书名+链接）。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'fanqie')
b = Browser(prof)
try:
    page = b.first_page()
    page.navigate('https://fanqienovel.com/main/writer', timeout=30)
    time.sleep(10)
    r = page.real_click_text('作品管理', "span,div,li,a")
    print('click 作品管理:', json.dumps(r, ensure_ascii=False)[:80])
    time.sleep(2)
    r2 = page.real_click_text('小说', "span,div,li,a", y_max=400)
    print('click 小说:', json.dumps(r2, ensure_ascii=False)[:80])
    time.sleep(6)
    info = page.call("""()=>{
      const links=[...document.querySelectorAll('a[href*=chapter-manage],a[href*=book-info]')]
        .map(a=>({href:decodeURIComponent(a.href).slice(0,110),
                  text:(a.innerText||'').trim().slice(0,24)}))
        .filter(x=>x.text);
      const txt=(document.body.innerText||'').split('\\n')
        .filter(l=>l.includes('嫡女')||l.includes('头七')||l.includes('重生')).slice(0,6);
      return {url:location.href.slice(0,100), links:links.slice(0,12), hits:txt};
    }""")
    print(json.dumps(info, ensure_ascii=False, indent=1)[:1400])
    io.open('tests/_out/fanqie_booklist3.json', 'w', encoding='utf-8').write(
        json.dumps(info, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
