"""
cabinet_tests.py — run: python3 -m coordination.cabinet_tests

Covers the promises the shelf makes: a borrow needs a reason, only one agent
holds a shelf at a time (others are refused/queued), external data and
over-ceiling spend are flagged for escalation, and the live board reports the
holder, the queue and the day's spend. Runs against a throwaway DB so it never
touches the real filing cabinet.
"""
import sqlite3
import tempfile
from pathlib import Path

from . import board, cabinet

ROOT = Path(__file__).parents[1]

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def _fresh_db():
    tmp = Path(tempfile.mkdtemp()) / "test_agency.db"
    con = sqlite3.connect(tmp)
    con.executescript((Path(__file__).parent / "schema.sql").read_text())
    con.commit()
    con.close()
    # point both modules at the throwaway DB (they read board.DB at call time)
    board.DB = tmp
    return tmp


def test_classify():
    print("Approval policy:")
    check("internal, no cost -> auto", cabinet.classify("internal", 0, 0), "auto")
    check("external -> escalate", cabinet.classify("external", 0, 0), "escalate")
    check("under ceiling -> auto", cabinet.classify("internal", 1.0, 5.0), "auto")
    check("over ceiling -> escalate", cabinet.classify("internal", 9.0, 5.0), "escalate")


def test_borrow_rules():
    print("Borrow rules:")
    _fresh_db()
    check("no reason -> refused", cabinet.borrow("email", "x", reason="")["ok"], False)
    a = cabinet.borrow("email", "leads-responder", "Leads", reason="reply to enquiry")
    check("first borrow ok", a["ok"], True)
    b = cabinet.borrow("email", "leads-hunter", "Leads", reason="cold intro", wait=False)
    check("second on same shelf is busy", b["ok"], False)
    # a DIFFERENT shelf is available in parallel
    c = cabinet.borrow("stock_images", "content-writer", "Content",
                       reason="header", wait=False)
    check("different shelf available in parallel", c["ok"], True)
    cabinet.release("email", "leads-responder", borrow_id=a["borrow_id"], usd_cost=0.0)
    d = cabinet.borrow("email", "leads-hunter", "Leads", reason="cold intro", wait=False)
    check("shelf reusable after release", d["ok"], True)
    cabinet.release("email", "leads-hunter", borrow_id=d["borrow_id"])
    cabinet.release("stock_images", "content-writer", borrow_id=c["borrow_id"])


def test_live_state():
    print("Live board:")
    _fresh_db()
    a = cabinet.borrow("seo_data", "seo-strategist", "SEO",
                       reason="keyword gaps", data_class="external")
    check("external borrow flagged escalate", a["approval"], "escalate")
    cabinet.borrow("seo_data", "leads-hunter", "Leads",
                   reason="directory scrape", wait=False)  # -> waiting/cancelled
    st = cabinet.shelf_state(["seo_data"])["seo_data"]
    check("board shows the holder", st["holder"]["agent"], "seo-strategist")
    check("recent shows escalate", st["recent"][0]["approval"], "escalate")
    cabinet.release("seo_data", "seo-strategist", borrow_id=a["borrow_id"], usd_cost=2.5)
    st2 = cabinet.shelf_state(["seo_data"])["seo_data"]
    check("holder cleared after release", st2["holder"], None)
    check("today spend recorded", st2["today_spend"], 2.5)


def test_the_daily_spend_cap_actually_bites():
    """
    "No money is spent above the daily cost cap without owner approval" is a
    red line. DAILY_SPEND_CAP_USD was written to .env by the setup screen and
    read by nothing at all, and classify()'s cost_ceiling is a per-borrow limit
    that defaults to 0.0 — which disables it. A thousand small borrows could
    run all day and trip nothing.
    """
    print("The daily spend cap:")
    import os
    real_db = board.DB
    board.DB = _fresh_db()
    keep = os.environ.get("DAILY_SPEND_CAP_USD")
    os.environ["DAILY_SPEND_CAP_USD"] = "2.00"
    try:
        check("the owner's cap is read", cabinet.daily_spend_cap(), 2.0)
        check("nothing spent yet", cabinet.spent_today(), 0.0)

        b = cabinet.borrow("seo_data", "a", "SEO", reason="pull gaps")
        check("a borrow under the cap is auto-granted", b["approval"], "auto")
        cabinet.release("seo_data", "a", borrow_id=b["borrow_id"], usd_cost=1.50)
        check("spend is counted from the borrow trail", cabinet.spent_today(), 1.5)

        b2 = cabinet.borrow("seo_data", "a", "SEO", reason="again", est_cost=1.00)
        check("one that would cross the line escalates first", b2["approval"], "escalate")
        cabinet.release("seo_data", "a", borrow_id=b2["borrow_id"], usd_cost=1.00)

        b3 = cabinet.borrow("seo_data", "a", "SEO", reason="third")
        check("past the cap, the shelf is refused", b3["ok"], False)
        check("and it says so plainly", b3.get("capped"), True)
        check("the shelf was never even locked",
              board.acquire_lock("shelf:seo_data", "someone", wait=False), True)
        board.release_lock("shelf:seo_data", "someone")

        # a cap of zero or junk must not read as "unlimited"
        os.environ["DAILY_SPEND_CAP_USD"] = "0"
        check("a zero cap falls back, never to infinity",
              cabinet.daily_spend_cap(), cabinet.DEFAULT_DAILY_SPEND_CAP)
        os.environ["DAILY_SPEND_CAP_USD"] = "not-a-number"
        check("junk falls back too", cabinet.daily_spend_cap(),
              cabinet.DEFAULT_DAILY_SPEND_CAP)
    finally:
        if keep is None:
            os.environ.pop("DAILY_SPEND_CAP_USD", None)
        else:
            os.environ["DAILY_SPEND_CAP_USD"] = keep
        board.DB = real_db


def test_only_shelves_something_borrows():
    """
    Twelve shelves, five of which no code ever opened. Each showed "no APIs on
    this shelf yet", which reads as a job left undone, so the owner went looking
    for keys to fill shelves that would never be used.

    The rule: a shelf exists only if a pipeline borrows it. This test greps for
    the borrow, so adding a decorative shelf back fails here.
    """
    print("Every shelf is one something actually borrows:")
    from providers import store
    # providers/ counts too: llm_extra is borrowed by routing.call_spare, not by
    # a pipeline. The rule is "something borrows it", not "a pipeline does".
    src = ""
    for folder in ("pipelines", "providers", "coordination"):
        for f in (ROOT / folder).glob("*.py"):
            if not f.name.endswith("_tests.py"):
                src += f.read_text()
    for shelf in store.SHELF_KINDS:
        if shelf == "other":
            continue          # the landing place for a tool saved with no shelf
        check(f"something borrows '{shelf}'", f'"{shelf}"' in src, True)

    gone = ("vision", "speech", "trends", "embedding", "image")
    for dead in gone:
        check(f"'{dead}' is not offered as a shelf any more",
              dead in store.SHELF_KINDS, False)
    check("and 'image' is not a model category either",
          "image" in store.MODEL_SHELF_KINDS, False)


def test_brains_are_not_a_shelf():
    """
    The owner asked whether agents would end up using the MD's model. They
    cannot: a brain is ROUTED per sector, never borrowed. The board used to
    render them under a heading that said 'shelf', which invited exactly that
    worry, so the answer is pinned in both places.
    """
    print("A brain is routed, never lent:")
    from providers import store, api
    check("there is no llm TOOL shelf", "llm" in store.SHELF_KINDS, False)
    out = api.cmd_shelves(None)
    kinds = {s["shelf"]: s["kind"] for s in out["shelves"]}
    if "llm" in kinds:
        check("llm is reported as a brain, not a shelf", kinds["llm"], "model")
    check("no empty model categories are injected any more",
          [n for n in ("image", "embedding") if n in kinds], [])
    html = (ROOT / "ui" / "setup.html").read_text()
    check("and the board says so where he reads it",
          "never shared" in html, True)


def test_a_sectors_brain_is_never_on_a_shelf():
    """
    The owner's point, and he was right: a shelf is a single-holder lock, so an
    agent that borrowed the MD's model would BLOCK the MD from its own brain
    until it handed it back — and would be spending the expensive model bought
    for planning on whatever the agent felt like.

    So a routed model is invisible to the shelf, and agents take spares from
    'llm_extra' instead.
    """
    print("A brain routed to a sector cannot be borrowed by anyone:")
    from providers import store, routing, client
    with store.isolated():
        cid = store.upsert_credential({"name": "N", "api_key": "k",
                                       "base_url": "https://x.test/v1"})["id"]
        boss = store.upsert_model(cid, {"api_model_id": "big-one",
                                        "category": "LLM"})["id"]
        store.upsert_model(cid, {"api_model_id": "spare-one",
                                 "category": "llm_extra"})
        d = store._load(); d["routing"]["md"] = {"primary": boss, "enabled": True}
        store._save(d)

        check("the routed model is known to be spoken for",
              boss in store.routed_model_ids(), True)
        cat = store.shelf_catalogue()
        check("and it is on NO shelf at all",
              any(i["id"] == boss for items in cat.values() for i in items), False)
        check("while the spare is on llm_extra",
              [i["name"] for i in cat.get("llm_extra", [])], ["N · spare-one"])

        real_db = board.DB
        board.DB = _fresh_db()
        real_chat = client.chat
        client.chat = lambda cred, m, msgs, **k: {"ok": True, "output": "ok", "status": 200}
        try:
            r = routing.call_spare("content-writer", [{"role": "user", "content": "hi"}],
                                   sector="Content", reason="second opinion")
            check("an agent gets a spare", r["ok"], True)
            check("and it is NOT the MD's brain", r["used"]["model"], "spare-one")
            held = cabinet.borrow("llm_extra", "x", "X", reason="prove released", wait=False)
            check("the shelf was handed back", held["ok"], True)
            if held["ok"]:
                cabinet.release("llm_extra", "x", borrow_id=held["borrow_id"])
        finally:
            client.chat = real_chat
            board.DB = real_db

    print("With no spare configured it refuses rather than reaching for a sector's:")
    with store.isolated():
        r = routing.call_spare("seo-strategist", [], reason="x")
        check("it refuses", r["ok"], False)
        check("and says how to stock the pool", "category 'llm_extra'" in r["error"], True)
        check("and why routed models are not offered",
              "lock that sector out" in r["error"], True)


def test_an_abandoned_waiter_is_reaped():
    """
    A queue row is cancelled by the waiter itself on timeout — which needs that
    process to still be alive. Kill a run step and the row sits 'waiting' for
    ever; the board then showed somebody queued for four days for a shelf
    nobody was holding, which reads as a deadlock and is really a corpse.
    """
    print("A waiter whose process died does not queue for ever:")
    real = board.DB
    board.DB = _fresh_db()
    try:
        import sqlite3 as _sq
        now = cabinet._now()
        con = _sq.connect(board.DB)
        con.execute("INSERT INTO shelf_queue(shelf,agent,sector,reason,data_class,"
                    "status,enqueued_at) VALUES('seo_data','ghost','SEO','x',"
                    "'internal','waiting',?)", (now - 4 * 86400,))
        con.execute("INSERT INTO shelf_queue(shelf,agent,sector,reason,data_class,"
                    "status,enqueued_at) VALUES('seo_data','live','SEO','x',"
                    "'internal','waiting',?)", (now - 5,))
        con.commit(); con.close()

        st = cabinet.shelf_state(["seo_data"])
        waiting = [q["agent"] for q in st["seo_data"]["queue"]]
        check("the four-day-old waiter is gone", "ghost" in waiting, False)
        check("the one that started five seconds ago is left alone",
              "live" in waiting, True)
    finally:
        board.DB = real


def main():
    test_classify()
    test_borrow_rules()
    test_live_state()
    test_the_daily_spend_cap_actually_bites()
    test_only_shelves_something_borrows()
    test_brains_are_not_a_shelf()
    test_a_sectors_brain_is_never_on_a_shelf()
    test_an_abandoned_waiter_is_reaped()
    print("\n" + ("all cabinet tests passed" if not _fail else f"{len(_fail)} FAILED: {_fail}"))
    return 1 if _fail else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
