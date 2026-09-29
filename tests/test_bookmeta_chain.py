# -*- coding: utf-8 -*-
"""连载链沿链继承单测（2026-09-29「都展示，都是用第一个」改版）。

口径：一本书一条链——开书资料（book_meta）、发布绑定（books.json）、已发
章号（publish-*.jsonl）整链共享，写入落到根任务，续写批次只读沿用：
  store.serial_chain_ids     祖先+后代收链，根在前，环安全
  store.inherited_book_meta  首个「生成完成」条目整链可见
  ledger.book_for/save_book/update_book  绑定沿链读、写落根
  ledger.published_chapters/recent      章号与历史沿链合并（防续写重发）

跑法：python tests/test_bookmeta_chain.py
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="bmchain-test-")).resolve()
os.environ["TUTTI_DATA"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from core import store  # noqa: E402
from core.publish import ledger  # noqa: E402

FAILS = []


def check(name, fn):
    try:
        fn()
        print("  ok   %s" % name)
    except Exception as e:
        FAILS.append(name)
        print("  FAIL %s: %r" % (name, e))


def expect(cond, msg=""):
    if not cond:
        raise AssertionError(msg or "expect failed")


def _mk_serial(title, continues=None, start=1, book_meta=None):
    task = store.create_task({
        "type": "serial_novel", "title": title, "goal": "g", "context": "",
        "workdir": str(_TMP), "serial": {
            "chapters": 4, "words_per_chapter": 1000,
            "start_chapter": start, "continues": continues or ""},
        "cleanup_enabled": False,
    })
    if book_meta:
        for plat, ent in book_meta.items():
            store.set_book_meta(task["id"], plat, ent)
    return task


DONE_META = {"status": "done", "at": "2026-09-29 10:00:00",
             "data": {"book_name": "七十三", "summary": "军户少年"}}

root = _mk_serial("边军", book_meta={"fanqie": dict(DONE_META)})
mid = _mk_serial("边军·续", continues=root["id"], start=5)
leaf = _mk_serial("边军·续2", continues=mid["id"], start=9)
plain = store.create_task({          # 非连载：链退化成自己
    "type": "code", "title": "普通任务", "goal": "g", "context": "",
    "workdir": str(_TMP), "cleanup_enabled": False})


def t_chain_ids():
    ids = store.serial_chain_ids(leaf["id"])
    expect(ids[0] == root["id"], "根应在前: %r" % ids)
    expect(set(ids) == {root["id"], mid["id"], leaf["id"]}, "三代应齐全: %r" % ids)
    expect(store.serial_chain_ids(root["id"])[:1] == [root["id"]], "根视角含后代")
    expect(mid["id"] in store.serial_chain_ids(root["id"]), "后代要收进根的链")
    expect(store.serial_chain_ids(plain["id"]) == [plain["id"]], "非连载退化自身")
    expect(store.serial_chain_ids("no-such") == ["no-such"], "未知任务回退自身")


def t_chain_cycle_safe():
    a = _mk_serial("环A", continues="环B占位")          # 先造 a
    b = _mk_serial("环B", continues=a["id"])           # b → a
    # 手工把 a → b 闭成环（create_task 不校验环，这里直接改盘上对象）
    ta = store.get_task(a["id"])
    ta["serial"]["continues"] = b["id"]
    ids = store.serial_chain_ids(a["id"])
    expect(a["id"] in ids and b["id"] in ids, "环内两个都该到: %r" % ids)
    expect(len(ids) == len(set(ids)), "环不得无限收")


def t_inherited_meta():
    for t in (mid, leaf):
        m = store.inherited_book_meta(t, "fanqie")
        expect(m.get("status") == "done" and m.get("data", {}).get("book_name") == "七十三",
               "续写批次应读到根的完成条目")
    expect(store.inherited_book_meta(root, "fanqie")["data"]["book_name"] == "七十三",
           "根读自己的")
    expect(store.inherited_book_meta(mid, "qimao") == {}, "整链都没有回自身空条目")


def t_inherited_meta_prefers_done():
    # 中段自己生成了失败条目：沿链仍应拿根的 done，而不是中段的 failed
    store.set_book_meta(mid["id"], "qimao", {"status": "failed", "error": "x"})
    m = store.inherited_book_meta(mid, "qimao")
    expect(m == {"status": "failed", "error": "x"},
           "整链无 done 时回自身条目（保留失败详情）")
    store.set_book_meta(root["id"], "qimao", dict(DONE_META))
    m = store.inherited_book_meta(mid, "qimao")
    expect(m.get("status") == "done", "链上有 done 就用 done")


def t_binding_read_inherit():
    ledger.save_book(root["id"], "fanqie", {"book_id": "B1", "title": "边军"})
    for tid in (root["id"], mid["id"], leaf["id"]):
        b = ledger.book_for(tid, "fanqie")
        expect(b and b.get("book_id") == "B1", "整链都应读到根的绑定: %s" % tid)
    expect(ledger.book_for(plain["id"], "fanqie") is None, "链外任务不得串台")
    expect(ledger.book_for("no-such", "fanqie") is None, "未知任务回退无绑定")


def t_binding_write_lands_root():
    # 续写批次上登记/补账：写到根的既有条目，不在链上分叉第二份
    ledger.save_book(leaf["id"], "fanqie", {"book_id": "B1", "title": "边军·新"})
    books = ledger.load_books()
    holders = [t for t, ents in books.items() if "fanqie" in (ents or {})]
    expect(holders == [root["id"]], "绑定持有人只应是根: %r" % holders)
    expect(ledger.book_for(root["id"], "fanqie")["title"] == "边军·新",
           "根条目应被更新而非新建")
    # 校准字段沿链落到持有人
    ledger.update_book(mid["id"], "fanqie", remote_total=12)
    expect(ledger.book_for(leaf["id"], "fanqie").get("remote_total") == 12,
           "中段发起校准要落在这本书的账上")
    ledger.update_book(plain["id"], "fanqie", remote_total=99)
    expect(ledger.book_for(root["id"], "fanqie").get("remote_total") == 12,
           "链外任务的 update 不得碰这本书")


def t_binding_self_entry_wins():
    # 老数据形态：根条目缺 book_id（建书没提上 id），续写批次发章时补找回
    ledger.save_book(root["id"], "qimao", {"title": "边军"})          # 无 id
    ledger.save_book(leaf["id"], "qimao", {"book_id": "Q9", "title": "边军"})
    b = ledger.book_for(leaf["id"], "qimao")
    expect(b.get("book_id") == "Q9", "自己条目优先于祖先缺 id 条目")
    # 根视角也能看到补回的 id（写落到了根的既有条目）
    expect(ledger.book_for(root["id"], "qimao").get("book_id") == "Q9",
           "补账应落根条目而非另立")


def t_books_for_view():
    # 前端视图统一口径（history/sync-published/pending 都走这里）
    got = ledger.books_for(leaf["id"])
    expect(set(got) >= {"fanqie", "qimao"}, "续写批次视图应见两平台绑定: %r" % sorted(got))
    expect(got["fanqie"].get("book_id") == "B1", "fanqie 绑定沿链可见")
    expect(got["qimao"].get("book_id") == "Q9", "qimao 绑定沿链可见")
    expect(ledger.books_for(plain["id"]) == {}, "链外任务视图为空")


def t_published_chapters_merge():
    ledger.record("fanqie", "upload_chapter", task_id=root["id"], chapter_no=1, ok=True)
    ledger.record("fanqie", "upload_chapter", task_id=leaf["id"], chapter_no=9, ok=True)
    for tid in (root["id"], mid["id"], leaf["id"]):
        done = ledger.published_chapters(tid, "fanqie")
        expect(done == {1, 9}, "章号应整链合并（防续写重发）: %s → %r" % (tid, done))
    expect(ledger.published_chapters(plain["id"], "fanqie") == set(),
           "链外任务不得串台")


def t_recent_merge():
    recs = ledger.recent(task_id=mid["id"], platform="fanqie", limit=50)
    nos = {r.get("chapter_no") for r in recs}
    expect(nos == {1, 9}, "历史应沿链合并: %r" % nos)
    expect(ledger.recent(task_id=plain["id"]) == [], "链外无历史")


for name, fn in sorted([(k[2:], v) for k, v in globals().items()
                        if k.startswith("t_") and callable(v)]):
    check(name, fn)

print("---- %s: %d fail" % ("test_bookmeta_chain", len(FAILS)))
sys.exit(1 if FAILS else 0)
