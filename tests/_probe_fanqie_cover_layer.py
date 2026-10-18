# -*- coding: utf-8 -*-
"""封面专题：点 选择封面 后弹层/页面形态全景（只开不选不提交）。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'fanqie')
b = Browser(prof)
out = {}
try:
    page = b.first_page()
    page.navigate('https://fanqienovel.com/main/writer/create', timeout=30)
    time.sleep(8)
    r = page.real_click_text('选择封面', "button,a,[class*=btn],div,span")
    print('点击选择封面:', json.dumps(r, ensure_ascii=False)[:90])
    time.sleep(3)
    # 全景：弹层/抽屉/文件输入/上传按钮
    st = page.call("""()=>{
      const vis=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);
        return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
      const out={inputs:[],dialogs:[],uploads:[]};
      for(const e of document.querySelectorAll('input')){
        const t=String(e.type||'');
        if(t==='file'||t==='text'||t==='hidden'){
          if(vis(e)||t==='file'){
            out.inputs.push({type:t,accept:String(e.accept||'').slice(0,40),
              ph:e.placeholder||'',name:e.name||''});}}}
      for(const e of document.querySelectorAll('[class*=modal],[class*=dialog],[class*=drawer],[class*=popover],[class*=popup],[class*=upload]')){
        if(!vis(e))continue;
        const t=(e.innerText||'').trim().slice(0,60);
        if(t)out.dialogs.push({cls:String(e.className).slice(0,60),t:t.replace(/\\n/g,'/')});}
      const lines=(document.body.innerText||'').split(String.fromCharCode(10))
        .filter(l=>l.includes('封面')||l.includes('上传')||l.includes('图片'));
      out.hints=[...new Set(lines)].slice(0,10);
      return out;}""")
    print(json.dumps(st, ensure_ascii=False, indent=1)[:1500])
    out['after_click'] = st
    shot = Path('tests/_out/shots')
    shot.mkdir(parents=True, exist_ok=True)
    page.screenshot(str(shot / 'cover-layer.png'))
finally:
    try:
        b.close()
    except Exception:
        pass
io.open('tests/_out/fanqie_cover_layer.json', 'w', encoding='utf-8').write(
    json.dumps(out, ensure_ascii=False, indent=1))
