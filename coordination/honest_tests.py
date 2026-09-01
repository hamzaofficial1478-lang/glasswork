"""
honest_tests.py — run: python -m coordination.honest_tests

Four screens that told the owner something untrue, and the reasons they did.

None of these was a crash. Every one of them was a number or a sentence that
looked fine, was wrong, and sent him looking in the wrong place — which is
worse than an error, because an error at least stops you.

  the release gate       red on his machine and green everywhere else, because
                         the test suite read his own .env
  the fallback warning   "no fallback" for five sectors that had one, because
                         SINGLE meant "one model ANSWERED", not "one is set up"
  the health score       40 on a day nothing at all happened
  the check              fifteen minutes, because probes retry
"""
import os
import sqlite3
import tempfile
import time
from pathlib import Path

from . import board, state
from providers import client, integrations, preflight

_fail = []
ROOT = Path(__file__).parents[1]


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


# --- the release gate -------------------------------------------------------


def test_the_suite_can_be_isolated_from_his_own_env_file():
    print("\nHis settings must not decide whether the release gate is green:")
    d = Path(tempfile.mkdtemp())
    envfile = d / ".env"
    envfile.write_text("SMTP_HOST=smtp.hostinger.com\nSITE_URL=https://his-site.ae\n",
                       encoding="utf-8")
    real_path, real_flag = integrations.ENV_PATH, os.environ.pop("AGENCY_IGNORE_DOTENV", None)
    integrations.ENV_PATH = envfile
    try:
        got = integrations.read_env()
        check("without the flag his file is read", got.get("SMTP_HOST"),
              "smtp.hostinger.com")

        os.environ["AGENCY_IGNORE_DOTENV"] = "1"
        got = integrations.read_env()
        check("with it, the file is invisible", got.get("SMTP_HOST"), None)
        check("and SITE_URL with it", got.get("SITE_URL"), None)

        # The real environment still wins — the flag hides the FILE, it does
        # not blindfold the process.
        os.environ["SITE_URL"] = "https://from-the-environment.ae"
        check("but an exported variable still comes through",
              integrations.read_env().get("SITE_URL"), "https://from-the-environment.ae")
        os.environ.pop("SITE_URL")

        # An explicit path is a deliberate act and beats the flag, so the few
        # tests that exist to prove .env behaviour keep working.
        check("an explicit path still reads the file",
              integrations.read_env(path=str(envfile)).get("SMTP_HOST"),
              "smtp.hostinger.com")
    finally:
        integrations.ENV_PATH = real_path
        os.environ.pop("AGENCY_IGNORE_DOTENV", None)
        if real_flag is not None:
            os.environ["AGENCY_IGNORE_DOTENV"] = real_flag


def test_the_setting_helper_goes_dark_with_it_too():
    print("\nAnd the SMTP path, which is what actually turned his gate red:")
    from pipelines import mail
    d = Path(tempfile.mkdtemp())
    envfile = d / ".env"
    envfile.write_text("SMTP_HOST=smtp.hostinger.com\nSMTP_USER=info@his.ae\n",
                       encoding="utf-8")
    real_path = integrations.ENV_PATH
    integrations.ENV_PATH = envfile
    keep = {k: os.environ.pop(k, None) for k in ("SMTP_HOST", "SMTP_USER")}
    # run.py sets the flag for the whole suite, which is the point of it. This
    # test has to see both sides of it, so it puts it back at the end.
    real_flag = os.environ.pop("AGENCY_IGNORE_DOTENV", None)
    try:
        check("his file supplies it", mail.setting("SMTP_HOST"), "smtp.hostinger.com")
        os.environ["AGENCY_IGNORE_DOTENV"] = "1"
        check("the suite sees nothing", mail.setting("SMTP_HOST"), "")
        r = mail._smtp_send({"id": 1, "email": "a@b.ae"}, "Subject\n\nbody")
        check("so 'no SMTP configured' is testable again", r["ok"], False)
        check("and says so", "not configured" in r["error"], True)
    finally:
        integrations.ENV_PATH = real_path
        os.environ.pop("AGENCY_IGNORE_DOTENV", None)
        if real_flag is not None:
            os.environ["AGENCY_IGNORE_DOTENV"] = real_flag
        for k, v in keep.items():
            if v is not None:
                os.environ[k] = v


# --- the fallback warning ---------------------------------------------------


def _chain(*links):
    return {"task": "seo", "chain": list(links),
            "working": sum(1 for l in links if l["ok"] is True),
            "cover": preflight._verdict(list(links))}


def test_a_failing_fallback_is_not_a_missing_one():
    print("\nThe complaint he made twice:")
    r = _chain({"name": "main · gpt-x", "ok": True, "cause": None},
               {"name": "backup · llama-y", "ok": False,
                "cause": "402 — the provider needs payment/credit"})
    check("still flagged", r["cover"], preflight.SINGLE)
    said = preflight._single_brain(r)
    check("it does NOT say there is no fallback",
          "no fallback" in said, False)
    check("it says the fallback is configured and failing",
          "set up and failing" in said, True)
    check("it names the model", "backup · llama-y" in said, True)
    check("and gives the provider's own reason — the thing he asked for",
          "402" in said, True)


def test_genuinely_one_model_still_says_so_plainly():
    print("\nAnd where he really has only one, the old words are right:")
    r = _chain({"name": "main · gpt-x", "ok": True, "cause": None})
    said = preflight._single_brain(r)
    check("named as no fallback", "no fallback" in said, True)
    check("and says what that costs",
          "stops the sector" in said, True)


def test_an_unreached_second_model_is_unknown_not_absent():
    print("\nA model nobody managed to call is not a model he never saved:")
    r = _chain({"name": "main · gpt-x", "ok": True, "cause": None},
               {"name": "backup · llama-y", "ok": None, "cause": "skipped (--quick)"})
    said = preflight._single_brain(r)
    check("reported as unknown", "unknown rather than no" in said, True)
    check("not as missing", "no fallback" in said, False)


# --- the health score -------------------------------------------------------


def _cabinet():
    d = Path(tempfile.mkdtemp())
    db = d / "agency.db"
    con = sqlite3.connect(db)
    con.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
    con.commit()
    con.close()
    return str(db)


def test_a_day_where_nothing_happened_does_not_score_forty():
    print("\n'no any sector did any work but agency health is still 40 why?':")
    real = board.DB
    board.DB = _cabinet()
    try:
        now = int(time.time())
        day0 = now - 3600
        con = sqlite3.connect(board.DB)
        con.row_factory = sqlite3.Row
        con.execute("INSERT INTO agent_runs(agent,status,started_at,ended_at)"
                    " VALUES('seo-strategist','ok',?,?)", (day0 + 10, day0 + 20))
        con.commit()

        idle, why = state._health(con, "SEO", "seo", day0, now)
        labels = {w["label"]: w["points"] for w in why}
        check("turning up is worth 10, not 30",
              labels.get("Turned up, but nothing came of it"), 10)
        check("and the old label is not used",
              "Ran and finished cleanly" in labels, False)
        check("the total is not 40", idle == 40, False)
        check("it is well under half", idle <= 25, True)

        # Now it actually does something.
        con.execute("INSERT INTO keyword_intel(phrase,seen_at) VALUES('glass dubai',?)",
                    (day0 + 30,))
        con.execute("INSERT INTO tasks(id,sector,title,detail,status,owner,"
                    "created_at,finished_at) VALUES('t1','SEO','a job','d','done',"
                    "'seo-strategist',?,?)", (day0, day0 + 40))
        con.commit()
        did, why2 = state._health(con, "SEO", "seo", day0, now)
        labels2 = {w["label"]: w["points"] for w in why2}
        check("a run that produced scores the full 30",
              labels2.get("Ran and finished cleanly"), 30)
        check("and beats the idle day", did > idle, True)
        con.close()
    finally:
        board.DB = real


def test_a_deep_unscored_pile_stops_paying_a_dividend():
    print("\n45 unscored calls is not a reason for free marks:")
    real = board.DB
    board.DB = _cabinet()
    try:
        now = int(time.time())
        day0 = now - 3600
        con = sqlite3.connect(board.DB)
        con.row_factory = sqlite3.Row
        con.execute("INSERT INTO agent_runs(agent,status,started_at,ended_at)"
                    " VALUES('seo-strategist','ok',?,?)", (day0 + 10, day0 + 20))
        for i in range(6):
            con.execute("INSERT INTO decisions(cycle,reasoning,expectation,"
                        "confidence,created_at) VALUES('seo-gap','r','e',0.7,?)",
                        (day0 + i,))
        con.commit()
        _sc, why = state._health(con, "SEO", "seo", day0, now)
        row = next(w for w in why if w["label"] == "Not yet judged")
        check("no free points", row["points"], 0)
        check("and it says what to do about it",
              "still" in row["detail"] and "unscored" in row["detail"], True)
        con.close()
    finally:
        board.DB = real


# --- the fifteen-minute check -----------------------------------------------


def test_a_probe_asks_once():
    print("\nA probe asks what is true now — retrying does not make it truer:")
    calls = []

    class _Reply:
        status_code, text, headers = 429, "{}", {}

    def stub(url, headers=None, data=None, timeout=None):
        calls.append(1)
        return _Reply()

    real = client.requests.post
    client.requests.post = stub
    try:
        cred = {"name": "k", "api_key": "sk-x"}
        model = {"id": "m", "api_model_id": "m-1", "category": "LLM"}
        client.probe_model(cred, model, timeout=1)
        check("one call, not three", len(calls), 1)

    finally:
        client.requests.post = real


def test_real_work_still_retries():
    print("\nBut the work itself still comes back after a moment:")
    calls = []

    class _Reply:
        status_code, text, headers = 429, "{}", {}

    def stub(url, headers=None, data=None, timeout=None):
        calls.append(1)
        return _Reply()

    real_post, real_sleep = client.requests.post, client.time.sleep
    client.requests.post = stub
    client.time.sleep = lambda s: None
    try:
        client.chat({"name": "k", "api_key": "sk-x"},
                    {"id": "m", "api_model_id": "m-1"},
                    [{"role": "user", "content": "x"}], timeout=1)
        check("three attempts on the real path", len(calls), client.MOST_TRIES)
    finally:
        client.requests.post = real_post
        client.time.sleep = real_sleep


def main():
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        raise SystemExit(1)
    print("honest: all good — the screens say what is actually true.")


if __name__ == "__main__":
    main()
