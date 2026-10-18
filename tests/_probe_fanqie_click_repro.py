# -*- coding: utf-8 -*-
"""复现：按服务同款代码路径点 连载模式→女频，逐步记录选中态（只读页面状态，不提交）。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser
from core.publish.flow import _checked_js

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'fanqie')
b = Browser(prof)
try:
    page = b.first_page()
    page.navigate('https://fanqienovel.com/main/writer/create', timeout=30)
    time.sleep(8)
    scope = "label.arco-radio,span.arco-radio,[class*=serial-radio]"
    log = []

    def attempt(text):
        r = page.real_click_text(text, scope)
        log.append({'step': text, 'click': r})
        time.sleep(0.6)
        v = page.call(_checked_js(), text, scope)
        log.append({'step': text, 'verify': v})
        return r, v

    for t in ('连载模式', '女频', '女频', '女频'):
        r, v = attempt(t)
        print(t, '-> click:', json.dumps(r, ensure_ascii=False)[:100],
              '| verify:', json.dumps(v, ensure_ascii=False)[:140])
        time.sleep(1.0)

    # 附加：整页 radio 状态快照
    snap = page.call("""()=>{
      return [...document.querySelectorAll('label.arco-radio')].map(e=>({
        text:(e.innerText||'').trim(), cls:String(e.className),
        inputChecked:(e.querySelector('input')||{}).checked}));
    }""")
    print('radios:', json.dumps(snap, ensure_ascii=False)[:500])
    io.open('tests/_out/fanqie_click_repro.json', 'w', encoding='utf-8').write(
        json.dumps({'log': log, 'radios': snap}, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
