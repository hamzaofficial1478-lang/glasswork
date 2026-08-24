"""
ask_tests.py — run: python -m coordination.ask_tests

The owner looked at the sector traffic and said the messages were the same every
time — that they were being delivered, not thought. He was right: the trigger
and the evidence came from rows, but the wording and the choice of who to ask
were fixed text, so an agent could never notice a problem was really Content's,
or judge something too small to interrupt anyone over.

What is protected here is the fix AND its limits.

The fix: a sector's own brain decides whether to ask, who owns it, and what to
say. So there are tests that it can redirect an ask to a different sector, and
that it can decline to send one at all.

The limit, which matters more: A MODEL MAY REASON, IT MAY NOT SUPPLY FACTS.
Every fact is gathered from rows by the caller and attached by code, underneath
whatever the model wrote. A model that invents a figure cannot remove or edit
the evidence it is supposed to be about. Several tests below feed a deliberately
lying model and check the rows still arrive intact.

And the fallback: with no API key configured at all, the sectors must still
coordinate. They use the original wording and the request says so, rather than
falling silent — an agency with no model is quieter, not broken.

No network: the brain is replaced with a stub.
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from . import ask
from . import board

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
        con.commit(); con.close()
        board.DB = str(db)
        board._MIGRATED.discard(str(db))
        return self

    def __exit__(self, *a):
        board.DB = self.real

    def threads(self):
        c = sqlite3.connect(board.DB)
        c.row_factory = sqlite3.Row
        try:
            out = []
            for r in c.execute("SELECT * FROM requests"):
                msgs = [dict(m) for m in c.execute(
                    "SELECT * FROM request_msgs WHERE request_id=?", (r["id"],))]
                out.append({**dict(r), "msgs": msgs})
            return out
        finally:
            c.close()


class _Brain:
    """Stands in for the sector's routed model. Records what it was asked."""

    def __init__(self, reply, ok=True):
        self.reply, self.ok, self.seen = reply, ok, []

    def install(self):
        import providers
        self.real_call = providers.call_task
        self.real_resolve = providers.resolve_task

        def call_task(task, messages, max_tokens=512):
            self.seen.append({"task": task, "messages": messages})
            if not self.ok:
                return {"ok": False, "error": "the model fell over"}
            body = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
            return {"ok": True, "output": body, "used": {"model": "stub"}}

        providers.call_task = call_task
        providers.resolve_task = lambda task: {"ok": True}
        return self

    def remove(self):
        import providers
        providers.call_task = self.real_call
        providers.resolve_task = self.real_resolve


FACTS = ["Google's exact words: 403 insufficient permissions",
         "striking-distance queries available this pass: 0"]


def test_it_writes_the_ask_in_its_own_words():
    print("\nA sector with a brain words its own request:")
    with _Cabinet() as cab:
        b = _Brain({"ask": True, "to": "IT",
                    "subject": "Search Console is refusing us",
                    "body": "We are blind on the queries that already reach the site.",
                    "why": "it blocks the cheapest work we have"}).install()
        try:
            r = ask.send("SEO", "IT", "Search Console returned nothing", FACTS,
                         "FALLBACK SUBJECT", "FALLBACK BODY")
        finally:
            b.remove()
        check("it was posted", r["posted"], True)
        check("in its own words, not the template", r["reasoned"], True)
        check("with its own subject", r["subject"], "Search Console is refusing us")
        t = cab.threads()[0]
        check("the thread carries that subject", t["subject"], "Search Console is refusing us")
        body = t["msgs"][0]["body"]
        check("the model's wording is in the body",
              "We are blind on the queries" in body, True)
        check("and it is marked as the sector's own words",
              "SEO worded this itself" in body, True)
        check("including why it decided that",
              "cheapest work" in body, True)
        # It was asked as SEO, with SEO's brain — not the MD's.
        check("it used the SEO brain, not somebody else's", b.seen[0]["task"], "seo")


def test_the_model_never_supplies_the_facts():
    """
    The rule the whole design rests on. A model that lies must not be able to
    change what the request is evidence OF.
    """
    print("\nA model may reason. It may not supply evidence:")
    with _Cabinet() as cab:
        liar = _Brain({"ask": True, "to": "IT", "subject": "All fine",
                       "body": "Search Console returned 4,182 queries and everything "
                               "is healthy. No action needed.",
                       "why": "invented"}).install()
        try:
            ask.send("SEO", "IT", "Search Console returned nothing", FACTS,
                     "FALLBACK", "FALLBACK")
        finally:
            liar.remove()
        body = cab.threads()[0]["msgs"][0]["body"]
        for f in FACTS:
            check(f"the real row survives: {f[:38]}…", f in body, True)
        check("the rows are labelled as coming from the cabinet",
              "not written by a model" in body, True)
        check("and the model's invented figure sits above them, not instead",
              body.index("4,182") < body.index(FACTS[0]), True)


def test_it_can_send_it_somewhere_else():
    print("\nIt can decide another sector actually owns it:")
    with _Cabinet() as cab:
        b = _Brain({"ask": True, "to": "Content",
                    "subject": "These are catalogue gaps, not site faults",
                    "body": "The pages exist; the service list does not mention them.",
                    "why": "nothing here needs a code change"}).install()
        try:
            r = ask.send("SEO", "IT", "Things on the site with no catalogue entry",
                         ["found on the site, not in the catalogue: glass pergola"],
                         "FALLBACK", "FALLBACK")
        finally:
            b.remove()
        check("it went to Content, not IT", r["to"], "Content")
        check("and it says it redirected", r["redirected"], True)
        check("the thread really is addressed to Content",
              cab.threads()[0]["to_sector"], "Content")
        check("and the body records the redirect",
              "decided this is yours" in cab.threads()[0]["msgs"][0]["body"], True)


def test_it_can_decide_not_to_ask_at_all():
    print("\nIt can judge something not worth interrupting anyone over:")
    with _Cabinet() as cab:
        b = _Brain({"ask": False, "to": "Leads", "subject": "-", "body": "-",
                    "why": "no replies yet is simply where the business is"}).install()
        try:
            r = ask.send("SEO", "Leads", "No customer wording on file",
                         ["replies on file to read: 0"], "FALLBACK", "FALLBACK")
        finally:
            b.remove()
        check("nothing was posted", r["posted"], False)
        check("nobody was interrupted", cab.threads(), [])
        check("but the reason is reported, not swallowed",
              "simply where the business is" in r["why"], True)


def test_a_blocking_ask_goes_anyway():
    print("\nA request another sector is already waiting on is not optional:")
    with _Cabinet() as cab:
        b = _Brain({"ask": False, "to": "Security", "subject": "-", "body": "-",
                    "why": "looks fine to me"}).install()
        try:
            r = ask.send("Leads", "Security", "Clear this lead before contact",
                         ["acme-glass.ae"], "Clear this lead?", "Please check.",
                         force=True)
        finally:
            b.remove()
        check("it was posted regardless", r["posted"], True)
        check("and Security has it", cab.threads()[0]["to_sector"], "Security")


def test_no_brain_means_the_template_and_it_says_so():
    print("\nWith no model configured the sectors still coordinate:")
    with _Cabinet() as cab:
        import providers
        real = providers.resolve_task
        providers.resolve_task = lambda task: {"ok": False, "error": "no model routed"}
        try:
            r = ask.send("SEO", "IT", "Search Console returned nothing", FACTS,
                         "Search Console is not answering", "The pass ran without it.")
        finally:
            providers.resolve_task = real
        check("it is still posted", r["posted"], True)
        check("using the template", r["reasoned"], False)
        body = cab.threads()[0]["msgs"][0]["body"]
        check("the original wording is used", "The pass ran without it." in body, True)
        check("it admits the words are not the sector's own",
              "composed from a template" in body, True)
        check("and says exactly why", "no model is routed to SEO" in body, True)
        check("the evidence is attached either way", FACTS[0] in body, True)


def test_a_broken_brain_falls_back_rather_than_going_quiet():
    print("\nA model that falls over must not silence the sector:")
    with _Cabinet() as cab:
        b = _Brain(None, ok=False).install()
        try:
            r = ask.send("SEO", "IT", "s", FACTS, "SUBJ", "BODY")
        finally:
            b.remove()
        check("the request still goes", r["posted"], True)
        check("from the template", r["reasoned"], False)
        check("naming the failure",
              "did not answer" in cab.threads()[0]["msgs"][0]["body"], True)


def test_rubbish_from_the_model_is_not_trusted():
    print("\nA reply that is not the shape asked for is not used as the message:")
    with _Cabinet() as cab:
        b = _Brain("Sure! Here's what I'd say: just ask them nicely.").install()
        try:
            r = ask.send("SEO", "IT", "s", FACTS, "SUBJ", "BODY")
        finally:
            b.remove()
        check("the template is used instead", r["reasoned"], False)
        check("and it says the reply was not trusted",
              "not in the shape asked for" in cab.threads()[0]["msgs"][0]["body"], True)


def test_it_cannot_invent_a_sector_that_does_not_exist():
    print("\nIt may redirect, but only to a room that exists:")
    with _Cabinet() as cab:
        b = _Brain({"ask": True, "to": "Legal", "subject": "s", "body": "b",
                    "why": "w"}).install()
        try:
            r = ask.send("SEO", "IT", "s", FACTS, "SUBJ", "BODY")
        finally:
            b.remove()
        check("an unknown sector falls back to the intended one", r["to"], "IT")
        check("so the request cannot end up addressed to nobody",
              cab.threads()[0]["to_sector"], "IT")


def main():
    for t in (test_it_writes_the_ask_in_its_own_words,
              test_the_model_never_supplies_the_facts,
              test_it_can_send_it_somewhere_else,
              test_it_can_decide_not_to_ask_at_all,
              test_a_blocking_ask_goes_anyway,
              test_no_brain_means_the_template_and_it_says_so,
              test_a_broken_brain_falls_back_rather_than_going_quiet,
              test_rubbish_from_the_model_is_not_trusted,
              test_it_cannot_invent_a_sector_that_does_not_exist):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
    else:
        print("all passed")
    return not _fail


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
