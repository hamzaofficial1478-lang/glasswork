"""
claude_team.py — every sector thinks with the Claude model the owner picked.

    python -m providers.claude_team                 the team, the accounts, what is struggling
    python -m providers.claude_team --test          one tiny call per model, both accounts
    python -m providers.claude_team --set it claude-opus-4-8
    python -m providers.claude_team --login-backup  sign the backup account in, once

WHY THIS REPLACED THE PROVIDER SHELF

The owner, after yet another preflight listing five sectors with "no working
brain": "no matter how many times we will be testing, changing the models
there will always be the error of some of the models". He was right. Every one
of those five was one free endpoint being unreachable that morning, and five
sectors sat on it. A cascade of keys only ever moved the problem around.

So there is one kind of brain now: Claude, through the Claude Code command on
his machine (providers/local_cli.py), signed in with his own account. Each
sector gets a model he chose for it:

    MD, Security        Opus 5      "not higher than this"
    IT                  Opus 4.8
    Content, SEO, Leads Sonnet 5
    Critic              Sonnet 5    a different mind from the MD it checks
    Report              Haiku 4.5   light, and it only scores the day

WHEN A MODEL DOES NOT ANSWER

His words: "MD opus 5 did not respond back, next is opus 4.8 ... like opus
4.7, 4.6". That is the ladder below — each model steps down to the next one,
never up, and never above Opus 5.

He also asked that a sector in an emergency be HANDED the model another sector
is using, with that sector stepping down to make room. Worth being straight
about what is and is not scarce, because it decides what the code does:

  A MODEL IS NOT A SEAT. MD and IT can both use Opus 4.8 at the same moment;
  nothing is taken from one by the other, so there is nothing to hand over.
  Making IT step down "to free Opus 4.8" would cost IT quality and give the
  MD nothing.

  WHAT IS SCARCE IS THE ACCOUNT'S ALLOWANCE. That is the thing a heavy day
  runs out of, and it is where the hand-over genuinely happens: when the main
  account is used up, MD, Security and IT move to the backup account at their
  FULL model, and the other sectors do not follow them there at full strength
  — they step down on the main account first, and on the backup account they
  get Sonnet 5 at most. The lesser sectors give way so the main three keep
  their strong models. That is his idea, applied to the thing that runs out.

  AND ONE STRUGGLING MODEL MOVES EVERYONE AT ONCE. When Opus 5 times out for
  the MD, that is written into the cabinet, and for the next few minutes every
  sector that would have used Opus 5 starts one rung down instead of each
  waiting out its own timeout to discover the same thing. That is the "whole
  shift" he described.

Inside a single call, Claude Code's own --fallback-model does the fast part:
an overloaded model is swapped for the next one without starting a second
process. This module does the rest — the slow model, the used-up account, the
signed-out backup — and remembers it.
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from . import local_cli

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "claude_team.local.json"

# ---------------------------------------------------------------------------
# THE MODELS, STRONGEST FIRST — AND NOTHING ABOVE OPUS 5
# ---------------------------------------------------------------------------

MODELS = (
    ("claude-opus-5", "Opus 5"),
    ("claude-opus-4-8", "Opus 4.8"),
    ("claude-opus-4-7", "Opus 4.7"),
    ("claude-opus-4-6", "Opus 4.6"),
    ("claude-sonnet-5", "Sonnet 5"),
    ("claude-sonnet-4-6", "Sonnet 4.6"),
    ("claude-haiku-4-5", "Haiku 4.5"),
)
LADDER = [m for m, _ in MODELS]
LABEL = dict(MODELS)
CEILING = LADDER[0]
_ALIASES = {"opus": CEILING, "sonnet": "claude-sonnet-5", "haiku": "claude-haiku-4-5"}

# ---------------------------------------------------------------------------
# THE TEAM
# ---------------------------------------------------------------------------

SECTORS = ("md", "security", "it", "content", "seo", "leads", "critic", "report")
SECTOR_LABEL = {"md": "Managing Director", "security": "Security", "it": "IT",
                "content": "Content", "seo": "SEO", "leads": "Leads",
                "critic": "Critic", "report": "Report"}
DEFAULT_TEAM = {
    "md": "claude-opus-5", "security": "claude-opus-5",
    "it": "claude-opus-4-8",
    "content": "claude-sonnet-5", "seo": "claude-sonnet-5",
    "leads": "claude-sonnet-5", "critic": "claude-sonnet-5",
    "report": "claude-haiku-4-5",
}
# Other names the codebase uses for the same desks.
_SAME_AS = {"director": "md", "overseer": "md", "reporter": "report"}

# The three he said cannot be switched off. They keep their full model on the
# backup account; everyone else is capped there, so the backup's allowance is
# kept for the work that matters most.
MAIN = ("md", "security", "it")
BACKUP_CAP_FOR_OTHERS = "claude-sonnet-5"

# A judgement that must be cross-checked is read by a DIFFERENT model: the
# Critic's. Two answers from one model is one opinion asked twice.
SECOND_OPINION = "critic"

# ---------------------------------------------------------------------------
# HOW HARD EACH CALL THINKS
# ---------------------------------------------------------------------------
#
# Claude Code on its own thinks at a high effort for everything, which is the
# right default for writing code and the wrong one for "rank these five
# keywords". Ordinary work runs at medium; the calls marked demanding
# (providers/effort.py — the MD's allocation, Security's clearances) run at
# high. Haiku 4.5 takes no effort setting at all, so none is sent.
EFFORT_ORDINARY = "medium"
EFFORT_DEMANDING = "high"
NO_EFFORT = ("claude-haiku-4-5",)

# ---------------------------------------------------------------------------
# TIME
# ---------------------------------------------------------------------------

BUDGET = local_cli.FLOOR_TIMEOUT   # one sector's call, every attempt together
LEAST_ATTEMPT = 45                 # not worth starting another with less left
FALLBACKS_PER_CALL = 2             # handed to the CLI's --fallback-model

# How long a model or an account is stepped round, by what went wrong.
SLOW_MINUTES = 10                  # timed out: the model is slow right now
BUSY_MINUTES = 5                   # overloaded, or the CLI had to fall back
LIMIT_MINUTES = 60                 # the account's allowance is used up
SIGNED_OUT_MINUTES = 10            # re-checked soon, in case he signs in
MISSING_MINUTES = 24 * 60          # the plan does not have this model

ANY = "*"

# ---------------------------------------------------------------------------
# THE BACKUP ACCOUNT
# ---------------------------------------------------------------------------
#
# A second Claude account is a second LOGIN FOLDER. Claude Code keeps its
# sign-in in a folder, and CLAUDE_CONFIG_DIR points it at another one, so two
# accounts sit side by side on one machine without either signing the other
# out. `--login-backup` makes the folder, opens the sign-in, and writes the
# folder into .env; after that it is automatic.
BACKUP_ENV = "CLAUDE_BACKUP_DIR"
DEFAULT_BACKUP_FOLDER = Path.home() / ".claude-backup"


def accounts() -> list:
    out = [{"id": "main", "label": "main Claude account", "config_dir": ""}]
    folder = local_cli._setting(BACKUP_ENV)
    if folder:
        out.append({"id": "backup", "label": "backup Claude account",
                    "config_dir": folder})
    return out


def _account(aid) -> dict:
    return next((a for a in accounts() if a["id"] == aid), {})


# ---------------------------------------------------------------------------
# READING AND SAVING THE TEAM
# ---------------------------------------------------------------------------

def normalise(sector) -> str:
    s = str(sector or "").strip().lower()
    return _SAME_AS.get(s, s)


def clamp(model) -> str:
    """A model on the ladder, or "" if it is not one we use. Above Opus 5 comes down."""
    # Claude reports what ran with a date on the end — claude-haiku-4-5-20251001
    # — and that is the same model as claude-haiku-4-5, not a step down from it.
    m = re.sub(r"-\d{8}$", "", str(model or "").strip().lower())
    if m in LADDER:
        return m
    if m in _ALIASES:
        return _ALIASES[m]
    capped = local_cli.clamp(m) if m else ""
    return capped if capped in LADDER else ""


def rank(model) -> int:
    m = clamp(model)
    return LADDER.index(m) if m else len(LADDER)


def below(model) -> list:
    """Every model under this one, strongest first — the step-down ladder."""
    m = clamp(model)
    return LADDER[LADDER.index(m) + 1:] if m else []


def _saved() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8")).get("team") or {}
    except Exception:
        return {}


def team() -> dict:
    """sector -> model: the defaults, with whatever he saved on top."""
    out = dict(DEFAULT_TEAM)
    for s, m in _saved().items():
        s, m = normalise(s), clamp(m)
        if s in DEFAULT_TEAM and m:
            out[s] = m
    return out


def model_for(sector) -> str:
    s = normalise(sector)
    return team().get(s) or DEFAULT_TEAM["content"]


def save_model(sector, model) -> dict:
    s, m = normalise(sector), clamp(model)
    if s not in DEFAULT_TEAM:
        return {"ok": False, "error": f"'{sector}' is not a sector. "
                                      f"Sectors: {', '.join(SECTORS)}"}
    if not m:
        return {"ok": False, "error": (f"'{model}' is not one of the models the "
                                       f"team uses: "
                                       + ", ".join(LABEL[x] for x in LADDER))}
    saved = _saved()
    saved[s] = m
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps({"team": saved}, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG)
    typed = str(model).strip().lower()
    note = ("" if typed in LADDER or typed in _ALIASES
            else f"{model} is above Opus 5, so {LABEL[m]}")
    return {"ok": True, "sector": s, "model": m, "label": LABEL[m], "note": note}


# ---------------------------------------------------------------------------
# WHAT IS STRUGGLING — SHARED BY EVERY SECTOR, IN THE CABINET
# ---------------------------------------------------------------------------
#
# The day runs three sectors side by side, each in its own process, so this
# cannot live in memory: the MD finding Opus 5 slow must reach IT's process.

def _conn():
    try:
        from coordination import board
        c = sqlite3.connect(str(board.DB), timeout=10, isolation_level=None)
    except Exception:                                        # pragma: no cover
        return None
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=10000")
    c.execute("CREATE TABLE IF NOT EXISTS claude_health ("
              "account TEXT, model TEXT, until INTEGER, why TEXT, at INTEGER,"
              " PRIMARY KEY (account, model))")
    c.execute("CREATE TABLE IF NOT EXISTS claude_usage ("
              "id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, at INTEGER,"
              " sector TEXT, agent TEXT, account TEXT, model_asked TEXT,"
              " model_ran TEXT, ok INTEGER, seconds REAL, input INTEGER,"
              " output INTEGER, cache_read INTEGER, cache_write INTEGER,"
              " api_usd REAL, why TEXT)")
    return c


def mark(account, model, why, minutes):
    c = _conn()
    if c is None:
        return
    now = int(time.time())
    with c:
        c.execute("INSERT INTO claude_health (account, model, until, why, at)"
                  " VALUES (?,?,?,?,?) ON CONFLICT(account, model) DO UPDATE SET"
                  " until=excluded.until, why=excluded.why, at=excluded.at",
                  (account, model, now + int(minutes * 60), why, now))
    c.close()


def down(account, model) -> str:
    """Why this model on this account is being stepped round, or "" if it is not."""
    c = _conn()
    if c is None:
        return ""
    try:
        r = c.execute("SELECT why, until FROM claude_health WHERE until > ?"
                      " AND account IN (?, ?) AND model IN (?, ?)"
                      " ORDER BY until DESC LIMIT 1",
                      (int(time.time()), account, ANY, model, ANY)).fetchone()
        return (r["why"] if r else "") or ""
    finally:
        c.close()


def clear(account=None, model=None):
    c = _conn()
    if c is None:
        return
    q, args = "DELETE FROM claude_health WHERE 1=1", []
    if account:
        q, args = q + " AND account=?", args + [account]
    if model:
        q, args = q + " AND model=?", args + [model]
    with c:
        c.execute(q, args)
    c.close()


def health() -> list:
    c = _conn()
    if c is None:
        return []
    try:
        now = int(time.time())
        return [{"account": r["account"], "model": r["model"],
                 "why": r["why"], "back_in_minutes": max(1, (r["until"] - now) // 60)}
                for r in c.execute("SELECT * FROM claude_health WHERE until > ?"
                                   " ORDER BY until DESC", (now,))]
    finally:
        c.close()


def _limit_scope(detail) -> str:
    """An allowance message that names a family used up only that family."""
    low = (detail or "").lower()
    for fam in ("opus", "sonnet", "haiku"):
        if f"{fam} limit" in low or f"{fam} usage" in low:
            return fam
    return ANY


def _remember(account, model, res):
    """Write down what a failure means, so no other sector has to rediscover it."""
    cause = (res.get("cause") or "").lower()
    if "timed out" in cause:
        mark(ANY, model, f"{LABEL.get(model, model)} was too slow to answer",
             SLOW_MINUTES)
    elif "overloaded" in cause:
        mark(ANY, model, f"{LABEL.get(model, model)} is overloaded", BUSY_MINUTES)
    elif "allowance" in cause:
        scope = _limit_scope(res.get("error"))
        who = _account(account).get("label", account)
        if scope == ANY:
            mark(account, ANY, f"the {who}'s allowance is used up", LIMIT_MINUTES)
        else:
            for m in LADDER:
                if scope in m:
                    mark(account, m, f"the {who}'s {scope.title()} allowance is "
                                     f"used up", LIMIT_MINUTES)
    elif "not signed in" in cause:
        mark(account, ANY, f"the {_account(account).get('label', account)} is "
                           f"not signed in", SIGNED_OUT_MINUTES)
    elif "not one the cli knows" in cause:
        mark(account, model, f"{LABEL.get(model, model)} is not available on the "
                             f"{_account(account).get('label', account)}",
             MISSING_MINUTES)


def record(sector, agent, account, asked, res):
    c = _conn()
    if c is None:
        return
    local = res.get("local") or {}
    u = local.get("usage") or {}
    ran = _ran(res, asked) if res.get("ok") else ""
    with c:
        c.execute("INSERT INTO claude_usage (day, at, sector, agent, account,"
                  " model_asked, model_ran, ok, seconds, input, output, cache_read,"
                  " cache_write, api_usd, why) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (time.strftime("%Y-%m-%d"), int(time.time()), sector, agent or "",
                   account, asked, ran, 1 if res.get("ok") else 0,
                   float(local.get("seconds") or sum(res.get("waited") or [0])),
                   u.get("input", 0), u.get("output", 0), u.get("cache_read", 0),
                   u.get("cache_write", 0), u.get("api_equivalent_usd", 0.0),
                   "" if res.get("ok") else (res.get("cause") or "")[:120]))
    c.close()


def answered_recently(model, hours=24) -> bool:
    """Has this model given a real answer, on any account, in the last day?"""
    c = _conn()
    if c is None:
        return False
    try:
        r = c.execute("SELECT 1 FROM claude_usage WHERE ok=1 AND at > ?"
                      " AND (model_ran=? OR model_asked=?) LIMIT 1",
                      (int(time.time()) - hours * 3600, model, model)).fetchone()
        return bool(r)
    finally:
        c.close()


def _per_sector(where, args) -> dict:
    c = _conn()
    if c is None:
        return {}
    try:
        rows = c.execute(
            "SELECT sector, count(*) calls, sum(ok) ok, sum(seconds) seconds,"
            " sum(input) input, sum(output) output, sum(cache_read) cache_read,"
            " sum(cache_write) cache_write, sum(api_usd) api_usd,"
            " group_concat(DISTINCT model_ran) models"
            " FROM claude_usage WHERE " + where + " AND sector != 'setup-test'"
            " GROUP BY sector", args).fetchall()
    finally:
        c.close()
    return {
        r["sector"]: {"calls": r["calls"], "answered": r["ok"] or 0,
                      "seconds": round(r["seconds"] or 0, 1),
                      "tokens_in": (r["input"] or 0) + (r["cache_read"] or 0)
                                   + (r["cache_write"] or 0),
                      "tokens_out": r["output"] or 0,
                      "api_equivalent_usd": round(r["api_usd"] or 0, 4),
                      "models": [m for m in (r["models"] or "").split(",") if m]}
            for r in rows}


def usage(day=None) -> dict:
    """Per sector, for one day: calls, tokens, time, and what the tokens would cost."""
    day = day or time.strftime("%Y-%m-%d")
    return {"day": day, "sectors": _per_sector("day=?", (day,))}


def usage_since(started, ended=None) -> dict:
    """Per sector, for one run: every call between its start and its end."""
    return _per_sector("at >= ? AND at <= ?",
                       (int(started or 0), int(ended or time.time()) + 1))


# ---------------------------------------------------------------------------
# WHO TRIES WHAT, IN WHAT ORDER
# ---------------------------------------------------------------------------

def candidates(sector, model=None) -> list:
    """
    (account id, model) in the order they are tried.

    Main sectors: their model on the main account, then the SAME model on the
    backup, then one rung down on each, and so on — a used-up account costs
    them nothing in quality. Everyone else: all the way down the main account
    first, then the backup no higher than Sonnet 5.
    """
    s = normalise(sector)
    top = clamp(model) or model_for(s)
    rungs = [top] + below(top)
    have_backup = len(accounts()) > 1
    if s in MAIN:
        out = []
        for m in rungs:
            out.append(("main", m))
            if have_backup:
                out.append(("backup", m))
        return out
    out = [("main", m) for m in rungs]
    if have_backup:
        out += [("backup", m) for m in rungs if rank(m) >= rank(BACKUP_CAP_FOR_OTHERS)]
    return out


def _effort_for(model, demanding) -> str:
    if model in NO_EFFORT:
        return ""
    return EFFORT_DEMANDING if demanding else EFFORT_ORDINARY


def _fallbacks(order, i, effort) -> list:
    """The next models on the same account, for the CLI's own quick step-down."""
    acc = order[i][0]
    out = []
    for a, m in order[i + 1:]:
        if len(out) >= FALLBACKS_PER_CALL:
            break
        if a != acc or m in out or down(a, m):
            continue
        if effort and m in NO_EFFORT:
            break                   # a model that cannot take the effort flag
        out.append(m)
    return out


def _is_install_problem(res) -> bool:
    cause = (res.get("cause") or "").lower()
    return "installed" in cause or "claude_cli" in cause or "could not start" in cause


def _ran(res, asked) -> str:
    ran = [clamp(m) or m for m in ((res.get("local") or {}).get("ran") or [])]
    return next((m for m in ran if m in LADDER), ran[0] if ran else asked)


# ---------------------------------------------------------------------------
# THE CALL
# ---------------------------------------------------------------------------

def call(sector, messages, max_tokens=512, timeout=0, *, model=None,
         demanding=None, agent="") -> dict:
    """
    Ask this sector's Claude. Returns
        {ok, task, output, used: {credential, model, account}, model_asked,
         stepped_down, failovers, skipped}
    — the shape routing.call_task has always returned, so nothing that asked a
    sector for an answer needs to know the brain changed.
    """
    s = normalise(sector)
    asked = clamp(model) or model_for(s)
    ready, why = local_cli.available()
    if not ready:
        return {"ok": False, "task": s, "error": why,
                "cause": local_cli._unavailable_cause(why),
                "failovers": [], "skipped": []}
    if demanding is None:
        from . import effort
        demanding = bool(effort.why_demanding())

    deadline = time.time() + max(BUDGET, int(timeout or 0))
    order = candidates(s, asked)
    failovers, skipped, tried = [], [], 0
    for i, (aid, m) in enumerate(order):
        acc = _account(aid)
        blocked = down(aid, m)
        if blocked:
            skipped.append({"account": aid, "model": m, "why": blocked})
            continue
        left = int(deadline - time.time())
        if tried and left < LEAST_ATTEMPT:
            break
        eff = _effort_for(m, demanding)
        fb = _fallbacks(order, i, eff)
        tried += 1
        res = local_cli._chat_as(None, m, messages, max_tokens, max(left, 1),
                                 config_dir=acc.get("config_dir", ""),
                                 fallback=fb, effort=eff, exact=True)
        record(s, agent, aid, m, res)
        if res.get("ok"):
            ran = _ran(res, m)
            if ran != m and rank(ran) > rank(m):
                # The CLI had to step down inside the call: the model asked for
                # was overloaded. Everyone steps round it for a few minutes.
                mark(ANY, m, f"{LABEL.get(m, m)} is overloaded — the last call "
                             f"stepped down to {LABEL.get(ran, ran)}", BUSY_MINUTES)
            stepped = "" if (ran == asked and aid == "main") else (
                f"{SECTOR_LABEL.get(s, s)} asked for {LABEL.get(asked, asked)}"
                f" and was answered by {LABEL.get(ran, ran)} on the {acc['label']}"
                + (f" — {skipped[0]['why']}" if skipped else
                   f" — {failovers[-1]['cause']}" if failovers else ""))
            return {"ok": True, "task": s, "output": res.get("output", ""),
                    "used": {"credential": acc["label"].capitalize(),
                             "model": ran, "account": aid},
                    "model_asked": asked, "stepped_down": stepped,
                    "failovers": failovers, "skipped": skipped,
                    "seconds": (res.get("local") or {}).get("seconds"),
                    "load": ""}
        failovers.append({"model": m, "account": aid, "status": res.get("status", 0),
                          "cause": res.get("cause", ""),
                          "error": (res.get("error") or "")[:300],
                          "waited": res.get("waited") or []})
        _remember(aid, m, res)
        if _is_install_problem(res):
            break                   # the same command serves both accounts
    return {"ok": False, "task": s, "failovers": failovers, "skipped": skipped,
            "model_asked": asked, "error": _why_nobody(s, asked, failovers, skipped)}


def _why_nobody(sector, asked, failovers, skipped) -> str:
    who = SECTOR_LABEL.get(sector, sector)
    if failovers and _is_install_problem(failovers[-1]):
        return failovers[-1]["error"] or failovers[-1]["cause"]
    bits = [f"{LABEL.get(f['model'], f['model'])} on the {f['account']} account: "
            f"{f['cause']}" for f in failovers[:4]]
    if skipped and not failovers:
        bits = [skipped[0]["why"]]
    hint = ""
    if len(accounts()) < 2:
        hint = (" A backup Claude account would have taken over — "
                "`python -m providers.claude_team --login-backup` sets one up.")
    return (f"{who} could not get an answer from Claude (asked for "
            f"{LABEL.get(asked, asked)}). " + "; ".join(bits) + "." + hint)


# ---------------------------------------------------------------------------
# TWO OPINIONS, FOR THE JUDGEMENTS THAT COST MONEY
# ---------------------------------------------------------------------------

def second_model(sector) -> str:
    """The Critic's model — or one rung below the sector's own, if they match."""
    own = model_for(sector)
    other = model_for(SECOND_OPINION)
    if other != own:
        return other
    lower = below(own)
    return lower[0] if lower else own


def deliberate(sector, messages, max_tokens=512, verdict_of=None, agent="") -> dict:
    """
    One question, two different Claude models. Same result shape as always:
    {ok, task, consulted, agreed, answers, verdict, split, why, failovers}.
    """
    s = normalise(sector)
    first = call(s, messages, max_tokens, demanding=True, agent=agent)
    second = call(s, messages, max_tokens, model=second_model(s), demanding=True,
                  agent=agent)
    answers, failovers = [], first.get("failovers", []) + second.get("failovers", [])
    for r in (first, second):
        if r.get("ok"):
            answers.append({"model": r["used"]["model"],
                            "credential": r["used"]["credential"],
                            "output": (r.get("output") or "").strip()})
    if not answers:
        return {"ok": False, "task": s, "error": first.get("error") or "no model answered",
                "consulted": 0, "agreed": None, "answers": [], "failovers": failovers}

    def _v(a):
        raw = a["output"]
        return (verdict_of(raw) if verdict_of else raw).strip().lower()

    if len(answers) < 2 or answers[0]["model"] == answers[1]["model"]:
        why = ("the second model failed, so this was not cross-checked"
               if len(answers) < 2 else
               f"both answers came from {LABEL.get(answers[0]['model'], answers[0]['model'])}"
               f" — one model asked twice is not a cross-check")
        return {"ok": True, "task": s, "consulted": 1, "agreed": None,
                "answers": answers[:1], "verdict": _v(answers[0]), "why": why,
                "failovers": failovers}
    v1, v2 = _v(answers[0]), _v(answers[1])
    agreed = v1 == v2
    return {"ok": True, "task": s, "consulted": 2, "agreed": agreed,
            "answers": answers, "verdict": v1 if agreed else None,
            "split": None if agreed else [v1, v2],
            "why": ("both models reached the same verdict" if agreed else
                    f"the two models disagreed: {v1!r} vs {v2!r}"),
            "failovers": failovers}


# ---------------------------------------------------------------------------
# CHECKING, FOR SETUP AND THE PREFLIGHT
# ---------------------------------------------------------------------------

def signed_in(account) -> dict:
    """Is this account signed in? Asks Claude Code itself, spends nothing."""
    ready, why = local_cli.available()
    if not ready:
        return {"ok": False, "why": why}
    acc = _account(account) if isinstance(account, str) else account
    try:
        p = subprocess.run(local_cli._launcher(local_cli.cli_path())
                           + ["auth", "status", "--json"],
                           capture_output=True, text=True, timeout=30,
                           env=local_cli.child_env(acc.get("config_dir", "")),
                           encoding="utf-8", errors="replace")
        d = json.loads(p.stdout or "{}")
    except Exception as e:
        return {"ok": False, "why": f"could not ask Claude Code: {e}"}
    ok = bool(d.get("loggedIn"))
    return {"ok": ok, "email": d.get("email") or d.get("account") or "",
            "folder": d.get("configDirectory") or acc.get("config_dir", ""),
            "why": "" if ok else f"the {acc.get('label', account)} is not signed in"}


def test(models=None, account="main") -> list:
    """One tiny real call per model — the only proof a model answers today."""
    acc = _account(account)
    if not acc:
        return [{"account": account, "ok": False,
                 "why": "no backup account is set up"}]
    out = []
    for m in (models or sorted(set(team().values()), key=rank)):
        res = local_cli._chat_as(None, m, [{"role": "user",
                                            "content": "Reply with exactly: OK"}],
                                 16, 120, config_dir=acc["config_dir"],
                                 effort=_effort_for(m, False), exact=True)
        record("setup-test", "", account, m, res)
        if res.get("ok"):
            clear(account, m)
            clear(ANY, m)
        else:
            _remember(account, m, res)
        out.append({"account": account, "model": m, "label": LABEL.get(m, m),
                    "ok": bool(res.get("ok")), "ran": _ran(res, m) if res.get("ok") else "",
                    "seconds": (res.get("local") or {}).get("seconds")
                               or (res.get("waited") or [None])[0],
                    "why": "" if res.get("ok") else (res.get("cause") or res.get("error"))})
    return out


def status() -> dict:
    ready, why = local_cli.available()
    t = team()
    return {
        "ready": ready, "why": why,
        # Which Claude Code answers, so "which one is it using?" has an answer
        # on the screen rather than a guess.
        "cli": local_cli.cli_path() if ready else "",
        "ceiling": CEILING,
        "models": [{"id": m, "label": LABEL[m]} for m in LADDER],
        "team": [{"sector": s, "label": SECTOR_LABEL[s], "model": t[s],
                  "model_label": LABEL[t[s]], "main": s in MAIN,
                  "default": DEFAULT_TEAM[s]} for s in SECTORS],
        "accounts": [{**a, **(signed_in(a) if ready else {"ok": False, "why": why})}
                     for a in accounts()],
        "backup_setting": BACKUP_ENV,
        "struggling": health(),
        "usage_today": usage(),
    }


def login_backup(folder=None) -> dict:
    """Make the backup account's login folder, sign it in, remember it in .env."""
    ready, why = local_cli.available()
    if not ready:
        return {"ok": False, "error": why}
    folder = str(Path(folder or DEFAULT_BACKUP_FOLDER).expanduser())
    Path(folder).mkdir(parents=True, exist_ok=True)
    print(f"\nA browser will open. Sign in with your BACKUP Claude account —\n"
          f"not the main one. Its login is kept in {folder}\n")
    subprocess.call(local_cli._launcher(local_cli.cli_path()) + ["auth", "login"],
                    env=local_cli.child_env(folder))
    from . import integrations
    integrations.write_env({BACKUP_ENV: folder})
    os.environ[BACKUP_ENV] = folder
    got = signed_in({"id": "backup", "label": "backup Claude account",
                     "config_dir": folder})
    clear("backup")
    return {"ok": got["ok"], "folder": folder, "email": got.get("email", ""),
            "error": got.get("why", "")}


# ---------------------------------------------------------------------------
# THE COMMAND LINE
# ---------------------------------------------------------------------------

def _print_status(st):
    print("\nTHE CLAUDE TEAM\n")
    if not st["ready"]:
        print("  Claude Code is not ready on this machine:\n")
        print("  " + st["why"].replace("\n", "\n  "))
        return
    for row in st["team"]:
        flag = "  (main)" if row["main"] else ""
        print(f"  {row['label']:<18} {row['model_label']:<11}{flag}")
    print("\nACCOUNTS\n")
    for a in st["accounts"]:
        state = "signed in" if a.get("ok") else (a.get("why") or "not signed in")
        who = f" as {a['email']}" if a.get("email") else ""
        print(f"  {a['label']:<22} {state}{who}")
    if len(st["accounts"]) < 2:
        print("  (no backup account — python -m providers.claude_team --login-backup)")
    if st["struggling"]:
        print("\nBEING STEPPED ROUND RIGHT NOW\n")
        for h in st["struggling"]:
            where = "every account" if h["account"] == ANY else h["account"]
            model = "every model" if h["model"] == ANY else LABEL.get(h["model"], h["model"])
            print(f"  {model:<12} on {where:<14} {h['why']} "
                  f"(tried again in {h['back_in_minutes']} min)")
    today = st["usage_today"]["sectors"]
    if today:
        print("\nTODAY\n")
        for s, u in sorted(today.items()):
            print(f"  {SECTOR_LABEL.get(s, s):<18} {u['calls']:>3} call(s)  "
                  f"{u['tokens_in'] + u['tokens_out']:>9,} tokens  "
                  f"{u['seconds']:>7}s")
    print()


def main(argv=None):
    ap = argparse.ArgumentParser(description="The Claude team: who thinks with what.")
    ap.add_argument("--test", action="store_true",
                    help="one tiny real call per model the team uses, on each account")
    ap.add_argument("--set", nargs=2, metavar=("SECTOR", "MODEL"))
    ap.add_argument("--login-backup", nargs="?", const="", metavar="FOLDER",
                    help="sign a second Claude account in, as the backup")
    ap.add_argument("--clear", action="store_true",
                    help="forget which models were struggling")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.set:
        r = save_model(*a.set)
        print(json.dumps(r, indent=2) if a.json else
              (f"{SECTOR_LABEL[r['sector']]} now thinks with {r['label']}"
               + (f" ({r['note']})" if r.get("note") else "")
               if r["ok"] else "could not: " + r["error"]))
        return 0 if r["ok"] else 1
    if a.login_backup is not None:
        r = login_backup(a.login_backup or None)
        print(json.dumps(r, indent=2) if a.json else
              (f"Backup account signed in{' as ' + r['email'] if r['email'] else ''}"
               f" — kept in {r['folder']}, and saved to .env as {BACKUP_ENV}."
               if r["ok"] else "Not signed in: " + (r.get("error") or "unknown")))
        return 0 if r["ok"] else 1
    if a.clear:
        clear()
        print("Forgotten — every model will be tried at the top again.")
        return 0
    if a.test:
        rows = test()
        if len(accounts()) > 1:
            rows += test(account="backup")
        if a.json:
            print(json.dumps(rows, indent=2))
        else:
            print()
            for r in rows:
                print(f"  {r['account']:<7} {r.get('label', ''):<11} "
                      + (f"answered in {r['seconds']}s" if r["ok"]
                         else f"FAILED — {r['why']}"))
            print()
        return 0 if all(r["ok"] for r in rows) else 1
    st = status()
    if a.json:
        print(json.dumps(st, indent=2))
    else:
        _print_status(st)
    return 0 if st["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
