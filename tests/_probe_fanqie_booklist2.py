# -*- coding: utf-8 -*-
"""枚举：番茄作者后台全部作品（等待完整渲染，抓书名+ID）。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'fanqie')
b = Browser(prof)
try:
    page = b.first_page()
    page.navigate('https://fanqienovel.com/main/writer/novel', timeout=30)
    time.sleep(15)
    info = page.call("""()=>{
      const txt=(document.body.innerText||'');
      // 书卡：含 管理章节/作品数据 等操作词的卡片
      const cards=[...document.querySelectorAll('div,li')]
        .filter(e=>{const t=(e.innerText||'');return t.includes('管理章节')||t.includes('作品数据');})
        .map(e=>(e.innerText||'').split('\\n').slice(0,4).join('/').slice(0,80));
      return {url:location.href.slice(0,100), n_cards:cards.length,
              cards:[...new Set(cards)].slice(0,10),
              sample:txt.split('\\n').filter(l=>l.trim()).slice(0,40)};
    }""")
    print(json.dumps(info, ensure_ascii=False, indent=1)[:2000])
    io.open('tests/_out/fanqie_booklist2.json', 'w', encoding='utf-8').write(
        json.dumps(info, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
