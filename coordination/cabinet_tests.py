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


def main():
    test_classify()
    test_borrow_rules()
    test_live_state()
    print("\n" + ("all cabinet tests passed" if not _fail else f"{len(_fail)} FAILED: {_fail}"))
    return 1 if _fail else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
