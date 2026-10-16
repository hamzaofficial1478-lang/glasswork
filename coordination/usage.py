"""
usage.py — how much of each outside service we have actually used.

    from coordination import usage
    usage.record("zyte", "render", n=1, usd=0.0015)
    print(usage.as_text())

WHY A SEPARATE METER

The cabinet already tracks money: every borrow can carry a usd_cost and
`spent_today()` sums it against the owner's daily cap. That is the right place
for money and it stays the right place. But money alone does not answer the two
questions the owner actually asks about a third-party API:

    "How many did we use?"        — a fact, countable, never in dispute
    "How close am I to the free   — a limit set by the provider's plan, which
     tier running out?"             has nothing to do with dollars until it
                                    is exceeded

Algolia is free until 10,000 searches a month and then it is not. Zyte is priced
per successful request on a tier that depends on the site. Neither of those is
expressible as "we spent $0.31 today", so neither was visible anywhere.

COUNTS ARE FACTS, MONEY IS AN ESTIMATE

Every row here carries both, and the reports say so in those words. We know
exactly how many renders we asked for. We do NOT know what Zyte charged for
them — their price depends on the target site and the volume discount — so the
dollar column is the owner's own rate from .env, multiplied out, and is labelled
"about" everywhere it is shown. A confident wrong number on a spend line is
worse than an honest range.

WHAT WE CANNOT SEE, WE SAY WE CANNOT SEE

The single most important line in this file is the one about Algolia searches.
A visitor typing in the search box queries Algolia from *their own browser* —
the request never touches this machine, and no amount of code here will ever
count it. A meter that showed "0 / 10,000 searches" while the owner's traffic
quietly burned through the allowance would be worse than no meter at all. So
searches are reported as UNMETERED, with the dashboard link, and the things we
genuinely do own — records pushed, write operations, renders bought — are
reported as counts we stand behind.
"""

import sqlite3
import time

try:
    from . import board
except ImportError:  # pragma: no cover — direct execution
    import board


def _conn():
    return board._conn()


def _now():
    return int(time.time())


def day_key(ts=None):
    return time.strftime("%Y-%m-%d", time.localtime(ts or _now()))


def month_key(ts=None):
    return time.strftime("%Y-%m", time.localtime(ts or _now()))


# ---------------------------------------------------------------------------
# WHAT EACH PLAN ALLOWS
# ---------------------------------------------------------------------------
#
# Published allowances, checked against the providers' own pricing pages. They
# are defaults, not gospel: plans change and the owner may be on a different
# one, so every number here can be overridden in .env and the text always tells
# him to confirm on his dashboard rather than trusting this file.
#
#   metered=True   we count it ourselves and the number is a fact
#   metered=False  it happens somewhere we cannot see; we say so and stop
#
LIMITS = [
    {
        "provider": "algolia", "kind": "search",
        "label": "Algolia searches", "period": "month",
        "allowance": 10_000, "env": "ALGOLIA_SEARCH_ALLOWANCE",
        "metered": False,
        "then": "$0.50 per extra 1,000 searches",
        "where": "https://dashboard.algolia.com/ → Usage",
        "why_unmetered": ("your visitors search from their own browsers, so "
                          "those requests never reach this machine — the true "
                          "count only exists on the Algolia dashboard"),
    },
    {
        "provider": "algolia", "kind": "record",
        "label": "Algolia records in the index", "period": "now",
        "allowance": 50_000, "env": "ALGOLIA_RECORD_ALLOWANCE",
        "metered": True,
        "then": "$0.40 per extra 1,000 records",
        "where": "https://dashboard.algolia.com/ → Usage",
        "note": ("this is the size of the index after the last push, not a "
                 "running total — re-indexing the same 40 pages keeps it at 40"),
    },
    {
        "provider": "algolia", "kind": "write",
        "label": "Algolia write operations", "period": "month",
        "allowance": 0, "env": "",
        "metered": True,
        "then": "included — writes are not separately billed on the free plan",
        "where": "https://dashboard.algolia.com/ → Usage",
    },
    {
        "provider": "zyte", "kind": "render",
        "label": "Zyte browser renders", "period": "day",
        "allowance": 0, "env": "",
        "metered": True,
        "then": "billed per successful request; the brake is your daily spend cap",
        "where": "https://app.zyte.com/ → Usage",
    },
    {
        "provider": "zyte", "kind": "fetch",
        "label": "Zyte plain fetches", "period": "day",
        "allowance": 0, "env": "",
        "metered": True,
        "then": "billed per successful request; the brake is your daily spend cap",
        "where": "https://app.zyte.com/ → Usage",
    },
]


def _allowance(meter):
    """The plan number, or the owner's override from .env."""
    name = meter.get("env") or ""
    if not name:
        return meter["allowance"]
    import os
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        try:
            from providers import integrations
            raw = (integrations.read_env().get(name) or "").strip()
        except Exception:
            raw = ""
    try:
        v = int(float(raw))
        return v if v > 0 else meter["allowance"]
    except (TypeError, ValueError):
        return meter["allowance"]


_WHEN = {"day": "today", "month": "this month", "now": "right now"}


def when(period):
    return _WHEN.get(period, period)


# ---------------------------------------------------------------------------
# RECORDING
# ---------------------------------------------------------------------------

def record(provider, kind, n=1, usd=0.0, note=""):
    """
    One line in the meter. Best-effort, like every other piece of bookkeeping
    here: a cabinet that has not been built yet must not break a fetch.

    Only charge `usd` for calls the provider actually bills. Zyte invoices
    successful responses only, so a failed render is recorded with n=1 and
    usd=0.0 — the request happened, the money did not.
    """
    try:
        with _conn() as c:
            board._migrate(c)
            c.execute(
                "INSERT INTO api_usage(at, day, month, provider, kind, n, usd, note)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (_now(), day_key(), month_key(), str(provider).lower(),
                 str(kind).lower(), int(n), float(usd or 0.0), (note or "")[:200]))
    except sqlite3.Error:
        pass
    return n


def set_gauge(provider, kind, value, note=""):
    """
    A level, not an addition — the number of records currently in the index,
    for instance. Stored as an ordinary row; readers take the LATEST rather
    than the sum. Pushing the same 40 pages every night must not read as 1,200
    records by the end of the month.
    """
    try:
        with _conn() as c:
            board._migrate(c)
            c.execute(
                "INSERT INTO api_usage(at, day, month, provider, kind, n, usd, note)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (_now(), day_key(), month_key(), str(provider).lower(),
                 str(kind).lower(), int(value), 0.0, (note or "")[:200]))
    except sqlite3.Error:
        pass
    return value


# ---------------------------------------------------------------------------
# READING
# ---------------------------------------------------------------------------

def _sum(provider, kind, period):
    where, args = ["provider=?", "kind=?"], [provider, kind]
    if period == "day":
        where.append("day=?"); args.append(day_key())
    elif period == "month":
        where.append("month=?"); args.append(month_key())
    try:
        with _conn() as c:
            board._migrate(c)
            r = c.execute("SELECT COALESCE(SUM(n),0) AS n, COALESCE(SUM(usd),0) AS usd"
                          " FROM api_usage WHERE " + " AND ".join(where), args).fetchone()
            return int(r["n"] or 0), float(r["usd"] or 0.0)
    except sqlite3.Error:
        return 0, 0.0


def _latest(provider, kind):
    try:
        with _conn() as c:
            board._migrate(c)
            r = c.execute("SELECT n, at FROM api_usage WHERE provider=? AND kind=?"
                          " ORDER BY at DESC, id DESC LIMIT 1",
                          (provider, kind)).fetchone()
            return (int(r["n"]), int(r["at"])) if r else (0, 0)
    except sqlite3.Error:
        return 0, 0


def spent_today() -> float:
    """Estimated third-party API money today, from the meter alone."""
    try:
        with _conn() as c:
            board._migrate(c)
            r = c.execute("SELECT COALESCE(SUM(usd),0) FROM api_usage WHERE day=?",
                          (day_key(),)).fetchone()
            return round(float(r[0] or 0.0), 4)
    except sqlite3.Error:
        return 0.0


def status():
    """
    One entry per meter, ready to print or render.

    verdict:  ok | watch | over | unmetered | idle
    """
    out = []
    for m in LIMITS:
        allowance = _allowance(m)
        if not m["metered"]:
            out.append({**m, "allowance": allowance, "used": None, "usd": 0.0,
                        "pct": None, "verdict": "unmetered"})
            continue
        if m["period"] == "now":
            used, at = _latest(m["provider"], m["kind"])
            usd = 0.0
            m = {**m, "as_of": at}
        else:
            used, usd = _sum(m["provider"], m["kind"], m["period"])
        pct = round(100.0 * used / allowance, 1) if allowance else None
        if not allowance:
            verdict = "ok" if used else "idle"
        elif used >= allowance:
            verdict = "over"
        elif pct >= 80:
            verdict = "watch"
        else:
            verdict = "ok"
        out.append({**m, "allowance": allowance, "used": used,
                    "usd": round(usd, 4), "pct": pct, "verdict": verdict})
    return out


def headroom(provider, kind):
    """
    (ok, message) — is there room left in a metered allowance?

    An unmetered or uncapped meter always returns True. It is not this
    function's job to guess at a number nobody can see; the daily spend cap in
    cabinet.py is what stands between the owner and a surprise invoice.
    """
    for m in status():
        if m["provider"] == provider and m["kind"] == kind:
            if m["verdict"] in ("unmetered", "idle") or not m["allowance"]:
                return True, ""
            if m["verdict"] == "over":
                return False, (f"{m['label']}: {m['used']:,} used of "
                               f"{m['allowance']:,} {when(m['period'])} — the plan "
                               f"allowance is gone. After this: {m['then']}.")
            if m["verdict"] == "watch":
                return True, (f"{m['label']}: {m['used']:,} of {m['allowance']:,} "
                              f"{when(m['period'])} ({m['pct']}%). Getting close.")
            return True, ""
    return True, ""


def as_text() -> str:
    lines = ["USAGE AND LIMITS", "=" * 60,
             "Counts are facts. Money is an estimate from your own rates in .env.",
             ""]
    for m in status():
        if m["verdict"] == "unmetered":
            lines.append(f"  ~ {m['label']}: NOT COUNTED HERE — {m['why_unmetered']}.")
            lines.append(f"      plan allowance {m['allowance']:,} per {m['period']}"
                         f" · check {m['where']}")
            continue
        mark = {"ok": "·", "idle": "·", "watch": "!", "over": "✕"}[m["verdict"]]
        if m["allowance"]:
            lines.append(f"  {mark} {m['label']}: {m['used']:,} of "
                         f"{m['allowance']:,} {when(m['period'])} ({m['pct']}%)")
        else:
            lines.append(f"  {mark} {m['label']}: {m['used']:,} {when(m['period'])}"
                         + (f" · about ${m['usd']:.4f}" if m["usd"] else ""))
        if m.get("note"):
            lines.append(f"      {m['note']}")
        if m["verdict"] in ("watch", "over"):
            lines.append(f"      then: {m['then']} · {m['where']}")
    total = spent_today()
    lines += ["", f"Estimated third-party API spend today: about ${total:.4f}",
              "Confirm real spend on each provider's own dashboard — these are "
              "your rates, not their invoice."]
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if "--json" in sys.argv:
        import json
        print(json.dumps({"ok": True, "meters": status(),
                          "spent_today": spent_today()}))
    else:
        print(as_text())
