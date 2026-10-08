"""
engine_tests.py — run: python -m providers.engine_tests

"go ahead to phase 3" — Claude, OpenAI or Gemini by the person's own API key,
chosen on the first screen, with the spending cap enforced on every call.

What is protected here:
  - his own copy keeps Claude Code exactly as it was: before accounts, and in
    the account his agency was brought into; a NEW account starts with no
    engine and cannot pick Claude Code with a Claude plan;
  - every sector still asks through claude_team.call and gets the same shape
    back, with the strongest model for the MD and the lightest for Report;
  - a model that fails steps down a tier; a refused key does not;
  - the daily cap stops a call BEFORE it is made, counts both websites, and
    leaves Claude Code's plan figures out of the money;
  - each company's reply is read right: text, refusals, cut-off answers, and
    the tokens priced into dollars;
  - a key is stored sealed and never comes back out — not in the status, not
    from the server;
  - the server keeps the Engine page behind the same lock as Setup.

Nothing here reaches an AI company: every call goes to a stand-in.
"""
import http.client
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

# Never the real cabinet, and never a real Claude Code call, even when run on
# its own (run.py sets both for the whole suite).
os.environ.setdefault("AGENCY_DB", str(Path(tempfile.mkdtemp(prefix="engine-db-")) / "agency.db"))
os.environ["CLAUDE_TEAM_OFF"] = "1"

import requests  # noqa: E402

from coordination import vault, workspace  # noqa: E402

from . import claude_team, engines, local_cli, preflight, routing  # noqa: E402

ROOT = Path(__file__).parents[1]
_fail = []
KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "DAILY_SPEND_CAP_USD")


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def _reply(text="OK", inp=1000, out=200, ran=""):
    def fake(eng, model, system, prompt, room, effort, key, timeout):
        fake.calls.append({"engine": eng, "model": model, "system": system, "prompt": prompt,
                           "room": room, "effort": effort, "key": key})
        return engines._answer(True, text, usage={"input": inp, "output": out},
                               ran=ran or model)
    fake.calls = []
    return fake


class _Place:
    """A data folder of its own, settings read from it, and no stray keys from the computer."""

    KEEP = ("AGENCY_DATA", "AGENCY_IGNORE_DOTENV", "AGENCY_KEY") + KEYS

    def __init__(self, isolated=False):
        self.isolated = isolated

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in self.KEEP}
        self.dir = Path(tempfile.mkdtemp(prefix="engines-"))
        os.environ["AGENCY_DATA"] = str(self.dir / "data")
        for k in ("AGENCY_KEY",) + KEYS:
            os.environ.pop(k, None)
        if self.isolated:
            os.environ["AGENCY_IGNORE_DOTENV"] = "1"
        else:
            os.environ.pop("AGENCY_IGNORE_DOTENV", None)
        self.transport = engines.TRANSPORT
        return self

    def __exit__(self, *a):
        engines.TRANSPORT = self.transport
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def account(self, name="Shop", brought_in=False):
        made = workspace.create_account(name)            # no password: plain files, in-process
        acct = made["account"]["id"]
        site = workspace.add_site(acct, name, "https://example.co.uk")["site"]["id"]
        workspace.open_site(acct, site)
        if brought_in:
            a = workspace.account(acct)
            a["brought_in"] = {"site": site, "at": int(time.time()), "copied": []}
            workspace._save(a)
        return acct, site


def _usage_rows(eng):
    c = engines._conn()
    try:
        return [dict(r) for r in c.execute("SELECT * FROM claude_usage WHERE engine=? ORDER BY id",
                                           (eng,))]
    finally:
        c.close()


def _clear_usage():
    c = engines._conn()
    with c:
        c.execute("DELETE FROM claude_usage")
    c.close()


# ---------------------------------------------------------------------------

def test_his_own_copy_keeps_claude_code():
    print("\nHis own copy keeps Claude Code; a new account starts with no engine:")
    with _Place(isolated=True):
        check("before any account, the engine is Claude Code", engines.engine(), "claude_code")
        check("  and readiness is Claude Code's own", engines.available(),
              local_cli.available())
        r = claude_team.call("md", [{"role": "user", "content": "hi"}])
        check("  a call still goes the Claude Code way", "Claude is switched off" in (r.get("error") or ""),
              True)
        check("  and the team status says so", claude_team.status().get("engine"), "claude_code")
    with _Place() as p:
        p.account("Brought In", brought_in=True)
        check("the account his agency was brought into keeps Claude Code",
              engines.engine(), "claude_code")
        check("  and may choose it", engines.claude_code_allowed(), True)
    with _Place() as p:
        p.account("New Shop")
        check("a new account has not chosen an engine", engines.engine(), "")
        r = claude_team.call("content", [{"role": "user", "content": "hi"}])
        check("  so a call says to choose one", (r["ok"], r.get("cause")),
              (False, "no AI engine chosen"))
        check("  in a sentence", "Engine page" in r["error"], True)
        r = engines.save({"engine": "claude_code"})
        check("  and Claude Code with a Claude plan is refused", r["ok"], False)
        check("    because of Anthropic's terms, said plainly", "Anthropic's" in r["error"], True)
        check("  it is not even offered", [o["id"] for o in engines.status()["options"]],
              ["anthropic", "openai", "gemini"])


def test_choosing_an_engine_and_its_key():
    print("\nChoosing an engine; the key is kept, and never comes back out:")
    with _Place() as p:
        acct, site = p.account()
        r = engines.save({"engine": "anthropic", "key": "short"})
        check("a key that is not a key is refused", r["ok"], False)
        secret = "sk-ant-api03-TESTKEY-not-real-9z8y"
        r = engines.save({"engine": "anthropic", "key": secret, "cap": "7.5"})
        check("a real-looking key is saved", r["ok"], True)
        check("  the engine is chosen", engines.engine(), "anthropic")
        acc_env = (workspace.account_dir(acct) / "account.env").read_text(encoding="utf-8")
        check("  into the person's own settings, not the website's",
              ("ANTHROPIC_API_KEY=" in acc_env,
               "ANTHROPIC_API_KEY" in (workspace.site_dir(acct, site) / "site.env").read_text(encoding="utf-8")),
              (True, False))
        check("  with the cap", "DAILY_SPEND_CAP_USD=7.5" in acc_env, True)
        st = engines.status()
        check("the status never carries the key", secret in json.dumps(st), False)
        check("  only its last four characters", next(o for o in st["options"]
                                                      if o["id"] == "anthropic")["key_hint"], "…" + secret[-4:])
        check("  nor does the team status", secret in json.dumps(claude_team.status()), False)
        check("models: strongest, everyday, lightest",
              engines.models("anthropic"),
              {"strong": "claude-opus-5-5", "standard": "claude-sonnet-5-5", "light": "claude-haiku-5-5"})
        r = engines.save({"engine": "anthropic", "models": {"light": "claude-haiku-4-5", "strong": "bad name!"}})
        check("a model name that is not one is refused", r["ok"], False)
        r = engines.save({"engine": "anthropic", "models": {"light": "claude-haiku-4-5"}})
        check("a different model for a tier is kept", engines.models()["light"], "claude-haiku-4-5")
        r = engines.save({"engine": "anthropic", "models": {"light": "claude-haiku-5-5"}})
        check("  and choosing the suggested one again goes back to the default",
              ((engines._config().get("models") or {}).get("anthropic") or {}).get("light"), None)
        r = engines.save({"engine": "anthropic", "forget_key": True})
        check("forgetting the key removes it", (r["ok"], engines.key("anthropic")), (True, ""))
        check("  and the engine says what is missing", "No Anthropic API key" in engines.available()[1], True)


def test_every_sector_asks_through_the_same_door():
    print("\nEvery sector asks through the same door, and gets the same shape back:")
    with _Place() as p:
        p.account()
        engines.save({"engine": "openai", "key": "sk-proj-TESTKEY-0000-1111"})
        engines.TRANSPORT = fake = _reply("Here is the plan.", inp=2000, out=400)
        _clear_usage()
        r = claude_team.call("md", [{"role": "system", "content": "You are the MD."},
                                     {"role": "user", "content": "Plan the day."}], 512)
        check("the MD is answered", (r["ok"], r["output"]), (True, "Here is the plan."))
        check("  by the strongest model", r["used"]["model"], "gpt-6-astra")
        check("  in the shape every caller reads",
              sorted(k for k in ("ok", "task", "output", "used", "model_asked", "stepped_down",
                                 "failovers", "skipped") if k in r),
              sorted(["ok", "task", "output", "used", "model_asked", "stepped_down", "failovers",
                      "skipped"]))
        check("  the system prompt goes as the system prompt", fake.calls[0]["system"], "You are the MD.")
        check("  with room for the model's thinking as well as the answer", fake.calls[0]["room"] >= 16000,
              True)
        check("  at ordinary effort", fake.calls[0]["effort"], "medium")
        r = claude_team.call("report", [{"role": "user", "content": "Score the day."}])
        check("Report gets the lightest model", r["used"]["model"], "gpt-6-luna")
        r = claude_team.call("seo", [{"role": "user", "content": "x"}])
        check("SEO gets the everyday model", r["used"]["model"], "gpt-6.1-sol")
        rows = _usage_rows("openai")
        check("every call is written down", len(rows), 3)
        check("  with what it cost", rows[0]["api_usd"], engines.cost("openai", "gpt-6-astra",
                                                                    {"input": 2000, "output": 400}))
        check("  which for 2,000 in and 400 out on GPT-6 Astra is $0.04", round(rows[0]["api_usd"], 6), 0.04)
        from . import ladder
        t = ladder.think("leads", [{"role": "user", "content": "x"}])
        check("ladder.think goes the same way", (t["ok"], t["used"]["model"]), (True, "gpt-6.1-sol"))
        check("routing names the API key and the step-down",
              [c["model_id"] for c in routing.resolve_task("md")["candidates"]],
              ["gpt-6-astra", "gpt-6.1-sol", "gpt-6-luna"])
        fake.calls.clear()
        d = claude_team.deliberate("md", [{"role": "user", "content": "Approve?"}], 64)
        check("a judgement that costs money asks two different models",
              sorted(c["model"] for c in fake.calls), ["gpt-6-astra", "gpt-6.1-sol"])
        check("  both thinking harder", {c["effort"] for c in fake.calls}, {"high"})
        check("  and compares them", d["consulted"], 2)
        from coordination import readiness
        b = readiness._brains("md")
        check("readiness shows the engine's model", (b["model"], b["engine"]), ("gpt-6-astra", "openai"))


def test_stepping_down():
    print("\nA model that fails steps down a tier; a refused key does not:")
    with _Place() as p:
        p.account()
        engines.save({"engine": "gemini", "key": "AIzaSy-TESTKEY-000000"})

        def busy_then_ok(eng, model, *a):
            busy_then_ok.calls.append(model)
            if model == "gemini-3.1-pro-preview":
                return engines._answer(False, cause=engines.BUSY, error="503 overloaded", status=503)
            return engines._answer(True, "done", usage={"input": 10, "output": 5}, ran=model)
        busy_then_ok.calls = []
        engines.TRANSPORT = busy_then_ok
        r = claude_team.call("security", [{"role": "user", "content": "Clear this lead?"}])
        check("an overloaded model hands over to the next tier", (r["ok"], r["used"]["model"]),
              (True, "gemini-3.8-flash"))
        check("  and says so", "Gemini 3.1 Pro" in r["stepped_down"] and "overloaded" in r["stepped_down"],
              True)

        def refused(eng, model, *a):
            refused.calls.append(model)
            return engines._answer(False, cause=engines.BAD_KEY, error="401", status=401)
        refused.calls = []
        engines.TRANSPORT = refused
        r = claude_team.call("md", [{"role": "user", "content": "x"}])
        check("a refused key is not tried again on a lighter model", refused.calls,
              ["gemini-3.1-pro-preview"])
        check("  and the error says where to make a new one",
              "aistudio.google.com" in r["error"], True)

        def declined(eng, model, *a):
            if model == "gemini-3.8-flash":
                return engines._answer(False, cause=engines.DECLINED, error="SAFETY")
            return engines._answer(True, "ok", usage={"input": 1, "output": 1}, ran=model)
        engines.TRANSPORT = declined
        r = claude_team.call("content", [{"role": "user", "content": "x"}])
        check("a declined request is tried on the lighter model", r["used"]["model"],
              "gemini-3.5-flash-lite")


def test_the_spending_cap():
    print("\nThe daily cap stops a call before it is made, across both websites:")
    with _Place() as p:
        acct, site = p.account()
        engines.save({"engine": "anthropic", "key": "sk-ant-api03-TESTKEY-cap-0001", "cap": "1"})
        _clear_usage()
        engines.TRANSPORT = fake = _reply("ok", inp=100_000, out=20_000)
        r = claude_team.call("md", [{"role": "user", "content": "x"}])
        spent = engines.ai_spent_today()
        check("a call is made and costs what the tokens cost", round(spent, 4),
              round(engines.cost("anthropic", "claude-opus-5-5", {"input": 100_000, "output": 20_000}), 4))
        from coordination import cabinet
        check("the cabinet's own total counts it", cabinet.spent_today() >= spent, True)
        for _ in range(3):
            claude_team.call("md", [{"role": "user", "content": "x"}])
        before = len(fake.calls)
        r = claude_team.call("md", [{"role": "user", "content": "x"}])
        check("once today's spending reaches the cap, the call is refused",
              (r["ok"], r.get("capped")), (False, True))
        check("  before anything is sent", len(fake.calls), before)
        check("  and the reason says how to go on", "Raise the cap on the Engine page" in r["error"], True)
        check("the morning check stops the day too", any("spending cap" in b for b in
                                                          preflight.sweep(quick=True)["blocking"]), True)
        # Claude Code's figures are what a plan's tokens WOULD cost; not money spent.
        c = engines._conn()
        with c:
            c.execute("DELETE FROM claude_usage")
            c.execute("INSERT INTO claude_usage (day, at, sector, account, model_asked, ok, api_usd,"
                      " engine) VALUES (?,?,?,?,?,?,?,?)",
                      (time.strftime("%Y-%m-%d"), int(time.time()), "md", "main", "claude-opus-5",
                       1, 9.0, None))
        c.close()
        check("Claude Code on his plan is not counted as money", engines.ai_spent_today(), 0.0)

        # Both websites count: the cap is the person's.
        second = workspace.add_site(acct, "Second", "https://second.co.uk")["site"]["id"]
        keep = os.environ.pop("AGENCY_DB")
        try:
            for s_id, usd in ((site, 0.25), (second, 0.5)):
                db = workspace.site_dir(acct, s_id) / "agency.db"
                con = sqlite3.connect(str(db))
                con.execute("CREATE TABLE IF NOT EXISTS claude_usage (day TEXT, api_usd REAL, engine TEXT)")
                con.execute("INSERT INTO claude_usage VALUES (?,?,?)",
                            (time.strftime("%Y-%m-%d"), usd, "anthropic"))
                con.commit()
                con.close()
            check("AI spending on both websites counts against one cap", engines.ai_spent_today(), 0.75)
        finally:
            os.environ["AGENCY_DB"] = keep


def test_reading_each_company():
    print("\nEach company's reply is read right:")
    a = engines.anthropic_parse({"model": "claude-opus-5-5", "stop_reason": "end_turn",
                                 "content": [{"type": "thinking", "thinking": ""},
                                             {"type": "text", "text": "Hello"}],
                                 "usage": {"input_tokens": 10, "output_tokens": 5}}, "claude-opus-5-5")
    check("Claude: the text, not the thinking", (a["ok"], a["text"]), (True, "Hello"))
    a = engines.anthropic_parse({"stop_reason": "refusal", "content": [],
                                 "stop_details": {"category": "cyber"},
                                 "usage": {"input_tokens": 10, "output_tokens": 0}}, "claude-opus-5-5")
    check("Claude: a refusal is a refusal, read before the content", (a["ok"], a["cause"]),
          (False, engines.DECLINED))
    a = engines.anthropic_parse({"model": "claude-opus-5-5", "stop_reason": "end_turn",
                                 "content": [{"type": "text", "text": "Answered"}],
                                 "usage": {"input_tokens": 99, "output_tokens": 99, "iterations": [
                                     {"type": "message", "input_tokens": 100, "output_tokens": 0,
                                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                                     {"type": "fallback_message", "model": "claude-opus-4-8",
                                      "input_tokens": 100, "output_tokens": 50,
                                      "cache_read_input_tokens": 0,
                                      "cache_creation_input_tokens": 0}]}}, "claude-opus-5-5")
    check("Claude: a server-side fallback says which model answered", a["ran"], "claude-opus-4-8")
    check("  and each hop is priced at its own model",
          engines._cost_of("anthropic", a["ran"], a),
          round(engines.cost("anthropic", "claude-opus-5-5", {"input": 100})
                + engines.cost("anthropic", "claude-opus-4-8", {"input": 100, "output": 50}), 6))
    o = engines.openai_parse({"model": "gpt-6.1-sol-2026-09-01", "status": "completed",
                              "output": [{"type": "reasoning", "summary": []},
                                         {"type": "message", "content": [
                                             {"type": "output_text", "text": "Hi "},
                                             {"type": "output_text", "text": "there"}]}],
                              "usage": {"input_tokens": 50, "output_tokens": 20,
                                        "input_tokens_details": {"cached_tokens": 10}}}, "gpt-6.1-sol")
    check("OpenAI: every text part, in order", (o["ok"], o["text"]), (True, "Hi there"))
    check("  the dated name is priced as the model",
          engines.cost("openai", "gpt-6.1-sol-2026-09-01", o["usage"]),
          engines.cost("openai", "gpt-6.1-sol", o["usage"]))
    o = engines.openai_parse({"output": [{"type": "message", "content": [
        {"type": "refusal", "refusal": "I can't help with that."}]}], "usage": {}}, "gpt-6.1-sol")
    check("OpenAI: a refusal", (o["ok"], o["cause"]), (False, engines.DECLINED))
    o = engines.openai_parse({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                              "output": [], "usage": {}}, "gpt-6.1-sol")
    check("OpenAI: nothing before the limit is a cut-off", o["cause"], engines.CUT)
    g = engines.gemini_parse({"modelVersion": "gemini-3.8-flash", "candidates": [
        {"finishReason": "STOP", "content": {"parts": [{"text": "thinking…", "thought": True},
                                                       {"text": "Answer"}]}}],
        "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 10,
                          "thoughtsTokenCount": 40}}, "gemini-3.8-flash")
    check("Gemini: the answer, not the thought", (g["ok"], g["text"]), (True, "Answer"))
    check("  thinking billed as output, as Google bills it", g["usage"]["output"], 50)
    g = engines.gemini_parse({"promptFeedback": {"blockReason": "SAFETY"}}, "gemini-3.8-flash")
    check("Gemini: a blocked prompt", g["cause"], engines.DECLINED)
    g = engines.gemini_parse({"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}]},
                             "gemini-3.8-flash")
    check("Gemini: nothing before the limit is a cut-off", g["cause"], engines.CUT)


def test_prices():
    print("\nTokens into dollars:")
    check("Opus 5.5: a million in and a million out is $24",
          engines.cost("anthropic", "claude-opus-5-5", {"input": 1_000_000, "output": 1_000_000}), 24.0)
    check("Anthropic counts cache reads beside the input",
          engines.cost("anthropic", "claude-sonnet-5-5", {"input": 0, "cached": 1_000_000}), 0.2)
    check("OpenAI counts cached tokens inside it",
          engines.cost("openai", "gpt-6.1-sol", {"input": 1_000_000, "cached": 1_000_000}), 0.1)
    check("a model the table does not know costs the dearest on its engine",
          engines.cost("openai", "gpt-9-imaginary", {"output": 1_000_000}), 50.0)
    check("Haiku 5.5 over 100,000 prompt tokens is the dearer rate",
          engines.cost("anthropic", "claude-haiku-5-5", {"input": 200_000}), 0.1)
    check("an announced price change applies from its day",
          (engines.cost("gemini", "gemini-3.8-flash", {"input": 1_000_000}, day="2026-12-31"),
           engines.cost("gemini", "gemini-3.8-flash", {"input": 1_000_000}, day="2027-01-01")),
          (0.75, 1.5))


class _Resp:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.text = json.dumps(body)

    def json(self):
        return self._body


def test_the_wire_openai_and_gemini():
    print("\nWhat goes to OpenAI and Google, and what a failure means:")
    real_post, waits = requests.post, engines.RETRY_WAITS
    sent = []

    def post(url, headers=None, json=None, timeout=None):
        sent.append({"url": url, "headers": headers, "json": json})
        return post.answers.pop(0)
    requests.post = post
    engines.RETRY_WAITS = (0, 0)
    try:
        post.answers = [_Resp(503, {"error": {"message": "busy"}}),
                        _Resp(200, {"model": "gpt-6.1-sol", "output": [
                            {"type": "message", "content": [{"type": "output_text", "text": "OK"}]}],
                            "usage": {"input_tokens": 5, "output_tokens": 1}})]
        r = engines._openai("gpt-6.1-sol", "Be brief.", "Say OK", 16000, "medium", "sk-secret-xyz", 60)
        check("OpenAI: a busy answer is tried again", (r["ok"], len(sent)), (True, 2))
        body = sent[-1]["json"]
        check("  the Responses API, with the system prompt as instructions",
              (sent[-1]["url"], body["instructions"], body["input"], body["max_output_tokens"],
               body["reasoning"]),
              ("https://api.openai.com/v1/responses", "Be brief.", "Say OK", 16000, {"effort": "medium"}))
        check("  the key in the Authorization header", sent[-1]["headers"]["Authorization"],
              "Bearer sk-secret-xyz")
        sent.clear()
        post.answers = [_Resp(400, {"error": {"message": "Unsupported parameter: 'reasoning.effort'"}}),
                        _Resp(200, {"output": [{"type": "message", "content": [
                            {"type": "output_text", "text": "OK"}]}], "usage": {}})]
        r = engines._openai("gpt-4.1", "", "x", 16000, "medium", "sk-secret-xyz", 60)
        check("  a model that does not reason is asked again without the setting",
              (r["ok"], "reasoning" in sent[-1]["json"]), (True, False))
        post.answers = [_Resp(401, {"error": {"message": "Incorrect API key provided"}})]
        check("  a refused key", engines._openai("gpt-6.1-sol", "", "x", 16000, "", "k" * 20, 60)["cause"],
              engines.BAD_KEY)
        post.answers = [_Resp(429, {"error": {"code": "insufficient_quota", "message": "quota"}})]
        check("  no credit is not a rate limit, and is not retried",
              engines._openai("gpt-6.1-sol", "", "x", 16000, "", "k" * 20, 60)["cause"], engines.NO_CREDIT)
        sent.clear()
        post.answers = [_Resp(200, {"candidates": [{"content": {"parts": [{"text": "OK"}]},
                                                    "finishReason": "STOP"}],
                                    "usageMetadata": {"promptTokenCount": 3}})]
        r = engines._gemini("gemini-3.8-flash", "Be brief.", "Say OK", 16000, "AIza-secret", 60)
        check("Gemini: answered", r["ok"], True)
        check("  generateContent for the model", sent[-1]["url"],
              "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent")
        check("  the key in a header, never in the address",
              (sent[-1]["headers"]["x-goog-api-key"], "AIza-secret" in sent[-1]["url"]), ("AIza-secret", False))
        check("  the system prompt as the system instruction",
              sent[-1]["json"]["systemInstruction"], {"parts": [{"text": "Be brief."}]})
        post.answers = [_Resp(400, {"error": {"message": "API key not valid. Please pass a valid API key."}})]
        check("  Google's 400 for a bad key reads as a refused key",
              engines._gemini("gemini-3.8-flash", "", "x", 16000, "k" * 20, 60)["cause"], engines.BAD_KEY)
        post.answers = [_Resp(404, {"error": {"message": "models/gemini-9 is not found"}})]
        check("  a model the key cannot use", engines._gemini("gemini-9", "", "x", 16000, "k" * 20, 60)["cause"],
              engines.NO_MODEL)
    finally:
        requests.post, engines.RETRY_WAITS = real_post, waits


def test_the_wire_anthropic():
    print("\nWhat goes to Anthropic, through its own library:")
    try:
        import anthropic
    except ImportError as e:
        print(f"  SKIP  Anthropic's library is not installed here ({e}) — pip install -r requirements.txt")
        return
    # The library's own HTTP package, whichever generation it ships with.
    http = sys.modules.get("httpx2") or sys.modules.get("httpx")
    calls = []

    class Msg:
        def __init__(self, model):
            self.model = model

        def model_dump(self):
            return {"model": self.model, "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "OK"}],
                    "usage": {"input_tokens": 12, "output_tokens": 3}}

    class Fake:
        reject_fallbacks = False

        def __init__(self, api_key=None, timeout=None, max_retries=None):
            self.messages = types.SimpleNamespace(create=self.plain)
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self.beta_create))
            calls.append({"key": api_key})

        def plain(self, **kw):
            calls.append({"how": "plain", **kw})
            return Msg(kw["model"])

        def beta_create(self, **kw):
            calls.append({"how": "beta", **kw})
            if Fake.reject_fallbacks:
                raise anthropic.BadRequestError(
                    "fallbacks: not supported for this model",
                    response=http.Response(400, request=http.Request("POST", "https://api.anthropic.com")),
                    body=None)
            return Msg(kw["model"])

    real = anthropic.Anthropic
    anthropic.Anthropic = Fake
    engines._FALLBACK_REFUSED.clear()
    try:
        r = engines._anthropic("claude-opus-5-5", "Be brief.", "Say OK", 16000, "high", "sk-ant-x", 60)
        sent = calls[-1]
        check("Opus: answered", r["ok"], True)
        check("  with Anthropic's own fallback for a declined request",
              (sent["how"], sent.get("fallbacks"), sent.get("betas")),
              ("beta", "default", ["server-side-fallback-2026-07-01"]))
        check("  effort set the current way, no thinking switch",
              (sent.get("output_config"), "thinking" in sent), ({"effort": "high"}, False))
        check("  the system prompt as the system prompt", sent.get("system"), "Be brief.")
        engines._anthropic("claude-haiku-4-5", "", "x", 16000, "", "sk-ant-x", 60)
        check("Haiku 4.5: no effort, no fallbacks", ("output_config" in calls[-1], calls[-1]["how"]),
              (False, "plain"))
        Fake.reject_fallbacks = True
        r = engines._anthropic("claude-opus-5", "", "x", 16000, "medium", "sk-ant-x", 60)
        check("a model that turns the fallback down is asked again without it",
              (r["ok"], calls[-1]["how"]), (True, "plain"))
        engines._anthropic("claude-opus-5", "", "x", 16000, "medium", "sk-ant-x", 60)
        check("  and is not offered it again", calls[-1]["how"], "plain")
    finally:
        anthropic.Anthropic = real
        engines._FALLBACK_REFUSED.clear()


def test_the_real_anthropic_library():
    print("\nThe real Anthropic library, against a stand-in on this computer:")
    try:
        import anthropic  # noqa: F401
    except ImportError as e:
        print(f"  SKIP  Anthropic's library is not installed here ({e})")
        return
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
            seen.append({"path": self.path, "beta": self.headers.get("anthropic-beta") or "",
                         "key": self.headers.get("x-api-key") or "", "body": body})
            reply = json.dumps({"id": "msg_1", "type": "message", "role": "assistant",
                                "model": body.get("model"), "stop_reason": "end_turn",
                                "content": [{"type": "text", "text": "OK"}],
                                "usage": {"input_tokens": 9, "output_tokens": 2}}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    keep = {k: os.environ.get(k) for k in ("ANTHROPIC_BASE_URL", "NO_PROXY", "no_proxy")}
    os.environ["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{srv.server_address[1]}"
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
    engines._FALLBACK_REFUSED.clear()
    try:
        r = engines._anthropic("claude-opus-5-5", "Be brief.", "Say OK", 16000, "medium",
                               "sk-ant-local-test", 30)
        check("Opus 5.5 answered through the library", (r["ok"], r["text"], r["usage"]["input"]),
              (True, "OK", 9))
        sent = seen[-1] if seen else {}
        check("  to the Messages API", sent.get("path", "").split("?")[0], "/v1/messages")
        check("  with the server-side fallback beta", "server-side-fallback-2026-07-01" in sent.get("beta", ""),
              True)
        b = sent.get("body") or {}
        check("  asking for Anthropic's default fallback", b.get("fallbacks"), "default")
        check("  effort in output_config, and no thinking switch",
              (b.get("output_config"), "thinking" in b), ({"effort": "medium"}, False))
        check("  the key in its header", sent.get("key"), "sk-ant-local-test")
        r = engines._anthropic("claude-haiku-5-5", "", "Say OK", 16000, "medium", "sk-ant-local-test", 30)
        check("Haiku 5.5 goes without fallbacks (it refuses them)",
              (r["ok"], "fallbacks" in (seen[-1].get("body") or {}), seen[-1].get("beta")), (True, False, ""))
    finally:
        srv.shutdown()
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_encrypted_end_to_end():
    print("\nAn encrypted account, end to end in its own process:")
    try:
        from sqlcipher3 import dbapi2  # noqa: F401
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
    except Exception as e:
        print(f"  SKIP  the encryption libraries are not installed here ({e})")
        return
    with _Place() as p:
        made = workspace.create_account("Bakery", "correct horse")
        acct = made["account"]["id"]
        site = workspace.add_site(acct, "Bakery", "https://bakery.de")["site"]["id"]
        workspace.open_site(acct, site)
        env = {k: v for k, v in os.environ.items() if k not in ("AGENCY_DB",) + KEYS}
        env.update(AGENCY_KEY=made["key"], CLAUDE_TEAM_OFF="1")
        code = (
            "import json\n"
            "from providers import engines, claude_team\n"
            "from coordination import cabinet\n"
            "before = engines.engine()\n"
            "saved = engines.save({'engine': 'openai', 'key': 'sk-proj-SEALED-KEY-123456', 'cap': '3'})\n"
            "def fake(eng, model, *a):\n"
            "    return engines._answer(True, 'fine', usage={'input': 1000, 'output': 500}, ran=model)\n"
            "engines.TRANSPORT = fake\n"
            "r = claude_team.call('content', [{'role': 'user', 'content': 'hi'}])\n"
            "print(json.dumps({'before': before, 'saved': saved['ok'], 'engine': engines.engine(),\n"
            "  'ok': r['ok'], 'model': r['used']['model'], 'ai': engines.ai_spent_today(),\n"
            "  'cap': cabinet.daily_spend_cap()}))\n")
        out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                             capture_output=True, text=True, encoding="utf-8", timeout=180)
        try:
            got = json.loads((out.stdout or "").strip().splitlines()[-1])
        except Exception:
            got = {"error": (out.stderr or "")[-400:]}
        check("a new encrypted account starts with no engine", got.get("before"), "")
        check("  chooses OpenAI with its key", (got.get("saved"), got.get("engine")), (True, "openai"))
        check("  Content is answered by the everyday model", (got.get("ok"), got.get("model")),
              (True, "gpt-6.1-sol"))
        check("  what it cost is counted ($0.007)", got.get("ai"), 0.007)
        check("  against the cap it saved", got.get("cap"), 3.0)
        acc_env = (workspace.account_dir(acct) / "account.env").read_bytes()
        check("the key is sealed on disk", (vault.is_sealed(acc_env), b"SEALED-KEY" in acc_env), (True, False))
        db = (workspace.site_dir(acct, site) / "agency.db").read_bytes()
        check("the AI ledger is in the encrypted cabinet", b"gpt-6.1-sol" in db, False)


def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _req(port, method, path, body=None, cookie=""):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=180)
    headers = {"Cookie": cookie} if cookie else {}
    if body is not None:
        body = json.dumps(body)
        headers["Content-Type"] = "application/json"
    c.request(method, path, body=body, headers=headers)
    r = c.getresponse()
    text = r.read().decode("utf-8", "replace")
    jar = "; ".join(v.split(";")[0] for k, v in r.getheaders() if k.lower() == "set-cookie")
    c.close()
    try:
        return r.status, json.loads(text), jar, text
    except Exception:
        return r.status, {}, jar, text


def test_the_real_server():
    print("\nThe real server: first run, the lock, and a key that never comes back:")
    if not shutil.which("node"):
        print("  SKIP  Node is not installed here")
        return
    try:
        from sqlcipher3 import dbapi2  # noqa: F401
    except Exception as e:
        print(f"  SKIP  the encryption libraries are not installed here ({e})")
        return
    with _Place() as p:
        port = _port()
        # Settings are read from the account's own files here, so the suite's
        # "ignore the settings file" switch stays off for this server.
        env = {k: v for k, v in os.environ.items()
               if k not in ("AGENCY_DB", "AGENCY_KEY", "AGENCY_IGNORE_DOTENV") + KEYS}
        env.update(PORT=str(port), AGENCY_DATA=str(p.dir / "data"), CLAUDE_TEAM_OFF="1")
        proc = subprocess.Popen(["node", str(ROOT / "server.js")], cwd=str(ROOT), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(80):
                try:
                    _req(port, "GET", "/api/ping")
                    break
                except Exception:
                    time.sleep(0.25)
            st, j, cookie, _ = _req(port, "POST", "/api/account/sign-up",
                                    {"name": "Plumber", "password": "correct horse",
                                     "confirm": "correct horse"})
            check("signing up hands the Engine page its ticket too", "glasswork_setup=" in cookie, True)
            _req(port, "POST", "/api/account/add-site", {"name": "Pipes", "url": "https://pipes.co.uk"}, cookie)
            st, j, _, _ = _req(port, "GET", "/api/engine-state", cookie=cookie)
            check("a new account has no engine yet, and the office can tell",
                  (st, j.get("chosen"), j.get("engine")), (200, False, ""))
            secret = "sk-ant-api03-SERVER-TEST-KEY-77"
            st, j, _, text = _req(port, "POST", "/api/engine",
                                  {"engine": "anthropic", "key": secret, "cap": "4"}, cookie)
            check("the first choice is saved without asking for the password again", (st, j.get("ok")),
                  (200, True))
            check("  and the reply does not carry the key", secret in text, False)
            st, j, _, text = _req(port, "GET", "/api/engine", cookie=cookie)
            opt = next((o for o in j.get("options") or [] if o["id"] == "anthropic"), {})
            check("the Engine page sees that a key is saved", (opt.get("key_saved"), opt.get("key_hint")),
                  (True, "…" + secret[-4:]))
            check("  but never the key", secret in text, False)
            st, j, _, _ = _req(port, "GET", "/api/engine-state", cookie=cookie)
            check("the office now sees Claude chosen, and the cap", (j.get("chosen"), j.get("name"), j.get("cap")),
                  (True, "Claude", 4.0))
            st, j, cookie2, _ = _req(port, "POST", "/api/account/sign-in",
                                     {"account": "Plumber", "password": "correct horse"})
            st, j, _, _ = _req(port, "POST", "/api/engine", {"engine": "openai"}, cookie2)
            check("after signing in again, changing it asks for the password",
                  (st, j.get("needsSetupUnlock")), (401, True))
            st, _, _, page = _req(port, "GET", "/engine", cookie=cookie2)
            check("  the page shows the lock", "AI engine" in page and 'name="next" value="/engine"' in page,
                  True)
            st, j, _, _ = _req(port, "GET", "/api/engine-state", cookie=cookie2)
            check("  while the top bar's facts stay open", (st, j.get("chosen")), (200, True))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()


def test_the_wording_of_the_mirrors():
    print("\nThe server and Python agree on what belongs to the person:")
    js = (ROOT / "server.js").read_text(encoding="utf-8")
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        check(f"{k} is the person's, in both", (k in workspace.ACCOUNT_KEYS, f'"{k}"' in js), (True, True))
    check("the engine file is the person's", "engine.local.json" in workspace.ACCOUNT_CONFIG, True)


def main():
    test_his_own_copy_keeps_claude_code()
    test_choosing_an_engine_and_its_key()
    test_every_sector_asks_through_the_same_door()
    test_stepping_down()
    test_the_spending_cap()
    test_reading_each_company()
    test_prices()
    test_the_wire_openai_and_gemini()
    test_the_wire_anthropic()
    test_the_real_anthropic_library()
    test_encrypted_end_to_end()
    test_the_real_server()
    test_the_wording_of_the_mirrors()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("All engine tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
