"""
seo_tests.py — run: python -m pipelines.seo_tests   (python3 on macOS/Linux)

SEO's failure modes: posting the same keyword gaps twice, letting a phrase
from another country through because it happens to contain "glass", or
shipping a gap list the Critic should have rejected. So these tests pin down
scope-filtering, intent/area tagging, the Critic's grounding checks, dedup
against what's already tracked, and that the shelf is always freed — even
when the Critic rejects the run.

_suggest() is monkeypatched everywhere here; no real network call is made,
and everything runs against a throwaway cabinet.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

from coordination import board, cabinet
from . import seo_gap as seo

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def _fresh_db():
    d = Path(tempfile.mkdtemp())
    db = d / "agency.db"
    con = sqlite3.connect(db)
    con.executescript((Path(__file__).parents[1] / "coordination" / "schema.sql").read_text())
    con.commit()
    con.close()
    return db


def main():
    _intent_and_area_tests()
    _scope_tests()
    _critic_tests()
    _full_cycle_tests()

    if _fail:
        print(f"\n{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("\nall SEO tests passed")


def _intent_and_area_tests():
    print("Intent (buy vs local vs info):")
    check("price signal is buy", seo._intent("glass partition price dubai"), "buy")
    check("installation signal is buy", seo._intent("aluminium door installation"), "buy")
    check("area alone (no buy word) is local", seo._intent("glass work sharjah"), "local")
    check("plain descriptive phrase is info", seo._intent("what is tempered glass"), "info")

    print("Area (from the fixed core list):")
    check("Dubai detected", seo._area_of("glass partition dubai"), "Dubai")
    check("Sharjah detected", seo._area_of("aluminium doors sharjah"), "Sharjah")
    check("no area -> blank", seo._area_of("glass partition price"), "")


def _scope_tests():
    print("In scope (this business' trade, in the UAE):")
    check("glass phrase in scope", seo._in_scope("glass partition dubai"), True)
    check("aluminium phrase in scope", seo._in_scope("aluminium windows price"), True)
    check("pergola in scope", seo._in_scope("pergola cost abu dhabi"), True)
    check("unrelated phrase out of scope", seo._in_scope("car insurance dubai"), False)

    print("Non-UAE noise is dropped even when it mentions glass:")
    check("India modifier dropped", seo._in_scope("glass partition price india"), False)
    check("Sri Lanka modifier dropped", seo._in_scope("aluminium doors sri lanka"), False)
    check("UK modifier dropped", seo._in_scope("glass balustrade uk"), False)
    check("a real UAE phrase with no such modifier survives",
          seo._in_scope("glass balustrade dubai marina"), True)


def _critic_tests():
    print("Critic (grounding, dedup, scope):")
    sound = [{"phrase": "glass partition dubai", "intent": "buy", "area": "Dubai"}]
    ok, reasons = seo.critic_check(sound, seeds_used=["glass partition"])
    check("a sound set passes", ok, True)

    ok, reasons = seo.critic_check([], seeds_used=["glass partition"])
    check("no gaps at all is rejected", ok, False)
    check("and says why", "no new gaps" in reasons[0], True)

    ok, reasons = seo.critic_check(sound, seeds_used=[])
    check("no real source evidence is rejected", ok, False)

    dup = sound + sound
    ok, reasons = seo.critic_check(dup, seeds_used=["glass partition"])
    check("duplicate phrases rejected", ok, False)

    off_scope = [{"phrase": "car insurance dubai", "intent": "info", "area": "Dubai"}]
    ok, reasons = seo.critic_check(off_scope, seeds_used=["car insurance"])
    check("out-of-scope phrase rejected", ok, False)


def _full_cycle_tests():
    real_db = board.DB
    board.DB = _fresh_db()
    real_suggest = seo._suggest
    try:
        print("A full cycle on a throwaway cabinet, network faked:")

        def fake_suggest(q):
            # every seed answers with one real-looking, in-scope suggestion
            return [f"{q} price dubai"]

        seo._suggest = fake_suggest
        out = seo.run(max_seeds=3)
        check("it runs", out["ok"], True)
        check("critic passed", out.get("critic"), "passed")
        check("gaps were found", out["gaps_found"] > 0, True)
        check("gaps were posted", out["posted"], True)

        con = sqlite3.connect(board.DB)
        stored = con.execute("SELECT count(*) FROM keywords").fetchone()[0]
        check("keywords actually landed in the cabinet", stored, out["stored"])
        req = con.execute("SELECT from_sector,to_sector,status FROM requests"
                          " ORDER BY created_at DESC LIMIT 1").fetchone()
        check("Content was asked, not told silently", req, ("SEO", "Content", "open"))
        held = con.execute("SELECT * FROM locks WHERE resource='shelf:seo_data'").fetchone()
        check("the seo_data shelf was released after the run", held, None)
        con.close()

        print("Re-running with the same seeds stores nothing new (dedup against tracked keywords):")
        out2 = seo.run(max_seeds=3)
        if out2.get("posted"):
            check("second run found genuinely new gaps only", out2["stored"] < out["stored"], True)
        else:
            check("second run found nothing new and the Critic said so", out2.get("critic"), "rejected")

        print("A network that answers nothing gets refused by the Critic, and still frees the shelf:")
        con = sqlite3.connect(board.DB)
        con.execute("DELETE FROM keywords")
        con.commit()
        con.close()
        seo._suggest = lambda q: []
        out3 = seo.run(max_seeds=3)
        check("nothing fetched -> critic rejects, not a false clean run", out3.get("posted"), False)
        held = cabinet.borrow("seo_data", "seo-strategist", "SEO", reason="prove it's free", wait=False)
        check("shelf is free even after a rejected run", held["ok"], True)
        if held["ok"]:
            cabinet.release("seo_data", "seo-strategist", borrow_id=held["borrow_id"])
    finally:
        seo._suggest = real_suggest
        board.DB = real_db


if __name__ == "__main__":
    main()
