"""
site_traffic.py — how much the agency asks of the owner's website, and how much
travels each way.

    python -m coordination.site_traffic          the last seven days
    python -m coordination.site_traffic --json

"how many requests per minute our program api can manage ... how much data
 does travel on the api"

Every request to the website connector (pipelines/site_api) is counted here at
the moment it is made: one more request, the bytes that went out, the bytes
that came back on the wire, and what those came to once unpacked — so the
saving from packing (gzip) is a number, not a claim. Requests are also counted
per minute, so the busiest minute of the day is on the screen next to the rule
that caps it (coordination/sitepace: one request a second, at most sixty a
minute, across every desk).

Counts only — never a page, a name or an address. Writing a count can never
stop a request: if the cabinet is busy, the count is skipped, not the work.
"""
import argparse
import json
import time

try:
    from . import board
except ImportError:  # pragma: no cover
    import board

KEEP_MINUTES = 3 * 24 * 60        # per-minute counts kept for three days


def _table(c):
    c.execute("""CREATE TABLE IF NOT EXISTS site_traffic (
        day TEXT PRIMARY KEY, requests INTEGER DEFAULT 0, failed INTEGER DEFAULT 0,
        sent INTEGER DEFAULT 0, received INTEGER DEFAULT 0, unpacked INTEGER DEFAULT 0,
        seconds REAL DEFAULT 0)""")
    c.execute("CREATE TABLE IF NOT EXISTS site_traffic_min (minute INTEGER PRIMARY KEY, n INTEGER DEFAULT 0)")


def note(sent, received, unpacked, seconds, ok=True, now=None):
    """One request: bytes out, bytes back on the wire, bytes once unpacked."""
    now = now or time.time()
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    minute = int(now // 60)
    try:
        with board._conn() as c:
            _table(c)
            c.execute("INSERT INTO site_traffic(day,requests,failed,sent,received,unpacked,seconds)"
                      " VALUES(?,1,?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET"
                      " requests=requests+1, failed=failed+excluded.failed, sent=sent+excluded.sent,"
                      " received=received+excluded.received, unpacked=unpacked+excluded.unpacked,"
                      " seconds=seconds+excluded.seconds",
                      (day, 0 if ok else 1, int(sent or 0), int(received or 0),
                       int(unpacked or 0), float(seconds or 0)))
            c.execute("INSERT INTO site_traffic_min(minute,n) VALUES(?,1)"
                      " ON CONFLICT(minute) DO UPDATE SET n=n+1", (minute,))
            if minute % 30 == 0:
                c.execute("DELETE FROM site_traffic_min WHERE minute < ?", (minute - KEEP_MINUTES,))
    except Exception:
        pass


def _mb(n):
    return round((n or 0) / 1048576, 2)


def summary(days=7, now=None) -> dict:
    """The last few days, newest first, with totals and the busiest minute."""
    now = now or time.time()
    out = {"ok": True, "days": [], "rule": "at most one request a second (60 a minute), one at a time"}
    try:
        with board._conn() as c:
            _table(c)
            rows = c.execute("SELECT day, requests, failed, sent, received, unpacked, seconds"
                             " FROM site_traffic ORDER BY day DESC LIMIT ?", (int(days),)).fetchall()
            mins = c.execute("SELECT minute, n FROM site_traffic_min").fetchall()
    except Exception as e:
        return {"ok": False, "error": str(e), "days": []}
    peak = {}
    for m, n in mins:
        d = time.strftime("%Y-%m-%d", time.localtime(m * 60))
        peak[d] = max(peak.get(d, 0), n)
    for r in rows:
        day, req, failed, sent, rec, unp, secs = tuple(r)
        out["days"].append({
            "day": day, "requests": req, "failed": failed,
            "sent_mb": _mb(sent), "received_mb": _mb(rec), "unpacked_mb": _mb(unp),
            "saved_by_packing_mb": _mb(max(0, (unp or 0) - (rec or 0))),
            "busiest_minute": peak.get(day, 0),
            "average_seconds": round((secs or 0) / req, 2) if req else 0})
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    t = next((d for d in out["days"] if d["day"] == today), None)
    out["today"] = t or {"day": today, "requests": 0, "failed": 0, "sent_mb": 0, "received_mb": 0,
                         "unpacked_mb": 0, "saved_by_packing_mb": 0, "busiest_minute": 0,
                         "average_seconds": 0}
    return out


def as_text(s) -> str:
    L = ["YOUR WEBSITE CONNECTION, BY DAY", "=" * 60, f"Rule: {s.get('rule')}", ""]
    if not s.get("days"):
        L.append("  Nothing yet — the agency has not talked to your website.")
    for d in s.get("days", []):
        L.append(f"  {d['day']}  {d['requests']:>5} requests ({d['failed']} failed), "
                 f"busiest minute {d['busiest_minute']}, "
                 f"out {d['sent_mb']} MB, back {d['received_mb']} MB "
                 f"(saved {d['saved_by_packing_mb']} MB by packing)")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="How much the agency asks of the website.")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    s = summary(a.days)
    print(json.dumps(s, indent=2) if a.json else as_text(s))


if __name__ == "__main__":
    main()
