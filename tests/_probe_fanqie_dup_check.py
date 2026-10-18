# -*- coding: utf-8 -*-
"""番茄重名探测：create 页填书名，读平台实时查重提示（判断书是否已存在）。"""
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
    # 用服务同款 fill 管道（React 受控安全）
    from core.publish import flow
    flow.run_flow(page, [{"do": "fill", "sel": "input[placeholder='请输入作品名称']",
                          "key": "title"}],
                  values={"title": "嫡女重生：我的符能渡人也能杀人"},
                  auto_submit=False, shot=lambda n: None, log=lambda s: None)
    time.sleep(3)
    st = page.call("""()=>{const hints=[];
      for(const e of document.querySelectorAll('[class*=error],[class*=tip],[class*=warn],[class*=message]')){
        const r=e.getBoundingClientRect();
        if(r.width<=0||r.height<=0)continue;
        const t=(e.innerText||'').trim();
        if(t&&t.length<60)hints.push(t);}
      const inp=document.querySelector('input[placeholder=\"请输入作品名称\"]');
      return {hints:[...new Set(hints)].slice(0,5),
              value:(inp?inp.value:'').slice(0,20)};}""")
    print(json.dumps(st, ensure_ascii=False, indent=1))
    io.open('tests/_out/fanqie_dup_check.json', 'w', encoding='utf-8').write(
        json.dumps(st, ensure_ascii=False))
finally:
    try:
        b.close()
    except Exception:
        pass
