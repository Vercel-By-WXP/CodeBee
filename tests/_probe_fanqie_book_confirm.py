# -*- coding: utf-8 -*-
"""验证：直接打开新建书的章节管理页，确认书名与状态（只读）。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'fanqie')
b = Browser(prof)
try:
    page = b.first_page()
    page.navigate('https://fanqienovel.com/main/writer/chapter-manage/7696359004347305022',
                  timeout=30)
    time.sleep(8)
    info = page.call("""()=>{
      const txt=(document.body.innerText||'');
      return {url:decodeURIComponent(location.href).slice(0,140),
              has_dinu:txt.includes('嫡女重生'),
              has_touqi:txt.includes('头七'),
              dinu_at:txt.indexOf('嫡女重生'),
              touqi_at:txt.indexOf('头七'),
              head:txt.split('\\n').filter(l=>l.trim()).slice(25,70)};
    }""")
    print(json.dumps(info, ensure_ascii=False, indent=1)[:1200])
    io.open('tests/_out/fanqie_book_confirm.json', 'w', encoding='utf-8').write(
        json.dumps(info, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
