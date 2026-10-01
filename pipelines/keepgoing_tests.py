"""
keepgoing_tests.py — run: python -m pipelines.keepgoing_tests

"the agents are not working as user order them and with out completing the
 tasks the program finish the daily run ... i ask for 20 leads it just give 8
 leads and that was it ... i asked to change the mail it did not change the
 dummy mail on webiste ... our website domain is .ae while we have .com domain
 ... the program should complete the task and should do as asked"

What has to hold:

  the day keeps going    after the main pass it goes round again for the jobs
                         still open, a leads number still short, and changes he
                         approved — and only for those sectors
  it knows when to stop  nothing left; a round that moved nothing; the ceiling;
                         his Stop — and it says what is still open, and why
  the MD asks again      a short leads number is asked for again with only what
                         is still to go, never twice at once
  his order reaches IT   a website change typed in any sector's box also goes
                         on IT's board, the only door to his site
  plain words work       "change the dummy mail", ".ae while we have .com" —
                         with no address or domain named in full — become the
                         whole-site change he meant
  one domain             every mention of his name with the old ending becomes
                         the one his site is on; addresses on the dead domain
                         become the one he reads; nobody else's is touched
  his yes stands         the chat's email change is carried out, not refused;
                         a red test suite keeps his approvals waiting; a change
                         that failed can be tried again or dropped
  "20 leads"             counts leads with an email — the ones he can see

A throwaway cabinet, a stand-in website, stand-in steps. Nothing leaves this
machine and no model is called.
"""
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

from coordination import board, chat_actions, changelog, dispatch, inbox, review, site, targets
from . import day_run, it_fix, it_release, leads_hunt, md_cycle, report_daily, seo_intel, site_api

_fail = []
ROOT = Path(__file__).parents[1]
HIS = "teamworlddeal@worlddealtechnicalservices.com"


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


class _Cabinet:
    def __enter__(self):
        self.real = (board.DB, targets.DB)
        d = Path(tempfile.mkdtemp())
        db = d / "agency.db"
        con = sqlite3.connect(db)
        con.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
        con.commit()
        con.close()
        board.DB = str(db)
        targets.DB = str(db)
        board.ensure_schema()
        return self

    def __exit__(self, *a):
        board.DB, targets.DB = self.real

    def sql(self, q, args=()):
        c = sqlite3.connect(board.DB)
        c.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in c.execute(q, args)]
            c.commit()
            return rows
        finally:
            c.close()


def _job(cab, tid, sector, title, desk=""):
    cab.sql("INSERT INTO tasks(id,sector,title,detail,status,risk,created_by,created_at,desk)"
            " VALUES(?,?,?,?, 'open', 'green', 'MD', ?, ?)",
            (tid, sector, title, title, int(time.time()) - 60, desk))


# What each stand-in step does to the cabinet, as a line of Python run in its
# own process — the real day starts every step as a process, and so does this.
_FINISH_ONE = ("c.execute(\"UPDATE tasks SET status='done', finished_at=strftime('%s','now')"
               " WHERE id=(SELECT id FROM tasks WHERE status='open' AND sector=? "
               "ORDER BY created_at LIMIT 1)\", ({sector!r},))")
_FIVE_LEADS = ("[c.execute(\"INSERT INTO leads(company,email,found_at) VALUES(?,?,"
               "strftime('%s','now'))\", ('Firm %d' % i, 'info@firm%d.ae' % i))"
               " for i in range(5)]")


class _Steps:
    """Stand-in steps: every module becomes a tiny process that does `acts[mod]`."""

    def __init__(self, acts):
        self.acts, self.ran = acts, []

    def __enter__(self):
        self.real = day_run.subprocess

        def popen(cmd, cwd=None, stdout=None, stderr=None, **kw):
            mod = cmd[2] if len(cmd) > 2 else ""
            self.ran.append(" ".join(cmd[2:]))
            act = self.acts.get(mod, "")
            code = ("import sqlite3; c=sqlite3.connect(%r); %s; c.commit(); print('{}')"
                    % (str(board.DB), act or "None"))
            return subprocess.Popen([sys.executable, "-c", code], stdout=stdout, stderr=stderr)
        day_run.subprocess = types.SimpleNamespace(
            Popen=popen, STDOUT=subprocess.STDOUT, TimeoutExpired=subprocess.TimeoutExpired)
        return self

    def __exit__(self, *a):
        day_run.subprocess = self.real


def test_the_day_keeps_going_until_his_jobs_are_done():
    print("\nThree of his jobs on IT's board: the day goes round until all three are done:")
    with _Cabinet() as cab:
        for i in range(3):
            _job(cab, f"t{i}", "IT", f"Owner's order: change number {i}", "it-fixer")
        rid = day_run._open_run("keep going", False, [])
        before = cab.sql("SELECT steps_total FROM day_runs WHERE id=?", (rid,))[0]["steps_total"]
        with _Steps({"pipelines.it_fix": _FINISH_ONE.format(sector="IT")}) as steps:
            more = day_run._keep_going(rid)
        check("three extra rounds, one job each", more["rounds"], 3)
        check("every job is done", cab.sql("SELECT count(*) n FROM tasks WHERE status='open'")[0]["n"], 0)
        check("and it says why it stopped",
              "everything you asked for today is done" in more["why"], True)
        check("nothing is left open", more["left"], [])
        check("only IT's steps ran — the Fixer, then the Releaser",
              sorted(set(steps.ran)), ["pipelines.it_fix", "pipelines.it_release"])
        rows = cab.sql("SELECT label, lane, status FROM day_steps WHERE run_id=? AND seq>=?",
                       (rid, day_run.FOLLOW_UP_SEQ))
        check("each extra step is on the run panel", len(rows), 6)
        check("  named for its round", rows[0]["label"], "Round 2 · IT patches what it found")
        check("  in its own lane", {r["lane"] for r in rows}, {"more"})
        check("  and settled", {r["status"] for r in rows}, {"ok"})
        after = cab.sql("SELECT steps_total FROM day_runs WHERE id=?", (rid,))[0]["steps_total"]
        check("the run's step count grows with it", after - before, 6)


def test_it_stops_when_a_round_moves_nothing():
    print("\nA job no desk can finish: one more round, then it stops and says so:")
    with _Cabinet() as cab:
        _job(cab, "t1", "IT", "Owner's order: something with no recipe", "it-fixer")
        _job(cab, "t2", "SEO", "Owner's order: find the gaps", "seo-strategist")
        rid = day_run._open_run("stuck", False, [])
        with _Steps({"pipelines.seo_gap": _FINISH_ONE.format(sector="SEO")}) as steps:
            more = day_run._keep_going(rid)
        check("SEO's job was done in the first extra round",
              cab.sql("SELECT status FROM tasks WHERE id='t2'")[0]["status"], "done")
        check("one round, not the ceiling", more["rounds"], 1)
        check("it says the round moved nothing more", "moved nothing" in more["why"], True)
        check("and IT's job is listed as still open", any(l.startswith("IT:") for l in more["left"]),
              True)
        check("  with why", "moved nothing" in " ".join(more["left"]), True)
        check("the Fixer was not run again and again", steps.ran.count("pipelines.it_fix"), 1)

    print("\n...a ceiling holds, whatever is on the board:")
    with _Cabinet() as cab:
        for i in range(6):
            _job(cab, f"t{i}", "IT", f"Owner's order: job {i}", "it-fixer")
        rid = day_run._open_run("ceiling", False, [])
        real = day_run.FOLLOW_UP_ROUNDS
        day_run.FOLLOW_UP_ROUNDS = 2
        try:
            with _Steps({"pipelines.it_fix": _FINISH_ONE.format(sector="IT")}):
                more = day_run._keep_going(rid)
        finally:
            day_run.FOLLOW_UP_ROUNDS = real
        check("two rounds and no more", more["rounds"], 2)
        check("and it says it was the ceiling", "most one day takes" in more["why"], True)
        check("what is left is listed", "4 job(s) still open" in " ".join(more["left"]), True)

    print("\n...a sector he switched off or did not pick is not run:")
    with _Cabinet() as cab:
        _job(cab, "t1", "Content", "Owner's order: write about pergolas", "content-writer")
        rid = day_run._open_run("only IT", False, [], ["IT", "Director", "Security", "Report"])
        with _Steps({}) as steps:
            more = day_run._keep_going(rid)
        check("no round for a sector not in this run", (more["rounds"], steps.ran), (0, []))
        check("and it says why it is still open", "not in this run" in " ".join(more["left"]), True)


def test_his_leads_number_is_met():
    print("\nHe asked for 20 leads and 8 came in: the MD asks again, the collectors go again:")
    with _Cabinet() as cab:
        targets.set_target("leads", 20)
        for i in range(8):
            cab.sql("INSERT INTO leads(company,email,found_at) VALUES(?,?,?)",
                    (f"Early {i}", f"info@early{i}.ae", int(time.time())))
        # and three phone-only finds, which he cannot see and do not count
        for i in range(3):
            cab.sql("INSERT INTO leads(company,phone,found_at) VALUES(?,?,?)",
                    (f"Phone {i}", f"+97150000000{i}", int(time.time())))
        left = day_run.what_is_left()
        check("12 still to find — counted by leads with an email", left["leads_short"], 12)
        rid = day_run._open_run("leads", False, [])
        with _Steps({"pipelines.leads_osm": _FIVE_LEADS}) as steps:
            more = day_run._keep_going(rid)
        check("three rounds of five got him there", more["rounds"], 3)
        check("the number is met", day_run.what_is_left()["leads_short"], 0)
        check("the MD asked each time before the collectors went",
              steps.ran.count("pipelines.md_cycle --top-up --json"), 3)
        order = [r for r in steps.ran if r.startswith("pipelines.")][:5]
        check("screening before anyone is written to",
              order.index("pipelines.security_screen") < order.index("pipelines.leads_outreach"),
              True)
        check("the MD's step says what it asked for",
              cab.sql("SELECT label FROM day_steps WHERE run_id=? AND module='pipelines.md_cycle'"
                      " AND seq>=? ORDER BY seq", (rid, day_run.FOLLOW_UP_SEQ))[0]["label"],
              "Round 2 · The MD asks for the 12 leads still to find")


def test_the_md_asks_only_for_what_is_still_to_go():
    print("\nThe MD's top-up: what is still to go, once:")
    with _Cabinet() as cab:
        r = md_cycle.top_up()
        check("no number set, nothing asked for", (r["ok"], r.get("idle")), (False, True))
        targets.set_target("leads", 20)
        for i in range(8):
            cab.sql("INSERT INTO leads(company,email,found_at) VALUES(?,?,?)",
                    (f"Early {i}", f"info@early{i}.ae", int(time.time())))
        r = md_cycle.top_up()
        check("it posted a job", r["ok"], True)
        t = cab.sql("SELECT sector, desk, detail FROM tasks WHERE id=?", (r["posted"],))[0]
        check("  for the collectors", (t["sector"], t["desk"]), ("Leads", "leads-hunter"))
        check("  saying what is still to go", "12 still to go" in t["detail"], True)
        check("  and that it means leads with an email", "with an email" in t["detail"], True)
        title = cab.sql("SELECT title FROM tasks WHERE id=?", (r["posted"],))[0]["title"]
        check("  posted as his order, so the one-job rule never holds it back",
              title.startswith("Owner's order:"), True)
        r2 = md_cycle.top_up()
        check("never twice at once", (r2["ok"], r2.get("idle")), (False, True))
        check("and it is on the MD's record",
              len(cab.sql("SELECT id FROM decisions WHERE cycle='md-top-up'")), 1)


def test_a_website_change_reaches_it_whichever_box():
    print("\nHis website change goes on IT's board, whichever box he typed it in:")
    said = ("fix the seo gaps and wrong seo wording like in code our website domain "
            "is .ae while we have .com domain")
    check("the .ae order is a website change", md_cycle.asks_for_site_change(said), True)
    check("the dummy mail is one",
          md_cycle.asks_for_site_change("change the dummy mail on website"), True)
    check("writing a post is not", md_cycle.asks_for_site_change(
        "write 1 post and publish on website"), False)
    check("nor is a number of leads", md_cycle.asks_for_site_change("find 20 leads"), False)
    out = md_cycle.plan({"sector_orders": {"SEO": said}, "targets": {"jobs": []},
                         "site": {}, "source_map_done": True})
    it = [c for c in out if c["sector"] == "IT"]
    check("an IT job for the Fixer", [(c["desk"], c["urgency"]) for c in it], [("it-fixer", 1)])
    check("  carrying his words", said in it[0]["detail"], True)
    check("and SEO keeps its own", any(c["sector"] == "SEO" for c in out), True)


class _Site:
    """His website as a dict, stubbed at site_api.call — everything above it is real."""

    def __init__(self, pages):
        self.pages, self.writes = dict(pages), []

    def __enter__(self):
        self.real = (site_api.call, site_api.settings, site.site_host)
        site_api.call = self._call
        site_api.settings = lambda env=None: {"mode": "full", "base": "https://x/agency-api",
                                              "token": "t" * 40, "site": "https://x"}
        site.site_host = lambda override="": "www.worlddealtechnicalservices.com"
        return self

    def __exit__(self, *a):
        site_api.call, site_api.settings, site.site_host = self.real

    def _call(self, path, method="GET", body=None, timeout=25, s=None):
        if path == "/pages" and method == "GET":
            return {"ok": True, "pages": [{"page": p, "bytes": 1, "changed": "t"}
                                          for p in self.pages if "/" not in p]}
        if path.startswith("/pages/") and method == "GET":
            name = path.split("/", 2)[2]
            if name not in self.pages:
                return {"ok": False, "error": "no such page", "status": 404}
            return {"ok": True, "page": name, "html": self.pages[name]}
        if path == "/pages" and method == "POST":
            self.writes.append(body["page"])
            self.pages[body["page"]] = body["html"]
            return {"ok": True, "page": body["page"], "backup": "b"}
        return {"ok": False, "error": f"no {method} {path}"}


_HIS_SITE = {
    "index.php": ('<link rel="canonical" href="https://www.worlddealtechnicalservices.ae/">'
                  '<script type="application/ld+json">{"url": "https://worlddealtechnicalservices.ae",'
                  ' "email": "info@worlddealtechnicalservices.com"}</script>'
                  '<?php include "includes/footer.php"; ?>'),
    "contact.php": ('<p>Write to info@worlddealtechnicalservices.ae or our supplier '
                    'sales@glass-supplier.ae — see www.glass-supplier.ae</p>'
                    '<a href="https://worlddealtechnicalservices.com/gallery.php">Gallery</a>'
                    '<img src="/assets/img/worlddeal.svg">'),
    "includes/footer.php": ('<a href="mailto:info@worlddealtechnicalservices.ae">'
                            'info@worlddealtechnicalservices.ae</a>'
                            '<a href="https://www.worlddealtechnicalservices.com/about.php">About</a>'),
}


def test_one_domain_everywhere():
    print("\nHis name with the old ending becomes the one his site is on, everywhere:")
    with _Cabinet(), _Site(_HIS_SITE) as s:
        plan = site_api.plan_domains()
        check("the old domain and the bare .com are found, and only his",
              sorted(plan["wrong"]), ["worlddealtechnicalservices.ae",
                                      "worlddealtechnicalservices.com (without www)"])
        r = site_api.use_one_domain(agent="it-releaser")
    check("it went", r["ok"], True)
    pages = s.pages
    check("no .ae of his is left anywhere",
          any("worlddealtechnicalservices.ae" in h for h in pages.values()), False)
    check("the canonical keeps its www", 'href="https://www.worlddealtechnicalservices.com/"'
          in pages["index.php"], True)
    check("the business details for Google say .com — at the www address the site is on",
          '"url": "https://www.worlddealtechnicalservices.com"' in pages["index.php"], True)
    check("an address on the dead domain becomes the one he reads, not a made-up info@",
          pages["includes/footer.php"].count(HIS), 2)
    check("a supplier's address and site are left alone",
          "sales@glass-supplier.ae" in pages["contact.php"]
          and "www.glass-supplier.ae" in pages["contact.php"], True)
    check("the right links are left as they were",
          "https://www.worlddealtechnicalservices.com/about.php" in pages["includes/footer.php"],
          True)
    check("a link to the bare .com is given its www — one address, not two copies",
          "https://www.worlddealtechnicalservices.com/gallery.php" in pages["contact.php"]
          and "//worlddealtechnicalservices.com" not in pages["contact.php"], True)
    check("each page written once", sorted(s.writes),
          ["contact.php", "includes/footer.php", "index.php"])
    check("the Releaser may carry it out", "use_one_domain" in it_release.ALLOWED_CALLS, True)


def test_plain_words_become_the_change_he_meant():
    print("\nHis words, with no address or domain named in full, become the change:")
    ctx = {"env": {"SITE_URL": "https://www.worlddealtechnicalservices.com",
                   "BUSINESS_EMAIL": HIS}}
    with _Cabinet(), _Site(_HIS_SITE):
        task = {"id": 0, "kind": "task", "severity": "high", "url": "",
                "detail": ("Owner's order: change the dummy mail on website to " + HIS)}
        plans = it_fix.plans_for(task, ctx)
        check("the dummy mail becomes one whole-site change",
              [(p["how"], p.get("call")) for p in plans], [("call", "use_one_email")])
        check("  saying which addresses", "info@worlddealtechnicalservices.ae" in plans[0]["why"],
              True)
        task["detail"] = ("Owner's order (website): fix the seo gaps and wrong seo wording "
                          "like in code our website domain is .ae while we have .com domain")
        plans = it_fix.plans_for(task, ctx)
        check("the .ae wording becomes the one-domain change",
              [(p["how"], p.get("call")) for p in plans], [("call", "use_one_domain")])
        check("  counting what it found", plans[0]["times"] >= 5, True)
        body = it_fix._for_the_owner(task, plans[0])
        check("  and the Releaser can read what it queued",
              it_release.plan_of({"id": 1, "detail": json.dumps(body)})["do"], "call")
        ticket = {"id": 7, "kind": "missing_viewport", "severity": "low", "url": "",
                  "detail": "change the email template wording"}
        check("a filed ticket that is not his instruction is left to its own recipe",
              [p.get("call") for p in it_fix.plans_for(ticket, ctx)
               if p.get("call") in ("use_one_email", "use_one_domain")], [])


def test_his_yes_stands():
    print("\nThe chat's email change is carried out, not refused:")
    row = {"id": 1, "detail": json.dumps({"change": {"call": "use_one_email"},
                                          "title": "Make every email address yours"})}
    check("the Releaser reads it as the routine it names", it_release.plan_of(row)["do"], "call")
    check("  and only an allowed one",
          it_release.plan_of({"id": 2, "detail": json.dumps(
              {"change": {"call": "undo_schema"}})})["do"], "refuse")

    print("\n...one refused before for that reason goes back in the queue, once:")
    with _Cabinet() as cab:
        cab.sql("INSERT INTO site_queue(agent,kind,detail,status,queued_at,applied_at,note)"
                " VALUES('it-fixer','use_one_email',?, 'failed', ?, ?, ?)",
                (json.dumps({"change": {"call": "use_one_email"}}), int(time.time()),
                 int(time.time()), "this approval carries no change and no instruction"))
        cab.sql("INSERT INTO site_queue(agent,kind,detail,status,queued_at,applied_at,note)"
                " VALUES('x','note',?, 'failed', ?, ?, ?)",
                (json.dumps({"why": "nothing"}), int(time.time()), int(time.time()),
                 "this approval carries no change and no instruction"))
        back = it_release._reopen_misread_calls()
        check("the email change is back", len(back), 1)
        check("  approved, with why",
              cab.sql("SELECT status, note FROM site_queue WHERE id=?", (back[0],))[0]["status"],
              "approved")
        check("a genuinely empty one stays where it was",
              cab.sql("SELECT status FROM site_queue WHERE kind='note'")[0]["status"], "failed")

    print("\n...and one failed only because the tests were red comes back by itself:")
    with _Cabinet() as cab:
        cab.sql("INSERT INTO site_queue(agent,kind,detail,status,queued_at,applied_at,note)"
                " VALUES('it-fixer','use_one_email',?, 'failed', ?, ?, ?)",
                (json.dumps({"change": {"call": "use_one_email"}}), int(time.time()),
                 int(time.time()), "the test suite is red (cabinet_tests) — nothing goes on"))
        back = board.reopen_site_changes(it_release.RED_SUITE_WORDS,
                                         why="waiting — put back after the tests were red")
        check("it is waiting again", len(back), 1)

    print("\n...and a change that did not go on can be tried again or dropped:")
    with _Cabinet() as cab:
        for n in range(2):
            cab.sql("INSERT INTO site_queue(agent,kind,detail,status,queued_at,applied_at,note)"
                    " VALUES('it-fixer','use_one_domain',?, 'failed', ?, ?, ?)",
                    (json.dumps({"change": {"call": "use_one_domain"},
                                 "title": "Make every mention of your domain .com"}),
                     int(time.time()), int(time.time()), "it errored: the line dropped"))
        nd = changelog.not_done()
        check("the Website changes panel lists both", len(nd), 2)
        check("  with what it was and why", (nd[0]["what"][:21], nd[0]["why"][:10]),
              ("Make every mention of", "it errored"))
        started = []
        real = chat_actions.start_release
        chat_actions.start_release = lambda: started.append(1) or True
        try:
            r = review.act("retry_change", [nd[0]["id"]], {})
        finally:
            chat_actions.start_release = real
        check("Try again puts it back, approved", (r["ok"], cab.sql(
            "SELECT status FROM site_queue WHERE id=?", (nd[0]["id"],))[0]["status"]),
              (True, "approved"))
        check("  and IT is started on it", started, [1])
        r = review.act("drop_change", [nd[1]["id"]], {})
        check("Drop it lets it go", cab.sql("SELECT status FROM site_queue WHERE id=?",
                                            (nd[1]["id"],))[0]["status"], "rejected")
        check("nothing failed is left on the panel", changelog.not_done(), [])


def test_he_is_not_ignored_about_the_domain():
    print("\nHe says the domain is wrong in the chat — it is recorded, even if the model forgets:")
    msg = "in code our website domain is .ae while we have .com domain, fix it"
    check("his words are read as the domain fix", chat_actions.asks_for_domain_fix(msg), True)
    check("an ordinary question is not",
          chat_actions.asks_for_domain_fix("how many leads came in today?"), False)
    with _Cabinet() as cab, _Site(_HIS_SITE):
        started = []
        real = chat_actions.start_release
        chat_actions.start_release = lambda: started.append(1) or True
        try:
            r = chat_actions.take("I'll look into it.", "it-fixer", msg)
        finally:
            chat_actions.start_release = real
        row = cab.sql("SELECT kind, status, detail FROM site_queue")[0]
        check("a change for the whole site, approved on his word",
              (row["kind"], row["status"]), ("use_one_domain", "approved"))
        check("that the Releaser can carry out",
              it_release.plan_of({"id": 1, "detail": row["detail"]})["do"], "call")
        check("and IT is started now", started, [1])
        check("the reply says so", "worlddealtechnicalservices.com" in r["reply"], True)


def test_the_run_panel_tells_the_whole_story():
    print("\nThe extra rounds run before the closing steps, and the run says what is left:")
    with _Cabinet() as cab:
        _job(cab, "t1", "IT", "Owner's order: one more", "it-fixer")
        real_steps, real_after = day_run.STEPS, day_run.LANE_AFTER
        day_run.STEPS = [("IT patches what it found", "pipelines.it_fix", "IT", "it"),
                         ("Report scores the day", "pipelines.report_daily", "Report", "close")]
        day_run.LANE_AFTER = {"it": (), "close": ("it",)}
        try:
            rid = day_run._open_run("story", False, [])
            report = {}
            with _Steps({"pipelines.it_fix": ""}):
                day_run._run_lanes(rid, None, keep_going=True, report=report)
            day_run._left_open(rid, report["more"])
            st = day_run.status(rid)
        finally:
            day_run.STEPS, day_run.LANE_AFTER = real_steps, real_after
        labels = [s["label"] for s in st["steps"]]
        check("the extra round is listed before Report", labels[-1], "Report scores the day")
        check("  and there is one", any(l.startswith("Round 2") for l in labels), True)
        rep = [s for s in st["steps"] if s["label"] == "Report scores the day"][0]
        more = [s for s in st["steps"] if s["label"].startswith("Round 2")]
        check("Report ran after it", rep["started_at"] >= max(s["ended_at"] for s in more), True)
        check("the run keeps what was left open, and why",
              bool(st["run"]["left_open"]["left"]) and bool(st["run"]["left_open"]["why"]), True)

    print("\n...and tests that call the lanes directly get no extra rounds:")
    with _Cabinet() as cab:
        _job(cab, "t1", "IT", "Owner's order: one more", "it-fixer")
        real_steps, real_after = day_run.STEPS, day_run.LANE_AFTER
        day_run.STEPS = [("Report scores the day", "pipelines.report_daily", "Report", "close")]
        day_run.LANE_AFTER = {"close": ()}
        try:
            rid = day_run._open_run("plain", False, [])
            with _Steps({}) as steps:
                day_run._run_lanes(rid, None)
        finally:
            day_run.STEPS, day_run.LANE_AFTER = real_steps, real_after
        check("only the listed step ran", steps.ran, ["pipelines.report_daily"])


def test_a_stop_ends_it_at_once():
    print("\nHe presses Stop: no further round starts:")
    with _Cabinet() as cab:
        _job(cab, "t1", "IT", "Owner's order: one more", "it-fixer")
        rid = day_run._open_run("stop", False, [])
        stop = types.SimpleNamespace(is_set=lambda: True, note=lambda: "stopped by you")
        with _Steps({}) as steps:
            more = day_run._keep_going(rid, stop)
        check("nothing ran", (more["rounds"], steps.ran), (0, []))
        check("and it says it was his Stop", more["why"], "you pressed Stop")


def test_each_round_is_a_run_of_its_own():
    print("\nOne job per sector per ROUND — the rule that stopped the last extra round:")
    with _Cabinet() as cab:
        rid = day_run._open_run("rounds", False, [])
        cab.sql("UPDATE day_runs SET started_at=? WHERE id=?", (int(time.time()) - 600, rid))
        cab.sql("INSERT INTO tasks(id,sector,title,status,created_at,claimed_at,finished_at)"
                " VALUES('m1','Leads','Collect today''s leads','done',?,?,?)",
                (int(time.time()) - 500, int(time.time()) - 400, int(time.time()) - 300))
        check("after the main pass, Leads has had its one job",
              dispatch._claimed_this_run("Leads", rid), 1)
        day_run._new_round(rid, int(time.time()))
        check("an extra round starts the count again",
              dispatch._claimed_this_run("Leads", rid), 0)
        cab.sql("INSERT INTO tasks(id,sector,title,status,created_at,claimed_at)"
                " VALUES('m2','Leads','the rest','in_progress',?,?)",
                (int(time.time()), int(time.time()) + 1))
        check("  and still allows only one in it", dispatch._claimed_this_run("Leads", rid), 1)
        _job(cab, "t1", "IT", "Owner's order: one more", "it-fixer")
        with _Steps({"pipelines.it_fix": _FINISH_ONE.format(sector="IT")}):
            day_run._keep_going(rid)
        check("the extra rounds write down when each began",
              bool(cab.sql("SELECT round_at FROM day_runs WHERE id=?", (rid,))[0]["round_at"]),
              True)


def test_the_md_answers_its_inbox_between_rounds():
    print("\nA question waiting for the MD is answered in the extra round:")
    with _Cabinet() as cab:
        cab.sql("INSERT INTO requests(id,from_sector,to_sector,subject,status,created_at)"
                " VALUES('q1','Leads','Director','Which emirate should Leads work first?',"
                "'open',?)", (int(time.time()) - 60,))
        check("it is counted as waiting for the MD", day_run.what_is_left()["md_questions"], 1)
        rid = day_run._open_run("md", False, [])
        answer = ("c.execute(\"INSERT INTO request_msgs(request_id,author,body,at) VALUES"
                  "('q1','managing-director','Dubai first',strftime('%s','now'))\"); "
                  "c.execute(\"UPDATE requests SET status='answered' WHERE id='q1'\")")
        with _Steps({"pipelines.md_cycle": answer}) as steps:
            more = day_run._keep_going(rid)
        check("the MD was asked to read its inbox, and plan nothing new",
              steps.ran, ["pipelines.md_cycle --inbox --json"])
        check("one round", more["rounds"], 1)
        check("nothing left", more["left"], [])
        check("the step says what it did",
              cab.sql("SELECT label FROM day_steps WHERE run_id=? AND seq>=?",
                      (rid, day_run.FOLLOW_UP_SEQ))[0]["label"],
              "Round 2 · The MD answers the 1 question(s) waiting for it")


def test_his_approval_starts_it_now():
    print("\nHe approves a website change: IT starts on it at once, not tomorrow:")
    with _Cabinet() as cab:
        cid = board.queue_site_change("it-fixer", "use_one_domain",
                                      {"change": {"call": "use_one_domain"}})
        started = []
        real = chat_actions.start_release
        chat_actions.start_release = lambda: started.append(1) or True
        try:
            r = board.decide_approval("site_change", cid, "approved")
            r2 = board.decide_approval("site_change", board.queue_site_change(
                "it-fixer", "x", {"change": {"call": "use_one_email"}}), "rejected")
        finally:
            chat_actions.start_release = real
        check("approved, and IT started", (r["ok"], r["started"], started), (True, True, [1]))
        check("  and he is told so", "now" in r["why"], True)
        check("turning one down starts nothing", (r2["started"], started), (False, [1]))


def test_it_is_not_failed_while_the_fix_waits_on_him():
    print("\nIT's check is not scored a failure while its fix waits for his yes:")
    with _Cabinet() as cab:
        cab.sql("INSERT INTO site_issues(id,kind,severity,detail,status,found_by,found_at)"
                " VALUES(1,'form_dead','critical','dead email','in_review','it-watcher',?)",
                (int(time.time()) - 5 * 86400,))
        con = sqlite3.connect(board.DB)
        try:
            verdict, why = report_daily._score_it_health(
                con, int(time.time()) - 5 * 86400, {"site_issue_ids": [1]})
            con.execute("UPDATE site_issues SET status='open' WHERE id=1")
            v2, _w2 = report_daily._score_it_health(
                con, int(time.time()) - 5 * 86400, {"site_issue_ids": [1]})
        finally:
            con.close()
        check("left unscored, not failed", verdict, None)
        check("  saying it waits on him", "waiting for your yes" in why, True)
        check("a ticket nobody has touched in days is still a failure", v2, "failed")


def test_search_console_says_what_google_said():
    print("\nSearch Console: one to-do, in Google's words, that closes itself:")
    check("never set up is the plain one",
          inbox.gsc_title("no GSC_PROPERTY set. Search Console is free"), inbox.GSC_OLD_TITLE)
    check("not a user says so",
          inbox.gsc_title("Google says this service account can see NO properties at all"),
          "Search Console: add the agency's Google account as a user on your property")
    check("a different spelling says so",
          inbox.gsc_title("GSC_PROPERTY is 'https://x.com/', which is not one of the 1"),
          "Search Console: the property on Setup is not written the way Google has it")
    with _Cabinet() as cab:
        board.try_post_task("IT", inbox.GSC_OLD_TITLE, "old", risk="amber",
                            created_by="IT inbox")
        tid = inbox.gsc_todo("Google says this service account can see NO properties at "
                             "all, so this is a permissions problem", email="sa@x.iam")
        rows = cab.sql("SELECT id, title, detail, status, risk FROM tasks"
                       " WHERE status IN ('open','in_progress')")
        check("still one to-do, not two", len(rows), 1)
        check("  the old one, retitled with what Google said",
              (rows[0]["id"] == tid, rows[0]["title"].startswith("Search Console: add")),
              (True, True))
        check("  naming the account to add", "sa@x.iam" in rows[0]["detail"], True)
        check("  and his to decide", rows[0]["risk"], "amber")
        check("a dropped line is not a job for him",
              inbox.gsc_todo("Google did not answer the Search Console login — the "
                             "connection was closed"), None)
        handoff = {"about": "handoff:gsc_down", "subject": "Search Console is not answering",
                   "_body": "Google's exact words: GSC_PROPERTY is 'https://x.com', which is "
                            "not one of the 1 property(ies) this service account can see"}
        con = sqlite3.connect(board.DB)
        try:
            reply = inbox.answer_handoff("IT", handoff, "it-fixer", con)
        finally:
            con.close()
        check("SEO's hand-off rewrites it with the new reason",
              cab.sql("SELECT title FROM tasks WHERE status='open'")[0]["title"],
              "Search Console: the property on Setup is not written the way Google has it")
        check("  and says it closes itself", "closes itself" in reply, True)
        tmp = Path(tempfile.mkdtemp()) / "gsc-status.json"
        real = seo_intel.GSC_STATUS
        seo_intel.GSC_STATUS = str(tmp)
        try:
            seo_intel.gsc_note(True, "", 12)
        finally:
            seo_intel.GSC_STATUS = real
        check("the day Google answers, the to-do is done",
              cab.sql("SELECT count(*) n FROM tasks WHERE status='open'")[0]["n"], 0)
        check("  with when", "Search Console answered on" in
              cab.sql("SELECT result FROM tasks WHERE id=?", (tid,))[0]["result"], True)
        check("and Setup can read what Google said",
              json.loads(tmp.read_text(encoding="utf-8"))["ok"], True)


def test_a_places_refusal_names_the_switch():
    print("\nGoogle refuses the Places key: he is told which switch to press:")
    msg = leads_hunt.places_refusal(403, '{"error": {"status": "PERMISSION_DENIED", '
                                    '"reason": "SERVICE_DISABLED", "message": "Places API '
                                    '(New) has not been used in project 123"}}')
    check("it names Places API (New)", "Places API (New)" in msg, True)
    check("  and why the old one shows as unavailable", "unavailable" in msg, True)
    check("billing is its own answer",
          "billing" in leads_hunt.places_refusal(403, "BILLING_DISABLED").lower(), True)


def test_search_console_api_switched_off_is_named():
    print("\nGoogle refusing even to list properties means the API is off — and it says so:")
    off_body = json.dumps({"error": {"code": 403, "status": "PERMISSION_DENIED",
                                     "message": "Google Search Console API has not been used in "
                                                "project 123 before or it is disabled.",
                                     "details": [{"reason": "SERVICE_DISABLED"}]}})
    fake = types.SimpleNamespace(status_code=403, text=off_body,
                                 json=lambda: json.loads(off_body))
    creds = types.SimpleNamespace(token="t")
    why = seo_intel._which_403(creds, "https://x", "https://www.x.com/", get=lambda *a, **k: fake)
    check("it says the API is switched off", "switched off" in why, True)
    check("  and where to switch it on",
          "console.cloud.google.com/apis/library/searchconsole.googleapis.com" in why, True)
    check("his to-do is titled for that",
          inbox.gsc_title(why), "Search Console: switch on the Search Console API in Google Cloud")
    other = json.dumps({"error": {"code": 403, "message": "User does not have sufficient permission"}})
    fake2 = types.SimpleNamespace(status_code=403, text=other, json=lambda: json.loads(other))
    why2 = seo_intel._which_403(creds, "https://x", "https://www.x.com/", get=lambda *a, **k: fake2)
    check("any other refusal carries Google's own words",
          "User does not have sufficient permission" in why2, True)


def main():
    for t in (test_the_day_keeps_going_until_his_jobs_are_done,
              test_it_stops_when_a_round_moves_nothing,
              test_his_leads_number_is_met,
              test_the_md_asks_only_for_what_is_still_to_go,
              test_a_website_change_reaches_it_whichever_box,
              test_one_domain_everywhere,
              test_plain_words_become_the_change_he_meant,
              test_his_yes_stands,
              test_he_is_not_ignored_about_the_domain,
              test_the_run_panel_tells_the_whole_story,
              test_a_stop_ends_it_at_once,
              test_each_round_is_a_run_of_its_own,
              test_the_md_answers_its_inbox_between_rounds,
              test_his_approval_starts_it_now,
              test_it_is_not_failed_while_the_fix_waits_on_him,
              test_search_console_says_what_google_said,
              test_a_places_refusal_names_the_switch,
              test_search_console_api_switched_off_is_named):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        sys.exit(1)
    print("keep going: all good — the day finishes what he asked, and says what is left.")


if __name__ == "__main__":
    main()
