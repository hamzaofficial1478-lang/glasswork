"""
site_progress.py — how his website is doing on Google, day by day.

    python -m pipelines.site_progress            write down today, and fetch Google's own numbers
    python -m pipelines.site_progress --json     the last 90 days, for the office (no requests)

"a live progress of website like how website is performing that will make user
 to trust our program more when user will see his website live progress on
 google"

TWO KINDS OF NUMBER, KEPT APART

  Google's own   clicks, how often the site was shown (impressions) and its
                 average position, for every day, straight from his Search
                 Console. Exact, and about two days late — Google publishes them
                 that way, so the last day or two are missing rather than zero.
  the agency's   what it measures itself, written down once a day: pages on
                 Google, how complete the website is (the blueprint), its SEO
                 score out of 100 (pipelines/seo_rules), how fast the home page
                 answered, leads found. Small facts, but they are
                 the ones the agency's own work moves.

Every number is counted, never estimated, and the screen says which is which.
Without Search Console the agency's own line still grows from the first day,
and the office says what connecting Search Console would add.
"""
import argparse
import json
import sqlite3
import sys
import time

from coordination import board

DAYS = 90
FIELDS = ("on_google", "pages", "have", "total", "speed_ms", "leads", "seo_score")


def _today(now=None):
    return time.strftime("%Y-%m-%d", time.localtime(now or time.time()))


def _tables(c):
    c.execute("""CREATE TABLE IF NOT EXISTS site_progress (
        day TEXT PRIMARY KEY, on_google INTEGER, pages INTEGER, have INTEGER, total INTEGER,
        speed_ms INTEGER, leads INTEGER)""")
    c.execute("CREATE TABLE IF NOT EXISTS gsc_days (day TEXT PRIMARY KEY, clicks INTEGER, impressions INTEGER,"
              " position REAL)")
    try:
        c.execute("ALTER TABLE site_progress ADD COLUMN seo_score INTEGER")
    except sqlite3.OperationalError:
        pass


def note(day=None, **fields):
    """Write some of today's numbers. Never raises — a count must never stop the work."""
    vals = {k: v for k, v in fields.items() if k in FIELDS and v is not None}
    if not vals:
        return
    day = day or _today()
    try:
        with board._conn() as c:
            _tables(c)
            c.execute("INSERT OR IGNORE INTO site_progress(day) VALUES(?)", (day,))
            c.execute("UPDATE site_progress SET " + ", ".join(f"{k}=?" for k in vals) + " WHERE day=?",
                      (*vals.values(), day))
    except sqlite3.Error:
        pass


def keep_google(rows):
    """Store Google's daily rows (day, clicks, impressions, position), replacing what was there."""
    try:
        with board._conn() as c:
            _tables(c)
            for r in rows:
                c.execute("INSERT OR REPLACE INTO gsc_days(day,clicks,impressions,position) VALUES(?,?,?,?)",
                          (r["day"], int(r.get("clicks") or 0), int(r.get("impressions") or 0),
                           float(r.get("position") or 0)))
    except sqlite3.Error:
        pass


def record() -> dict:
    """Today's numbers, from what the desks already found; and Google's, if Search Console is connected."""
    out = {"ok": True}
    try:
        from . import seo_index
        st = seo_index.status()
        if st.get("ok") and st.get("pages"):
            note(on_google=int(st.get("on_google") or 0), pages=len(st["pages"]))
    except Exception:
        pass
    try:
        from . import site_blueprint
        b = site_blueprint.stored()
        if b.get("checked_at"):
            note(have=len(b.get("have") or []), total=len(b.get("have") or []) + len(b.get("gaps") or []),
                 seo_score=(b.get("seo") or {}).get("score"))
    except Exception:
        pass
    try:
        with board._conn() as c:
            n = c.execute("SELECT count(*) FROM leads WHERE COALESCE(email,'')!=''").fetchone()[0]
        note(leads=int(n or 0))
    except Exception:
        pass
    try:
        from . import seo_intel
        rows, why = seo_intel.gsc_daily(DAYS)
    except Exception as e:
        rows, why = [], f"{type(e).__name__}: {e}"
    if rows:
        keep_google(rows)
    out["google_days"] = len(rows)
    out["google_why"] = why
    out["head"] = (f"recorded today; {len(rows)} day(s) of Google's own numbers" if rows else
                   "recorded today's numbers" + (f" — Google's own numbers need Search Console ({why[:120]})"
                                                 if why else ""))
    return out


def _weighted(rows):
    imp = sum(r["impressions"] for r in rows)
    return round(sum(r["position"] * r["impressions"] for r in rows) / imp, 1) if imp else None


def _change(now, then):
    if now is None or then in (None, 0):
        return None
    return round((now - then) / then * 100)


def summary(days=DAYS, now=None) -> dict:
    """The series and the headline numbers, for the office. Reads only."""
    now = now or time.time()
    since = _today(now - days * 86400)
    try:
        with board._conn() as c:
            _tables(c)
            g = [dict(r) for r in c.execute("SELECT day, clicks, impressions, position FROM gsc_days"
                                            " WHERE day>=? ORDER BY day", (since,))]
            s = [dict(r) for r in c.execute("SELECT * FROM site_progress WHERE day>=? ORDER BY day", (since,))]
            first = c.execute("SELECT * FROM site_progress ORDER BY day LIMIT 1").fetchone()
            first = dict(first) if first else {}
    except sqlite3.Error as e:
        return {"ok": False, "error": str(e)}
    last28, prev28 = g[-28:], g[-56:-28]
    tiles = {}
    if g:
        c_now, c_then = sum(r["clicks"] for r in last28), sum(r["clicks"] for r in prev28) if prev28 else None
        i_now, i_then = sum(r["impressions"] for r in last28), sum(r["impressions"] for r in prev28) if prev28 else None
        p_now, p_then = _weighted(last28), _weighted(prev28) if prev28 else None
        tiles["clicks"] = {"value": c_now, "change": _change(c_now, c_then)}
        tiles["impressions"] = {"value": i_now, "change": _change(i_now, i_then)}
        tiles["position"] = {"value": p_now, "then": p_then,
                             "better": (p_then is not None and p_now is not None and p_now < p_then)}
    latest = next((r for r in reversed(s) if r.get("pages")), None)
    if latest:
        tiles["on_google"] = {"value": latest["on_google"], "of": latest["pages"],
                              "first": first.get("on_google"), "first_day": first.get("day")}
    done = next((r for r in reversed(s) if r.get("total")), None)
    if done:
        pct = round(done["have"] / done["total"] * 100)
        f = round(first["have"] / first["total"] * 100) if first.get("total") else None
        tiles["complete"] = {"value": pct, "first": f}
    scored = next((r for r in reversed(s) if r.get("seo_score") is not None), None)
    if scored:
        f = next((r["seo_score"] for r in s if r.get("seo_score") is not None), None)
        tiles["seo"] = {"value": scored["seo_score"], "first": first.get("seo_score") if first.get("seo_score")
                        is not None else f}
    gsc_ok, gsc_why = True, ""
    if not g:
        try:
            from . import seo_intel
            gsc_ok, gsc_why = seo_intel.gsc_ready()
        except Exception:
            gsc_ok, gsc_why = False, ""
    return {"ok": True, "google": g, "site": s, "tiles": tiles, "since": first.get("day") or "",
            "google_connected": bool(g) or gsc_ok, "google_why": gsc_why,
            "note": "Google's own numbers arrive about two days late."}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="How the website is doing on Google, day by day.")
    ap.add_argument("--json", action="store_true", help="the last 90 days, read only")
    a = ap.parse_args(argv)
    board.ensure_schema()
    if a.json:
        print(json.dumps(summary(), indent=2))
        return 0
    with board.agent_run("reporter") as run:
        r = record()
        run.note("Progress: " + r.get("head", ""))
    print(r.get("head"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
