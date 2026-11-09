"""
chat_actions_tests.py — run: python -m coordination.chat_actions_tests

"why they cannot record ticket from chat? ... make all sector and their agents
 like this so they can post and record the ticket from the chat ... where ever
 the dummy mail is showing replace it with our orignal mail"

What has to hold:

  any desk can record    a ticket, an order for any sector, a text change
  it is checked          by Python, not trusted from the model
  his word is his yes    a change is approved at once only when what it puts
                         on the site is in his own message
  the dummy email        every address on his own domains, on every page and
                         the shared footer, becomes his real one — in one pass,
                         other people's addresses untouched
  he is not ignored      "the email on my website is a dummy" is recorded even
                         when the model forgets to

A throwaway cabinet, a fake website, a fake model. Nothing leaves this machine.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

from . import agent_chat, board, chat_actions

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
        self.real = (board.DB, agent_chat.DB)
        d = Path(tempfile.mkdtemp())
        db = d / "agency.db"
        con = sqlite3.connect(db)
        con.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
        con.commit()
        con.close()
        board.DB = agent_chat.DB = str(db)
        board.ensure_schema()
        return self

    def __exit__(self, *a):
        board.DB, agent_chat.DB = self.real

    def sql(self, q, args=()):
        c = sqlite3.connect(board.DB)
        c.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in c.execute(q, args)]
        finally:
            c.close()


def test_any_desk_records_what_he_asks_for():
    print("\nA desk records his ticket and his order, checked:")
    with _Cabinet() as cab:
        reply = ("That footer address is dead, so enquiries vanish.\n"
                 'ACTION: {"do": "ticket", "title": "Dead email in the footer", '
                 '"detail": "footer shows an address on a domain with no mail"}\n'
                 'ACTION: {"do": "order", "sector": "Content", "title": "Write a post on '
                 'shower glass", "detail": "800 words, his photos"}')
        r = chat_actions.take(reply, "managing-director", "sort the footer and write a post")
        check("the ACTION lines are not shown to him",
              "ACTION:" in r["reply"], False)
        check("both recorded", len(r["done"]), 2)
        t = cab.sql("SELECT kind, found_by, status, detail FROM site_issues")[0]
        check("a ticket for IT, from the desk he spoke to",
              (t["status"], t["found_by"]), ("open", "managing-director"))
        check("  saying he asked for it", "the owner asked for this in the chat" in t["detail"], True)
        task = cab.sql("SELECT sector, created_by, title FROM tasks")[0]
        check("an order on the right sector's board, as his",
              (task["sector"], task["created_by"]), ("Content", "owner"))
        check("the reply says what was recorded", "Recorded:" in r["reply"], True)

    print("\n...and what does not pass the checks is not recorded:")
    with _Cabinet():
        r = chat_actions.take('ACTION: {"do": "order", "sector": "Sales", "title": "x"}\n'
                              'ACTION: {"do": "delete_site"}', "it-fixer", "hi")
        check("nothing recorded", r["done"], [])
        check("and he is told why", len(r["refused"]), 2)


def test_his_word_is_his_yes_for_a_text_change():
    print("\nA text change he spelled out is approved on his word:")
    with _Cabinet() as cab:
        started = []
        real = chat_actions.start_release
        chat_actions.start_release = lambda: started.append(1) or True
        try:
            r = chat_actions.take('ACTION: {"do": "replace", "find": "Call us on 04 000", '
                                  '"with": "Call us on 04 123 4567"}', "it-fixer",
                                  "change the phone line to Call us on 04 123 4567")
            row = cab.sql("SELECT status, detail FROM site_queue")[0]
            check("approved at once", row["status"], "approved")
            check("and IT is started to put it on the site", started, [1])
            r2 = chat_actions.take('ACTION: {"do": "replace", "find": "Opening hours 9-5", '
                                   '"with": "Opening hours 8-6"}', "it-fixer", "fix the hours")
            row2 = cab.sql("SELECT status FROM site_queue ORDER BY id DESC")[0]
            check("text he did not give waits for his yes", row2["status"], "pending")
            r3 = chat_actions.take('ACTION: {"do": "replace", "find": "<footer>", '
                                   '"with": "<script>x</script>"}', "it-fixer", "<script>x</script>")
            check("page code cannot be changed from the chat", r3["done"], [])
        finally:
            chat_actions.start_release = real


def test_the_dummy_email_is_his_address_everywhere():
    print("\nThe email on his website: every address on his domains becomes his:")
    from pipelines import site_api, it_release
    footer = ('<p class="footer-info"><a href="mailto:info@worlddealtechnicalservices.ae">'
              'info@worlddealtechnicalservices.ae</a></p><img src="logo@2x.png">')
    pages = {"index.php": '<script type="application/ld+json">{"email": '
                          '"info@worlddealtechnicalservices.com"}</script><?php include "includes/footer.php"; ?>',
             "contact.php": "<p>Write to info@worlddealtechnicalservices.ae or our supplier "
                            "sales@glass-supplier.ae</p>",
             "includes/footer.php": footer}
    writes = []

    def call(path, method="GET", body=None, timeout=25, s=None):
        if path == "/pages" and method == "GET":
            return {"ok": True, "pages": [{"page": p, "bytes": 1, "changed": "t"}
                                          for p in pages if "/" not in p]}
        if path.startswith("/pages/") and method == "GET":
            name = path.split("/", 2)[2]
            if name not in pages:
                return {"ok": False, "error": "no such page", "status": 404}
            return {"ok": True, "page": name, "html": pages[name]}
        if path == "/pages" and method == "POST":
            writes.append(body["page"])
            pages[body["page"]] = body["html"]
            return {"ok": True, "page": body["page"], "backup": "b"}
        return {"ok": False, "error": f"no {method} {path}"}

    from coordination import site
    real = (site_api.call, site_api.settings, site.site_host)
    site_api.call = call
    site_api.settings = lambda env=None: {"mode": "full", "base": "https://x/agency-api",
                                          "token": "t" * 40, "site": "https://x"}
    site.site_host = lambda override="": "www.worlddealtechnicalservices.com"
    try:
        with _Cabinet():
            plan = site_api.plan_emails()
            check("both dummies found, and nothing else",
                  sorted(plan["wrong"]), ["info@worlddealtechnicalservices.ae",
                                          "info@worlddealtechnicalservices.com"])
            r = site_api.use_one_email(agent="it-releaser")
    finally:
        site_api.call, site_api.settings, site.site_host = real
    check("it went", r["ok"], True)
    check("the footer every page shares now has his address",
          pages["includes/footer.php"].count(HIS), 2)
    check("so do the business details for Google", HIS in pages["index.php"], True)
    check("and the contact page", HIS in pages["contact.php"], True)
    check("a supplier's address is left alone", "sales@glass-supplier.ae" in pages["contact.php"], True)
    check("an image name that looks like an address is left alone",
          "logo@2x.png" in pages["includes/footer.php"], True)
    check("each page written once", sorted(writes), ["contact.php", "includes/footer.php", "index.php"])
    check("the Releaser knows how to carry it out", "use_one_email" in it_release.ALLOWED_CALLS, True)


def test_he_is_not_ignored_about_the_email():
    print("\nHe says the website email is a dummy — it is recorded, even if the model forgets:")
    with _Cabinet() as cab:
        started = []
        real = chat_actions.start_release
        chat_actions.start_release = lambda: started.append(1) or True
        try:
            msg = ("the mail on webiste is dummy while our orignal contact mail is " + HIS +
                   " so replace it where ever the dummy mail is showing")
            r = chat_actions.take("I can't point to that dummy email anywhere in my record.",
                                  "it-fixer", msg)
        finally:
            chat_actions.start_release = real
        row = cab.sql("SELECT kind, status, detail FROM site_queue")[0]
        check("a change to make every address his", (row["kind"], row["status"]),
              ("use_one_email", "approved"))
        check("and IT is started on it now", started, [1])
        check("the reply says so", "every email address on your website becomes" in r["reply"], True)


def test_the_chat_carries_it_out():
    print("\nThe chat itself: a desk's ACTION becomes a recorded ticket:")
    import providers
    with _Cabinet() as cab:
        real = (providers.call_task, providers.resolve_task)
        providers.resolve_task = lambda task: {"ok": True}
        providers.call_task = lambda task, msgs, **k: {
            "ok": True, "used": {"model": "fake"},
            "output": ("Logged it for the Fixer.\n"
                       'ACTION: {"do": "ticket", "title": "Header phone is wrong", '
                       '"detail": "the header shows an old number"}')}
        try:
            r = agent_chat.ask("it-fixer", "the phone in the header is wrong, sort it")
        finally:
            providers.call_task, providers.resolve_task = real
        check("recorded", len(r.get("recorded") or []), 1)
        check("the ticket exists", len(cab.sql("SELECT id FROM site_issues")), 1)
        check("and the reply names it", "ticket #" in r["reply"], True)
        prompt_has = []
        providers.resolve_task = lambda task: {"ok": True}
        providers.call_task = lambda task, msgs, **k: (
            prompt_has.append("RECORD WORK ON THE BOARD" in msgs[0]["content"]
                              and HIS in msgs[0]["content"])
            or {"ok": True, "output": "ok"})
        try:
            agent_chat.ask("content-writer", "hello")
        finally:
            providers.call_task, providers.resolve_task = real
        check("every desk is told it can record work, and his real email", prompt_has, [True])


def main():
    for t in (test_any_desk_records_what_he_asks_for,
              test_his_word_is_his_yes_for_a_text_change,
              test_the_dummy_email_is_his_address_everywhere,
              test_he_is_not_ignored_about_the_email,
              test_the_chat_carries_it_out):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        sys.exit(1)
    print("chat actions: all good — any desk records what he asks, checked, and the email is his.")


if __name__ == "__main__":
    main()
