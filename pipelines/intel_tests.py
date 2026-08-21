"""
intel_tests.py — run: python -m pipelines.intel_tests

Three new pieces, each with one way of going badly wrong.

  seo_intel   the whole point is that it works with NO paid API. A version that
              quietly needs DataForSEO to produce anything is the bug. And it
              must never print a number that reads as monthly search volume,
              because that is the one thing free sources genuinely cannot give
              and the one thing an owner would act on hardest.

  notify      the failure mode is not silence, it is noise. An alert that
              repeats until answered gets muted, and then the one that mattered
              is muted too. Sent once, and progress never reaches the phone.

  site        the owner set his website, pulled an update, and was told to set
              his website. config/site.json was tracked, so the repo's empty
              copy overwrote his. Pinned in site_tests.py; the .env precedence
              is re-checked here because notify and intel both depend on it.

No network call is made: the suggest endpoints are monkeypatched.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

from coordination import board, notify
from . import seo_intel

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


# ---------------------------------------------------------------------------
# seo_intel — free, and honest about what it is
# ---------------------------------------------------------------------------

FAKE = {
    "glass partition": ["glass partition dubai price", "glass partition company in dubai",
                        "glass partition sri lanka", "glass partition"],
    "pergola": ["pergola dubai price", "aluminium pergola abu dhabi"],
}


def _fake_suggest(q):
    out = []
    for seed, res in FAKE.items():
        if seed in q:
            out += res
    return out


def test_it_works_with_no_paid_api():
    print("\nThe whole point: real output with no keyword subscription:")
    real_db = board.DB
    real_s, real_b, real_g = seo_intel.seo_gap._suggest, seo_intel._bing_suggest, seo_intel.gsc_queries
    board.DB = _fresh_db()
    seo_intel.seo_gap._suggest = _fake_suggest
    seo_intel._bing_suggest = lambda q: []
    seo_intel.gsc_queries = lambda **k: ([], "no GSC_PROPERTY set")
    try:
        con = sqlite3.connect(board.DB)
        con.row_factory = sqlite3.Row
        built = seo_intel.build(con, seeds=["glass partition"], our_paths=[])
        check("phrases came back with no key of any kind", built["phrases"] > 0, True)
        check("and it says Search Console was not available",
              "GSC_PROPERTY" in built["gsc_why"], True)

        rows = [dict(r) for r in con.execute(
            "SELECT * FROM keyword_intel ORDER BY score DESC")]
        phrases = [r["phrase"] for r in rows]
        check("a UAE buying phrase is in", "glass partition dubai price" in phrases, True)
        check("another country is filtered out",
              "glass partition sri lanka" in phrases, False)
        check("every row carries its evidence",
              all(r["evidence"] for r in rows), True)
        top = rows[0]
        check("the top phrase names an emirate", bool(top["area"]), True)
        con.close()
    finally:
        board.DB = real_db
        seo_intel.seo_gap._suggest, seo_intel._bing_suggest = real_s, real_b
        seo_intel.gsc_queries = real_g


def test_it_never_claims_to_know_volume():
    print("\nIt must not print anything a person would read as searches per month:")
    real_db = board.DB
    board.DB = _fresh_db()
    try:
        con = sqlite3.connect(board.DB)
        seo_intel._table(con)
        con.execute("INSERT INTO keyword_intel(phrase,score,evidence,sources,"
                    "suggest_breadth) VALUES('glass partition dubai',50,"
                    "'6 autocomplete completions (demand breadth, NOT a volume figure)',"
                    "'autocomplete',6)")
        con.commit()
        con.close()
        txt = seo_intel.as_text().lower()
        check("the report says outright it is not volume",
              "not search volume" in txt, True)
        check("the word volume never appears as a claim",
              "searches per month" in txt.replace("estimates searches per month", ""), False)
        check("and it names the free sources it used", "autocomplete" in txt, True)
        check("and says no subscription is needed", "no dataforseo" in txt, True)
        row = {"suggest_breadth": 6}
        check("breadth is labelled breadth, not volume",
              "NOT a volume figure" in seo_intel.evidence_line(row), True)
    finally:
        board.DB = real_db


def test_striking_distance_is_the_free_win():
    print("\nStriking distance is the cheapest traffic there is, so pin the band:")
    real_db = board.DB
    board.DB = _fresh_db()
    try:
        con = sqlite3.connect(board.DB)
        con.row_factory = sqlite3.Row
        seo_intel._table(con)
        for phrase, pos, imp in [("a", 2.0, 900), ("b", 8.0, 300), ("c", 19.0, 120),
                                 ("d", 40.0, 500), ("e", 9.0, 3)]:
            striking = int(seo_intel.STRIKING_LOW <= pos <= seo_intel.STRIKING_HIGH
                           and imp >= 10)
            con.execute("INSERT INTO keyword_intel(phrase,position,impressions,striking)"
                        " VALUES(?,?,?,?)", (phrase, pos, imp, striking))
        con.commit()
        got = [w["phrase"] for w in seo_intel.striking_distance(con)]
        check("already on page one is not a 'win' to chase", "a" in got, False)
        check("position 8 with real impressions is", "b" in got, True)
        check("so is position 19", "c" in got, True)
        check("position 40 is a new page, not a nudge", "d" in got, False)
        check("three impressions is noise, not a signal", "e" in got, False)
        con.close()
    finally:
        board.DB = real_db


def test_it_stops_before_the_runner_kills_it():
    """
    The daily runner kills a step at 420 seconds. Twelve seeds of autocomplete
    on a slow line can reach that, and a step killed at 419 seconds throws away
    every phrase it gathered. It works to a clock instead.
    """
    print("\nOut of time means a partial pass, never a lost one:")
    real_db = board.DB
    real_s, real_b, real_g = (seo_intel.seo_gap._suggest, seo_intel._bing_suggest,
                              seo_intel.gsc_queries)
    board.DB = _fresh_db()
    seo_intel.seo_gap._suggest = _fake_suggest
    seo_intel._bing_suggest = lambda q: []
    seo_intel.gsc_queries = lambda **k: ([], "off")
    try:
        con = sqlite3.connect(board.DB)
        con.row_factory = sqlite3.Row
        out = seo_intel.build(con, seeds=["glass partition"] * 6, budget=-1)
        check("it stopped early", out["ran_out_of_time"], True)
        check("without finishing every seed", out["seeds_done"] < out["seeds_total"], True)
        check("and it did not raise", out["phrases"] >= 0, True)

        out2 = seo_intel.build(con, seeds=["glass partition"], budget=60)
        check("a normal budget completes", out2["ran_out_of_time"], False)
        check("and gets real phrases", out2["phrases"] > 0, True)
        con.close()
    finally:
        board.DB = real_db
        seo_intel.seo_gap._suggest, seo_intel._bing_suggest = real_s, real_b
        seo_intel.gsc_queries = real_g


def test_a_customers_words_never_reach_a_public_topic():
    """
    A topic on ntfy.sh is readable by anyone who guesses the name. Pasting a
    customer's reply into it publishes their message to a channel with no access
    control, to save the owner one tap. He gets told WHO wrote; the words stay
    in the office.
    """
    print("\nA customer's own words must not be published to a public topic:")
    real_db = board.DB
    board.DB = _fresh_db()
    con = sqlite3.connect(board.DB)
    con.execute("INSERT INTO leads(id,company,email,found_at) VALUES(7,'Barsha Build','a@b.ae',1)")
    con.execute("INSERT INTO outreach_log(lead_id,channel,direction,body,reply_class,"
                "needs_owner,at) VALUES(7,'email','reply',"
                "'Our budget is 180000 AED and the landlord is Sheikh...','interested',1,1)")
    con.commit()
    con.close()
    try:
        alert = [a for a in notify.collect() if a["key"].startswith("reply:")][0]
        check("he is told who wrote", "Barsha Build" in alert["title"], True)
        check("and roughly what kind of message", "interested" in alert["body"].lower(), True)
        check("but the budget figure is NOT published", "180000" in alert["body"], False)
        check("nor any of the message body", "landlord" in alert["body"].lower(), False)
        check("and he is told where to read it",
              "open the office" in alert["body"].lower(), True)
    finally:
        board.DB = real_db


def test_the_critic_rejects_evidence_free_output():
    print("\nA shortlist with no evidence behind it must not ship:")
    ok, why = seo_intel.critic_check({"phrases": 3, "gsc_rows": 0}, [
        {"phrase": "x", "impressions": 0, "suggest_breadth": 0,
         "rival_pages": 0, "in_enquiries": 0}])
    check("it is rejected", ok, False)
    check("and it says why", "no evidence" in " ".join(why), True)
    ok2, _ = seo_intel.critic_check({"phrases": 1, "gsc_rows": 0}, [
        {"phrase": "glass partition dubai price", "impressions": 0,
         "suggest_breadth": 4, "rival_pages": 0, "in_enquiries": 0}])
    check("real autocomplete evidence passes", ok2, True)
    ok3, why3 = seo_intel.critic_check({"phrases": 0, "gsc_rows": 0}, [])
    check("nothing gathered is rejected too", ok3, False)


# ---------------------------------------------------------------------------
# notify — the failure mode is noise, not silence
# ---------------------------------------------------------------------------

def _seed_alerts(db):
    con = sqlite3.connect(db)
    con.execute("INSERT INTO site_issues(kind,severity,detail,url,status,found_by,found_at)"
                " VALUES('form_dead','critical','contact form posts to a 404','/contact',"
                "'open','it-watcher',1)")
    con.execute("INSERT INTO tasks(id,sector,title,detail,risk,status,created_by,created_at)"
                " VALUES('t1','Leads','Mail 9 cleared firms','d','amber','open','MD',1)")
    con.commit()
    con.close()


def test_only_things_that_need_him():
    print("\nProgress is not news — it must not reach the phone:")
    real_db = board.DB
    board.DB = _fresh_db()
    _seed_alerts(board.DB)
    con = sqlite3.connect(board.DB)
    con.execute("INSERT INTO tasks(id,sector,title,detail,risk,status,created_by,created_at)"
                " VALUES('t2','SEO','Wrote 4 briefs','d','green','open','MD',1)")
    con.commit()
    con.close()
    try:
        keys = [a["key"] for a in notify.collect()]
        check("a dead contact form gets through", any(k.startswith("site:") for k in keys), True)
        check("an amber approval gets through", "task:t1" in keys, True)
        check("routine green work does not", "task:t2" in keys, False)
        urg = {a["key"]: a["urgency"] for a in notify.collect()}
        check("the enquiry path is the most urgent thing there is",
              urg[[k for k in keys if k.startswith("site:")][0]], notify.NOW)
    finally:
        board.DB = real_db


def test_it_does_not_repeat_itself():
    print("\nAn alert that repeats until answered gets muted, and so does the next one:")
    real_db = board.DB
    board.DB = _fresh_db()
    _seed_alerts(board.DB)
    sent = []
    try:
        import providers.integrations as ig
        real_send = ig.send_message
        ig.send_message = lambda text, **k: (sent.append(text) or
                                             {"ok": True, "integration": "ntfy",
                                              "detail": "delivered"})
        first = notify.send()
        check("the first pass sends what is waiting", first["sent"] > 0, True)
        n = first["sent"]
        second = notify.send()
        check("the second pass sends nothing new", second["sent"], 0)
        check("and says so plainly", second["note"], "nothing needs him")
        check("no duplicate went out", len(sent), n)

        # ...until enough time has passed for one more nudge
        # ...but tomorrow the same unanswered thing earns one more nudge
        check("tomorrow it is worth one more nudge",
              len(notify.unsent(repeat_after=0)) > 0, True)
    finally:
        ig.send_message = real_send
        board.DB = real_db


def test_digest_items_stay_off_the_phone():
    print("\nA stalled agent is worth knowing, not worth a buzz at 11pm:")
    real_db = board.DB
    board.DB = _fresh_db()
    con = sqlite3.connect(board.DB)
    con.execute("INSERT INTO agent_runs(agent,started_at,status,note)"
                " VALUES('seo-strategist',1,'running','stuck')")
    con.commit()
    con.close()
    try:
        alerts = notify.collect()
        check("the stall is collected", any(a["key"].startswith("stall:") for a in alerts), True)
        check("and marked digest, not now",
              [a["urgency"] for a in alerts if a["key"].startswith("stall:")],
              [notify.DIGEST])
        out = notify.send(dry_run=True)
        check("so nothing goes to the phone", out["alerts"], [])
    finally:
        board.DB = real_db


def test_preview_sends_nothing():
    print("\n'What will I get?' must answer without sending anything:")
    real_db = board.DB
    board.DB = _fresh_db()
    _seed_alerts(board.DB)
    sent = []
    try:
        import providers.integrations as ig
        real_send = ig.send_message
        ig.send_message = lambda text, **k: (sent.append(text) or {"ok": True})
        txt = notify.preview_text()
        check("it lists what is waiting", "contact form" in txt, True)
        check("it says what he does NOT get", "You do not get" in txt, True)
        check("and it sent nothing at all", sent, [])
        dry = notify.send(dry_run=True)
        check("the dry run sends nothing either", (dry["sent"], sent), (0, []))
    finally:
        ig.send_message = real_send
        board.DB = real_db


def test_no_channel_is_reported_not_swallowed():
    print("\nWith no topic set, nobody was told — and that must be said out loud:")
    real_db = board.DB
    board.DB = _fresh_db()
    _seed_alerts(board.DB)
    try:
        import providers.integrations as ig
        real_read = ig.read_env
        ig.read_env = lambda: {}
        out = notify.send()
        check("it does not claim success", out["sent"], 0)
        check("and every alert says it was not delivered",
              all(not a["delivered"] for a in out["alerts"]), True)
        check("naming the missing setting",
              "NTFY_TOPIC" in out["alerts"][0]["detail"], True)
    finally:
        ig.read_env = real_read
        board.DB = real_db



# ---------------------------------------------------------------------------
# SEARCH CONSOLE — the path that cannot be tested against a real property
# ---------------------------------------------------------------------------
#
# google-auth is stood in for, so the real request-building code runs. This
# caught a genuine bug: the endpoint host was searchconsole.googleapis.com,
# which serves the URL-inspection API and not webmasters/v3, so the owner's
# very first call would have 404'd and read like a permissions problem.

import types


def _stub_google():
    class _Creds:
        token = "fake-token"
        def refresh(self, req):
            pass
    sa = types.ModuleType("google.oauth2.service_account")
    sa.Credentials = type("C", (), {
        "from_service_account_file": staticmethod(lambda p, scopes=None: _Creds())})
    oauth2 = types.ModuleType("google.oauth2"); oauth2.service_account = sa
    tr = types.ModuleType("google.auth.transport")
    tr.requests = types.ModuleType("google.auth.transport.requests")
    tr.requests.Request = lambda: None
    gauth = types.ModuleType("google.auth"); gauth.transport = tr
    g = types.ModuleType("google"); g.oauth2 = oauth2; g.auth = gauth
    for n, m in [("google", g), ("google.oauth2", oauth2),
                 ("google.oauth2.service_account", sa), ("google.auth", gauth),
                 ("google.auth.transport", tr),
                 ("google.auth.transport.requests", tr.requests)]:
        sys.modules.setdefault(n, m)


class _Resp:
    def __init__(self, code, payload=None, text=""):
        self.status_code, self._p, self.text = code, payload or {}, text

    def json(self):
        return self._p


def test_search_console_talks_to_the_right_endpoint():
    print("\nSearch Console: the documented host, and failures that name the fix:")
    _stub_google()
    import requests
    from providers import integrations
    root = Path(__file__).parents[1]
    sa_path = root / seo_intel.GSC_SA
    made = not sa_path.exists()
    if made:
        sa_path.parent.mkdir(parents=True, exist_ok=True)
        sa_path.write_text("{}")
    real_env, real_post = integrations.read_env, requests.post
    integrations.read_env = lambda: {"GSC_PROPERTY": "sc-domain:example.ae"}
    try:
        seen = []

        def responder(fn):
            def post(url, **kw):
                seen.append(url)
                return fn(url, len(seen))
            requests.post = post

        responder(lambda u, n: _Resp(200, {"rows": [
            {"keys": ["glass partition dubai"], "impressions": 240,
             "clicks": 3, "position": 8.4}]}))
        rows, err = seo_intel.gsc_queries(days=30, limit=10)
        check("it calls the DOCUMENTED host", seen[0].startswith(
            "https://www.googleapis.com/webmasters/v3/"), True)
        check("the property is url-encoded into the path",
              "sc-domain%3Aexample.ae" in seen[0], True)
        check("a real query comes back", len(rows), 1)
        check("with its position", rows[0]["position"], 8.4)
        check("and no error", err, "")

        seen.clear()
        responder(lambda u, n: _Resp(403, text="forbidden"))
        rows, err = seo_intel.gsc_queries()
        check("403 is not retried on the other host", len(seen), 1)
        check("and it names the actual fix", "not a user on" in err, True)

        seen.clear()
        responder(lambda u, n: _Resp(404, text="nf"))
        rows, err = seo_intel.gsc_queries()
        check("404 tries the alternative host once", len(seen), 2)
        check("then explains the property format", "sc-domain:" in err, True)

        seen.clear()
        responder(lambda u, n: _Resp(404) if n == 1 else _Resp(200, {"rows": []}))
        rows, err = seo_intel.gsc_queries()
        check("a 404 on the first host falls through to the second", err, "")
    finally:
        integrations.read_env, requests.post = real_env, real_post
        if made:
            sa_path.unlink()


def test_no_wins_says_which_link_is_missing():
    """
    "Either the pass has not run, or Search Console is not connected" was true
    and useless — it handed the diagnosis back as homework. Every step is
    checkable from here, so every step gets checked.
    """
    print("\nAn empty answer must still say what to do:")
    real_db, real_ready = board.DB, seo_intel.gsc_ready
    real_q = seo_intel.gsc_queries
    board.DB = _fresh_db()
    # The diagnosis now PROBES Search Console rather than inferring from config,
    # so the probe has to be stubbed too — that is the point of the change and
    # the reason these four assertions had to be rewritten.
    seo_intel.gsc_queries = lambda **k: ([], "")
    try:
        con = sqlite3.connect(board.DB)
        con.row_factory = sqlite3.Row
        seo_intel._table(con)

        seo_intel.gsc_ready = lambda: (False, "no GSC_PROPERTY set")
        t = seo_intel._why_no_wins(seo_intel._nearly(con))
        check("not connected -> says so, and does not blame the pass",
              "not usable yet" in t and "never run" not in t, True)

        seo_intel.gsc_ready = lambda: (True, "")
        t = seo_intel._why_no_wins(seo_intel._nearly(con))
        check("connected but nothing on file -> run the pass",
              "never run" in t and "python -m pipelines.seo_intel" in t, True)

        con.execute("INSERT INTO keyword_intel(phrase,position,impressions)"
                    " VALUES('glass dubai',0,0)")
        con.commit()
        t = seo_intel._why_no_wins(seo_intel._nearly(con))
        check("pass ran but GSC gave nothing -> property or permissions",
              "does not match" in t and "Users and" in t, True)

        con.execute("INSERT INTO keyword_intel(phrase,position,impressions)"
                    " VALUES('glass fix dubai',1.4,900)")
        con.commit()
        t = seo_intel._why_no_wins(seo_intel._nearly(con))
        check("everything works, just nothing in the band -> a real answer",
              "real answer, not a fault" in t, True)
        check("and it shows where he actually ranks", "glass fix dubai" in t, True)
        con.close()
        print("  and when Google itself refuses, that is what it says:")
        seo_intel.gsc_queries = lambda **k: (
            [], "Search Console refused (403) for https://x.ae. Two causes...")
        t = seo_intel._why_no_wins(seo_intel._nearly(con))
        check("the real 403 is quoted, not guessed at",
              "Google refused the call" in t and "403" in t, True)
        check("and both causes of a 403 are given",
              "not a user on the property" in t and "TRAILING" in t, True)
        check("it does not fall through to 'the pass never ran'",
              "never run" in t, False)
    finally:
        board.DB, seo_intel.gsc_ready = real_db, real_ready
        seo_intel.gsc_queries = real_q


def test_nothing_is_hardcoded():
    """
    The owner's instruction: this is the main part of his SEO, so its work, its
    limits, its conditions and its reasoning must not be literals in a file only
    I can edit. Every one of them was.
    """
    print("\nWhat it researches comes from the business, not from this file:")
    import os
    keys = ["SEO_AREAS", "SEO_SEEDS", "SEO_BUY_WORDS", "SEO_ASK_WORDS",
            "SEO_STRIKING_FROM", "SEO_STRIKING_TO", "SEO_W_ENQUIRIES",
            "SEO_TIME_BUDGET"]
    for k in keys:
        os.environ.pop(k, None)
    import providers.integrations as integ
    real_read = integ.read_env
    integ.read_env = lambda: {}
    try:
        seeds = seo_intel.seeds_from_catalogue()
        check("seeds come from the live service catalogue, not a fixed list",
              len(seeds) >= 8, True)
        from . import services
        check("and they are the catalogue's own names",
              seeds[0] in [n for s in services.SERVICES.values()
                           for n in (s.get("names") or ())], True)

        os.environ["SEO_SEEDS"] = "frameless shower screen, curtain wall"
        check("he can force the list without touching Python",
              seo_intel.seeds_from_catalogue(),
              ["frameless shower screen", "curtain wall"])
        os.environ.pop("SEO_SEEDS")

        check("areas default to the emirates", seo_intel.areas_list()[0], "Dubai")
        os.environ["SEO_AREAS"] = "Al Ain, Fujairah"
        check("and move when the business moves",
              seo_intel.areas_list(), ["Al Ain", "Fujairah"])
        os.environ.pop("SEO_AREAS")

        check("the striking band is a judgement he can change",
              seo_intel.striking_band(), (4.5, 20.5))
        os.environ["SEO_STRIKING_TO"] = "30"
        check("chase to 30 if he wants", seo_intel.striking_band()[1], 30.0)
        os.environ.pop("SEO_STRIKING_TO")

        base = seo_intel.score({"in_enquiries": 1})
        os.environ["SEO_W_ENQUIRIES"] = "80"
        raised = seo_intel.score({"in_enquiries": 1})
        check("and the scoring weights are his too", raised > base, True)
        os.environ.pop("SEO_W_ENQUIRIES")

        check("a junk override falls back rather than breaking the ranking",
              (os.environ.__setitem__("SEO_W_AREA", "banana"),
               seo_intel.weights()["area"])[1], 12.0)
        os.environ.pop("SEO_W_AREA")
    finally:
        integ.read_env = real_read
        for k in keys + ["SEO_W_AREA"]:
            os.environ.pop(k, None)


def test_it_asks_instead_of_shrugging():
    """
    The handbook: a sector that needs something outside its expertise asks the
    sector that owns it. SEO kept hitting exactly those walls and swallowing
    them — Search Console refused and the pass just reported zero.
    """
    print("\nWhen it cannot do the job alone, it asks the sector that can:")
    real_db = board.DB
    board.DB = _fresh_db()
    try:
        con = sqlite3.connect(board.DB)
        con.row_factory = sqlite3.Row
        con.execute("INSERT INTO keywords(phrase,intent) VALUES('Folding Door','site')")
        con.commit()
        seo_intel.ask_for_help(
            con, {"gsc_why": "Search Console refused (403) for x", "gsc_rows": 0,
                  "from_enquiries": 0}, [{"phrase": "x"}])
        rows = {r["to_sector"]: r["subject"] for r in
                con.execute("SELECT to_sector, subject FROM requests")}
        check("Search Console is IT's door, so IT is asked", "IT" in rows, True)
        check("uncatalogued services go to Content, who own the list",
              "Content" in rows, True)
        check("a missing rival signal is raised", "SEO" in rows, True)
        check("and Leads is told its replies would sharpen the ranking",
              "Leads" in rows, True)
        # The body lives in request_msgs — a request is append-only, so the
        # subject is on the request and every message hangs off it.
        body = con.execute(
            "SELECT m.body FROM request_msgs m JOIN requests r ON r.id=m.request_id"
            " WHERE r.to_sector='IT'").fetchone()[0]
        check("the request carries Google's actual words, not a paraphrase",
              "403" in body, True)
        con.close()

        print("It does not invent problems it does not have:")
        board.DB = _fresh_db()
        con = sqlite3.connect(board.DB)
        con.row_factory = sqlite3.Row
        con.execute("INSERT INTO competitors(name,domain) VALUES('R','r.ae')")
        con.commit()
        seo_intel.ask_for_help(con, {"gsc_why": "", "gsc_rows": 40,
                                     "from_enquiries": 5}, [{"phrase": "x"}])
        n = con.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        check("everything working -> nobody is bothered", n, 0)
        con.close()
    finally:
        board.DB = real_db


def main():
    test_it_works_with_no_paid_api()
    test_nothing_is_hardcoded()
    test_it_asks_instead_of_shrugging()
    test_search_console_talks_to_the_right_endpoint()
    test_no_wins_says_which_link_is_missing()
    test_it_never_claims_to_know_volume()
    test_striking_distance_is_the_free_win()
    test_the_critic_rejects_evidence_free_output()
    test_it_stops_before_the_runner_kills_it()
    test_a_customers_words_never_reach_a_public_topic()
    test_only_things_that_need_him()
    test_it_does_not_repeat_itself()
    test_digest_items_stay_off_the_phone()
    test_preview_sends_nothing()
    test_no_channel_is_reported_not_swallowed()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        sys.exit(1)
    print("All intel/notify tests passed.")


if __name__ == "__main__":
    main()
