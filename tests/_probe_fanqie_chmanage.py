# -*- coding: utf-8 -*-
"""盘点：番茄《嫡女重生》章节管理页实况——卷结构/各章状态/定时日期。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'fanqie')
b = Browser(prof)
try:
    page = b.first_page()
    page.navigate('https://fanqienovel.com/main/writer/chapter-manage/7697858830406798398',
                  timeout=30)
    time.sleep(10)
    st = page.call("""()=>{
      const txt=(document.body.innerText||'');
      const lines=txt.split(String.fromCharCode(10)).filter(l=>l.trim());
      const vols=lines.filter(l=>l.indexOf('卷')>=0).slice(0,8);
      const chs=lines.filter(l=>/第\\s*[0-9]+\\s*章/.test(l)).slice(0,15);
      const sched=lines.filter(l=>l.indexOf('2026-')>=0).slice(0,12);
      return {url:decodeURIComponent(location.href).slice(0,110),
              vols:vols, chs:chs, sched:sched};}""")
    print(json.dumps(st, ensure_ascii=False, indent=1)[:1500])
    io.open('tests/_out/fanqie_chmanage.json', 'w', encoding='utf-8').write(
        json.dumps(st, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
