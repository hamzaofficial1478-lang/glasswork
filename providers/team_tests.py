"""
team_tests.py — run: python3 -m providers.team_tests

The Claude team: which model each sector thinks with, and what happens when
one does not answer.

THE OWNER'S RULES, EACH PINNED BELOW

  MD and Security on Opus 5, "not higher than this"; IT on Opus 4.8; Content,
  SEO and Leads on Sonnet 5; Report on Haiku 4.5.

  "MD opus 5 did not respond back, next is opus 4.8 ... like opus 4.7, 4.6" —
  each model steps down the ladder, never up.

  A sector in an emergency gets the stronger model and the others give way.
  A model is not a seat, so what is actually handed over is the backup
  account's allowance: MD, Security and IT keep their full model on it, and
  the others step down on the main account first and are capped at Sonnet 5
  on the backup.

  One struggling model moves every sector at once.

Nothing here reaches the real Claude: every call goes to a stand-in.
"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from coordination import board
from . import claude_team as T, ladder, local_cli, routing

_fail = []
ROOT = Path(__file__).parents[1]


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


class _Team:
    """A throwaway cabinet and team file, a stand-in for Claude, and the backup switched as asked."""

    def __init__(self, answers=None, backup=False):
        # answers: {(account, model): "ok" | cause string | callable(model, kw)}
        self.answers, self.backup, self.calls = answers or {}, backup, []

    def __enter__(self):
        d = Path(tempfile.mkdtemp(prefix="team-"))
        self.real = (board.DB, T.CONFIG, local_cli._chat_as, local_cli.available,
                     os.environ.get(T.BACKUP_ENV))
        board.DB = str(d / "agency.db")
        sqlite3.connect(board.DB).close()
        T.CONFIG = d / "claude_team.local.json"
        if self.backup:
            os.environ[T.BACKUP_ENV] = str(d / "backup-login")
        else:
            os.environ.pop(T.BACKUP_ENV, None)
        local_cli.available = lambda: (True, "")
        local_cli._chat_as = self._fake
        return self

    def __exit__(self, *a):
        board.DB, T.CONFIG, local_cli._chat_as, local_cli.available, was = self.real
        if was is None:
            os.environ.pop(T.BACKUP_ENV, None)
        else:
            os.environ[T.BACKUP_ENV] = was

    def _fake(self, cred, model, messages, max_tokens=512, timeout=0, *,
              config_dir="", fallback=(), effort="", exact=False):
        acc = "backup" if config_dir else "main"
        self.calls.append({"account": acc, "model": model, "fallback": list(fallback),
                           "effort": effort, "timeout": timeout, "exact": exact})
        a = self.answers.get((acc, model), self.answers.get(("*", model), "ok"))
        if callable(a):
            a = a(model, fallback)
        if isinstance(a, tuple):           # ("ok", "model that actually ran")
            a, ran = a
        else:
            ran = model
        if a == "ok":
            return {"ok": True, "status": 200, "output": f"answer from {ran}",
                    "text": f"answer from {ran}", "waited": [1.0],
                    "local": {"ran": [ran], "seconds": 1.0,
                              "usage": {"input": 10, "output": 5, "cache_read": 800,
                                        "cache_write": 0, "api_equivalent_usd": 0.001}}}
        return {"ok": False, "status": 408 if "timed out" in a else 1,
                "cause": a, "error": a, "waited": [2.0]}


ASK = [{"role": "user", "content": "rank these"}]
SLOW = "timed out"
LIMIT = "the Claude plan's usage allowance is used up"
BUSY = "the model is overloaded right now"


def test_the_team_is_the_one_he_set():
    print("\nThe team, as the owner set it:")
    with _Team():
        t = T.team()
        check("MD on Opus 5", t["md"], "claude-opus-5")
        check("Security on Opus 5", t["security"], "claude-opus-5")
        check("IT on Opus 4.8", t["it"], "claude-opus-4-8")
        check("Content, SEO and Leads on Sonnet 5",
              (t["content"], t["seo"], t["leads"]), ("claude-sonnet-5",) * 3)
        check("the Critic on Sonnet 5 — a different mind from the MD", t["critic"],
              "claude-sonnet-5")
        check("Report on Haiku 4.5", t["report"], "claude-haiku-4-5")
        check("'director' is the MD's desk", T.model_for("director"), "claude-opus-5")

    print("\nA row saves on its own, and nothing above Opus 5 is taken:")
    with _Team():
        r = T.save_model("seo", "claude-haiku-4-5")
        check("saved", (r["ok"], T.model_for("seo")), (True, "claude-haiku-4-5"))
        check("the rest untouched", T.model_for("content"), "claude-sonnet-5")
        r = T.save_model("it", "claude-opus-5-5")
        check("Opus 5.5 comes down to Opus 5", (r["ok"], r["model"]), (True, "claude-opus-5"))
        check("and says so", "above Opus 5" in r["note"], True)
        check("Fable too", T.save_model("md", "claude-fable-5-1")["model"], "claude-opus-5")
        check("an unknown model is refused", T.save_model("md", "gpt-9")["ok"], False)
        check("so is a sector that does not exist", T.save_model("hr", "claude-sonnet-5")["ok"], False)
        saved = json.loads(T.CONFIG.read_text(encoding="utf-8"))["team"]
        check("the file holds only what he changed", sorted(saved), ["it", "md", "seo"])

    print("\nClaude's dated names are the same model:")
    check("claude-haiku-4-5-20251001 is Haiku 4.5",
          T.clamp("claude-haiku-4-5-20251001"), "claude-haiku-4-5")


def test_the_ladder_steps_down_never_up():
    print("\nEach model's ladder is everything below it:")
    check("Opus 5 -> 4.8 -> 4.7 -> 4.6 -> Sonnet -> Haiku",
          [T.LABEL[m] for m in T.below("claude-opus-5")],
          ["Opus 4.8", "Opus 4.7", "Opus 4.6", "Sonnet 5", "Sonnet 4.6", "Haiku 4.5"])
    check("IT's starts at 4.7", T.below("claude-opus-4-8")[0], "claude-opus-4-7")
    check("Haiku has nowhere lower", T.below("claude-haiku-4-5"), [])

    print("\nThe MD's Opus 5 does not answer — the call steps down to Opus 4.8:")
    with _Team({("main", "claude-opus-5"): SLOW}) as t:
        r = routing.call_task("md", ASK)
        check("answered", r["ok"], True)
        check("by Opus 4.8", r["used"]["model"], "claude-opus-4-8")
        check("and the result says so, in words",
              "asked for Opus 5 and was answered by Opus 4.8" in r["stepped_down"], True)
        check("the failure is on the record", r["failovers"][0]["cause"], SLOW)
        check("and the budget is shared, not handed out fresh",
              [c["exact"] for c in t.calls], [True, True])


def test_one_struggling_model_moves_everyone():
    print("\nOpus 5 times out for the MD; Security starts one rung down without waiting:")
    with _Team({("main", "claude-opus-5"): SLOW}) as t:
        routing.call_task("md", ASK)
        t.calls.clear()
        r = routing.call_task("security", ASK)
        check("Security never tried Opus 5", [c["model"] for c in t.calls], ["claude-opus-4-8"])
        check("it was stepped round, and that is recorded",
              r["skipped"][0]["model"], "claude-opus-5")
        check("the office can see it", T.health()[0]["model"], "claude-opus-5")
        check("IT's own model was never touched", T.down("main", "claude-opus-4-8"), "")
        t.calls.clear()
        T.clear()
        routing.call_task("security", ASK)
        check("once forgotten, Opus 5 is tried at the top again",
              t.calls[0]["model"], "claude-opus-5")

    print("\nWhen the CLI itself had to fall back, everyone steps round too:")
    with _Team({("main", "claude-opus-5"): ("ok", "claude-opus-4-8")}):
        r = routing.call_task("md", ASK)
        check("answered by what actually ran", r["used"]["model"], "claude-opus-4-8")
        check("and Opus 5 is marked busy for the others",
              "overloaded" in T.down("main", "claude-opus-5"), True)


def test_the_main_three_keep_their_model_on_the_backup():
    print("\nThe main account runs out; the MD moves to the backup at Opus 5:")
    with _Team({("main", "claude-opus-5"): LIMIT}, backup=True) as t:
        r = routing.call_task("md", ASK)
        check("answered on the backup account", r["used"]["account"], "backup")
        check("still on Opus 5", r["used"]["model"], "claude-opus-5")
        check("and the step is written down",
              "backup Claude account" in r["stepped_down"], True)
        check("the whole main account is stepped round, not one model",
              "allowance" in T.down("main", "claude-sonnet-5"), True)
        t.calls.clear()
        r = routing.call_task("it", ASK)
        check("IT goes straight to the backup at Opus 4.8",
              [(c["account"], c["model"]) for c in t.calls], [("backup", "claude-opus-4-8")])

    print("\n...and Content does NOT follow it there at full strength:")
    order = None
    with _Team(backup=True):
        order = T.candidates("content")
    main_first = [m for a, m in order if a == "main"]
    on_backup = [m for a, m in order if a == "backup"]
    check("every rung on the main account comes first",
          order[:len(main_first)], [("main", m) for m in main_first])
    check("and on the backup it is Sonnet 5 at most",
          all(T.rank(m) >= T.rank("claude-sonnet-5") for m in on_backup), True)
    with _Team(backup=True):
        md = T.candidates("md")
    check("the MD alternates accounts rung by rung",
          md[:4], [("main", "claude-opus-5"), ("backup", "claude-opus-5"),
                   ("main", "claude-opus-4-8"), ("backup", "claude-opus-4-8")])

    print("\nAn Opus-only limit leaves Sonnet on the same account alone:")
    with _Team({("main", "claude-opus-5"): LIMIT + " — Opus limit reached"}):
        routing.call_task("md", ASK)
        check("Opus 4.8 stepped round", bool(T.down("main", "claude-opus-4-8")), True)
        check("Sonnet 5 still tried", T.down("main", "claude-sonnet-5"), "")


def test_nobody_answering_is_one_honest_sentence():
    print("\nEvery rung fails — the error names what happened and the fix:")
    with _Team({("*", m): BUSY for m in T.LADDER}):
        r = routing.call_task("report", ASK)
        check("not ok", r["ok"], False)
        check("it names the sector", r["error"].startswith("Report could not get an answer"), True)
        check("and the cause", "overloaded" in r["error"], True)
        check("and what would have saved it", "--login-backup" in r["error"], True)

    print("\nA missing install stops at once — the same command serves both accounts:")
    with _Team({("main", "claude-opus-5"): "claude code is not installed here"},
               backup=True) as t:
        r = routing.call_task("md", ASK)
        check("one attempt, not fourteen", len(t.calls), 1)
        check("and the install message is the error", "not installed" in r["error"], True)

    print("\nWith the suite's switch on, nothing is called at all:")
    real = local_cli.available
    os.environ[local_cli.OFF_ENV] = "1"
    try:
        local_cli.available = real
        with _Team() as t:
            local_cli.available = real
            r = routing.call_task("md", ASK)
            check("refused", r["ok"], False)
            check("no call made", t.calls, [])
            check("resolve_task says no too", routing.resolve_task("md")["ok"], False)
    finally:
        local_cli.available = real


def test_the_clock_is_shared_across_attempts():
    print("\nA slow first attempt leaves the second only what is left:")
    import time as _time
    from types import SimpleNamespace
    now = [1_000_000.0]

    def slow_opus(model, fallback):
        now[0] += 300                   # it sat there for five minutes
        return SLOW
    real_time = T.time
    T.time = SimpleNamespace(time=lambda: now[0], strftime=_time.strftime)
    try:
        with _Team({("main", "claude-opus-5"): slow_opus}) as t:
            routing.call_task("md", ASK)
        check("the first attempt had the whole budget", t.calls[0]["timeout"], T.BUDGET)
        check("the second only the remainder", t.calls[1]["timeout"], T.BUDGET - 300)
    finally:
        T.time = real_time


def test_how_hard_each_call_thinks():
    print("\nOrdinary work at medium, the costly judgements at high, Haiku at neither:")
    with _Team() as t:
        routing.call_task("content", ASK)
        routing.deliberate("security", ASK)
        routing.call_task("report", ASK)
    check("ordinary", t.calls[0]["effort"], "medium")
    check("a judgement where being wrong costs money",
          [c["effort"] for c in t.calls[1:3]], ["high", "high"])
    check("Haiku is sent no effort flag", t.calls[3]["effort"], "")

    print("\nThe CLI's own quick step-down gets the next rungs on the same account:")
    check("Opus 5 falls back to 4.8 then 4.7",
          t.calls[1]["fallback"], ["claude-opus-4-8", "claude-opus-4-7"])


def test_a_second_opinion_comes_from_a_different_model():
    print("\nSecurity's clearance is read by Opus 5 AND the Critic's Sonnet 5:")
    with _Team():
        r = routing.deliberate("security", ASK, verdict_of=lambda x: "clear")
        check("two models consulted", r["consulted"], 2)
        check("different ones", [a["model"] for a in r["answers"]],
              ["claude-opus-5", "claude-sonnet-5"])
        check("they agree", r["agreed"], True)

    print("\nIf both answers came from the same model, it is not a cross-check:")
    with _Team({("main", "claude-opus-5"): ("ok", "claude-sonnet-5")}):
        r = routing.deliberate("security", ASK, verdict_of=lambda x: "clear")
        check("counted as one opinion", r["consulted"], 1)
        check("and it says why", "one model asked twice" in r["why"], True)

    print("\nWhen the Critic and the sector share a model, the second is one rung down:")
    with _Team():
        T.save_model("critic", "claude-opus-5")
        check("Opus 5 is checked by Opus 4.8", T.second_model("md"), "claude-opus-4-8")


def test_the_old_doors_still_open():
    print("\nEvery caller that asked the old way gets the same shapes back:")
    with _Team():
        r = routing.resolve_task("content")
        check("resolve_task names the model", r["route"]["primary"], "claude-sonnet-5")
        check("an unknown sector is refused in words",
              "not a sector" in routing.resolve_task("hr")["error"], True)
        th = ladder.think("leads", ASK, agent="leads-writer")
        check("think() answers on its own model", (th["ok"], th["rung"]), (True, "own"))
        sp = ladder.spread("seo", [("a", ASK), ("b", ASK), ("c", ASK)])
        check("spread() does every job", (sp["done"], sp["failed"]), (3, 0))
        check("who_could_answer is cheap and yes", ladder.who_could_answer("content")["ok"], True)
        check("nothing is borrowed any more", ladder.state()["on_the_directors_brain"], [])


def test_every_call_is_counted_per_sector():
    print("\nToday's usage, per sector, from Claude's own report:")
    with _Team():
        routing.call_task("content", ASK)
        routing.call_task("content", ASK)
        routing.call_task("report", ASK)
        u = T.usage()["sectors"]
        check("two calls for Content", u["content"]["calls"], 2)
        check("with their tokens, cached reads included",
              u["content"]["tokens_in"], 2 * (10 + 800))
        check("and the model that answered", u["report"]["models"], ["claude-haiku-4-5"])
        check("a model that answered today counts as proven",
              T.answered_recently("claude-sonnet-5"), True)
        check("one that has not, does not", T.answered_recently("claude-opus-4-6"), False)


def test_the_setup_page_and_preflight_show_the_team():
    print("\nThe Setup page has the team, and none of the old provider screens:")
    html = (ROOT / "ui" / "setup.html").read_text(encoding="utf-8")
    check("the Claude team section", "The Claude team" in html, True)
    check("no 'Add a provider'", "Add a provider" in html, False)
    check("no 'Who runs what'", "Who runs what" in html, False)
    check("the backup sign-in is explained", "--login-backup" in html, True)
    server = (ROOT / "server.js").read_text(encoding="utf-8")
    check("the server has the team routes", '"/api/team"' in server, True)
    check("and not the routing ones", 'app.get("/api/routing"' in server, False)
    check("and the team is behind the Setup lock", '"/api/team", "/api/preflight"' in server, True)


def main():
    for t in (test_the_team_is_the_one_he_set,
              test_the_ladder_steps_down_never_up,
              test_one_struggling_model_moves_everyone,
              test_the_main_three_keep_their_model_on_the_backup,
              test_nobody_answering_is_one_honest_sentence,
              test_the_clock_is_shared_across_attempts,
              test_how_hard_each_call_thinks,
              test_a_second_opinion_comes_from_a_different_model,
              test_the_old_doors_still_open,
              test_every_call_is_counted_per_sector,
              test_the_setup_page_and_preflight_show_the_team):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        sys.exit(1)
    print("all Claude team tests passed")


if __name__ == "__main__":
    main()
