# -*- coding: utf-8 -*-
"""诊断：番茄创建页改版后 女频/男频 控件的真实 DOM 形态（只读，不填表不提交）。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'fanqie')
b = Browser(prof)
try:
    page = b.first_page()
    page.navigate('https://fanqienovel.com/main/writer/create', timeout=30)
    time.sleep(8)
    js = """()=>{
      const vis=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);
        return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
      const out={url:location.href,title:document.title,items:[]};
      for(const e of document.querySelectorAll('input[type=radio],[role=radio],[class*=radio],[class*=segment],[class*=switch],[class*=checker]')){
        if(!vis(e))continue;
        const r=e.getBoundingClientRect();
        const label=e.closest('label')||e.parentElement;
        out.items.push({tag:e.tagName,cls:String(e.className).slice(0,90),
          text:((label&&label.innerText)||e.value||'').trim().slice(0,40).replace(/\\n/g,'/'),
          x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height),
          checked:e.checked!==undefined?e.checked:String(e.className).includes('checked')});
      }
      const seen=new Set();
      out.items=out.items.filter(i=>{const k=i.text+'|'+i.x+'|'+i.y;
        if(seen.has(k))return false;seen.add(k);return true;});
      out.body_has_nv=document.body.innerText.includes('女频');
      out.body_has_lz=document.body.innerText.includes('连载');
      return out;
    }"""
    info = page.call(js)
    io.open('tests/_out/fanqie_create_dom.json', 'w', encoding='utf-8').write(
        json.dumps(info, ensure_ascii=False, indent=1))
    print('url:', info.get('url'))
    print('女频 on page:', info.get('body_has_nv'), '| 连载 on page:', info.get('body_has_lz'))
    print('items:', len(info.get('items') or []))
    for it in (info.get('items') or [])[:50]:
        print(json.dumps(it, ensure_ascii=False))
finally:
    try:
        b.close()
    except Exception:
        pass
