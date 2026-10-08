"""
engines.py — which AI company does the agency's thinking, and what it costs.

    python -m providers.engines              the engine, its models, today's spend
    python -m providers.engines --json       the same, for the office
    python -m providers.engines --test       one tiny real call per model
    echo '{…}' | python -m providers.engines --save

"go ahead to phase 3" — the plan (docs/PUBLIC-LAUNCH-PLAN.md, idea 2): the
first screen after making an account asks how the agency should think —
Claude, OpenAI or Google Gemini — with the person's own API key, and the red
line on spending becomes real for every user.

THE ENGINES

  claude_code   Claude Code on this computer, signed in with the owner's own
                plan (providers/claude_team.py). Unchanged, and kept for his
                own copy: before accounts exist, and inside the account his
                agency was brought into. NOT offered to a new account —
                Anthropic's terms say a third-party product may not offer
                claude.ai sign-in or plan limits unless Anthropic approves it.
  anthropic     Claude by API key, through Anthropic's official library.
  openai        OpenAI by API key (the Responses API).
  gemini        Google Gemini by API key (generateContent).

Subscriptions for OpenAI and Gemini (driving their command-line tools with a
personal plan) are not built: their terms have to be checked first, the same
way Anthropic's were.

ONE DOOR, SAME ANSWER

Every sector already asks through providers/claude_team.call. That function
hands the call here when an API engine is chosen, and this returns exactly the
shape it always returned — {ok, task, output, used, model_asked,
stepped_down, failovers, skipped} — so not one pipeline had to change.

STRONG, EVERYDAY, LIGHT

Each sector is given a tier, and each engine maps the tiers to its models, so
the Managing Director still gets the strongest mind and Report the lightest
on every engine. The person can change which model fills each tier.

When a model fails — overloaded, too slow, declined, not on the account — the
call steps down to the next tier's model, never up, and says so on the result.

THE SPENDING CAP

API calls cost real money, so the daily cap (DAILY_SPEND_CAP_USD, the one the
cabinet already enforces on every paid tool) is checked before EVERY call,
against everything spent today: AI calls on both of the account's websites,
plus the paid tools. A call that would pass the cap is not made; the sector
gets a plain sentence saying why, and the owner's alert says the same. Three
sectors run side by side, so the cap can be passed by at most the one call
each was already making.

What a call cost is worked out from the tokens the AI company reports and the
prices below — an estimate, labelled "about" everywhere it is shown, because
the bill on the company's own dashboard is the real figure.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

from coordination import workspace as _workspace

CLAUDE_CODE, ANTHROPIC, OPENAI, GEMINI = "claude_code", "anthropic", "openai", "gemini"
API_ENGINES = (ANTHROPIC, OPENAI, GEMINI)
ENGINES = (CLAUDE_CODE,) + API_ENGINES

NAME = {CLAUDE_CODE: "Claude Code on this computer", ANTHROPIC: "Claude",
        OPENAI: "OpenAI", GEMINI: "Google Gemini"}
COMPANY = {ANTHROPIC: "Anthropic", OPENAI: "OpenAI", GEMINI: "Google"}
KEY_SETTING = {ANTHROPIC: "ANTHROPIC_API_KEY", OPENAI: "OPENAI_API_KEY",
               GEMINI: "GEMINI_API_KEY"}
KEY_PAGE = {ANTHROPIC: "https://console.anthropic.com/settings/keys",
            OPENAI: "https://platform.openai.com/api-keys",
            GEMINI: "https://aistudio.google.com/apikey"}
BLURB = {
    ANTHROPIC: "Claude models by Anthropic, paid per use with your own API key.",
    OPENAI: "GPT models by OpenAI, paid per use with your own API key.",
    GEMINI: "Gemini models by Google, paid per use with your own API key.",
    CLAUDE_CODE: "Claude Code installed on this computer, signed in with your own "
                 "Claude plan. Kept for your own copy of the agency.",
}

CONFIG = "engine.local.json"

# ---------------------------------------------------------------------------
# TIERS — which sector gets which strength of mind
# ---------------------------------------------------------------------------

TIERS = ("strong", "standard", "light")
TIER_LABEL = {"strong": "Strongest", "standard": "Everyday", "light": "Lightest"}
TIER_WHY = {
    "strong": "decisions that cost money if wrong: the day's plan, clearing "
              "leads, and changes to your website",
    "standard": "writing, SEO, finding leads and checking the plan",
    "light": "scoring the day for the report",
}
# The same three the Claude team keeps on their full model (claude_team.MAIN).
TIER_OF = {"md": "strong", "security": "strong", "it": "strong",
           "content": "standard", "seo": "standard", "leads": "standard",
           "critic": "standard", "report": "light"}

# ---------------------------------------------------------------------------
# MODELS AND PRICES — checked against each company's own pages, 8 October 2026
# ---------------------------------------------------------------------------
#
# Dollars per million tokens: in, out, and input read from the company's
# cache. Model names and prices change; every model here can be swapped on the
# Engine page, and a model this table does not know is counted at the dearest
# price on its engine, so the cap still holds.

PRICES_CHECKED = "8 October 2026"

MODELS = {
    ANTHROPIC: (("claude-opus-5-5", "Claude Opus 5.5"),
                ("claude-opus-5", "Claude Opus 5"),
                ("claude-sonnet-5-5", "Claude Sonnet 5.5"),
                ("claude-sonnet-5", "Claude Sonnet 5"),
                ("claude-haiku-5-5", "Claude Haiku 5.5"),
                ("claude-haiku-4-5", "Claude Haiku 4.5")),
    OPENAI: (("gpt-6-astra", "GPT-6 Astra"),
             ("gpt-6.1-sol", "GPT-6.1 Sol"),
             ("gpt-6-luna", "GPT-6 Luna"),
             ("gpt-5.5", "GPT-5.5"),
             ("gpt-5.4-mini", "GPT-5.4 mini"),
             ("gpt-5-mini", "GPT-5 mini"),
             ("gpt-5-nano", "GPT-5 nano")),
    GEMINI: (("gemini-3.1-pro-preview", "Gemini 3.1 Pro (preview)"),
             ("gemini-3.8-flash", "Gemini 3.8 Flash"),
             ("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite"),
             ("gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite")),
}

DEFAULTS = {
    ANTHROPIC: {"strong": "claude-opus-5-5", "standard": "claude-sonnet-5-5",
                "light": "claude-haiku-5-5"},
    OPENAI: {"strong": "gpt-6-astra", "standard": "gpt-6.1-sol", "light": "gpt-6-luna"},
    GEMINI: {"strong": "gemini-3.1-pro-preview", "standard": "gemini-3.8-flash",
             "light": "gemini-3.5-flash-lite"},
}

# "over": (prompt tokens, in, out) — the dearer rate for a very long prompt.
# "from": (day, in, out, cached) — a price change already announced.
PRICES = {
    "claude-opus-5-5": {"in": 4.00, "out": 20.00, "cached": 0.20},
    "claude-opus-5": {"in": 5.00, "out": 25.00, "cached": 0.50},
    "claude-sonnet-5-5": {"in": 2.00, "out": 10.00, "cached": 0.20},
    "claude-sonnet-5": {"in": 2.00, "out": 10.00, "cached": 0.20},
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "cached": 0.01,
                         "over": (100_000, 0.50, 2.50)},
    "claude-haiku-4-5": {"in": 1.00, "out": 5.00, "cached": 0.10},
    "claude-opus-4-8": {"in": 5.00, "out": 25.00, "cached": 0.50},
    "gpt-6-astra": {"in": 10.00, "out": 50.00, "cached": 1.00},
    "gpt-6.1-sol": {"in": 2.00, "out": 10.00, "cached": 0.10},
    "gpt-6-luna": {"in": 0.10, "out": 0.50, "cached": 0.01},
    "gpt-5.5": {"in": 5.00, "out": 30.00, "cached": 0.50},
    "gpt-5.4-mini": {"in": 0.75, "out": 4.50, "cached": 0.075},
    "gpt-5-mini": {"in": 0.25, "out": 2.00, "cached": 0.025},
    "gpt-5-nano": {"in": 0.05, "out": 0.40, "cached": 0.005},
    "gemini-3.1-pro-preview": {"in": 2.00, "out": 12.00, "cached": 0.20,
                               "over": (200_000, 4.00, 18.00)},
    "gemini-3.8-flash": {"in": 0.75, "out": 3.75, "cached": 0.075,
                         "from": ("2027-01-01", 1.50, 7.50, 0.15)},
    "gemini-3.5-flash-lite": {"in": 0.30, "out": 2.50, "cached": 0.03},
    "gemini-3.1-flash-lite": {"in": 0.25, "out": 1.50, "cached": 0.025},
}
CACHE_WRITE = 1.25          # Anthropic: writing the cache costs 1.25x input

# Claude Haiku 4.5 takes no effort setting.
ANTHROPIC_NO_EFFORT = ("claude-haiku-4-5",)
# Server-side fallback when a model's safety check declines a request — "re-runs
# a declined request on another model server-side instead of returning the
# refusal". Opus only: Claude Haiku 5.5 rejects the parameter outright.
ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"
ANTHROPIC_SERVER_FALLBACK = ("claude-opus-5-5", "claude-opus-5")

EFFORT_ORDINARY, EFFORT_DEMANDING = "medium", "high"

# ---------------------------------------------------------------------------
# TIME
# ---------------------------------------------------------------------------

BUDGET = 360                # one sector's call, every attempt together
PER_ATTEMPT = 240           # the longest one attempt may take
LEAST_ATTEMPT = 30          # not worth starting another with less left
# The reply AND the model's thinking come out of the same allowance, so a
# caller's "512 tokens" is a size for the answer, not a limit for the model.
ROOM_FLOOR, ROOM_CEILING = 16_000, 32_000

# What failed, and whether a lighter model is worth trying.
BAD_KEY, NO_CREDIT, RATE, BUSY, SLOW, DECLINED, NO_MODEL, CUT, EMPTY, UNREACHABLE, OTHER = (
    "the API key was refused", "the account has no credit left",
    "rate limited", "overloaded", "timed out", "declined by the model's safety check",
    "model not available on this key", "answer cut off", "empty answer",
    "could not reach the AI company", "error")
STOP_HERE = (BAD_KEY, NO_CREDIT)   # a lighter model on the same key fails the same way

# Tests put a fake company here: (engine, model, system, prompt, room, effort,
# key, timeout) -> the normalised answer _send returns.
TRANSPORT = None

_FALLBACK_REFUSED = set()   # models that turned the fallbacks parameter down


# ---------------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------------

def _suite_isolated() -> bool:
    return str(os.environ.get("AGENCY_IGNORE_DOTENV", "")).strip().lower() in (
        "1", "true", "yes", "on")


def _config_path() -> Path:
    return _workspace.config_path(CONFIG)


def _config() -> dict:
    p = _config_path()
    # The suite plans against the defaults, never the owner's own choice (see
    # claude_team._saved): his file sits in config/ before accounts exist.
    if _suite_isolated() and p == _workspace.ROOT / "config" / CONFIG:
        return {}
    try:
        d = json.loads(_workspace.read_secret(p, "") or "{}")
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def _save_config(d: dict) -> None:
    _workspace.write_secret(_config_path(), json.dumps(d, indent=2))


def _setting(name: str) -> str:
    """
    A setting from the person's own settings file. Before accounts exist the
    computer's environment counts too — it is the owner's machine. Inside an
    account it does not: a key exported on the computer would otherwise be
    spent for whoever happened to sign in.
    """
    try:
        from . import integrations
        got = (integrations.read_env().get(name) or "").strip()
    except Exception:
        got = ""
    if got:
        return got
    if _workspace.legacy():
        return (os.environ.get(name) or "").strip()
    return ""


def claude_code_allowed() -> bool:
    """His own copy: before any account, or the account his agency was brought into."""
    a = _workspace.active()
    if not a:
        return True
    try:
        return bool(_workspace.account(a["account"]).get("brought_in"))
    except Exception:
        return False


def engine() -> str:
    """The engine in use, or "" when a new account has not chosen one yet."""
    chosen = str(_config().get("engine") or "").strip().lower()
    if chosen in API_ENGINES:
        return chosen
    if claude_code_allowed():
        return CLAUDE_CODE
    return ""


def is_api() -> bool:
    return engine() in API_ENGINES


def key(eng=None) -> str:
    eng = eng or engine()
    return _setting(KEY_SETTING[eng]) if eng in KEY_SETTING else ""


def key_hint(eng) -> str:
    k = key(eng)
    return ("…" + k[-4:]) if len(k) >= 12 else ("saved" if k else "")


def label(model) -> str:
    for rows in MODELS.values():
        for m, lab in rows:
            if m == model:
                return lab
    return model


def models(eng=None) -> dict:
    """tier -> model for this engine: the defaults with the person's choices on top."""
    eng = eng or engine()
    out = dict(DEFAULTS.get(eng, {}))
    saved = ((_config().get("models") or {}).get(eng) or {})
    for t, m in saved.items():
        if t in TIERS and isinstance(m, str) and _model_ok(m):
            out[t] = m.strip()
    return out


def _model_ok(m) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:\-]{1,80}", str(m or "").strip()))


def tier_of(sector) -> str:
    from . import claude_team
    return TIER_OF.get(claude_team.normalise(sector), "standard")


def model_for(sector, eng=None) -> str:
    eng = eng or engine()
    if eng == CLAUDE_CODE:
        from . import claude_team
        return claude_team.model_for(sector)
    return models(eng).get(tier_of(sector), "")


def second_model(sector) -> str:
    """The second opinion: the Critic's tier, or the tier below when they match."""
    mine = model_for(sector)
    other = model_for("critic")
    if other != mine:
        return other
    lower = _below(engine(), mine)
    return lower[0] if lower else mine


def _below(eng, model) -> list:
    """The lighter tiers' models, strongest first, for stepping down."""
    tiers = models(eng)
    order = [tiers[t] for t in TIERS if t in tiers]
    if model in order:
        rest = order[order.index(model) + 1:]
    else:
        rest = order[1:]
    out = []
    for m in rest:
        if m != model and m not in out:
            out.append(m)
    return out


# ---------------------------------------------------------------------------
# IS IT READY?
# ---------------------------------------------------------------------------

NOT_CHOSEN = ("No AI engine is chosen for this account yet — open the Engine page "
              "and pick Claude, OpenAI or Google Gemini.")


def available(eng=None) -> tuple:
    """(ready, why_not) for the engine in use — checked before every call, spends nothing."""
    eng = engine() if eng is None else eng
    if eng == CLAUDE_CODE:
        from . import local_cli
        return local_cli.available()
    if not eng:
        return False, NOT_CHOSEN
    if not key(eng):
        return False, (f"No {COMPANY[eng]} API key is saved yet — paste one on the "
                       f"Engine page (you get it at {KEY_PAGE[eng]}).")
    if TRANSPORT is not None:
        return True, ""                           # the tests' stand-in company
    if eng == ANTHROPIC:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, ("Anthropic's library is not installed — run: "
                           "pip install -r requirements.txt")
    from . import local_cli
    if local_cli.switched_off():
        return False, (f"{NAME[eng]} is switched off for this run ({local_cli.OFF_ENV}) — "
                       "the test suite never spends real money.")
    return True, ""


def _unavailable_cause(why: str) -> str:
    if why == NOT_CHOSEN:
        return "no AI engine chosen"
    if "switched off" in why:
        return "the AI is switched off for this run"
    if "API key" in why:
        return "no API key saved"
    if "library" in why:
        return "a library is missing"
    return "the AI engine is not ready"


# ---------------------------------------------------------------------------
# WHAT IT COSTS
# ---------------------------------------------------------------------------

def _price(eng, model, day=None) -> dict:
    p = PRICES.get(model)
    if p is None:
        # A dated name (gpt-6-astra-2026-09-30) is the same model.
        base = max((m for m in PRICES if model.startswith(m + "-")), key=len, default="")
        p = PRICES.get(base)
    if p is None:
        # Unknown: the dearest on its engine, so the cap is never under-counted.
        known = [PRICES[m] for m, _ in MODELS.get(eng, ()) if m in PRICES]
        p = max(known, key=lambda x: x["out"]) if known else {"in": 10.0, "out": 50.0,
                                                              "cached": 1.0}
    p = dict(p)
    later = p.get("from")
    if later and (day or time.strftime("%Y-%m-%d")) >= later[0]:
        p.update({"in": later[1], "out": later[2], "cached": later[3]})
    return p


def cost(eng, model, usage, day=None) -> float:
    """Dollars for one call, from the tokens the company reported."""
    p = _price(eng, model, day)
    inp = int(usage.get("input") or 0)
    cached = int(usage.get("cached") or 0)
    write = int(usage.get("cache_write") or 0)
    out = int(usage.get("output") or 0)
    rate_in, rate_out = p["in"], p["out"]
    over = p.get("over")
    if over and inp + cached + write > over[0]:
        rate_in, rate_out = over[1], over[2]
    if eng == ANTHROPIC:
        # Anthropic reports cache reads and writes ALONGSIDE input_tokens.
        usd = inp * rate_in + cached * p["cached"] + write * rate_in * CACHE_WRITE
    else:
        # OpenAI and Google count cached tokens INSIDE the input total.
        usd = max(0, inp - cached) * rate_in + cached * p["cached"]
    return round((usd + out * rate_out) / 1_000_000, 6)


def estimate(eng, model, system, prompt, max_tokens) -> float:
    """A rough upper guess at one call's cost, for the cap — before it is made."""
    inp = int(len((system or "") + (prompt or "")) / 3.5) + 50
    out = min(_room(max_tokens), max(int(max_tokens or 0), 512) + 2000)
    return cost(eng, model, {"input": inp, "output": out})


def _room(max_tokens) -> int:
    return min(ROOM_CEILING, max(ROOM_FLOOR, int(max_tokens or 0) * 2))


# ---------------------------------------------------------------------------
# THE LEDGER — every call, in the cabinet the Claude team already writes to
# ---------------------------------------------------------------------------

_HAS_ENGINE_COLUMN = set()


def _conn():
    from . import claude_team
    c = claude_team._conn()
    if c is not None:
        _ensure_column(c, str(_db_path()))
    return c


def _db_path() -> Path:
    from coordination import board
    return Path(board.DB)


def _ensure_column(c, where):
    if where in _HAS_ENGINE_COLUMN:
        return
    cols = {r[1] for r in c.execute("PRAGMA table_info(claude_usage)")}
    if "engine" not in cols:
        try:
            c.execute("ALTER TABLE claude_usage ADD COLUMN engine TEXT")
        except sqlite3.Error:
            pass
    _HAS_ENGINE_COLUMN.add(where)


def record(sector, agent, eng, asked, ran, ok, seconds, usage, usd, why=""):
    c = _conn()
    if c is None:
        return
    u = usage or {}
    with c:
        c.execute("INSERT INTO claude_usage (day, at, sector, agent, account,"
                  " model_asked, model_ran, ok, seconds, input, output, cache_read,"
                  " cache_write, api_usd, why, engine)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (time.strftime("%Y-%m-%d"), int(time.time()), sector, agent or "",
                   eng, asked, ran if ok else "", 1 if ok else 0, float(seconds or 0),
                   int(u.get("input") or 0), int(u.get("output") or 0),
                   int(u.get("cached") or 0), int(u.get("cache_write") or 0),
                   float(usd or 0), (why or "")[:120], eng))
    c.close()


def _cabinets() -> list:
    """Every cabinet whose AI spending counts against this account's cap."""
    if os.environ.get("AGENCY_DB"):
        return [Path(os.environ["AGENCY_DB"])]
    a = _workspace.active()
    if not a:
        return [_workspace.db_path()]
    sites = _workspace.account_dir(a["account"]) / "sites"
    try:
        return [d / "agency.db" for d in sorted(sites.iterdir())
                if (d / "agency.db").exists()]
    except OSError:
        return [_workspace.db_path()]


def ai_spent_today(day=None) -> float:
    """
    Dollars paid to AI companies today, across both of the account's websites.
    Claude Code's figures are left out: they are what the same tokens WOULD
    cost by API, paid for by his plan, not money spent.
    """
    day = day or time.strftime("%Y-%m-%d")
    total = 0.0
    marks = ",".join("?" * len(API_ENGINES))
    for db in _cabinets():
        if not Path(db).exists():
            continue                   # connecting would make an empty cabinet
        try:
            c = sqlite3.connect(str(db), timeout=10)
        except Exception:
            continue
        try:
            r = c.execute("SELECT COALESCE(SUM(api_usd),0) FROM claude_usage"
                          f" WHERE day=? AND engine IN ({marks})",
                          (day, *API_ENGINES)).fetchone()
            total += float(r[0] or 0)
        except sqlite3.Error:
            pass                       # no AI calls on that website yet
        finally:
            c.close()
    return round(total, 6)


def cap_block(est=0.0) -> str:
    """Why this call may not be made because of the cap, or "" when it may."""
    try:
        from coordination import cabinet
        cap, so_far = cabinet.daily_spend_cap(), cabinet.spent_today()
    except Exception as e:
        return f"the spending cap could not be checked ({e}), so nothing was spent"
    if so_far >= cap:
        return (f"Today's spending cap is reached (about ${so_far:.2f} of ${cap:.2f}). "
                "The AI is not asked anything more today. Raise the cap on the "
                "Engine page if you want the agency to keep going.")
    if est and so_far + est > cap:
        return (f"This call would pass today's spending cap (about ${so_far:.2f} spent, "
                f"cap ${cap:.2f}, this call about ${est:.2f}), so it was not made. "
                "Raise the cap on the Engine page if you want the agency to keep going.")
    return ""


# ---------------------------------------------------------------------------
# THE CALL
# ---------------------------------------------------------------------------

def call(sector, messages, max_tokens=512, timeout=0, *, model=None,
         demanding=None, agent="") -> dict:
    """Ask this sector's model on the chosen API engine. Same shape as claude_team.call."""
    from . import claude_team, local_cli
    s = claude_team.normalise(sector)
    eng = engine()
    ready, why = available(eng)
    if not ready:
        return {"ok": False, "task": s, "error": why, "cause": _unavailable_cause(why),
                "failovers": [], "skipped": []}
    if demanding is None:
        from . import effort
        demanding = bool(effort.why_demanding())
    asked = (model or "").strip() or model_for(s, eng)
    order = [asked] + _below(eng, asked)
    system, prompt = local_cli.split_messages(messages)
    secret = key(eng)
    deadline = time.time() + max(BUDGET, int(timeout or 0))
    failovers, skipped = [], []
    for m in order:
        left = int(deadline - time.time())
        if failovers and left < LEAST_ATTEMPT:
            break
        blocked = cap_block(estimate(eng, m, system, prompt, max_tokens))
        if blocked:
            record(s, agent, eng, m, "", False, 0, {}, 0, "spending cap")
            return {"ok": False, "task": s, "error": blocked, "capped": True,
                    "cause": "daily spending cap reached", "model_asked": asked,
                    "failovers": failovers, "skipped": skipped}
        t0 = time.time()
        res = _send(eng, m, system, prompt, _room(max_tokens),
                    _effort(eng, m, demanding), secret, max(1, min(left, PER_ATTEMPT)))
        took = round(time.time() - t0, 2)
        ran = res.get("ran") or m
        usd = _cost_of(eng, ran, res)
        record(s, agent, eng, m, ran, res.get("ok"), took, res.get("usage"), usd,
               "" if res.get("ok") else res.get("cause", ""))
        if res.get("ok"):
            stepped = "" if m == asked and not res.get("note") else (
                f"{claude_team.SECTOR_LABEL.get(s, s)} asked for {label(asked)} and was "
                f"answered by {label(ran)}"
                + (f" — {failovers[-1]['cause']}" if failovers else "")
                + (f" ({res['note']})" if res.get("note") else ""))
            return {"ok": True, "task": s, "output": res.get("text", ""),
                    "used": {"credential": f"{NAME[eng]} API key", "model": ran,
                             "account": eng},
                    "model_asked": asked, "stepped_down": stepped,
                    "failovers": failovers, "skipped": skipped,
                    "seconds": took, "usd": usd, "engine": eng, "load": ""}
        failovers.append({"model": m, "account": eng, "status": res.get("status", 0),
                          "cause": res.get("cause", OTHER),
                          "error": _scrub(res.get("error") or "", secret)[:300],
                          "waited": [took]})
        if res.get("cause") in STOP_HERE:
            break
    return {"ok": False, "task": s, "failovers": failovers, "skipped": skipped,
            "model_asked": asked, "engine": eng,
            "cause": failovers[-1]["cause"] if failovers else OTHER,
            "error": _why_nobody(s, eng, asked, failovers)}


def _cost_of(eng, ran, res) -> float:
    """Each hop priced at its own model when the company ran more than one."""
    hops = res.get("hops")
    if hops:
        return round(sum(cost(eng, h.get("model") or ran, h) for h in hops), 6)
    return cost(eng, ran, res.get("usage") or {})


def _why_nobody(sector, eng, asked, failovers) -> str:
    from . import claude_team
    who = claude_team.SECTOR_LABEL.get(sector, sector)
    bits = [f"{label(f['model'])}: {f['cause']}" for f in failovers[:4]]
    hint = ""
    last = failovers[-1]["cause"] if failovers else ""
    if last == BAD_KEY:
        hint = f" Check the key on the Engine page — a new one is made at {KEY_PAGE[eng]}."
    elif last == NO_CREDIT:
        hint = f" Add credit on your {COMPANY[eng]} account, then press Test on the Engine page."
    return (f"{who} could not get an answer from {NAME[eng]} (asked for {label(asked)}). "
            + "; ".join(bits) + "." + hint)


def _effort(eng, model, demanding) -> str:
    if eng == GEMINI:
        return ""                 # Gemini thinks as much as it decides to
    if eng == ANTHROPIC and model in ANTHROPIC_NO_EFFORT:
        return ""
    return EFFORT_DEMANDING if demanding else EFFORT_ORDINARY


def _scrub(text, secret) -> str:
    return text.replace(secret, "[key]") if secret else text


def _answer(ok, text="", *, cause="", error="", status=0, usage=None, ran="",
            note="", hops=None) -> dict:
    return {"ok": bool(ok), "text": text if ok else "", "cause": cause, "error": error,
            "status": status, "usage": usage or {}, "ran": ran, "note": note,
            "hops": hops or []}


def _send(eng, model, system, prompt, room, eff, secret, timeout) -> dict:
    if TRANSPORT is not None:
        return TRANSPORT(eng, model, system, prompt, room, eff, secret, timeout)
    try:
        if eng == ANTHROPIC:
            return _anthropic(model, system, prompt, room, eff, secret, timeout)
        if eng == OPENAI:
            return _openai(model, system, prompt, room, eff, secret, timeout)
        if eng == GEMINI:
            return _gemini(model, system, prompt, room, secret, timeout)
    except Exception as e:                                   # pragma: no cover
        return _answer(False, cause=OTHER, error=_scrub(str(e), secret))
    return _answer(False, cause=OTHER, error=f"unknown engine {eng}")


# ---- Anthropic: the official library ----------------------------------------

def _anthropic(model, system, prompt, room, eff, secret, timeout) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=secret, timeout=float(timeout), max_retries=2)
    kw = {"model": model, "max_tokens": room,
          "messages": [{"role": "user", "content": prompt or "(no request)"}]}
    if system:
        kw["system"] = system
    if eff:
        kw["output_config"] = {"effort": eff}
    fallback = model in ANTHROPIC_SERVER_FALLBACK and model not in _FALLBACK_REFUSED
    try:
        try:
            if fallback:
                msg = client.beta.messages.create(**kw, fallbacks="default",
                                                  betas=[ANTHROPIC_FALLBACK_BETA],
                                                  timeout=float(timeout))
            else:
                msg = client.messages.create(**kw, timeout=float(timeout))
        except anthropic.BadRequestError as e:
            if not (fallback and "fallback" in str(e).lower()):
                raise
            _FALLBACK_REFUSED.add(model)
            msg = client.messages.create(**kw, timeout=float(timeout))
    except anthropic.AuthenticationError as e:
        return _answer(False, cause=BAD_KEY, error=str(e), status=401)
    except anthropic.PermissionDeniedError as e:
        return _answer(False, cause=BAD_KEY, error=str(e), status=403)
    except anthropic.NotFoundError as e:
        return _answer(False, cause=NO_MODEL, error=str(e), status=404)
    except anthropic.RateLimitError as e:
        return _answer(False, cause=RATE, error=str(e), status=429)
    except anthropic.APITimeoutError as e:
        return _answer(False, cause=SLOW, error=str(e))
    except anthropic.APIConnectionError as e:
        return _answer(False, cause=UNREACHABLE, error=str(e))
    except anthropic.APIStatusError as e:
        text = str(e)
        if "credit balance" in text.lower() or "billing" in text.lower():
            return _answer(False, cause=NO_CREDIT, error=text, status=e.status_code)
        busy = e.status_code in (500, 502, 503, 504, 529) or "overloaded" in text.lower()
        return _answer(False, cause=BUSY if busy else OTHER, error=text,
                       status=e.status_code)
    return anthropic_parse(msg.model_dump(), model)


def anthropic_parse(d, asked) -> dict:
    """A Messages API reply (as a dict) in the one shape every engine returns."""
    u = d.get("usage") or {}
    usage = {"input": u.get("input_tokens") or 0, "output": u.get("output_tokens") or 0,
             "cached": u.get("cache_read_input_tokens") or 0,
             "cache_write": u.get("cache_creation_input_tokens") or 0}
    ran = d.get("model") or asked
    hops = []
    for it in u.get("iterations") or []:
        if not isinstance(it, dict):
            continue
        hop_model = it.get("model") or asked
        hops.append({"model": hop_model, "input": it.get("input_tokens") or 0,
                     "output": it.get("output_tokens") or 0,
                     "cached": it.get("cache_read_input_tokens") or 0,
                     "cache_write": it.get("cache_creation_input_tokens") or 0})
        if it.get("type") == "fallback_message" and it.get("model"):
            ran = it["model"]
    if hops:
        usage = {k: sum(h[k] for h in hops) for k in ("input", "output", "cached",
                                                        "cache_write")}
    stop = d.get("stop_reason")
    if stop == "refusal":
        cat = ((d.get("stop_details") or {}).get("category") or "")
        return _answer(False, cause=DECLINED, usage=usage, ran=ran, hops=hops,
                       error="the model declined this request"
                             + (f" ({cat})" if cat else ""))
    text = "".join(b.get("text") or "" for b in d.get("content") or []
                   if isinstance(b, dict) and b.get("type") == "text")
    if not text.strip():
        return _answer(False, cause=CUT if stop == "max_tokens" else EMPTY,
                       usage=usage, ran=ran, hops=hops,
                       error=f"no text in the reply (stopped: {stop})")
    return _answer(True, text, usage=usage, ran=ran, hops=hops,
                   note="the answer was cut off at its length limit"
                        if stop == "max_tokens" else "")


# ---- OpenAI: the Responses API ------------------------------------------------

OPENAI_URL = "https://api.openai.com/v1/responses"


def _openai(model, system, prompt, room, eff, secret, timeout) -> dict:
    body = {"model": model, "input": prompt or "(no request)", "max_output_tokens": room}
    if system:
        body["instructions"] = system
    if eff:
        body["reasoning"] = {"effort": eff}
    headers = {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}
    status, d, err = _post(OPENAI_URL, headers, body, timeout)
    if status == 400 and eff and "reasoning" in err.lower():
        # A model that does not reason turns the effort setting down.
        body.pop("reasoning", None)
        status, d, err = _post(OPENAI_URL, headers, body, timeout)
    if status != 200:
        return _http_failure(status, err, d)
    return openai_parse(d, model)


def openai_parse(d, asked) -> dict:
    u = d.get("usage") or {}
    cached = ((u.get("input_tokens_details") or {}).get("cached_tokens") or 0)
    usage = {"input": u.get("input_tokens") or 0, "output": u.get("output_tokens") or 0,
             "cached": cached, "cache_write": 0}
    ran = d.get("model") or asked
    text, refusal = [], []
    for item in d.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") == "output_text":
                text.append(part.get("text") or "")
            elif part.get("type") == "refusal":
                refusal.append(part.get("refusal") or "")
    reason = ((d.get("incomplete_details") or {}).get("reason") or "")
    if refusal and not "".join(text).strip():
        return _answer(False, cause=DECLINED, usage=usage, ran=ran,
                       error="the model declined: " + " ".join(refusal)[:200])
    if reason == "content_filter" and not "".join(text).strip():
        return _answer(False, cause=DECLINED, usage=usage, ran=ran,
                       error="the reply was stopped by OpenAI's content filter")
    out = "".join(text)
    if not out.strip():
        return _answer(False, cause=CUT if reason == "max_output_tokens" else EMPTY,
                       usage=usage, ran=ran,
                       error=f"no text in the reply ({d.get('status') or 'no status'})")
    return _answer(True, out, usage=usage, ran=ran,
                   note="the answer was cut off at its length limit"
                        if reason == "max_output_tokens" else "")


# ---- Google: generateContent --------------------------------------------------

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _gemini(model, system, prompt, room, secret, timeout) -> dict:
    body = {"contents": [{"role": "user", "parts": [{"text": prompt or "(no request)"}]}],
            "generationConfig": {"maxOutputTokens": room}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    # The key goes in a header, never the address, so it is in no log line.
    headers = {"x-goog-api-key": secret, "Content-Type": "application/json"}
    status, d, err = _post(GEMINI_URL.format(model=model), headers, body, timeout)
    if status != 200:
        return _http_failure(status, err, d, google=True)
    return gemini_parse(d, model)


_GEMINI_DECLINED = ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION",
                    "IMAGE_SAFETY")


def gemini_parse(d, asked) -> dict:
    u = d.get("usageMetadata") or {}
    usage = {"input": u.get("promptTokenCount") or 0,
             # Google bills thinking as output.
             "output": (u.get("candidatesTokenCount") or 0) + (u.get("thoughtsTokenCount") or 0),
             "cached": u.get("cachedContentTokenCount") or 0, "cache_write": 0}
    ran = d.get("modelVersion") or asked
    blocked = (d.get("promptFeedback") or {}).get("blockReason")
    if blocked:
        return _answer(False, cause=DECLINED, usage=usage, ran=ran,
                       error=f"Google blocked the request ({blocked})")
    cands = d.get("candidates") or []
    first = cands[0] if cands else {}
    finish = first.get("finishReason") or ""
    parts = ((first.get("content") or {}).get("parts") or [])
    out = "".join(p.get("text") or "" for p in parts
                  if isinstance(p, dict) and not p.get("thought"))
    if not out.strip():
        if finish in _GEMINI_DECLINED:
            return _answer(False, cause=DECLINED, usage=usage, ran=ran,
                           error=f"Google stopped the reply ({finish})")
        return _answer(False, cause=CUT if finish == "MAX_TOKENS" else EMPTY,
                       usage=usage, ran=ran, error=f"no text in the reply ({finish or 'no reason'})")
    return _answer(True, out, usage=usage, ran=ran,
                   note="the answer was cut off at its length limit"
                        if finish == "MAX_TOKENS" else "")


# ---- plain HTTP, for OpenAI and Google ---------------------------------------

RETRY_STATUSES = (429, 500, 502, 503, 504)
RETRY_WAITS = (2, 6)


def _post(url, headers, body, timeout):
    """(status, json, error text). Retries a busy company twice, politely."""
    import requests
    deadline = time.time() + timeout
    waits = list(RETRY_WAITS)
    while True:
        try:
            r = requests.post(url, headers=headers, json=body,
                              timeout=max(5, deadline - time.time()))
        except requests.Timeout as e:
            return -1, {}, f"timed out: {e}"
        except requests.RequestException as e:
            return 0, {}, f"could not connect: {type(e).__name__}"
        try:
            d = r.json()
        except ValueError:
            d = {}
        if r.status_code == 200:
            return 200, d, ""
        err = json.dumps(d.get("error") if isinstance(d, dict) and d.get("error") else d)[:600] \
            if d else (r.text or "")[:600]
        quota = "insufficient_quota" in err
        if r.status_code in RETRY_STATUSES and waits and not quota:
            wait = waits.pop(0)
            try:
                wait = max(wait, min(20, int(r.headers.get("retry-after") or 0)))
            except ValueError:
                pass
            if time.time() + wait < deadline - 10:
                time.sleep(wait)
                continue
        return r.status_code, d, err


def _http_failure(status, err, d, google=False) -> dict:
    low = (err or "").lower()
    if status == -1:
        cause = SLOW
    elif status == 0:
        cause = UNREACHABLE
    elif status in (401, 403) or (google and status == 400 and "api key" in low):
        cause = BAD_KEY
    elif "insufficient_quota" in low or "billing" in low or "credit" in low:
        cause = NO_CREDIT
    elif status == 429:
        cause = RATE
    elif status == 404 or ("model" in low and ("not found" in low or "does not exist" in low)):
        cause = NO_MODEL
    elif status in (500, 502, 503, 504) or "overloaded" in low:
        cause = BUSY
    else:
        cause = OTHER
    return _answer(False, cause=cause, error=err or f"HTTP {status}", status=max(status, 0))


# ---------------------------------------------------------------------------
# TESTING, SAVING, AND WHAT THE ENGINE PAGE SHOWS
# ---------------------------------------------------------------------------

def test(eng=None, only=None) -> list:
    """One tiny real call per model the tiers use — the only proof a key works today."""
    eng = eng or engine()
    if eng not in API_ENGINES:
        return [{"engine": eng, "ok": False, "why": "not an API engine"}]
    ready, why = available(eng)
    tiers = models(eng)
    wanted = [m for m in dict.fromkeys(tiers[t] for t in TIERS if t in tiers)
              if not only or m == only]
    rows = []
    for m in wanted:
        if not ready:
            rows.append({"engine": eng, "account": eng, "model": m, "label": label(m),
                         "ok": False, "why": why, "seconds": 0, "usd": 0})
            continue
        blocked = cap_block(estimate(eng, m, "", "Reply with exactly: OK", 16))
        if blocked:
            rows.append({"engine": eng, "account": eng, "model": m, "label": label(m),
                         "ok": False, "why": blocked, "seconds": 0, "usd": 0})
            continue
        t0 = time.time()
        res = _send(eng, m, "", "Reply with exactly: OK", _room(16), _effort(eng, m, False),
                    key(eng), 120)
        took = round(time.time() - t0, 2)
        ran = res.get("ran") or m
        usd = _cost_of(eng, ran, res)
        record("setup-test", "", eng, m, ran, res.get("ok"), took, res.get("usage"), usd,
               "" if res.get("ok") else res.get("cause", ""))
        rows.append({"engine": eng, "account": eng, "model": m, "label": label(m),
                     "ok": bool(res.get("ok")), "ran": ran if res.get("ok") else "",
                     "seconds": took, "usd": usd,
                     "why": "" if res.get("ok") else
                            (res.get("cause") or OTHER) + (": " + _scrub(res.get("error") or "",
                                                                         key(eng))[:200]
                                                           if res.get("error") else "")})
    return rows


def save(data: dict) -> dict:
    """
    What the Engine page sent: {engine, key, models: {tier: model}, cap}.
    The key is written to the person's own settings file (sealed with their
    account key) and never sent back.
    """
    from . import integrations
    if not isinstance(data, dict):
        return {"ok": False, "error": "nothing to save"}
    cfg = _config()
    eng = str(data.get("engine") or cfg.get("engine") or "").strip().lower()
    if eng and eng not in ENGINES:
        return {"ok": False, "error": f"'{eng}' is not an engine. Engines: "
                                      + ", ".join(e for e in ENGINES)}
    if eng == CLAUDE_CODE and not claude_code_allowed():
        return {"ok": False, "error": (
            "Claude Code with a Claude plan is not offered in this version: Anthropic's "
            "terms do not let a third-party product use claude.ai sign-in or plan "
            "limits without their approval. Choose Claude with an API key instead.")}
    secret = str(data.get("key") or "").strip()
    if secret:
        if eng not in KEY_SETTING:
            return {"ok": False, "error": "pick Claude, OpenAI or Google Gemini before "
                                          "pasting a key"}
        if len(secret) < 12 or re.search(r"\s", secret):
            return {"ok": False, "error": "that does not look like an API key — copy it "
                                          "again, without spaces"}
        r = integrations.write_env({KEY_SETTING[eng]: secret})
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error") or "the key could not be saved"}
    if data.get("forget_key") and eng in KEY_SETTING:
        forget = integrations.forget_env([KEY_SETTING[eng]])
        if not forget.get("ok"):
            return {"ok": False, "error": forget.get("error") or "could not forget the key"}
    chosen = data.get("models")
    if isinstance(chosen, dict) and eng in API_ENGINES:
        mine = dict((cfg.get("models") or {}).get(eng) or {})
        for t, m in chosen.items():
            m = str(m or "").strip()
            if t not in TIERS:
                continue
            if not m or m == DEFAULTS[eng].get(t):
                mine.pop(t, None)
            elif _model_ok(m):
                mine[t] = m
            else:
                return {"ok": False, "error": f"'{m}' is not a model name"}
        cfg.setdefault("models", {})[eng] = mine
    if "cap" in data and str(data.get("cap") or "").strip():
        try:
            cap = float(str(data["cap"]).strip().lstrip("$"))
        except ValueError:
            return {"ok": False, "error": "the daily cap must be a number of dollars"}
        if cap <= 0 or cap > 10_000:
            return {"ok": False, "error": "the daily cap must be between $0.01 and $10,000"}
        r = integrations.write_env({"DAILY_SPEND_CAP_USD": f"{cap:g}"})
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error") or "the cap could not be saved"}
    if eng:
        cfg["engine"] = eng
    cfg["chosen_at"] = int(time.time())
    _save_config(cfg)
    return {"ok": True, "engine": engine(), "ready": available()[0]}


def _history(days=7) -> dict:
    """tier -> (input tokens, output tokens) per day, from the agency's own recent work."""
    c = _conn()
    if c is None:
        return {}
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    try:
        rows = c.execute("SELECT sector, count(DISTINCT day) days,"
                         " sum(input + cache_read + cache_write) tin, sum(output) tout"
                         " FROM claude_usage WHERE day >= ? AND ok = 1"
                         " AND sector != 'setup-test' GROUP BY sector", (since,)).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        c.close()
    days_seen = max([r[1] for r in rows] or [0])
    if not days_seen:
        return {}
    out = {}
    for sector, _d, tin, tout in rows:
        t = TIER_OF.get(sector)
        if not t:
            continue
        a, b = out.get(t, (0, 0))
        out[t] = (a + (tin or 0) / days_seen, b + (tout or 0) / days_seen)
    return out


# A typical day when the agency has no history yet: tokens in and out per tier.
TYPICAL_DAY = {"strong": (150_000, 40_000), "standard": (500_000, 120_000),
               "light": (40_000, 8_000)}


def estimate_day(eng=None) -> dict:
    """About what a day costs on this engine at these models — from history when there is some."""
    eng = eng or engine()
    if eng not in API_ENGINES:
        return {"usd": 0.0, "basis": "plan"}
    seen = _history()
    basis = "your last days" if seen else "a typical day"
    use = seen or TYPICAL_DAY
    tiers = models(eng)
    usd = sum(cost(eng, tiers.get(t, ""), {"input": int(i), "output": int(o)})
              for t, (i, o) in use.items() if tiers.get(t))
    return {"usd": round(usd, 2), "basis": basis}


def status() -> dict:
    """Everything the Engine page shows. Never a key — only whether one is saved."""
    from . import claude_team
    eng = engine()
    ready, why = available(eng)
    try:
        from coordination import cabinet
        cap, spent_all = cabinet.daily_spend_cap(), cabinet.spent_today()
    except Exception:
        cap, spent_all = 0.0, 0.0
    options = []
    for e in API_ENGINES:
        tiers = models(e)
        options.append({
            "id": e, "name": NAME[e], "company": COMPANY[e], "blurb": BLURB[e],
            "key_page": KEY_PAGE[e], "key_saved": bool(key(e)), "key_hint": key_hint(e),
            "models": [{"id": m, "label": lab, "in": PRICES.get(m, {}).get("in"),
                        "out": PRICES.get(m, {}).get("out")} for m, lab in MODELS[e]],
            "tiers": tiers, "defaults": DEFAULTS[e],
            "estimate": estimate_day(e)})
    if claude_code_allowed():
        options.append({"id": CLAUDE_CODE, "name": NAME[CLAUDE_CODE],
                        "blurb": BLURB[CLAUDE_CODE], "key_saved": True})
    return {
        "ok": True, "engine": eng, "name": NAME.get(eng, ""), "chosen": bool(eng),
        "ready": ready, "why": why, "claude_code_allowed": claude_code_allowed(),
        "options": options,
        "tiers": [{"id": t, "label": TIER_LABEL[t], "why": TIER_WHY[t],
                   "sectors": [claude_team.SECTOR_LABEL[s] for s in claude_team.SECTORS
                               if TIER_OF.get(s) == t]} for t in TIERS],
        "cap": cap, "spent_ai": ai_spent_today(), "spent_all": spent_all,
        "estimate": estimate_day(eng), "prices_checked": PRICES_CHECKED,
        "usage_today": claude_team.usage(),
    }


def brief() -> dict:
    """The few facts the office shows on every screen. Cheap; no key, no history."""
    eng = engine()
    ready, why = available(eng)
    try:
        from coordination import cabinet
        cap, spent_all = cabinet.daily_spend_cap(), cabinet.spent_today()
    except Exception:
        cap, spent_all = 0.0, 0.0
    return {"ok": True, "engine": eng, "name": NAME.get(eng, ""), "chosen": bool(eng),
            "api": eng in API_ENGINES, "ready": ready, "why": why,
            "spent_ai": ai_spent_today(), "spent_all": spent_all, "cap": cap}


def team_status() -> dict:
    """claude_team.status()'s shape, for an API engine — so Setup and the preflight read it."""
    from . import claude_team
    eng = engine()
    ready, why = available(eng)
    tiers = models(eng) if eng in API_ENGINES else {}
    rows = []
    for s in claude_team.SECTORS:
        m = tiers.get(TIER_OF[s], "")
        rows.append({"sector": s, "label": claude_team.SECTOR_LABEL[s], "model": m,
                     "model_label": label(m), "main": TIER_OF[s] == "strong",
                     "default": DEFAULTS.get(eng, {}).get(TIER_OF[s], ""),
                     "tier": TIER_OF[s]})
    return {
        "ready": ready, "why": why, "cli": "", "ceiling": "",
        "engine": eng, "engine_name": NAME.get(eng, "no engine chosen"),
        "models": [{"id": m, "label": lab} for m, lab in MODELS.get(eng, ())],
        "team": rows,
        "accounts": [{"id": eng or "none",
                      "label": f"{NAME.get(eng, 'AI')} API key",
                      "config_dir": "", "ok": ready, "why": why}],
        "backup_setting": "", "struggling": [],
        "usage_today": claude_team.usage(),
    }


def brain(task) -> dict:
    """readiness: this sector's model, whether it is ready, whether it has answered lately."""
    eng = engine()
    ready, why = available(eng)
    m = model_for(task, eng)
    c = _conn()
    proven = False
    if c is not None:
        try:
            proven = bool(c.execute(
                "SELECT 1 FROM claude_usage WHERE ok=1 AND at > ? AND engine=?"
                " AND (model_ran=? OR model_asked=?) LIMIT 1",
                (int(time.time()) - 86400, eng, m, m)).fetchone())
        except sqlite3.Error:
            proven = False
        finally:
            c.close()
    return {"ok": ready, "error": "" if ready else why, "model": m, "label": label(m),
            "proven": proven, "struggling": "", "backup": False,
            "main": TIER_OF.get(task) == "strong", "engine": eng}


# ---------------------------------------------------------------------------
# THE COMMAND LINE
# ---------------------------------------------------------------------------

def _print(st):
    print("\nTHE AI ENGINE\n")
    if not st["chosen"]:
        print("  " + NOT_CHOSEN)
        return
    print(f"  {st['name']}" + ("" if st["ready"] else f" — not ready: {st['why']}"))
    if st["engine"] in API_ENGINES:
        opt = next(o for o in st["options"] if o["id"] == st["engine"])
        print()
        for t in st["tiers"]:
            m = opt["tiers"].get(t["id"], "")
            print(f"  {t['label']:<10} {label(m):<26} {', '.join(t['sectors'])}")
        est = st["estimate"]
        print(f"\n  About ${est['usd']:.2f} a day at these models ({est['basis']}).")
    print(f"  Spent on AI today: about ${st['spent_ai']:.2f}. "
          f"Everything today: about ${st['spent_all']:.2f} of the ${st['cap']:.2f} cap.")
    print(f"  Prices last checked {st['prices_checked']}.\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Which AI does the agency's thinking.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--test", action="store_true", help="one tiny real call per model")
    ap.add_argument("--model", default="", help="with --test: only this model")
    ap.add_argument("--save", action="store_true", help="read the Engine page's JSON on stdin")
    a = ap.parse_args(argv)
    if a.save:
        print(json.dumps(save(json.loads(sys.stdin.read() or "{}"))))
        return 0
    if a.test:
        rows = test(only=a.model or None)
        if a.json:
            print(json.dumps({"ok": all(r["ok"] for r in rows), "results": rows}))
        else:
            for r in rows:
                print(f"  {r.get('label', ''):<26} "
                      + (f"answered in {r['seconds']}s (about ${r['usd']:.4f})" if r["ok"]
                         else f"FAILED — {r['why']}"))
        return 0 if rows and all(r["ok"] for r in rows) else 1
    st = status()
    if a.json:
        print(json.dumps(st))
    else:
        _print(st)
    return 0 if st["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
