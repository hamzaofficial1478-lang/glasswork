"""
leftovers_tests.py — run: python -m coordination.leftovers_tests

"what about the tasks that were unanswered or that was in queue? ... add a
 button that should say resume or start from where you left ... it will finish
 that unanswered and queued tasks first as if we do not either delete or finish
 those unanswered or queued tasks they can make garbage for us and also the
 double load on models"

What has to hold:

  the survey reads and changes nothing
  the clear-up puts back what a closed program was holding, folds copies,
    answers chases and hand-offs, closes orphans — and calls no model
  it never touches a live desk's work, his own decisions, or a run in progress
  "put away" lets the rest go, keeping every reason and everything that is his
  a resume runs what did not finish and what is waiting, and nothing else
  the MD does not stack new work on a sector that has a queue

Throwaway cabinet. No network, no model.
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

from . import board, leftovers

_fail = []
ROOT = Path(__file__).parents[1]


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


class _Cabinet:
    def __enter__(self):
        self.real = board.DB
        d = Path(tempfile.mkdtemp())
        db = d / "agency.db"
        con = sqlite3.connect(db)
        con.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
        con.commit()
        con.close()
        board.DB = str(db)
        board.ensure_schema()
        return self

    def __exit__(self, *a):
        board.DB = self.real

    def sql(self, q, args=()):
        c = sqlite3.connect(board.DB)
        c.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in c.execute(q, args)]
            c.commit()
            return rows
        finally:
            c.close()


DEAD_PID = 2 ** 22 + 12345       # no process has this number


def _task(cab, tid, sector, title, status="open", owner=None, risk="green",
          created_by="MD", ago=600, about=None):
    now = int(time.time())
    cab.sql("INSERT INTO tasks(id,sector,title,detail,status,owner,risk,created_by,"
            "created_at,claimed_at,about) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (tid, sector, title, "d", status, owner, risk, created_by, now - ago,
             now - ago if owner else None, about))


def _stopped_run(cab, statuses):
    """A day run cut off part-way: {label: status} for its steps."""
    from pipelines import day_run
    now = int(time.time())
    cab.sql("INSERT INTO day_runs(id,started_at,ended_at,status,started_by,steps_total,"
            "steps_done) VALUES('prev',?,?, 'stopped','owner',?,0)",
            (now - 3600, now - 3000, len(day_run.STEPS)))
    for i, (label, mod, _room, lane) in enumerate(day_run.STEPS):
        cab.sql("INSERT INTO day_steps(run_id,seq,label,module,status,lane)"
                " VALUES('prev',?,?,?,?,?)", (i, label, mod, statuses.get(label, "ok"), lane))


# ---------------------------------------------------------------------------

def test_nothing_left_says_so():
    print("\nA clean cabinet has nothing left over:")
    with _Cabinet():
        s = leftovers.survey()
        check("nothing to resume", s["resumable"], False)
        check("and it says so plainly", s["head"].startswith("Nothing is left over"), True)


def test_the_survey_reads_and_changes_nothing():
    print("\nThe survey counts what is left and touches none of it:")
    with _Cabinet() as cab:
        _task(cab, "t1", "SEO", "Find page-two wins", status="in_progress",
              owner="seo-strategist")
        board.post_request("Overseer", "SEO", "You have not answered", "chase")
        board.post_request("IT", "SEO", "Which pages need FAQ schema?", "real")
        before = cab.sql("SELECT id, status FROM tasks") + cab.sql("SELECT id, status FROM requests")
        s = leftovers.survey()
        check("the held job is seen as a dead claim", s["tasks"]["dead_claims"], 1)
        check("the chase is seen as paperwork", s["requests"]["chases"], 1)
        check("the real question is seen as waiting", s["requests"]["waiting"], 1)
        check("SEO has work waiting", "SEO" in s["by_room"], True)
        after = cab.sql("SELECT id, status FROM tasks") + cab.sql("SELECT id, status FROM requests")
        check("nothing changed", after, before)


def test_the_clear_up():
    print("\nThe clear-up puts right what needs no thinking:")
    with _Cabinet() as cab:
        # held by a desk whose program was closed
        _task(cab, "dead", "SEO", "Find page-two wins", status="in_progress",
              owner="seo-strategist")
        # the same job twice
        _task(cab, "a", "IT", "Fix the contact form (3 errors)", ago=900)
        _task(cab, "b", "IT", "Fix the contact form (5 errors)", ago=300)
        # his own decision, old and unclaimed — never touched
        _task(cab, "his", "SEO", "Claim 3 free listings", risk="amber", ago=9 * 86400)
        # a claimed question with no answer, from a desk that stopped
        q = board.post_request("IT", "Content", "Which photos go on the pergola page?", "q")
        board.claim_request(q, "content-writer")
        # the same question twice, differently worded only in numbers
        d1 = board.post_request("SEO", "IT", "Add FAQ markup to 6 pages", "x")
        d2 = board.post_request("SEO", "IT", "Add FAQ markup to 7 pages", "x")
        # a chase, and a hand-off
        ch = board.post_request("Overseer", "SEO", "You have not answered", "chase")
        ho = board.post_request("SEO", "IT", "Search Console is not answering", "x",
                                about="handoff:gsc_down")
        # an orphan: filed as a job that was later withdrawn
        orph = board.post_request("Leads", "IT", "Check the enquiry form", "x")
        board.claim_request(orph, "it-watcher")
        board.reply_request(orph, "it-watcher", "Taken. Filed as task w1.")
        _task(cab, "w1", "IT", "Check the enquiry form", about=f"requests:{orph}")
        cab.sql("UPDATE tasks SET status='withdrawn', result='nobody picked it up' WHERE id='w1'")

        # Old enough for the Overseer to have things to say about them — the
        # clear-up must still not say any of it.
        cab.sql("UPDATE tasks SET created_at=created_at-2*86400 WHERE id IN ('a','b')")
        cab.sql("UPDATE requests SET created_at=created_at-2*86400")
        n_before = len(cab.sql("SELECT id FROM requests"))
        r = leftovers.tidy()
        check("it ran", r["ok"], True)
        check("and posted no new message of its own — no new reminders",
              len(cab.sql("SELECT id FROM requests")), n_before)
        st = {t["id"]: t["status"] for t in cab.sql("SELECT id, status FROM tasks")}
        check("the dead claim is back on the board", st["dead"], "open")
        check("one copy of the job goes, the original stays",
              (st["a"], st["b"]), ("open", "withdrawn"))
        check("his own decision is untouched", st["his"], "open")
        rq = {x["id"]: x["status"] for x in cab.sql("SELECT id, status FROM requests")}
        check("the dead question is back in the inbox", rq[q], "open")
        check("the copy question is closed, the original kept", (rq[d1], rq[d2]),
              ("open", "closed"))
        check("and the copy says where the answer will be",
              d1 in cab.sql("SELECT body FROM request_msgs WHERE request_id=?"
                            " ORDER BY id DESC LIMIT 1", (d2,))[0]["body"], True)
        check("the chase is answered and closed", rq[ch], "closed")
        check("the hand-off is answered and closed", rq[ho], "closed")
        check("the orphan is closed, with what became of its job", rq[orph], "closed")
        check("and every line of it is reported", len(r["done"]) >= 5, True)


def test_work_nobody_picked_up_for_days_is_really_withdrawn():
    """
    board.expire_unclaimed wrote to a column tasks does not have, so it raised
    every time, the Overseer swallowed it, and no abandoned job was ever taken
    off a board. This is the test that would have caught it.
    """
    print("\nA job nobody picked up in days is actually withdrawn now:")
    with _Cabinet() as cab:
        _task(cab, "old", "SEO", "Track two more keywords",
              ago=(board.UNCLAIMED_DAYS + 1) * 86400)
        _task(cab, "new", "SEO", "Look at the rivals", ago=3600)
        gone = board.expire_unclaimed()
        check("the old one goes", [g["id"] for g in gone], ["old"])
        row = cab.sql("SELECT status, result, finished_at FROM tasks WHERE id='old'")[0]
        check("marked withdrawn, with the reason and when",
              (row["status"], "not done" in row["result"], bool(row["finished_at"])),
              ("withdrawn", True, True))
        check("the new one stays",
              cab.sql("SELECT status FROM tasks WHERE id='new'")[0]["status"], "open")


def test_the_gap_list_is_left_for_the_briefs_desk():
    print("\nSEO's gap list is what the briefs desk works from — it is not settled early:")
    with _Cabinet() as cab:
        old = board.post_request("SEO", "Content", "Keyword gaps, week 1", "- a",
                                 about="handoff:keyword_gaps")
        cab.sql("UPDATE requests SET created_at=created_at-600 WHERE id=?", (old,))
        new = board.post_request("SEO", "Content", "Keyword gaps, week 2", "- b",
                                 about="handoff:keyword_gaps")
        leftovers.tidy()
        rq = {x["id"]: x["status"] for x in cab.sql("SELECT id, status FROM requests")}
        check("the newest list stays for the desk", rq[new], "open")
        check("the older copy is folded into it", rq[old], "closed")


def test_a_live_desk_and_a_live_run_are_left_alone():
    print("\nA desk that is working keeps its work:")
    with _Cabinet() as cab:
        cab.sql("INSERT INTO agent_runs(agent,started_at,status,pid,host)"
                " VALUES('seo-strategist',?, 'running',?,?)",
                (int(time.time()) - 60, os.getpid(), board._this_host()))
        _task(cab, "live", "SEO", "Find page-two wins", status="in_progress",
              owner="seo-strategist")
        leftovers.tidy()
        check("still claimed", cab.sql("SELECT status FROM tasks WHERE id='live'")[0]["status"],
              "in_progress")

    print("\nAnd nothing is cleared up under a run that is going:")
    with _Cabinet() as cab:
        cab.sql("INSERT INTO day_runs(id,started_at,status,pid,host)"
                " VALUES('now',?, 'running',?,?)",
                (int(time.time()), os.getpid(), board._this_host()))
        r = leftovers.tidy()
        check("refused", r["ok"], False)
        check("and says why", "run is going" in r["error"], True)


def test_put_away_keeps_what_is_his():
    print("\n'Put the leftovers away' lets the rest go, and keeps what is his:")
    with _Cabinet() as cab:
        _task(cab, "q1", "SEO", "Track two more keywords")
        _task(cab, "mine", "Content", "Case study on the Marina pergola", created_by="owner")
        _task(cab, "amb", "IT", "You need to set SMTP_HOST", risk="amber")
        asked = board.post_request("IT", "SEO", "Which pages need FAQ schema?", "q")
        his = board.post_request("Owner", "Content", "Write about shower glass", "q")
        r = leftovers.put_away()
        st = {t["id"]: t["status"] for t in cab.sql("SELECT id, status FROM tasks")}
        check("the queued job is withdrawn", st["q1"], "withdrawn")
        check("with the reason on it",
              "Put away by the owner" in cab.sql("SELECT result FROM tasks WHERE id='q1'")[0]["result"],
              True)
        check("a job he posted himself stays", st["mine"], "open")
        check("his amber decision stays", st["amb"], "open")
        rq = {x["id"]: x["status"] for x in cab.sql("SELECT id, status FROM requests")}
        check("the unanswered question is closed with a note", rq[asked], "closed")
        check("a question he asked stays", rq[his], "open")
        check("nothing was deleted", len(cab.sql("SELECT id FROM tasks")), 3)
        check("and the count is reported", (r["jobs"], r["questions"]), (1, 1))


def test_a_resume_runs_only_what_is_left():
    from pipelines import day_run
    print("\nResume runs what did not finish and what is waiting — nothing else:")
    with _Cabinet() as cab:
        _stopped_run(cab, {"SEO finds the gaps": "stopped",
                           "SEO watches the rivals": "waiting",
                           "Content writes the pages": "waiting"})
        prev = leftovers.last_run()
        check("the cut-off run is seen", prev["interrupted"], True)
        check("with its unfinished steps",
              sorted(u["label"] for u in prev["unfinished"]),
              ["Content writes the pages", "SEO finds the gaps", "SEO watches the rivals"])
        skip = day_run.resume_plan(prev, ["Leads"])
        runs = {day_run.STEPS[i][0] for i in range(len(day_run.STEPS)) if i not in skip}
        check("the unfinished steps run", {"SEO finds the gaps", "SEO watches the rivals",
                                            "Content writes the pages"} <= runs, True)
        check("Leads has work waiting, so Leads works it", "Leads collects" in runs, True)
        check("the cheap bookends run", {"The Overseer checks the agency itself",
                                          "Report scores the day"} <= runs, True)
        check("what finished last time is not repeated", "IT checks the site" in runs, False)
        check("the MD does not plan again on top", "The MD plans the day" in runs, False)
        check("and a skipped step says why",
              "not repeated" in skip[[i for i, s in enumerate(day_run.STEPS)
                                      if s[0] == "IT checks the site"][0]], True)


def test_resume_start_end_to_end():
    from pipelines import day_run
    print("\nPressing Resume, start to finish, with the steps faked:")
    ran = []

    def quick_popen(cmd, cwd=None, stdout=None, stderr=None, **kw):
        ran.append(cmd[-1])
        return subprocess.Popen([sys.executable, "-c", "print('{}')"],
                                stdout=stdout, stderr=stderr)

    real = (day_run.subprocess, day_run.preflight)
    day_run.subprocess = types.SimpleNamespace(Popen=quick_popen, STDOUT=subprocess.STDOUT,
                                               TimeoutExpired=subprocess.TimeoutExpired)
    day_run.preflight = lambda quick=False: {"ready": True, "blocking": [], "warnings": []}
    try:
        with _Cabinet() as cab:
            r = day_run.start(resume=True)
            check("with nothing left, nothing runs", (r["ran"], r.get("nothing_to_resume")),
                  (False, True))
            check("and the door is let go", cab.sql("SELECT 1 FROM locks"), [])

        with _Cabinet() as cab:
            _stopped_run(cab, {"SEO finds the gaps": "stopped"})
            ran.clear()
            r = day_run.start(resume=True)
            check("it ran", r["ran"], True)
            check("and says it picked up where it left off",
                  r["why"].startswith("Picked up where the last run left off"), True)
            check("the step that was cut off ran", "pipelines.seo_gap" in ran, True)
            check("a step that finished did not", "pipelines.it_health" in ran, False)
            st = day_run.status(r["run_id"])
            check("the run points back at the one it resumed",
                  st["run"]["resume_of"], "prev")
            check("no step is left waiting",
                  [s["label"] for s in st["steps"] if s["status"] == "waiting"], [])
    finally:
        day_run.subprocess, day_run.preflight = real


def test_the_md_finishes_what_is_waiting_first():
    from pipelines import md_cycle
    print("\nThe MD does not stack new work on a sector with a queue:")
    facts = {"backlog": {"SEO": md_cycle.BACKLOG_FIRST}, "open_titles": set(),
             "missing": [], "recent_failures": []}
    c = {"sector": "SEO", "title": "Look for page-two wins", "evidence": "ranking drop",
         "risk": "green", "urgency": 4}
    kept, rejected = md_cycle.critic_check([c], facts)
    check("its own idea waits", [k["title"] for k in kept], [])
    check("and the reason says so", "finished first" in rejected[0]["why"], True)
    own = {**c, "title": "The owner's order", "urgency": 1}
    kept, _ = md_cycle.critic_check([own], facts)
    check("the owner's order still goes through", [k["title"] for k in kept],
          ["The owner's order"])


def test_his_orders_reach_a_desk_and_go_first():
    """
    "i start from resume button and run the program now the program has
     stopped but in the sectors reports many tasks are still unassigned why"
    """
    from . import desks, dispatch
    print("\nHis order finds the desk that does it — 'publish' is no veto for him:")
    check("'write 1 post and publish on website' goes to the Writer",
          desks.for_owner_order("Content", "write 1 post and publish on website"),
          "content-writer")
    check("'check and fix the bugs in code' goes to the Fixer",
          desks.for_owner_order("IT", "check and fix the bugs in code in Home page"),
          "it-fixer")
    check("and the Writer alone would still be refused it without the name",
          desks.verdict("content-writer",
                        {"title": "Owner's order: write 1 post and publish on website"})["allowed"],
          False)

    print("\n...and it goes before the agency's own work, even mid-run:")
    with _Cabinet() as cab:
        # Another IT desk is mid-job, so the sector counts as busy.
        _task(cab, "md1", "IT", "Fix the pricing table", status="in_progress",
              owner="it-fixer")
        _task(cab, "his", "IT", "Owner's order: check for dummy mail on the contact page")
        got = dispatch.next_task("IT", "it-watcher")
        check("the watcher takes his order although IT already has a job going",
              (got or {}).get("id"), "his")

    print("\nThe briefs pass hands a 'write a post' order to the writing pass:")
    from pipelines import content_briefs
    class _J:
        def __init__(self, text):
            self.text = text.lower()
        def wants(self, *w):
            import re as _re
            return any(_re.search(r"(?<![a-z0-9])" + _re.escape(x) + r"(?![a-z0-9])",
                                  self.text) for x in w)
    check("declines 'write 1 post and publish'",
          content_briefs.is_briefing_work(_J("Owner's order: write 1 post and publish on website")),
          False)
    check("takes 'Keyword-gap list ready'",
          content_briefs.is_briefing_work(_J("Keyword-gap list ready")), True)


def test_reminders_are_not_turned_into_jobs():
    from pipelines import md_cycle
    print("\nThe MD answers the Overseer's escalations instead of making jobs of them:")
    with _Cabinet() as cab:
        rid = board.post_request(
            "Overseer", "Director",
            "Escalating: 2 task(s) on the SEO board have been open up to 3 hour(s) and nob",
            "detail", about="faults:7")
        item = {"id": rid, "from": "Overseer", "subject":
                "Escalating: 2 task(s) on the SEO board have been open up to 3 hour(s) and nob",
                "points": 50, "why": [], "waited": "1h", "copies": 1,
                "about": "faults:7", "position": "first"}
        md_cycle.clear_inbox({"inbox": {"next": [item], "waiting": []}})
        check("no job posted", cab.sql("SELECT id FROM tasks"), [])
        check("and the escalation is answered and closed",
              cab.sql("SELECT status FROM requests WHERE id=?", (rid,))[0]["status"], "closed")

    print("\n...and the ones already made are taken off; his orders get a desk:")
    with _Cabinet() as cab:
        _task(cab, "n1", "SEO", "From the MD: Escalating: 2 task(s) on the SEO board have",
              about="faults:7")
        _task(cab, "n2", "IT", "From the MD: IT cannot fix this on its own: seo-strategist crashed")
        _task(cab, "o1", "Content", "Owner's order: write 1 post and publish on website")
        _task(cab, "real", "IT", "From the MD: Check the contact form", about="site_issues:3")
        leftovers.tidy()
        st = {t["id"]: t for t in cab.sql("SELECT id, status, desk FROM tasks")}
        check("the reminder-jobs are withdrawn", (st["n1"]["status"], st["n2"]["status"]),
              ("withdrawn", "withdrawn"))
        check("a real MD job stays", st["real"]["status"], "open")
        check("his order is named for the Writer", st["o1"]["desk"], "content-writer")


def main():
    for t in (test_nothing_left_says_so,
              test_the_survey_reads_and_changes_nothing,
              test_the_clear_up,
              test_work_nobody_picked_up_for_days_is_really_withdrawn,
              test_the_gap_list_is_left_for_the_briefs_desk,
              test_a_live_desk_and_a_live_run_are_left_alone,
              test_put_away_keeps_what_is_his,
              test_a_resume_runs_only_what_is_left,
              test_resume_start_end_to_end,
              test_the_md_finishes_what_is_waiting_first,
              test_his_orders_reach_a_desk_and_go_first,
              test_reminders_are_not_turned_into_jobs):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        raise SystemExit(1)
    print("leftovers: all good — nothing is done twice, nothing is left lying about.")


if __name__ == "__main__":
    main()
