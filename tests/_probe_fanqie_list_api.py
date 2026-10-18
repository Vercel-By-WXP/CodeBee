# -*- coding: utf-8 -*-
"""枚举5：装 fetch/XHR 监听 → 真实点击 作品管理→小说 → 抓书单接口响应体。"""
import sys, time, json, io
from pathlib import Path
sys.path.insert(0, 'app')
from core.publish.browser import Browser

prof = str(Path.home() / '.codebee' / 'publish_profiles' / 'fanqie')
b = Browser(prof)
REC = ("()=>{window.__resp=[];"
       "if(!window.__rec){window.__rec=1;"
       "const of=window.fetch;"
       "window.fetch=async(...a)=>{const r=await of(...a);"
       "try{const t=await r.clone().text();"
       "window.__resp.push({u:String(a[0]).slice(0,140),s:r.status,b:t.slice(0,600)});}catch(e){}"
       "return r;};"
       "const oo=XMLHttpRequest.prototype.open,os=XMLHttpRequest.prototype.send;"
       "XMLHttpRequest.prototype.open=function(m,u){this.__u=u;return oo.apply(this,arguments);};"
       "XMLHttpRequest.prototype.send=function(){this.addEventListener('load',()=>{"
       "try{window.__resp.push({u:String(this.__u||'').slice(0,140),s:this.status,"
       "b:String(this.responseText).slice(0,600)});}catch(e){}});return os.apply(this,arguments);};}"
       "return true;}")
CLICK = ("(s)=>{const els=[...document.querySelectorAll('span,div,li,a')]"
         ".filter(e=>e.getBoundingClientRect().width>0);"
         "const a=els.find(e=>(e.innerText||'').trim()===s);"
         "if(!a)return{err:'nf '+s}; a.click(); return{ok:true};}")
JS_LIST = ("()=>{const out=[];"
           "const anchors=document.querySelectorAll('a[href]');"
           "for(let i=0;i<anchors.length;i++){"
           "const h=anchors[i].href||'';"
           "const t=(anchors[i].innerText||'').trim();"
           "if(t.length>0 && (h.indexOf('chapter-manage')>=0 || h.indexOf('book-info')>=0)){"
           "out.push({h:h.slice(0,100), t:t.slice(0,24)});}}"
           "return {url:location.href.slice(0,110), links:out.slice(0,14)};}")
try:
    page = b.first_page()
    page.navigate('https://fanqienovel.com/main/writer/create', timeout=30)
    time.sleep(8)
    page.call(REC)
    page.call(CLICK, '作品管理')
    time.sleep(2.5)
    page.call(CLICK, '小说')
    time.sleep(6)
    resp = page.call("()=>window.__resp||[]")
    interesting = [r0 for r0 in resp
                   if any(k in r0.get('b', '') for k in ('嫡女', '头七', 'book_id', 'book_name', 'novel'))]
    print('捕获响应数:', len(resp), '相关:', len(interesting))
    for r0 in interesting[:8]:
        print(json.dumps(r0, ensure_ascii=False)[:500])
    print('页面:', json.dumps(page.call(JS_LIST), ensure_ascii=False)[:400])
    io.open('tests/_out/fanqie_list_api.json', 'w', encoding='utf-8').write(
        json.dumps(resp, ensure_ascii=False, indent=1))
finally:
    try:
        b.close()
    except Exception:
        pass
