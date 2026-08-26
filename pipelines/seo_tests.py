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
    con.executescript((Path(__file__).parents[1] / "coordination" / "schema.sql").read_text(encoding="utf-8"))
    con.commit()
    con.close()
    return db


def test_backlinks_and_guest_posts():
    """
    The owner asked for backlink building and guest posting. Both are worth
    doing badly, so what this sector refuses is as pinned as what it does.
    """
    import sqlite3
    from . import seo_backlinks as bl

    print("\nThe link list refuses the tactics that cost you the domain:")
    for tactic in ("paid links", "pbn", "link exchange", "comment spam",
                   "article spinning"):
        check(f"'{tactic}' is refused in writing", tactic in bl.REFUSED_TACTICS, True)
    check("and nothing on the seed list is paid",
          {e[3] for e in bl.SEEDS} & {"paid"}, set())

    print("\nEvery stream moves, none is starved:")
    con = sqlite3.connect(board.DB)
    con.row_factory = sqlite3.Row
    bl.build(con)
    short = bl.shortlist(con)
    kinds = {}
    for r in short:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    # The first ranking sorted everything into one list and took the top eight.
    # Citations won every tiebreak, so a guest post never surfaced and the
    # sector would have filed them for ever without handing one over.
    check("citations lead, because they are what moves the map pack",
          kinds.get("citation"), 4)
    check("but a guest post always reaches the list", kinds.get("guest_post"), 2)
    check("and so do suppliers and trade bodies",
          (kinds.get("supplier"), kinds.get("association")), (2, 2))
    check("the easiest wins come first inside a kind",
          short[0]["effort"], "free")

    print("\nThe critic catches a starved stream:")
    only_citations = [r for r in short if r["kind"] == "citation"]
    ok, reasons = bl.critic_check({"added": 1, "already_known": 0}, only_citations,
                                  {"possible": False},
                                  waiting={"citation", "guest_post"})
    check("a list with no guest post is rejected", ok, False)
    check("and it says which stream would never move",
          any("guest_post" in r for r in reasons), True)

    print("\nRunning twice does not duplicate a single row:")
    first = bl.build(con)
    again = bl.build(con)
    check("the second pass adds nothing", again["added"], 0)
    check("and recognises them all", again["already_known"], len(bl.SEEDS))
    n = con.execute("SELECT count(*) FROM backlinks").fetchone()[0]
    check("one row per opportunity, not per run", n, len(bl.SEEDS))

    print("\nA rival-gap claim needs an index behind it:")
    gaps = bl.rival_gaps(con)
    check("with no backlink API it does not pretend", gaps["possible"], False)
    check("and names what would turn it on", "Ahrefs" in gaps["why_not"], True)
    con.close()


def main():
    _intent_and_area_tests()
    _scope_tests()
    _critic_tests()
    _full_cycle_tests()
    test_backlinks_and_guest_posts()

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

    # HIS REAL GAP LIST. Forty phrases came back and these were in it.
    print("\nOther Pakistani cities, not just the two that were listed:")
    check("Islamabad — this one reached his live gap list",
          seo._in_scope("aluminium work in islamabad"), False)
    for city in ("rawalpindi", "faisalabad", "chennai", "colombo", "jeddah"):
        check(f"{city} dropped", seo._in_scope(f"glass work {city}"), False)

    print("\nSomebody hunting a named rival is not a customer:")
    check("a reviews search is navigational",
          seo._in_scope("febtech glass works dubai reviews"), False)
    check("so is a photos search",
          seo._in_scope("febtech glass works dubai photos"), False)
    check("and a contact-number search",
          seo._in_scope("al noor glass contact number"), False)
    print("  ...but the suffix alone must not kill an honest phrase:")
    check("'glass door photos' is somebody browsing designs",
          seo._in_scope("frameless glass door dubai"), True)

    print("\nA rival's NAME is learned from the competitors table, not a list:")
    import sqlite3
    import tempfile
    from pathlib import Path
    from coordination import board
    real_db = board.DB
    db = Path(tempfile.mkdtemp()) / "a.db"
    con = sqlite3.connect(db)
    con.executescript(Path("coordination/schema.sql").read_text(encoding="utf-8"))
    con.execute("INSERT INTO competitors(name,domain) VALUES"
                "('Fairoozi Aluminium & Glass Works','fairoozi.ae')")
    con.commit()
    con.close()
    board.DB = str(db)
    try:
        check("only the distinctive word is learned",
              sorted(seo._rival_names()), ["fairoozi"])
        check("so the rival's own phrase is dropped",
              seo._in_scope("fairoozi aluminium & glass works abu dhabi"), False)
        print("  ...and the trade's common words are NOT learned, or the whole")
        print("     gap list would empty itself:")
        for w in ("glass", "aluminium", "works", "dubai"):
            check(f"'{w}' is not a rival name", w in seo._rival_names(), False)
        check("an ordinary phrase still passes",
              seo._in_scope("glass partition abu dhabi"), True)
    finally:
        board.DB = real_db


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
