"""
content_tests.py — run: python -m pipelines.content_tests  (python3 on macOS/Linux)

Content's failure mode is not a crash, it is VOLUME: twelve thin pages that
compete with each other and win no work. So these tests are aimed at the rules
that stop that, plus the one concurrency property the whole board rests on —
two agents must never both claim the same job.

Everything runs against a throwaway cabinet; the real one is never touched.
"""
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

from coordination import board
from . import content_briefs as cb

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def _fresh_db():
    """A real cabinet built from the real schema, in a temp dir."""
    d = Path(tempfile.mkdtemp())
    db = d / "agency.db"
    schema = (Path(__file__).parents[1] / "coordination" / "schema.sql").read_text(encoding="utf-8")
    con = sqlite3.connect(db)
    con.executescript(schema)
    con.commit()
    con.close()
    return db


def test_clustering():
    print("Clustering (one page per service+area):")
    rows = [
        ("glass partition dubai", "Dubai", "local"),
        ("glass partition dubai price", "Dubai", "buy"),
        ("office glass partition dubai", "Dubai", "buy"),
        ("glass partition abu dhabi", "Abu Dhabi", "local"),
        ("pergola dubai", "Dubai", "local"),
        ("best restaurants dubai", "Dubai", "info"),   # names no service -> dropped
    ]
    gs = cb.cluster(rows)
    keys = sorted((g["service"], g["area"]) for g in gs)
    check("same service+area collapses to one page", keys,
          [("glass partition", "Abu Dhabi"), ("glass partition", "Dubai"), ("pergola", "Dubai")])
    dubai_partition = next(g for g in gs
                           if g["service"] == "glass partition" and g["area"] == "Dubai")
    check("three phrases behind the Dubai partition page", len(dubai_partition["phrases"]), 3)
    check("a phrase naming no service is not a page",
          any("restaurant" in p for g in gs for p in g["phrases"]), False)
    check("buying intent sorts first", gs[0]["service"], "glass partition")


def test_specific_service_beats_generic():
    """'glass partition' must not be swallowed by the generic 'glass' bucket."""
    print("Service matching (specific before generic):")
    check("partition", cb._service_of("frameless glass partition dubai"), "glass partition")
    check("shower", cb._service_of("shower glass dubai price"), "shower glass")
    check("railing", cb._service_of("glass balustrade marina"), "glass railing")
    check("generic still matches", cb._service_of("glass repair dubai"), "glass work")
    check("aluminium door before aluminium", cb._service_of("aluminium door sharjah"),
          "aluminium doors")
    check("no service -> None", cb._service_of("cheap flights"), None)


def test_district_beats_emirate():
    print("Area matching (district before emirate):")
    check("district wins", cb._area_of("glass railing dubai marina"), "Dubai Marina")
    check("emirate when no district", cb._area_of("glass railing dubai"), "Dubai")
    check("stored area is trusted", cb._area_of("anything", "Sharjah"), "Sharjah")
    check("no area -> blank", cb._area_of("glass railing"), "")


def test_critic_gate():
    print("Critic (the anti-volume rules):")
    good = [{"url": "/a", "title": "A", "evidence": {"phrases_behind_this_page": 3}, "_ready": True},
            {"url": "/b", "title": "B", "evidence": {"phrases_behind_this_page": 2}, "_ready": False}]
    check("a sound set passes", cb.critic_check(good, 10)[0], True)

    dupe = [dict(good[0]), dict(good[0])]
    ok, why = cb.critic_check(dupe, 10)
    check("two pages on one URL is rejected", (ok, any("same URL" in r for r in why)), (False, True))

    thin = [{"url": "/a", "title": "A", "evidence": {"phrases_behind_this_page": 1}, "_ready": True}]
    ok, why = cb.critic_check(thin, 10)
    check("a page on one phrase is rejected", (ok, any("thin" in r for r in why)), (False, True))

    # the MD's gate: exactly one brief may be ready to draft
    spree = [{"url": f"/p{i}", "title": f"P{i}",
              "evidence": {"phrases_behind_this_page": 3}, "_ready": True} for i in range(4)]
    ok, why = cb.critic_check(spree, 10)
    check("a drafting spree is rejected", (ok, any("gated this at one" in r for r in why)),
          (False, True))

    check("no keywords at all is rejected", cb.critic_check([], 0)[0], False)


def test_brief_is_grounded():
    print("Briefs are grounded in real phrases:")
    g = {"service": "glass partition", "area": "Dubai", "buy": 2, "local": 1, "info": 0,
         "phrases": ["glass partition dubai", "glass partition dubai price",
                     "office glass partition dubai"]}
    b = cb.make_brief(g, 1)
    check("primary is the head term", b["primary_keyword"], "glass partition dubai")
    check("every secondary came from the cluster",
          set(b["secondary_keywords"]) <= set(g["phrases"]), True)
    check("evidence count matches the input", b["evidence"]["phrases_behind_this_page"], 3)
    check("url is a clean slug", b["url"], "/glass-partition-dubai")
    check("the enquiry route is mandatory",
          any("WhatsApp" in m for m in b["must_include"]), True)
    check("real photos are mandatory", any("No stock" in m for m in b["must_include"]), True)
    # the red line the handbook draws around pricing has to survive into the brief
    check("no unapproved pricing on the page",
          any("owner's sign-off" in d for d in b["do_not"]), True)


def test_claim_is_atomic():
    """
    The property the whole board rests on: eight agents race for one request and
    exactly ONE may win. If this ever returns 2, two agents are doing the same
    job and overwriting each other.
    """
    print("Claim, don't grab (8 agents race for 1 request):")
    real_db = board.DB
    board.DB = _fresh_db()
    try:
        rid = board.post_request("SEO", "Content", "Keyword-gap list ready", "body")
        winners, lock = [], threading.Lock()

        def go(n):
            if board.claim_request(rid, f"agent-{n}"):
                with lock:
                    winners.append(n)

        threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        check("exactly one agent claims it", len(winners), 1)

        rows = board.open_requests_for("Content")
        check("it is no longer open to anyone else", rows, [])
    finally:
        board.DB = real_db


def test_rerun_does_not_duplicate():
    """
    Running twice must not mint a competing twin of every page. This is the
    duplicate-URL rule the Critic already enforces inside one run, applied
    across runs — which is where it was actually being broken.
    """
    print("Re-running is safe:")
    real_db = board.DB
    board.DB = _fresh_db()
    try:
        con = sqlite3.connect(board.DB)
        for ph, ar, it in [("glass partition dubai", "Dubai", "buy"),
                           ("glass partition dubai price", "Dubai", "buy"),
                           ("pergola dubai", "Dubai", "buy"),
                           ("pergola dubai price", "Dubai", "buy")]:
            con.execute("INSERT INTO keywords(phrase,area,intent) VALUES(?,?,?)", (ph, ar, it))
        con.commit()
        con.close()

        board.post_request("SEO", "Content", "Keyword-gap list ready", "gaps")
        first = cb.run(agent="content-writer")
        check("first run briefs the pages", first["stored"], 2)

        board.post_request("SEO", "Content", "Keyword-gap list ready", "same gaps again")
        second = cb.run(agent="content-writer")
        check("second run stores nothing new", second["stored"], 0)
        check("and says why", second["already_briefed"], 2)

        con = sqlite3.connect(board.DB)
        titles = [r[0] for r in con.execute("SELECT title FROM content_queue")]
        check("no page was briefed twice", len(titles), len(set(titles)))
        check("still just the two pages", len(titles), 2)
        con.close()

        # and the Critic refuses outright if a clash is somehow handed to it
        clash = [{"url": "/glass-partition-dubai", "title": "x",
                  "evidence": {"phrases_behind_this_page": 3}, "_ready": True}]
        ok, why = cb.critic_check(clash, 10, already={"/glass-partition-dubai"})
        check("Critic rejects a page that already exists",
              (ok, any("already briefed" in r for r in why)), (False, True))
    finally:
        board.DB = real_db


def test_end_to_end_on_a_temp_cabinet():
    print("Full cycle on a throwaway cabinet:")
    real_db = board.DB
    board.DB = _fresh_db()
    try:
        con = sqlite3.connect(board.DB)
        for ph, ar, it in [("glass partition dubai", "Dubai", "local"),
                           ("glass partition dubai price", "Dubai", "buy"),
                           ("office glass partition dubai", "Dubai", "buy"),
                           ("pergola dubai", "Dubai", "local"),
                           ("pergola dubai price", "Dubai", "buy")]:
            con.execute("INSERT INTO keywords(phrase,area,intent) VALUES(?,?,?)", (ph, ar, it))
        con.commit()
        con.close()

        idle = cb.run(agent="t")
        check("refuses to invent work with no request", idle["ok"], False)
        check("and says what the cabinet holds", idle["keywords_in_cabinet"], 5)
        check("and names the command that fixes it",
              "pipelines.seo_gap" in idle["error"], True)

        board.post_request("SEO", "Content", "Keyword-gap list ready", "gaps attached")
        r = cb.run(agent="content-writer")
        check("it runs", r["ok"], True)
        check("critic passed", r["critic"], "passed")
        check("exactly one is ready to draft",
              sum(1 for p in r["pages"] if p["status"] == "briefed"), 1)
        check("the rest are held for SEO",
              all(p["status"] == "blocked_on_seo" for p in r["pages"][1:]), True)

        con = sqlite3.connect(board.DB)
        con.row_factory = sqlite3.Row
        # nothing may be drafted or published by this agent
        drafted = con.execute(
            "SELECT COUNT(*) FROM content_queue WHERE draft IS NOT NULL"
            " OR status IN ('drafted','approved','published')").fetchone()[0]
        check("nothing was drafted or published", drafted, 0)
        # the handoff was answered and closed, and SEO was asked back
        st = dict(con.execute("SELECT status,handler FROM requests WHERE from_sector='SEO'"
                              " AND to_sector='Content'").fetchone())
        check("SEO's request was answered and closed", st["status"], "closed")
        check("and it records who handled it", st["handler"], "content-writer")
        back = con.execute("SELECT COUNT(*) FROM requests WHERE from_sector='Content'"
                           " AND to_sector='SEO'").fetchone()[0]
        check("SEO was asked to clear volume (its call, not ours)", back, 1)
        msgs = con.execute("SELECT COUNT(*) FROM request_msgs").fetchone()[0]
        check("the thread was appended to, not overwritten", msgs >= 3, True)
        con.close()
    finally:
        board.DB = real_db


def main():
    test_clustering()
    test_specific_service_beats_generic()
    test_district_beats_emirate()
    test_critic_gate()
    test_brief_is_grounded()
    test_claim_is_atomic()
    test_rerun_does_not_duplicate()
    test_end_to_end_on_a_temp_cabinet()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all content tests passed")


if __name__ == "__main__":
    main()
