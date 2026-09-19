# -*- coding: utf-8 -*-
"""七猫发章真机验证：足字数正文 + 真实点击「立即发布」+ 轮询跳转。"""
import json
import sys
import time

sys.path.insert(0, "app")
from core.publish.browser import Browser, Page  # noqa: E402

b = Browser.attach(59839)
pages = b.pages()
tgt = [t for t in pages if "book-upload" in (t.get("url") or "")][-1]
pg = Page(tgt["webSocketDebuggerUrl"])

para = (u"山脚的集市刚开，卖菜的老周头远远就招呼他。竹篓里的药材成色好，不到晌午就出了大半。"
        u"剩下的几株七叶胆，他留着给药铺的陈掌柜看，若价钱合适，下学期的学费就有着落了。"
        u"陈掌柜捏起一株对着光瞧了半晌，眉头忽紧忽松，最后从柜台底下摸出个布包，一枚一枚数出十七块钱。"
        u"林远把钱贴身收好，又买了二两盐、一块皂角，剩下的钱在兜里焐得发热。"
        u"回山的路要走两个时辰，他盘算着绕去后崖看看昨天布下的套子，若套住了野鸡，明早又能添一笔进项。")
body = para * 5

pg.fill("textarea[placeholder*='章节名称']", u"第二章 集市")
pg.fill(".q-contenteditable:not(.book)", body)
time.sleep(1)
n = pg.evaluate("(function(){ try { return (document.querySelector('.q-contenteditable:not(.book)')||{innerText:''}).innerText.replace(/\\s/g,'').length; } catch(e){ return -1; } })()")
print("正文字数:", n)

r = pg.real_click_text(u"立即发布", scope="a,[class*=btn]")
print("click:", r)
for i in range(25):
    time.sleep(1)
    u = pg.url()
    if "book-upload" not in u:
        print("t+%d jump: %s" % (i + 1, u[:80]))
        break
print("FINAL:", pg.url()[:95])
