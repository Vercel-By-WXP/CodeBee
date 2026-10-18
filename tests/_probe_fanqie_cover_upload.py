# -*- coding: utf-8 -*-
"""封面假设验证：选择封面→setFileInputFiles 上传→再点立即创建→抓请求。"""
import sys, time, json, io, base64
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser
from core.publish import flow
from core.publish import fanqie as fq
from core.publish import manager as mgr

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'fanqie')
meta_task = json.load(io.open(
    r'C:\Users\HP\AppData\Roaming\CodeBee\tasks\t-20261017-115114-8191.json',
    encoding='utf-8'))
data = (meta_task.get('book_meta') or {}).get('fanqie', {}).get('data') or {}
values = fq.values_create_book(dict(data, book_name=data.get('book_name') or '嫡女重生：我的符能渡人也能杀人'))
ov = json.load(io.open(r'C:\Users\HP\AppData\Roaming\CodeBee\publish\flows-fanqie.json',
                       encoding='utf-8'))
steps = ov['create_book']
sub_idx = next(i for i, s in enumerate(steps) if s.get('do') == 'submit')
steps = steps[:sub_idx]
steps = mgr._with_tag_steps(steps, fq.tag_groups(data), values, fq)

# 造一张 1x1 橙色 PNG 当测试封面
png = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==')
cover_path = Path('tests/_out/test_cover.png').resolve()
cover_path.write_bytes(png)

logs = []
b = Browser(prof)
try:
    page = b.first_page()
    page.navigate('https://fanqienovel.com/main/writer/create', timeout=30)
    time.sleep(6)
    page.call("""()=>{window.__resp=[];
      const of=window.fetch;
      window.fetch=async(...a)=>{const r=await of(...a);
        try{const t=await r.clone().text();
          window.__resp.push({u:String(a[0]).slice(0,140),s:r.status,b:t.slice(0,300)});}catch(e){}
        return r;};
      const oo=XMLHttpRequest.prototype.open,os=XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.open=function(m,u){this.__u=u;return oo.apply(this,arguments);};
      XMLHttpRequest.prototype.send=function(){this.addEventListener('load',()=>{
        try{window.__resp.push({u:String(this.__u||'').slice(0,140),s:this.status,
          b:String(this.responseText).slice(0,300)});}catch(e){}});return os.apply(this,arguments);};}""")
    flow.run_flow(page, steps, values=values, config=fq.CONFIG,
                  auto_submit=True, dup_check=False,
                  shot=lambda n: None, log=lambda s: print('LOG', s))
    time.sleep(1)
    # 点 选择封面，找 file input
    r = page.real_click_text('选择封面', "button,a,[class*=btn],div,span")
    print('点击选择封面:', json.dumps(r, ensure_ascii=False)[:90])
    time.sleep(2)
    fi = page.call("""()=>{
      const inp=document.querySelector('input[type=file]');
      if(!inp)return{nf:true};
      return {found:true, accept:String(inp.accept||''), cls:String(inp.className).slice(0,40)};}""")
    print('file input:', json.dumps(fi, ensure_ascii=False))
    if fi.get('found'):
        doc = page.send('DOM.getDocument', {}, timeout=8.0)
        node = page.send('DOM.querySelector',
                         {'nodeId': doc['root']['nodeId'], 'selector': 'input[type=file]'},
                         timeout=8.0)
        page.send('DOM.setFileInputFiles',
                  {'files': [str(cover_path)], 'nodeId': node['nodeId']}, timeout=8.0)
        print('已塞入测试封面')
        time.sleep(3)
        # 封面可能需要确认按钮
        page.real_click_text('确认', "button,[class*=btn]")
        time.sleep(2)
    # 点 立即创建
    r2 = page.real_click_text('立即创建', "button")
    print('点击立即创建:', json.dumps(r2, ensure_ascii=False)[:90])
    time.sleep(8)
    resp = page.call("()=>window.__resp||[]")
    print('== 网络捕获 ==')
    for r0 in (resp or []):
        print(json.dumps(r0, ensure_ascii=False)[:380])
    print('最终URL:', page.call("()=>location.href").slice(0, 110) if False else
          page.call("()=>location.href.slice(0,110)"))
    io.open('tests/_out/fanqie_cover_test.json', 'w', encoding='utf-8').write(
        json.dumps(resp, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
