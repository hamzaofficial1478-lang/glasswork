"""
leads_view.py — the leads behind the number on the dashboard.

'Leads today: 0' is a fact with nothing behind it. The owner cannot see who they
are, where they came from, whether anybody has written to them, or why one was
skipped — and a lead count he cannot open is a score, not a tool.

Read-only. Every field comes from the rows the Hunter, the Scanner and Outreach
already wrote; nothing here is derived, guessed or scored.
"""
import argparse
import json
import sqlite3
import time

try:
    from . import board
except ImportError:  # pragma: no cover
    import board


def _conn():
    c = sqlite3.connect(board.DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _rows(c, q, a=()):
    try:
        return [dict(r) for r in c.execute(q, a)]
    except Exception:
        return []


def _ago(secs):
    secs = max(0, int(secs))
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _day_start(now):
    t = time.localtime(now)
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


def build(scope="today", limit=200) -> dict:
    """
    Who the leads are, what has happened to each, and what is missing.

    `scope` is 'today' or 'all'. The dashboard tile counts today, so opening the
    tile must show today by default or the number and the list disagree.
    """
    now = int(time.time())
    day0 = _day_start(now)
    c = _conn()
    try:
        board._migrate(c)
        where, args = ("WHERE found_at >= ?", (day0,)) if scope == "today" else ("", ())
        rows = _rows(c, f"SELECT * FROM leads {where} ORDER BY found_at DESC"
                        f" LIMIT {int(limit)}", args)
        ids = [r["id"] for r in rows]
        touch = {}
        if ids:
            q = ("SELECT lead_id, direction, reply_class, needs_owner, at, channel"
                 " FROM outreach_log WHERE lead_id IN (%s) ORDER BY at"
                 % ",".join("?" * len(ids)))
            for m in _rows(c, q, tuple(ids)):
                touch.setdefault(m["lead_id"], []).append(m)

        out = []
        for r in rows:
            log = touch.get(r["id"], [])
            sent = [m for m in log if m["direction"] == "sent"]
            reps = [m for m in log if m["direction"] == "reply"]
            out.append({
                "id": r["id"], "company": r.get("company") or "—",
                "name": r.get("name") or "", "email": r.get("email") or "",
                "phone": r.get("phone") or "", "area": r.get("area") or "",
                "segment": r.get("segment") or "", "source": r.get("source") or "",
                "stage": r.get("stage") or "new",
                "email_valid": bool(r.get("email_valid")),
                "score": r.get("score"),
                "found": _ago(now - int(r.get("found_at") or now)),
                "found_at": r.get("found_at"),
                # What the Harvester read off their own website. Maps holds
                # none of this — see pipelines/harvest.py.
                "website": r.get("website") or "",
                "whatsapp": r.get("whatsapp") or "",
                "address": r.get("address") or "",
                "contact_kind": r.get("contact_kind") or "",
                "contacted": len(sent),
                "replied": len(reps),
                "needs_owner": any(m["needs_owner"] for m in reps),
                "last": (_ago(now - int(log[-1]["at"] or now)) if log else ""),
                # Why nothing has happened yet, when nothing has. A lead sitting
                # untouched is the most common thing on this list and the least
                # explained.
                "waiting_on": ("" if sent else
                               # A lead with no mail address but a chat button
                               # they published is reachable, and calling it
                               # "no address on file" hides that.
                               "no address on file — but they published a chat "
                               "button, so this one is reachable"
                               if not (r.get("email") or "").strip()
                               and (r.get("whatsapp") or "").strip()
                               else "no email address on file"
                               if not (r.get("email") or "").strip()
                               else "email never verified" if not r.get("email_valid")
                               else "not contacted yet"),
            })

        total_all = _rows(c, "SELECT count(*) n FROM leads")
        by_stage = {x["stage"] or "new": x["n"] for x in _rows(
            c, "SELECT stage, count(*) n FROM leads GROUP BY stage")}
        return {"ok": True, "scope": scope, "at": now,
                "leads": out, "shown": len(out),
                "today": len(_rows(c, "SELECT id FROM leads WHERE found_at>=?", (day0,))),
                "total": (total_all[0]["n"] if total_all else 0),
                "by_stage": by_stage,
                "head": (f"{len(out)} found today" if scope == "today" and out else
                         f"{len(out)} on file" if out else
                         "none today — the Hunter has not found anyone yet"
                         if scope == "today" else "no leads on file yet")}
    finally:
        c.close()


def as_text(scope="today") -> str:
    d = build(scope)
    L = [f"LEADS — {d['head']}  ({d['total']} on file in total)"]
    for x in d["leads"]:
        L.append(f"  {x['company'][:34]:<34} {x['stage']:<10} {x['area'][:12]:<12} "
                 f"{x['found']:>8}"
                 + (f"  · {x['waiting_on']}" if x["waiting_on"] else
                    f"  · {x['contacted']} sent, {x['replied']} replied"))
    if not d["leads"]:
        L.append("  (nothing)")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="The leads behind the number.")
    ap.add_argument("--scope", default="today", choices=["today", "all"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    print(json.dumps(build(a.scope)) if a.json else as_text(a.scope))


if __name__ == "__main__":
    main()
