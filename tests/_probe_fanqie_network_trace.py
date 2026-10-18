# -*- coding: utf-8 -*-
"""终版诊断：装网络监听器 + 服务同款流程跑建书，抓创建接口的真实响应。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser
from core.publish import flow
from core.publish import fanqie as fq

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'fanqie')
meta_task = json.load(io.open(
    r'C:\Users\HP\AppData\Roaming\CodeBee\tasks\t-20261017-115114-8191.json',
    encoding='utf-8'))
data = (meta_task.get('book_meta') or {}).get('fanqie', {}).get('data') or {}
values = fq.values_create_book(dict(data, book_name=data.get('book_name') or '嫡女重生：我的符能渡人也能杀人'))
ov = json.load(io.open(r'C:\Users\HP\AppData\Roaming\CodeBee\publish\flows-fanqie.json',
                       encoding='utf-8'))
steps = ov['create_book']
# 与 manager 同款：按 tag_groups 追加逐标签点击步
from core.publish import manager as mgr
steps = mgr._with_tag_steps(steps, fq.tag_groups(data), values, fq)

logs = []
b = Browser(prof)
try:
    page = b.first_page()
    page.navigate('https://fanqienovel.com/main/writer/create', timeout=30)
    time.sleep(6)
    # 装网络监听（fetch + XHR）
    page.call("""()=>{window.__resp=[];
      if(!window.__rec){window.__rec=1;
        const of=window.fetch;
        window.fetch=async(...a)=>{const r=await of(...a);
          try{const t=await r.clone().text();
            window.__resp.push({url:String(a[0]).slice(0,140),status:r.status,body:t.slice(0,400)});}catch(e){}
          return r;};
        const oo=XMLHttpRequest.prototype.open, os=XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.open=function(m,u){this.__u=u;return oo.apply(this,arguments);};
        XMLHttpRequest.prototype.send=function(){this.addEventListener('load',()=>{
          try{window.__resp.push({url:String(this.__u||'').slice(0,140),status:this.status,
            body:String(this.responseText).slice(0,400)});}catch(e){}});return os.apply(this,arguments);};}
      return true;}""")
    shot_dir = Path('tests/_out/shots')
    shot_dir.mkdir(parents=True, exist_ok=True)
    n = -1
    try:
        n = flow.run_flow(page, steps, values=values, config=fq.CONFIG,
                          auto_submit=True, dup_check=True,
                          shot=lambda name: page.screenshot(str(shot_dir / (name + '.png'))),
                          log=lambda s: (logs.append(s), print('LOG', s)))
    except Exception as e:
        print('流程异常:', str(e)[:120])
    finally:
        time.sleep(3)
        try:
            resp = page.call("()=>window.__resp||[]")
            url = page.call("()=>location.href")
        except Exception:
            resp, url = [], 'page gone'
        print('== 网络捕获 ==')
        for r0 in (resp or []):
            print(json.dumps(r0, ensure_ascii=False)[:420])
        io.open('tests/_out/fanqie_net_trace.json', 'w', encoding='utf-8').write(
            json.dumps({'logs': logs, 'resp': resp, 'url': url}, ensure_ascii=False, indent=1))
        print('流程执行到步:', n, '| 最终URL:', url)
finally:
    try:
        b.close()
    except Exception:
        pass
