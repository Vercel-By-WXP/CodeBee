# -*- coding: utf-8 -*-
"""七猫编辑器分卷 UI 探测：神瞳书（有章有卷）的章节编辑页找 分卷 控件（只读）。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'qimao')
b = Browser(prof)
try:
    page = b.first_page()
    # 神瞳鉴宝 chapter-manage（书号从书籍管理列表来——用管理页直达）
    page.navigate('https://zuozhe.qimao.com/front/book-manage', timeout=30)
    time.sleep(8)
    # 点 神瞳鉴宝 的 上传章节（第一本书卡）
    r = page.real_click_text('上传章节', "button,a,[role=button],[class*=btn]", y_max=300)
    print('点击上传章节:', json.dumps(r, ensure_ascii=False)[:80])
    time.sleep(6)
    st = page.call("""()=>{const out={url:location.href.slice(0,100),vol:[]};
      const lines=(document.body.innerText||'').split(String.fromCharCode(10));
      out.volLines=lines.filter(function(l){
        return l.indexOf('卷')>=0&&l.indexOf('分卷')<0?false:(l.indexOf('卷')>=0);}).slice(0,12);
      const sels=[...document.querySelectorAll('select,[class*=volume],[class*=juan],[class*=chapter-vol]')]
        .filter(e=>e.getBoundingClientRect().width>0)
        .map(e=>({cls:String(e.className).slice(0,50),
                  t:(e.innerText||'').trim().slice(0,50)}));
      out.volEls=sels.slice(0,8);
      return out;}""")
    print(json.dumps(st, ensure_ascii=False, indent=1)[:1200])
    io.open('tests/_out/qimao_editor_vol.json', 'w', encoding='utf-8').write(
        json.dumps(st, ensure_ascii=False, indent=1))
    shot = Path('tests/_out/shots'); shot.mkdir(parents=True, exist_ok=True)
    page.screenshot(str(shot / 'qimao-editor.png'))
finally:
    try:
        b.close()
    except Exception:
        pass
