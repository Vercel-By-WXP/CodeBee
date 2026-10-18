# -*- coding: utf-8 -*-
"""诊断2：七猫站点弹层完整结构 + 不点站点直接确认是否可达表单（不填不提交）。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'qimao')
b = Browser(prof)
out = {}
try:
    page = b.first_page()
    page.navigate('https://zuozhe.qimao.com/front/book-manage', timeout=30)
    time.sleep(6)
    r = page.real_click_text('新建小说', "button,a,[role=button],[class*=btn]")
    print('click 新建小说:', json.dumps(r, ensure_ascii=False)[:80])
    time.sleep(3)
    full = page.call("""()=>{
      const dlg=document.querySelector('.client-select .el-dialog__body')||
                 document.querySelector('.el-dialog__body');
      if(!dlg)return{none:true};
      const opts=[...dlg.querySelectorAll('label,li,span,div.radio,[class*=radio],[class*=check]')]
        .map(e=>({tag:e.tagName,cls:String(e.className).slice(0,50),
          text:(e.innerText||'').trim().slice(0,30).replace(/\\n/g,'/'),
          sel:String(e.className).includes('checked')||String(e.className).includes('active')
              ||String(e.className).includes('selected')||(e.querySelector('input')||{}).checked}))
        .filter(x=>x.text);
      return {full:(dlg.innerText||'').slice(0,600), opts:opts.slice(0,40)};
    }""")
    out['dialog'] = full
    print('弹层全文:', json.dumps(full.get('full', ''), ensure_ascii=False)[:500])
    for o in (full.get('opts') or [])[:20]:
        print('  opt:', json.dumps(o, ensure_ascii=False))
    r2 = page.real_click_text('确认', "button,[class*=btn],a")
    print('click 确认:', json.dumps(r2, ensure_ascii=False)[:80])
    time.sleep(4)
    info = page.call("""()=>({
      url:location.href,
      ta:!!document.querySelector("textarea[placeholder*='作品名称']"),
      err:([...document.querySelectorAll('[class*=error],[class*=message],.el-form-item__error')]
        .map(e=>(e.innerText||'').trim()).filter(x=>x).slice(0,3)).join('|')})""")
    print('结果:', json.dumps(info, ensure_ascii=False))
    out['result'] = info
    io.open('tests/_out/qimao_dialog_full.json', 'w', encoding='utf-8').write(
        json.dumps(out, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
