# -*- coding: utf-8 -*-
"""番茄编辑分卷 UI 探测：卷列表/重命名/移动章节能力（只看不改）。"""
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
    time.sleep(9)
    # 找并点 编辑分卷/设置
    r = page.real_click_text('编辑分卷', "button,a,span,div,[class*=btn]")
    print('点击编辑分卷:', json.dumps(r, ensure_ascii=False)[:80])
    time.sleep(3)
    st = page.call("""()=>{const out={dialogs:[]};
      for(const d of document.querySelectorAll('[role=dialog],.arco-modal,[class*=modal]')){
        const r=d.getBoundingClientRect();
        if(r.width<=0||r.height<=0)continue;
        const t=(d.innerText||'').trim().slice(0,200);
        if(t)out.dialogs.push({cls:String(d.className).slice(0,50),t:t.split(String.fromCharCode(10)).join('/')});}
      const inputs=[...document.querySelectorAll('input[type=text],input:not([type])')]
        .filter(e=>e.getBoundingClientRect().width>0)
        .map(e=>({v:String(e.value||'').slice(0,24),ph:e.placeholder||''}));
      out.inputs=inputs.slice(0,8);
      const btns=[...document.querySelectorAll('button')]
        .filter(e=>e.getBoundingClientRect().width>0)
        .map(e=>(e.innerText||'').trim()).filter(t=>t&&t.length<=8);
      out.btns=[...new Set(btns)].slice(0,12);
      return out;}""")
    print(json.dumps(st, ensure_ascii=False, indent=1)[:1200])
    io.open('tests/_out/fanqie_editvol.json', 'w', encoding='utf-8').write(
        json.dumps(st, ensure_ascii=False, indent=1))
    shot = Path('tests/_out/shots'); shot.mkdir(parents=True, exist_ok=True)
    page.screenshot(str(shot / 'fanqie-editvol.png'))
finally:
    try:
        b.close()
    except Exception:
        pass
