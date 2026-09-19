# -*- coding: utf-8 -*-
"""清理七猫草稿箱：删除旧草稿只留最新一条，然后走 v9 发布链。"""
import json
import sys
import time

sys.path.insert(0, "app")
from core.publish.browser import Browser  # noqa: E402


def click_visible(pg, text, timeout_s=10, scope="a,button,[class*=btn]"):
    for i in range(int(timeout_s / 0.4)):
        pos = pg.call("""(t) => {
          const vis = e => e.getBoundingClientRect().width > 0;
          const els = [...document.querySelectorAll('%s')]
            .filter(e => vis(e) && (e.innerText||'').trim() === t);
          if (!els.length) return null;
          const r = els[0].getBoundingClientRect();
          return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
        }""" % scope, text)
        if pos:
            pg.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1})
            pg.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1})
            return True
        time.sleep(0.4)
    return False


b = Browser.attach(59839)
pg = b.first_page()
pg.navigate("about:blank")
pg.navigate("https://zuozhe.qimao.com/front/book-manage/draft?id=13515627", timeout=30)
time.sleep(6)

# 循环删除倒序第二条（保留第一条=最新），直到只剩一条
for rnd in range(15):
    n = pg.call("""() => {
      const vis = e => e.getBoundingClientRect().width > 0;
      return [...document.querySelectorAll('tr.el-table__row,[class*=row]')].filter(e =>
        vis(e) && /第\d+章/.test(e.innerText||'')).length;
    }""")
    print("round %d rows=%s" % (rnd, n))
    if not n or int(n) <= 1:
        break
    ok = pg.call("""() => {
      const vis = e => e.getBoundingClientRect().width > 0;
      const rows = [...document.querySelectorAll('tr.el-table__row,[class*=row]')].filter(e =>
        vis(e) && /第\d+章/.test(e.innerText||''));
      if (rows.length < 2) return false;
      const del = [...rows[1].querySelectorAll('a,span,[class*=btn]')].find(e =>
        (e.innerText||'').trim() === '删除');
      if (!del) return false;
      del.scrollIntoView({block:'center'});
      const act = del.closest('a,button,[class*=btn]') || del;
      act.click();
      return true;
    }""")
    if ok:
        time.sleep(1)
        click_visible(pg, u"确认", 6) or click_visible(pg, u"确定", 4)
        time.sleep(1.5)
print("cleanup done")

# v9 链：发布最新草稿
pos = pg.call("""() => {
  const vis = e => e.getBoundingClientRect().width > 0;
  const t = [...document.querySelectorAll('a,span,button,[class*=btn]')]
    .filter(e => vis(e) && (e.innerText||'').trim() === '立即发布'
              && e.closest('tr,[class*=row],[class*=item],[class*=list]'));
  if (!t.length) return null;
  const r = t[0].getBoundingClientRect();
  return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
}""")
if pos:
    pg.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1})
    pg.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1})
    print("1) 立即发布 clicked")
    print("2) 更正序号去发布:", click_visible(pg, u"更正序号去发布", 15))
    print("3) 确认发布:", click_visible(pg, u"确认发布", 15))
time.sleep(10)
pg2 = b.new_page()
pg2.navigate("about:blank")
pg2.navigate("https://zuozhe.qimao.com/front/book-manage/manage?id=13515627", timeout=30)
time.sleep(7)
body = pg2.evaluate('(document.body.innerText||"").replace(/\\s+/g," ")')
i = body.find("顺序")
print("CHAPTERS:", body[i:i+360])
