# -*- coding: utf-8 -*-
"""v9：七猫草稿发布三层弹窗闭环（同一 page 内）+ 章节上线验证。"""
import json
import sys
import time

sys.path.insert(0, "app")
from core.publish.browser import Browser  # noqa: E402


def click_visible(pg, text, timeout_s=15):
    """轮询找可见文本按钮并真实点击。"""
    for i in range(int(timeout_s / 0.4)):
        pos = pg.call("""(t) => {
          const vis = e => e.getBoundingClientRect().width > 0;
          const els = [...document.querySelectorAll('a,button,[class*=btn]')]
            .filter(e => vis(e) && (e.innerText||'').trim() === t);
          if (!els.length) return null;
          const r = els[0].getBoundingClientRect();
          return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
        }""", text)
        if pos:
            pg.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1})
            pg.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1})
            return True
        time.sleep(0.4)
    return False


b = Browser.attach(59839)
pg = b.new_page()
pg.navigate("https://zuozhe.qimao.com/front/book-manage/draft?id=13515627", timeout=30)
time.sleep(6)

info = pg.call("""() => {
  const vis = e => e.getBoundingClientRect().width > 0;
  const t = [...document.querySelectorAll('a,span,button,[class*=btn]')]
    .filter(e => vis(e) && (e.innerText||'').trim() === '立即发布'
              && e.closest('tr,[class*=row],[class*=item],[class*=list]'));
  if (!t.length) return null;
  const r = t[0].getBoundingClientRect();
  return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
}""")
pg.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": info["x"], "y": info["y"], "button": "left", "clickCount": 1})
pg.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": info["x"], "y": info["y"], "button": "left", "clickCount": 1})
print("1) 立即发布 clicked")

print("2) 更正序号去发布:", click_visible(pg, u"更正序号去发布", 12))
print("3) 确认发布:", click_visible(pg, u"确认发布", 12))
time.sleep(10)
print("URL:", pg.url()[:90])
pg.screenshot("data/publish/shots/qm-v9-final.png")

pg2 = b.new_page()
pg2.navigate("https://zuozhe.qimao.com/front/book-manage/manage?id=13515627", timeout=30)
time.sleep(7)
body = pg2.evaluate("(function(){ try { return (document.body.innerText||'').replace(/\\s+/g,' ').slice(150,430); } catch(e){ return 'ERR'; } })()")
print("CHAPTERS:", body[:280])
