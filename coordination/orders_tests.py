"""
orders_tests.py — run: python -m coordination.orders_tests

"almost 10th time i asked to change the dummy mail ... there is no any room
 where i can see that the commands i gave how much work get done what remains
 and why"

What has to hold:

  done means a visitor sees it   after the email change, IT reads his live page;
                                 an address still showing means the job is NOT
                                 done, and he is told where it must come from
                                 (his admin panel, when no file holds it) — with
                                 one to-do, not one a run
  the fixer checks the page too  "already right" only when the live page agrees
  his orders, followed           each order: the jobs it became, what each said,
                                 the changes it made and their state, and what
                                 is left with its reason

A throwaway cabinet and a stand-in website. Nothing leaves this machine.
"""
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from . import board, orders_view, proposals

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
        from . import targets
        self.real = (board.DB, targets.DB)
        d = Path(tempfile.mkdtemp())
        db = d / "agency.db"
        con = sqlite3.connect(db)
        con.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
        con.commit()
        con.close()
        board.DB = targets.DB = str(db)
        board.ensure_schema()
        return self

    def __exit__(self, *a):
        from . import targets
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


FOOTER_LIVE = ('<footer><a href="mailto:info@worlddealtechnicalservices.ae">'
               'info@worlddealtechnicalservices.ae</a> <a href="mailto:sales@supplier.ae">x</a>'
               '</footer><script type="application/ld+json">{"email": "' + HIS + '"}</script>')


def test_done_means_a_visitor_sees_it():
    from coordination import site
    from pipelines import it_fix, it_release, site_api
    print("\nThe live page still shows the dummy address: the job is not done, and he is told why:")
    real_site = (site.site_url, site.site_host)
    site.site_url = lambda override="": "https://www.worlddealtechnicalservices.com"
    site.site_host = lambda override="": "worlddealtechnicalservices.com"
    try:
        left = site_api.still_showing(get=lambda u: (200, FOOTER_LIVE) if u.endswith(".com/")
                                      else (404, ""))
        check("the dummy is seen on the live page, and only his", left["emails"],
              {"info@worlddealtechnicalservices.ae": 2})
        real_plan = site_api.plan_emails
        site_api.plan_emails = lambda s=None: {"ok": True, "wrong": {}, "pages": [], "times": 0}
        try:
            why = site_api.why_still_showing(left)
        finally:
            site_api.plan_emails = real_plan
        check("no file holds it, so it says: your admin panel", "admin panel" in why, True)
        check("  with the address to put there", HIS in why, True)
        check("  and the other place it can be", "File Manager" in why, True)

        with _Cabinet() as cab:
            cid = board.queue_site_change("it-fixer", "use_one_email",
                                          {"change": {"call": "use_one_email"}})
            board.decide_site_change(cid, "approved")
            real = (site_api.use_one_email, site_api.still_showing, site_api.why_still_showing,
                    site_api.why_not_ready, it_release.tests_pass)
            from coordination import sitepace
            real_rest = sitepace.resting
            site_api.use_one_email = lambda agent=None, s=None, dry_run=False: {
                "ok": True, "head": "every email address on your website was already " + HIS}
            site_api.still_showing = lambda s=None, get=None: left
            site_api.why_still_showing = lambda l, s=None: "your admin panel holds it"
            site_api.why_not_ready = lambda s=None: ""
            it_release.tests_pass = lambda timeout=None: {"ok": True, "took_s": 1, "why": "green"}
            sitepace.resting = lambda: {}
            try:
                it_release.run()
                it_release.run()
            finally:
                (site_api.use_one_email, site_api.still_showing, site_api.why_still_showing,
                 site_api.why_not_ready, it_release.tests_pass) = real
                sitepace.resting = real_rest
            row = cab.sql("SELECT status, note FROM site_queue WHERE id=?", (cid,))[0]
            check("the change is NOT marked done", row["status"], "failed")
            check("  saying what is still showing and why", "admin panel" in row["note"], True)
            todo = cab.sql("SELECT title, risk FROM tasks WHERE about='owner:still-showing'")
            check("one to-do for him, in his words",
                  [(t["title"], t["risk"]) for t in todo],
                  [("Change the email in your admin panel", "amber")])

        print("\n...and the fixer no longer answers 'already right' while the page disagrees:")
        real_plan, real_left = site_api.plan_emails, site_api.still_showing
        site_api.plan_emails = lambda s=None: {"ok": True, "wrong": {}, "pages": [], "times": 0}
        site_api.still_showing = lambda s=None, get=None: left
        try:
            plans = it_fix.plans_for({"id": 0, "kind": "task", "severity": "high", "url": "",
                                      "detail": "Owner's order: change the dummy mail on the "
                                                "website to " + HIS}, {"env": {"BUSINESS_EMAIL": HIS}})
        finally:
            site_api.plan_emails, site_api.still_showing = real_plan, real_left
        check("it hands it to him with the admin-panel step",
              [(p["how"], "admin panel" in " ".join(p.get("steps") or []))
               for p in plans if p.get("recipe") == "fix_dead_email"], [("advice", True)])
    finally:
        site.site_url, site.site_host = real_site


def test_his_orders_followed_to_the_end():
    print("\nHis orders room: each order, its jobs, its changes, what is left and why:")
    with _Cabinet() as cab:
        day = time.strftime("%Y-%m-%d")
        proposals.set_orders("IT", "change the dummy mail on the website to " + HIS)
        proposals.set_orders("Leads", "find 20 leads with an email in Dubai")
        proposals.set_orders("Content", "write 1 blog post about pergolas")
        now = int(time.time())
        cab.sql("INSERT INTO tasks(id,sector,title,detail,status,owner,result,created_by,"
                "created_at,claimed_at,finished_at) VALUES('t1','IT',?,?,'done','it-fixer',?,"
                "'MD',?,?,?)",
                ("Owner's order: change the dummy mail on the website to " + HIS,
                 "change the dummy mail on the website to " + HIS,
                 json.dumps({"tickets_read": 1, "queued_for_owner": 1}), now, now, now))
        cab.sql("INSERT INTO site_queue(agent,kind,detail,status,queued_at) VALUES('it-fixer',"
                "'fix_task',?, 'pending', ?)",
                (json.dumps({"task_id": "t1", "change": {"run": "use_one_email"},
                             "why": "the dummy is on 2 pages"}), now))
        cab.sql("INSERT INTO tasks(id,sector,title,detail,status,created_by,created_at)"
                " VALUES('t2','Leads','Owner''s order: find 20 leads with an email in Dubai',"
                "'find 20 leads with an email in Dubai','done','MD',?)", (now,))
        cab.sql("INSERT INTO tasks(id,sector,title,detail,status,created_by,created_at)"
                " VALUES('t3','Leads','Owner''s order: find the 12 more leads with an email he "
                "asked for today','12 still to go','open','MD',?)", (now,))
        r = orders_view.report()
        by = {o["sector"]: o for o in r["orders"]}
        check("all three orders are listed", sorted(by), ["Content", "IT", "Leads"])
        it = by["IT"]
        check("IT's order: waiting for his yes", it["state"], "waiting for you")
        check("  showing the job and what the fixer did",
              [(j["who"], j["status"]) for j in it["jobs"]], [("it-fixer", "done")])
        check("  the change under his own words, with its state",
              [(c["id"] > 0, c["says"]) for c in it["changes"]], [(True, "waiting for your yes")])
        check("  and what is left says to approve it", "approve it here" in " ".join(it["left"]), True)
        lead = by["Leads"]
        check("Leads: the morning job and the top-up both followed", len(lead["jobs"]), 2)
        check("  being worked, with why", (lead["state"], "next round" in " ".join(lead["left"])),
              ("in progress", True))
        content = by["Content"]
        check("Content's order never became a job yet: not started, and why",
              (content["state"], "Run the day" in " ".join(content["left"])),
              ("not started", True))
        check("the day it was given", it["day"], day)


class _HisSite:
    """His files through an OLD connector that lists the top level only."""

    def __init__(self, pages, listed, broken=()):
        self.pages, self.listed, self.broken, self.writes = dict(pages), list(listed), set(broken), []

    def __enter__(self):
        from coordination import site
        from pipelines import site_api
        self.real = (site_api.call, site_api.settings, site.site_host, site.site_url)
        site_api.call = self._call
        site_api.settings = lambda env=None: {"mode": "full", "base": "https://x/agency-api",
                                              "token": "t" * 40,
                                              "site": "https://www.worlddealtechnicalservices.com"}
        site.site_host = lambda override="": "worlddealtechnicalservices.com"
        site.site_url = lambda override="": "https://www.worlddealtechnicalservices.com"
        return self

    def __exit__(self, *a):
        from coordination import site
        from pipelines import site_api
        site_api.call, site_api.settings, site.site_host, site.site_url = self.real

    def _call(self, path, method="GET", body=None, timeout=25, s=None):
        if path == "/pages" and method == "GET":
            return {"ok": True, "pages": [{"page": p, "bytes": 1, "changed": "t"} for p in self.listed]}
        if path.startswith("/pages/") and method == "GET":
            name = path.split("/", 2)[2]
            if name in self.broken:
                return {"ok": False, "error": "the server answered 500", "status": 500}
            if name not in self.pages:
                return {"ok": False, "error": "no such page", "status": 404}
            return {"ok": True, "page": name, "html": self.pages[name]}
        if path == "/pages" and method == "POST":
            self.writes.append(body["page"])
            self.pages[body["page"]] = body["html"]
            return {"ok": True, "page": body["page"], "backup": "b"}
        return {"ok": False, "error": f"no {method} {path}"}


DUMMY = "info@worlddealtechnicalservices.ae"
HIS_FILES = {
    "index.php": '<?php include "includes/header.php"; ?><p>home</p><?php include "includes/footer.php"; ?>',
    "contact.php": '<p>Email us: <a href="mailto:' + DUMMY + '">' + DUMMY + '</a></p>',
    "includes/header.php": '<div class="sidemenu"><a href="mailto:' + DUMMY + '">' + DUMMY + '</a></div>',
    "includes/footer.php": '<footer><a href="mailto:' + DUMMY + '">' + DUMMY + '</a></footer>',
}


def test_every_file_is_searched_footer_included():
    from pipelines import site_api
    print("\nHis footer, header and contact page are all searched — even with an old connector:")
    with _Cabinet(), _HisSite(HIS_FILES, listed=["index.php", "contact.php"]):
        r = site_api.plan_replace(DUMMY, HIS)
        emails = site_api.plan_emails()
    check("the dummy is found in all three files",
          sorted(h["page"] for h in r["pages"]),
          ["contact.php", "includes/footer.php", "includes/header.php"])
    check("  each with the line it is on", all(DUMMY in h["sample"] for h in r["pages"]), True)
    check("the email routine sees the same three",
          sorted(p["page"] for p in emails["pages"]),
          ["contact.php", "includes/footer.php", "includes/header.php"])

    print("\n...a file that will not open is named, never passed off as 'not in your files':")
    with _Cabinet(), _HisSite(HIS_FILES, listed=["index.php", "contact.php", "includes/footer.php"],
                              broken={"includes/footer.php"}):
        r = site_api.plan_replace(DUMMY, HIS)
        why = site_api.why_still_showing({"emails": {DUMMY: 2}})
    check("the unreadable file is listed", r["unreadable"], ["includes/footer.php"])
    check("and the reason says it, not 'admin panel'",
          ("includes/footer.php" in why or "includes/header.php" in why, "admin panel" in why),
          (True, False))


def test_his_order_is_his_yes():
    from pipelines import it_fix
    print("\nHis own order to change the email is approved by that order — no second yes:")
    with _Cabinet() as cab, _HisSite(HIS_FILES, listed=["index.php", "contact.php"]):
        con = sqlite3.connect(board.DB)
        con.row_factory = sqlite3.Row
        try:
            plan = it_fix.plans_for({"id": 0, "kind": "task", "severity": "high", "url": "",
                                     "detail": "Owner's order: change the dummy mail on the "
                                               "website to " + HIS, "task_id": "t9",
                                     "his": True}, {"env": {"BUSINESS_EMAIL": HIS}})
            mine = [p for p in plan if p.get("call") == "use_one_email"]
            it_fix._hand_on("it-fixer", {"id": 0, "kind": "task", "severity": "high", "url": "",
                                         "detail": "Owner's order: change the dummy mail",
                                         "task_id": "t9", "his": True}, mine[0], con)
            it_fix._hand_on("it-fixer", {"id": 0, "kind": "seo", "severity": "low", "url": "",
                                         "detail": "an agency finding", "his": False},
                            {"how": "call", "call": "repair_share_buttons", "why": "x",
                             "pages": ["a.php"]}, con)
        finally:
            con.close()
        rows = {r["kind"]: r for r in cab.sql("SELECT kind, status, note, detail FROM site_queue")}
    check("the email change is planned across all three files", bool(mine), True)
    check("his order's change is approved at once", rows["fix_task"]["status"], "approved")
    check("  saying why", "your own order" in rows["fix_task"]["note"], True)
    check("  and tied to his order", json.loads(rows["fix_task"]["detail"])["task_id"], "t9")
    check("an agency finding still waits for his yes", rows["fix_seo"]["status"], "pending")


def test_find_and_replace_from_the_office():
    from coordination import review
    from pipelines import it_release, site_api
    print("\nFind and replace from the office: approved on his word, checked on the live site:")
    with _Cabinet() as cab:
        bad = review.act("site_replace", [], {"find": "<footer>", "replace_with": "<script>"})
        check("page code cannot be changed from here", bad["ok"], False)
        from coordination import chat_actions
        real_start = chat_actions.start_release
        chat_actions.start_release = lambda: True
        try:
            r = review.act("site_replace", [], {"find": DUMMY, "replace_with": HIS})
        finally:
            chat_actions.start_release = real_start
        row = cab.sql("SELECT id, status, detail FROM site_queue")[0]
        check("approved at once, because he typed both", (r["ok"], row["status"]), (True, "approved"))
        check("and the Releaser reads it as a text change",
              it_release.plan_of(row)["do"], "replace")

    print("\n...a replace that finds nothing while the live site still shows it is NOT done:")
    with _Cabinet() as cab, _HisSite({"index.php": "<p>home</p>"}, listed=["index.php"]):
        cid = board.queue_site_change("owner", "replace_text",
                                      {"change": {"replace": DUMMY, "with": HIS}})
        board.decide_site_change(cid, "approved")
        from coordination import sitepace
        real = (site_api.live_has, site_api.why_not_ready, it_release.tests_pass, sitepace.resting)
        site_api.live_has = lambda text, get=None: ["https://www.worlddealtechnicalservices.com/"]
        site_api.why_not_ready = lambda s=None: ""
        it_release.tests_pass = lambda timeout=None: {"ok": True, "took_s": 1, "why": "green"}
        sitepace.resting = lambda: {}
        try:
            it_release.run()
        finally:
            site_api.live_has, site_api.why_not_ready, it_release.tests_pass, sitepace.resting = real
        row = cab.sql("SELECT status, note FROM site_queue WHERE id=?", (cid,))[0]
    check("not marked done", row["status"], "failed")
    check("  and says it is still on the live site", "still on your live site" in row["note"], True)


def main():
    for t in (test_done_means_a_visitor_sees_it, test_his_orders_followed_to_the_end,
              test_every_file_is_searched_footer_included, test_his_order_is_his_yes,
              test_find_and_replace_from_the_office):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        sys.exit(1)
    print("orders: all good — done means he can see it, and every order is followed.")


if __name__ == "__main__":
    main()
