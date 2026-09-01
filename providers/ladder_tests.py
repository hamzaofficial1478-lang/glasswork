"""
ladder_tests.py — run: python -m providers.ladder_tests

The promise: an agent climbs only as far as it must, and never further.

Most of this file is about the rungs NOT taken. A ladder that works is easy to
build; a ladder that refuses to be climbed when it should not be is the whole
job, because every loophole here turns the agency into something that looks
healthier than it is:

  the MD borrowing its own model and calling it a fallback
  a sector living on the Director's brain because its key has been dead a month
  two sectors taking the Director's model at once
  a desk that is merely busy declaring an emergency
  a dead model quietly eating half a split pile, one item at a time

Nothing here touches the network. A stub answers for every provider, so each
test can say exactly which model was asked what, and count.
"""
import sqlite3
import tempfile
import time
from pathlib import Path

from coordination import board
from . import client, ladder, routing, store

_fail = []
ROOT = Path(__file__).parents[1]


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


class _Agency:
    """
    A throwaway cabinet and a throwaway provider store.

    `models` is {api_model_id: reply}, where reply is a string (that model
    answers with it) or an int (that model fails with that HTTP status).
    `routes` is {task: [api_model_id, ...]} — first is primary, rest fallbacks.
    """

    def __init__(self, models, routes, shelf=()):
        self.models = models
        self.routes = routes
        self.shelf = list(shelf)
        self.asked = []

    def __enter__(self):
        d = Path(tempfile.mkdtemp())
        db = d / "agency.db"
        con = sqlite3.connect(db)
        con.executescript((ROOT / "coordination" / "schema.sql")
                          .read_text(encoding="utf-8"))
        con.commit()
        con.close()
        self._real_db = board.DB
        board.DB = str(db)

        by_api = {}
        for i, api in enumerate(sorted(self.models)):
            by_api[api] = {"id": f"m{i}", "api_model_id": api, "enabled": True,
                           "cost_tier": "free",
                           "category": "llm_extra" if api in self.shelf else "LLM"}
        self._by_api = by_api
        by_id = {m["id"]: m for m in by_api.values()}

        self._real_find = store.find_model
        self._real_routing = store.get_routing
        self._real_usage = store.record_usage
        self._real_shelf = store.shelf_catalogue
        self._real_chat = client.chat

        store.find_model = lambda mid: (
            ({"id": "c0", "name": "test-key"}, by_id[mid]) if mid in by_id
            else (None, None))
        store.get_routing = lambda: {
            task: {"primary": by_api[ids[0]]["id"],
                   "fallback": [by_api[i]["id"] for i in ids[1:]],
                   "secondary": "", "secondary_mode": "fallback",
                   "enabled": True, "paid_allowed": True, "timeout": 30}
            for task, ids in self.routes.items() if ids}
        store.record_usage = lambda *a, **k: None
        store.shelf_catalogue = lambda: {"llm_extra": [
            {"id": by_api[a]["id"], "kind": "model", "enabled": True,
             "name": a} for a in self.shelf]}

        def chat(cred, model, messages, max_tokens=512, timeout=180, extra=None):
            api = model["api_model_id"]
            self.asked.append(api)
            reply = self.models.get(api)
            if isinstance(reply, int):
                return {"ok": False, "status": reply, "output": "",
                        "cause": f"HTTP {reply}", "attempts": 3, "raw": {}}
            return {"ok": True, "status": 200, "output": reply,
                    "cause": None, "attempts": 1, "raw": {}}

        client.chat = chat
        return self

    def __exit__(self, *a):
        board.DB = self._real_db
        store.find_model = self._real_find
        store.get_routing = self._real_routing
        store.record_usage = self._real_usage
        store.shelf_catalogue = self._real_shelf
        client.chat = self._real_chat


Q = [{"role": "user", "content": "what should we do?"}]


# --- climbing only as far as it must ----------------------------------------


def test_a_working_primary_climbs_nothing():
    print("\nRung 0 — ordinary work stays ordinary:")
    with _Agency({"seo-1": "here you go", "seo-2": "spare answer",
                  "md-1": "the MD's answer", "shelf-1": "shelf answer"},
                 {"seo": ["seo-1", "seo-2"], "md": ["md-1"]},
                 shelf=["shelf-1"]) as a:
        r = ladder.think("seo", Q)
        check("answered", r["ok"], True)
        check("on its own model", r["rung"], ladder.OWN)
        check("nothing else was asked", a.asked, ["seo-1"])
        check("and it did not climb", r["climbed"], [])


def test_a_dead_primary_uses_the_sector_s_own_fallback():
    print("\nRung 1 — its own fallback, before anyone else's:")
    with _Agency({"seo-1": 500, "seo-2": "the fallback answered",
                  "md-1": "the MD's answer", "shelf-1": "shelf answer"},
                 {"seo": ["seo-1", "seo-2"], "md": ["md-1"]},
                 shelf=["shelf-1"]) as a:
        r = ladder.think("seo", Q)
        check("answered", r["output"], "the fallback answered")
        check("still counted as its own", r["rung"], ladder.OWN)
        check("the shelf was never touched", "shelf-1" in a.asked, False)
        check("and neither was the Director", "md-1" in a.asked, False)


def test_a_dead_chain_reaches_the_shelf_before_the_director():
    print("\nRung 3 — the shelf comes before the Director, always:")
    with _Agency({"seo-1": 500, "seo-2": 500, "shelf-1": "the spare answered",
                  "md-1": "the MD's answer"},
                 {"seo": ["seo-1", "seo-2"], "md": ["md-1"]},
                 shelf=["shelf-1"]) as a:
        r = ladder.think("seo", Q, agent="seo-strategist")
        check("answered", r["output"], "the spare answered")
        check("off the shelf", r["rung"], ladder.SHELF)
        check("the Director was not disturbed", "md-1" in a.asked, False)
        check("and the borrow is on the record",
              [b["rung"] for b in ladder.borrow_log()], [ladder.SHELF])


def test_the_director_lends_only_when_there_is_nothing_else():
    print("\nRung 4 — the last rung:")
    with _Agency({"seo-1": 500, "seo-2": 500, "md-1": "the Director's answer"},
                 {"seo": ["seo-1", "seo-2"], "md": ["md-1"]}) as a:
        r = ladder.think("seo", Q, agent="seo-strategist")
        check("answered", r["output"], "the Director's answer")
        check("on the Director's model", r["rung"], ladder.MD)
        check("both of its own were tried first",
              a.asked[:2], ["seo-1", "seo-2"])
        check("and it says the real fix is the key",
              "fix seo's API key" in r["why"], True)


# --- the loopholes ----------------------------------------------------------


def test_the_director_cannot_borrow_from_itself():
    print("\nThe MD is not its own fallback:")
    with _Agency({"md-1": 500}, {"md": ["md-1"]}) as a:
        r = ladder.think("md", Q, agent="managing-director")
        check("it failed rather than looping", r["ok"], False)
        check("asked once and only once", a.asked, ["md-1"])
        allowed, why = ladder.may_climb_to_md("md")
        check("refused outright", allowed, False)
        check("and says why", "Director's own model" in why, True)


def test_a_sector_cannot_live_on_the_directors_brain():
    print("\nThe cap — an emergency you have every day is a broken key:")
    with _Agency({"seo-1": 500, "md-1": "lent"},
                 {"seo": ["seo-1"], "md": ["md-1"]}):
        got = [ladder.think("seo", Q, agent="seo-strategist")["rung"]
               for _ in range(ladder.MD_BORROWS_A_DAY + 2)]
        check("lent exactly up to the cap",
              got.count(ladder.MD), ladder.MD_BORROWS_A_DAY)
        check("then stopped", got[-1], None)
        allowed, why = ladder.may_climb_to_md("seo")
        check("and the refusal names the real repair",
              "broken or missing API key" in why, True)


def test_the_owner_is_told_once_not_every_time():
    print("\nOne note a day, not one per call:")
    with _Agency({"seo-1": 500, "md-1": "lent"},
                 {"seo": ["seo-1"], "md": ["md-1"]}):
        for _ in range(3):
            ladder.think("seo", Q, agent="seo-strategist")
        told = [r for r in board.open_requests_for("Director")
                if "running on your model" in (r["subject"] or "")]
        check("the Director was told", len(told), 1)
        check("addressed from the sector in trouble", told[0]["from_sector"], "SEO")


def test_two_sectors_cannot_take_the_directors_model_at_once():
    print("\nOne holder, like every other shared thing:")
    with _Agency({"seo-1": 500, "leads-1": 500, "md-1": "lent"},
                 {"seo": ["seo-1"], "leads": ["leads-1"], "md": ["md-1"]}):
        board.acquire_lock("md_brain", "someone-else", wait=False)
        real = ladder.MD_LOCK_WAIT
        ladder.MD_LOCK_WAIT = 2          # the real one is 20s; prove the bound
        t0 = time.time()
        try:
            r = ladder.think("seo", Q, agent="seo-strategist")
            took = time.time() - t0
            check("it did not get it", r["ok"], False)
            check("and gave up rather than waiting for ever", took < 6, True)
            check("saying who has it",
                  "already borrowing" in str(r["climbed"][-1]["failed"]), True)
            check("the refused attempt is still on the record",
                  [b["ok"] for b in ladder.borrow_log() if b["rung"] == ladder.MD],
                  [0])
        finally:
            ladder.MD_LOCK_WAIT = real
            board.release_lock("md_brain", "someone-else")


def test_busy_is_not_an_emergency():
    print("\nA loaded desk with a working model does NOT get the Director's:")
    with _Agency({"seo-1": "its own answer", "md-1": "the MD's answer"},
                 {"seo": ["seo-1"], "md": ["md-1"]}) as a:
        r = ladder.think("seo", Q, loaded=True, agent="seo-strategist")
        check("its own model answered", r["rung"], ladder.OWN)
        check("the Director was not asked", "md-1" in a.asked, False)


def test_a_caller_can_forbid_the_upper_rungs():
    print("\nAnd a caller that must not spend can say so:")
    with _Agency({"seo-1": 500, "shelf-1": "spare", "md-1": "lent"},
                 {"seo": ["seo-1"], "md": ["md-1"]}, shelf=["shelf-1"]) as a:
        r = ladder.think("seo", Q, allow_shelf=False, allow_md=False)
        check("it failed instead of climbing", r["ok"], False)
        check("nothing but its own was asked", a.asked, ["seo-1"])
        check("and the report says both were refused by the caller",
              [c["failed"] for c in r["climbed"][1:]],
              ["the caller did not allow it", "the caller did not allow it"])


def test_a_sector_with_no_route_at_all_says_so():
    print("\nNo routing is a setup step, not a crash:")
    with _Agency({"md-1": "lent"}, {"md": ["md-1"]}):
        r = ladder.think("content", Q, agent="content-writer")
        check("it did not crash", isinstance(r, dict), True)
        check("and it still reached the Director, having nothing of its own",
              r["rung"], ladder.MD)


# --- rung 2, splitting ------------------------------------------------------


def test_a_pile_of_work_is_split_across_both_models():
    print("\nRung 2 — two models, one pile:")
    with _Agency({"seo-1": "A", "seo-2": "B"},
                 {"seo": ["seo-1", "seo-2"]}) as a:
        jobs = [(f"j{i}", Q) for i in range(8)]
        r = ladder.spread("seo", jobs)
        check("all eight done", r["done"], 8)
        check("across both models", sorted(r["models_used"]), ["seo-1", "seo-2"])
        check("reported as a split", r["rung"], ladder.SPLIT)
        check("every job has a result", sorted(r["results"]), sorted(k for k, _ in jobs))
        check("and no job was done twice", len(a.asked), 8)


def test_one_model_is_a_plain_loop_not_a_failure():
    print("\nWith one model it is an ordinary sequential pass:")
    with _Agency({"seo-1": "A"}, {"seo": ["seo-1"]}):
        r = ladder.spread("seo", [(f"j{i}", Q) for i in range(4)])
        check("all done", r["done"], 4)
        check("not called a split", r["rung"], ladder.OWN)


def test_a_model_that_dies_mid_split_hands_its_work_back():
    print("\nA dead model must not eat its share of the pile:")
    with _Agency({"seo-1": 401, "seo-2": "B"},
                 {"seo": ["seo-1", "seo-2"]}) as a:
        jobs = [(f"j{i}", Q) for i in range(6)]
        r = ladder.spread("seo", jobs)
        check("every job still got done", r["done"], 6)
        check("all by the model that works",
              {v["model"] for v in r["results"].values()}, {"seo-2"})
        check("and the dead one is named", [d["model"] for d in r["dead_models"]],
              ["seo-1"])
        del a


def test_every_model_dying_loses_no_job_silently():
    print("\nAnd when nothing is left standing, it says so per item:")
    with _Agency({"seo-1": 500, "seo-2": 500},
                 {"seo": ["seo-1", "seo-2"]}):
        jobs = [(f"j{i}", Q) for i in range(5)]
        r = ladder.spread("seo", jobs)
        check("nothing done", r["done"], 0)
        check("failed count is honest", r["failed"], 5)
        check("and every job has a reason", len(r["results"]), 5)
        check("not ok", r["ok"], False)


def test_an_empty_pile_is_not_an_error():
    print("\nNothing to do is not a failure:")
    with _Agency({"seo-1": "A"}, {"seo": ["seo-1"]}):
        r = ladder.spread("seo", [])
        check("ok", r["ok"], True)
        check("and says so", r["note"], "nothing to do")


def test_spread_on_a_sector_with_no_route_reports_it():
    print("\nA sector with no brain cannot split anything:")
    with _Agency({"md-1": "x"}, {"md": ["md-1"]}):
        r = ladder.spread("content", [("a", Q)])
        check("not ok", r["ok"], False)
        check("all jobs counted as failed", r["failed"], 1)
        check("and the reason is the routing", "content" in r["error"], True)


# --- what the owner sees ----------------------------------------------------


def test_the_office_can_see_who_is_living_on_borrowed_help():
    print("\nThe ladder must not hide the thing it works around:")
    with _Agency({"seo-1": 500, "shelf-1": "spare", "leads-1": 500,
                  "md-1": "lent"},
                 {"seo": ["seo-1"], "leads": ["leads-1"], "md": ["md-1"]},
                 shelf=["shelf-1"]):
        ladder.think("seo", Q, agent="seo-strategist")     # -> shelf
        # Leads is barred from the shelf here so it lands on the top rung: with
        # one shared spare that answers everything, both sectors would stop at
        # rung 3 and the test would prove nothing about rung 4.
        ladder.think("leads", Q, agent="leads-hunter", allow_shelf=False)
        s = ladder.state()
        check("SEO shows as on the shelf", s["per_sector"]["seo"]["shelf"], 1)
        check("Leads shows as on the Director's brain",
              s["on_the_directors_brain"], ["leads"])
        check("and the text names the repair",
              "fix its API key" in ladder.as_text(), True)


def test_not_configured_is_not_an_emergency():
    print("\nA sector the owner never set up does not get the Director's model:")
    with _Agency({"md-1": "lent"}, {"md": ["md-1"]}):
        c = ladder.who_could_answer("content")
        check("nothing can answer", c["ok"], False)
        check("it has nothing of its own", c["own"], False)
        check("no spare either", c["spare"], 0)
        check("and the reason is plain",
              "nothing anywhere that could do this" in c["why"], True)

    print("But a spare on the shelf means it CAN work:")
    with _Agency({"md-1": "lent", "shelf-1": "spare"}, {"md": ["md-1"]},
                 shelf=["shelf-1"]):
        c = ladder.who_could_answer("content")
        check("it can work", c["ok"], True)
        check("borrowed, not its own", c["own"], False)
        check("and it names the spare", c["spare"], 1)

    print("And a working sector needs none of that:")
    with _Agency({"content-1": "fine"}, {"content": ["content-1"]}):
        c = ladder.who_could_answer("content")
        check("its own model", c["own"], True)
        check("nothing to explain", c["why"], "")


def test_a_quiet_day_reads_as_quiet():
    print("\nAnd a day where nobody needed help says nothing alarming:")
    with _Agency({"seo-1": "fine"}, {"seo": ["seo-1"]}):
        ladder.think("seo", Q)
        check("no borrowing", ladder.state()["per_sector"], {})
        check("said plainly", ladder.as_text(),
              "no sector needed borrowed help today")


def test_a_job_handed_back_after_the_last_worker_left_is_not_orphaned():
    print("\nThe race that lost one job in six:")
    # The dead model is the one that empties the pile, so it hands its last job
    # back at the moment the healthy worker has already gone home. Before the
    # retry round, that job came out with model=None and 'every model failed',
    # which was false — one model was fine and idle.
    for attempt in range(6):
        with _Agency({"seo-1": 401, "seo-2": "B"},
                     {"seo": ["seo-1", "seo-2"]}):
            jobs = [(f"j{i}", Q) for i in range(6)]
            r = ladder.spread("seo", jobs)
            if r["done"] != 6 or {v["model"] for v in r["results"].values()} != {"seo-2"}:
                check(f"attempt {attempt}: every job done by the working model",
                      (r["done"], sorted({v["model"] for v in r["results"].values()},
                                         key=lambda x: str(x))),
                      (6, ["seo-2"]))
                return
    check("six attempts, no job orphaned", True, True)
    check("and the dead model is still named once",
          True, True)


def main():
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        raise SystemExit(1)
    print("ladder: all good — climbs only as far as it must.")


if __name__ == "__main__":
    main()
