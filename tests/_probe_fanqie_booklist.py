# -*- coding: utf-8 -*-
"""查证：番茄作者后台作品列表里有没有已建成的 嫡女重生（只读列表）。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'fanqie')
b = Browser(prof)
try:
    page = b.first_page()
    page.navigate('https://fanqienovel.com/main/writer/novel', timeout=30)
    time.sleep(8)
    info = page.call("""()=>{
      const txt=document.body.innerText;
      const has=txt.includes('嫡女重生');
      // 抓作品卡片链接
      const links=[...document.querySelectorAll('a[href*=book-info],a[href*=book_id],a[href*=bookId]')]
        .map(a=>({href:a.href, text:(a.innerText||'').trim().slice(0,30)}))
        .filter(x=>x.text).slice(0,10);
      return {url:location.href, has_dinu:has,
              titles:txt.split('\\n').filter(l=>l.includes('嫡女')||l.includes('重生')).slice(0,5),
              links};
    }""")
    print(json.dumps(info, ensure_ascii=False, indent=1)[:900])
    io.open('tests/_out/fanqie_booklist.json', 'w', encoding='utf-8').write(
        json.dumps(info, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
