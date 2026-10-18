# -*- coding: utf-8 -*-
"""诊断：七猫建书入口改版排查（只导航到创建表单，不填不提交）。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'qimao')
b = Browser(prof)
out = {'stages': []}

def dump(page, stage):
    info = page.call("""()=>{
      const vis=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);
        return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'
        &&s.pointerEvents!=='none';};
      const btns=[...document.querySelectorAll('button,a,[role=button],[class*=btn]')]
        .filter(vis).map(e=>(e.innerText||'').trim().slice(0,20)).filter(x=>x);
      const uniq=[...new Set(btns)].slice(0,50);
      const tas=[...document.querySelectorAll('textarea,input')].filter(vis)
        .map(e=>({tag:e.tagName, ph:e.placeholder||'', name:e.name||''}));
      const dialogs=[...document.querySelectorAll('[class*=modal],[class*=dialog],[class*=drawer]')]
        .filter(vis).map(e=>({cls:String(e.className).slice(0,60),
          text:(e.innerText||'').trim().slice(0,80).replace(/\\n/g,'/')}));
      return {url:location.href, title:document.title, btns:uniq, inputs:tas, dialogs};
    }""")
    out['stages'].append({'stage': stage, **info})
    print('==', stage, '|', info.get('url'))
    print('  btns:', '、'.join(info.get('btns', [])[:20]))
    print('  inputs:', json.dumps(info.get('inputs', []), ensure_ascii=False)[:300])
    print('  dialogs:', json.dumps(info.get('dialogs', []), ensure_ascii=False)[:200])

try:
    page = b.first_page()
    page.navigate('https://zuozhe.qimao.com/front/book-manage', timeout=30)
    time.sleep(6)
    dump(page, 'book-manage')
    r = page.real_click_text('新建小说', "button,a,[role=button],[class*=btn]")
    print('click 新建小说:', json.dumps(r, ensure_ascii=False)[:100])
    if not (r or {}).get('ok'):
        r = page.real_click_text('创建作品', "button,a,[role=button],[class*=btn]")
        print('click 创建作品:', json.dumps(r, ensure_ascii=False)[:100])
    time.sleep(3)
    dump(page, 'after-新建')
    for t in ('七猫中文网', '网站特色'):
        r = page.real_click_text(t, "button,a,[role=button],[class*=btn],li,span,div", y_max=600)
        if (r or {}).get('ok'):
            print('click', t, '-> ok')
            break
    time.sleep(1.5)
    dump(page, 'after-站点选择')
    r = page.real_click_text('确认', "button,[class*=btn]")
    print('click 确认:', json.dumps(r, ensure_ascii=False)[:100])
    time.sleep(4)
    dump(page, 'after-确认')
    io.open('tests/_out/qimao_create_dom.json', 'w', encoding='utf-8').write(
        json.dumps(out, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
