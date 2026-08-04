"""
state.py — the live snapshot the Control Room reads.

Reads the filing cabinet (agency.db) and returns the exact shape ui/agency.html
renders: header metrics, the six rooms with their real task feed, the approvals
waiting on the owner, the security feed, and the shelf. No secrets leave here.

The Node server shells into this (python3 -m coordination.state) and merges in
the .env-derived connections, so the dashboard shows the real agency instead of
placeholder data. Every read is wrapped so a missing table or an empty cabinet
degrades to zeros, never a crash.
"""
import json
import sqlite3
import time
from pathlib import Path

try:
    from . import cabinet
except ImportError:  # pragma: no cover - script/path fallback
    import cabinet

DB = Path(__file__).parent / "agency.db"

# key, display name, task-sector match, is-security-room
SECTORS = [
    ("IT", "IT & Site Health", "it", False),
    ("Content", "Content & Social", "content", False),
    ("SEO", "SEO & Rankings", "seo", False),
    ("Leads", "Leads & Outreach", "leads", False),
    ("Report", "Reports & Scorecard", "report", False),
    ("Security", "Security", "security", True),
]


def _conn():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def _day_start():
    lt = time.localtime()
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))


def _one(c, q, args=(), default=0):
    try:
        r = c.execute(q, args).fetchone()
        return r[0] if r and r[0] is not None else default
    except Exception:
        return default


def _clamp(n, lo, hi):
    return max(lo, min(hi, int(n)))


def _hhmm(ts):
    try:
        return time.strftime("%H:%M", time.localtime(ts))
    except Exception:
        return ""


def _hitrate(c):
    try:
        rows = c.execute("SELECT outcome FROM decisions WHERE outcome IS NOT NULL"
                         " ORDER BY scored_at DESC LIMIT 30").fetchall()
    except Exception:
        return None
    if not rows:
        return None
    wins = sum(1 for r in rows if r[0] == "worked")
    return round(wins / len(rows), 2)


def _room_stats(c, key, day0, open_tasks):
    """Return (foot[2], score) for a room, from real counts."""
    base = _clamp(92 - open_tasks * 4, 60, 96)
    if key == "IT":
        issues = _one(c, "SELECT count(*) FROM site_issues WHERE status IN ('open','fixing','in_review')")
        perf = _one(c, "SELECT perf FROM lighthouse ORDER BY at DESC LIMIT 1", default=None)
        return [f"{perf if perf is not None else '—'} perf score", f"{issues} tickets open"], _clamp(base - issues * 6, 55, 96)
    if key == "Content":
        review = _one(c, "SELECT count(*) FROM content_queue WHERE status IN ('drafted','in_review')")
        queued = _one(c, "SELECT count(*) FROM content_queue WHERE status IN ('idea','briefed','approved')")
        return [f"{review} in review", f"{queued} queued"], base
    if key == "SEO":
        kw = _one(c, "SELECT count(*) FROM keywords")
        ranks = _one(c, "SELECT count(*) FROM rankings")
        return [f"{kw} keywords", f"{ranks} ranks tracked"], base
    if key == "Leads":
        valid = _one(c, "SELECT count(*) FROM leads WHERE found_at>=? AND email_valid=1", (day0,))
        replies = _one(c, "SELECT count(*) FROM outreach_log WHERE direction='reply' AND at>=?", (day0,))
        return [f"{valid} valid today", f"{replies} replies"], base
    if key == "Report":
        hr = _hitrate(c)
        unscored = _one(c, "SELECT count(*) FROM decisions WHERE outcome IS NULL")
        return ([f"MD hit-rate {int(hr * 100)}%" if hr is not None else "MD hit-rate —",
                 f"{unscored} to score"],
                (int(hr * 100) if hr is not None else base))
    if key == "Security":
        today = _one(c, "SELECT count(*) FROM security_events WHERE at>=?", (day0,))
        flagged = _one(c, "SELECT count(*) FROM security_events WHERE kind IN ('scam_lead','fraud','phishing')")
        return [f"{today} events today", f"{flagged} flagged"], _clamp(96 - today * 5, 60, 98)
    return ["—", "—"], base


def build():
    c = _conn()
    day0 = _day_start()

    # ---- rooms, each with its real task feed from the board ----
    rooms = []
    for key, name, match, sec in SECTORS:
        try:
            trows = c.execute(
                "SELECT title,status,owner FROM tasks"
                " WHERE lower(sector)=? AND status IN ('open','in_progress')"
                " ORDER BY created_at DESC LIMIT 6", (match,)).fetchall()
        except Exception:
            trows = []
        agents = [{"n": (t["owner"] or "unassigned"), "t": t["title"],
                   "s": "working" if t["status"] == "in_progress" else "waiting"} for t in trows]
        open_tasks = _one(c, "SELECT count(*) FROM tasks WHERE lower(sector)=? AND status IN ('open','in_progress')", (match,))
        foot, score = _room_stats(c, key, day0, open_tasks)
        rooms.append({"key": key, "name": name, "score": score, "foot": foot,
                      "sec": sec, "agents": agents})

    # ---- header metrics ----
    leads_today = _one(c, "SELECT count(*) FROM leads WHERE found_at>=?", (day0,))
    spend = _one(c, "SELECT COALESCE(SUM(usd_cost),0) FROM agent_runs WHERE started_at>=?", (day0,), 0.0)
    spend += _one(c, "SELECT COALESCE(SUM(usd_cost),0) FROM borrows WHERE granted_at>=?", (day0,), 0.0)
    health = round(sum(r["score"] for r in rooms) / len(rooms)) if rooms else 0
    open_all = _one(c, "SELECT count(*) FROM tasks WHERE status IN ('open','in_progress')")
    md_state = f"{open_all} tasks on the board" if open_all else "idle — board clear"

    # ---- approvals waiting on the owner (amber/red tasks + queued site changes) ----
    approvals = []
    try:
        for t in c.execute("SELECT sector,title,detail,risk FROM tasks"
                           " WHERE risk IN ('amber','red') AND status IN ('open','in_progress')"
                           " ORDER BY created_at DESC LIMIT 6"):
            approvals.append({"lvl": f"{t['risk']} · {t['sector']}", "text": t["title"],
                              "why": t["detail"] or "Needs your nod."})
    except Exception:
        pass
    try:
        for s in c.execute("SELECT agent,kind,detail FROM site_queue WHERE status='pending'"
                           " ORDER BY queued_at DESC LIMIT 3"):
            approvals.append({"lvl": "amber · IT", "text": f"Apply queued change: {s['kind']}",
                              "why": ((s["detail"] or "")[:150] + " — queued by " + (s["agent"] or "IT"))})
    except Exception:
        pass

    # ---- security feed ----
    sevmap = {"critical": "hi", "high": "hi", "hi": "hi",
              "medium": "mid", "mid": "mid", "low": "lo", "lo": "lo"}
    security = []
    try:
        for e in c.execute("SELECT kind,severity,detail,at FROM security_events ORDER BY at DESC LIMIT 6"):
            security.append({"sev": sevmap.get((e["severity"] or "lo").lower(), "mid"),
                             "title": (e["kind"] or "event").replace("_", " ").title(),
                             "sub": (e["detail"] or "")[:80], "time": _hhmm(e["at"])})
    except Exception:
        pass

    # ---- the shelf (shared API cabinet) ----
    try:
        state = cabinet.shelf_state(["llm", "image", "embedding", "seo_data",
                                     "stock_images", "email", "crawler", "whatsapp"])
        try:  # how many APIs/models actually sit on each shelf
            from providers import store as _store
            cat = _store.shelf_catalogue()
        except Exception:
            cat = {}
        shelf = []
        for k, v in sorted(state.items()):
            # the borrow that is open right now tells us WHICH sector holds it
            active = next((b for b in v.get("recent", []) if b.get("status") == "active"), None)
            shelf.append({
                "shelf": k,
                "holder": (v["holder"]["agent"] if v.get("holder") else None),
                "holder_sector": (active or {}).get("sector"),
                "using": (active or {}).get("item"),
                "queue": len(v.get("queue", [])),
                "queued_by": [q.get("agent") for q in v.get("queue", [])],
                "spend": v.get("today_spend", 0),
                "items": len(cat.get(k, [])),
                "names": [i.get("name", "") for i in cat.get(k, [])],
            })
    except Exception:
        shelf = []

    c.close()
    return {"ok": True, "ts": int(time.time()),
            "md": {"state": md_state},
            "metrics": {"health": health, "leads": leads_today, "spend": f"${spend:.2f}"},
            "rooms": rooms, "approvals": approvals, "security": security, "shelf": shelf}


if __name__ == "__main__":
    print(json.dumps(build()))
