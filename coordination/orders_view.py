"""
orders_view.py — every order he gave, what became of it, what is left and why.

    python -m coordination.orders_view           the last week, as text
    python -m coordination.orders_view --json    the same, for the office

"there is no any room where i can see that the commands i gave how much work
 get done what remains and why? only the activity log"

The activity log is every agent's every line, newest first, in the agency's
words. His question is the other way round: start from what HE asked for and
follow it — which board it went on, which desk took it, what that desk said,
what change it made, whether the change is waiting for his yes, on his site,
or stuck, and the one sentence on why. That is this file. It reads the cabinet
and changes nothing.

WHERE HIS ORDERS COME FROM

  the order boxes      sector_orders, one per sector per day (Run the day)
  the chat             a job a desk recorded for him (created_by 'owner'), a
                       website change asked for in the chat
  his numbers          a leads number short of its target is followed too

HOW AN ORDER IS FOLLOWED

  the jobs             tasks titled "Owner's order…" carrying his words — the
                       MD's job, IT's copy of a website order, the leads top-up
  the changes          site changes made for those jobs (by the job's id, or by
                       his words on the change)
  the state            done / waiting for you / being worked / stuck / not
                       started — and what is left, each with its reason
"""
import argparse
import json
import sqlite3
import time

from . import board

OWNER_ORDER = "Owner's order"
DAYS = 7


def _rows(c, q, args=()):
    try:
        return [dict(r) for r in c.execute(q, args)]
    except sqlite3.Error:
        return []


def _day_start(day):
    try:
        return int(time.mktime(time.strptime(day, "%Y-%m-%d")))
    except Exception:
        return 0


def _detail(raw):
    try:
        d = json.loads(raw or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _words(text):
    return " ".join((text or "").lower().split())


TASK_SAYS = {
    "open": "on the {sector} board, waiting for its desk — taken in the next round or run",
    "in_progress": "{owner} is working on it now",
    "done": "done",
    "blocked": "could not be done",
    "failed": "could not be done",
    "withdrawn": "folded into another job that does the same thing",
}

CHANGE_SAYS = {
    "pending": "waiting for your yes",
    "approved": "approved — IT is putting it on your website",
    "applied": "on your website",
    "failed": "could not go on",
    "rejected": "you turned it down",
    "withdrawn": "withdrawn",
}


def _result_text(t):
    r = t.get("result") or ""
    d = _detail(r) if r.strip().startswith("{") else {}
    if d:
        for k in ("head", "note", "summary", "message"):
            if d.get(k):
                return str(d[k])[:400]
        bits = [f"{k.replace('_', ' ')} {v}" for k, v in d.items()
                if isinstance(v, (int, str)) and v not in ("", 0)][:5]
        return ", ".join(bits)[:400]
    return r[:400]


def _change(row):
    d = _detail(row.get("detail"))
    ch = d.get("change") or {}
    what = (d.get("title") or d.get("why") or (ch.get("call") or ch.get("run") or "")
            or row.get("kind") or "a change")
    return {"id": row["id"], "kind": row.get("kind") or "", "status": row.get("status"),
            "says": CHANGE_SAYS.get(row.get("status"), row.get("status") or ""),
            "what": str(what)[:240], "note": (row.get("note") or "")[:500],
            "when": row.get("applied_at") or row.get("decided_at") or row.get("queued_at")}


def _state(tasks, changes, leads=None):
    """(state, what is left — each with its reason)."""
    left = []
    for ch in changes:
        if ch["status"] == "pending":
            left.append(f"“{ch['what'][:90]}” is waiting for your yes — approve it here")
        elif ch["status"] == "approved":
            left.append(f"“{ch['what'][:90]}” is approved and IT is putting it on")
        elif ch["status"] == "failed":
            note = ch['note'] or 'no reason given'
            left.append(f"“{ch['what'][:90]}” could not go on — "
                        + (note if len(note) <= 160 else note[:157].rsplit(' ', 1)[0] + "… (the full "
                           "reason is on the change above)"))
    for t in tasks:
        if t["status"] == "open":
            left.append(f"{t['sector']} has not taken the job yet — it goes in the next round "
                        "or run")
        elif t["status"] == "in_progress":
            left.append(f"{t.get('owner') or t['sector']} is still working on it")
        elif t["status"] in ("blocked", "failed"):
            left.append(f"{t['sector']} could not do it: {t['said'] or 'no reason given'}")
    if leads and leads.get("short"):
        left.append(f"{leads['short']} of the {leads['asked']} leads with an email are still "
                    "to find today")
    if not tasks and not changes:
        return "not started", left
    if any(c["status"] == "failed" for c in changes) or \
            any(t["status"] in ("blocked", "failed") for t in tasks):
        return "stuck", left
    if any(c["status"] == "pending" for c in changes):
        return "waiting for you", left
    if any(t["status"] in ("open", "in_progress") for t in tasks) or \
            any(c["status"] == "approved" for c in changes) or (leads and leads.get("short")):
        return "in progress", left
    return "done", left


def report(days=DAYS) -> dict:
    board.ensure_schema()
    since_day = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    since = int(time.time()) - days * 86400
    with board._conn() as c:
        board._migrate(c)
        orders = _rows(c, "SELECT day, sector, orders, updated_at FROM sector_orders"
                          " WHERE day>=? AND COALESCE(orders,'')!='' ORDER BY day DESC, sector",
                       (since_day,))
        tasks = _rows(c, "SELECT * FROM tasks WHERE created_at>=? AND (title LIKE ?"
                         " OR created_by='owner') ORDER BY created_at",
                      (since, OWNER_ORDER + "%"))
        changes = _rows(c, "SELECT id, kind, detail, status, note, queued_at, decided_at,"
                           " applied_at FROM site_queue WHERE queued_at>=? ORDER BY id",
                        (since,))
        last_run = (_rows(c, "SELECT started_at, status FROM day_runs ORDER BY started_at"
                             " DESC LIMIT 1") or [{}])[0]
    for t in tasks:
        t["said"] = _result_text(t)
    by_task = {}
    by_words = []
    for row in changes:
        d = _detail(row["detail"])
        if d.get("task_id"):
            by_task.setdefault(str(d["task_id"]), []).append(row)
        elif d.get("from_your_instruction") or "the owner asked for this" in _words(d.get("why")):
            by_words.append((row, _words(d.get("found")) + " " + _words(d.get("why"))))

    leads_t = {}
    try:
        from . import targets
        for j in targets.status().get("jobs") or []:
            if j.get("name") == "leads" and j.get("per_day"):
                leads_t = {"asked": int(j["per_day"]), "done": int(j.get("done_today") or 0),
                           "short": int(j.get("remaining") or 0)}
    except Exception:
        leads_t = {}

    used = set()
    out = []

    def follow(day, sector, text, origin, saved_at):
        start = _day_start(day) if day else (saved_at or 0) - 60
        key = _words(text)[:40]
        mine = [t for t in tasks if str(t["id"]) not in used
                and (t.get("created_at") or 0) >= start - 60
                and (key and key in _words(f"{t['title']} {t.get('detail') or ''}"))]
        if sector == "Leads":
            mine += [t for t in tasks if str(t["id"]) not in used and t not in mine
                     and t["sector"] == "Leads" and (t.get("created_at") or 0) >= start
                     and t["title"].startswith(OWNER_ORDER + ": find the ")]
        for t in mine:
            used.add(str(t["id"]))
        chs = []
        for t in mine:
            chs += [_change(r) for r in by_task.get(str(t["id"]), [])]
        for row, words in by_words:
            if key and key in words and row["id"] not in {x["id"] for x in chs}:
                chs.append(_change(row))
        steps = [{"who": t.get("owner") or t.get("desk") or t["sector"], "sector": t["sector"],
                  "status": t["status"],
                  "says": TASK_SAYS.get(t["status"], t["status"]).format(
                      sector=t["sector"], owner=t.get("owner") or t["sector"]),
                  "said": t["said"], "title": t["title"],
                  "when": t.get("finished_at") or t.get("claimed_at") or t.get("created_at")}
                 for t in mine]
        leads = leads_t if (sector == "Leads" and day == time.strftime("%Y-%m-%d")
                            and leads_t) else None
        state, left = _state(steps, chs, leads)
        if state == "not started":
            ran_after = (last_run.get("started_at") or 0) > (saved_at or 0)
            left.append("the day has run since you gave it and it never became a job — "
                        "that is a fault: tell IT or the MD in the chat" if ran_after else
                        "the MD puts it on the board when you press Run the day")
        out.append({"day": day, "sector": sector, "text": text, "origin": origin,
                    "state": state, "jobs": steps, "changes": chs, "left": left,
                    "leads": leads})

    for o in orders:
        follow(o["day"], o["sector"], o["orders"], "order box", o.get("updated_at"))
    for t in tasks:
        if str(t["id"]) in used or (t.get("created_by") or "") != "owner":
            continue
        follow(time.strftime("%Y-%m-%d", time.localtime(t.get("created_at") or 0)),
               t["sector"], t["title"], "chat", t.get("created_at"))
    # Website changes asked for in the chat, not tied to any order above.
    for row, words in by_words:
        if any(row["id"] in {x["id"] for x in o["changes"]} for o in out):
            continue
        ch = _change(row)
        state, left = _state([], [ch])
        out.append({"day": time.strftime("%Y-%m-%d", time.localtime(row.get("queued_at") or 0)),
                    "sector": "IT", "text": ch["what"], "origin": "chat", "state": state,
                    "jobs": [], "changes": [ch], "left": left, "leads": None})

    out.sort(key=lambda o: o["day"], reverse=True)
    counts = {}
    for o in out:
        counts[o["state"]] = counts.get(o["state"], 0) + 1
    head = (", ".join(f"{n} {s}" for s, n in sorted(counts.items(), key=lambda x: -x[1]))
            if out else "no orders in the last week")
    return {"ok": True, "orders": out, "counts": counts, "head": head}


def as_text(r) -> str:
    L = [f"YOUR ORDERS — {r['head']}"]
    for o in r["orders"]:
        L.append(f"\n{o['day']}  {o['sector']}  [{o['state'].upper()}]  ({o['origin']})")
        L.append(f"  “{o['text'][:160]}”")
        for j in o["jobs"]:
            L.append(f"  · {j['who']}: {j['says']}" + (f" — {j['said'][:160]}" if j["said"] else ""))
        for ch in o["changes"]:
            L.append(f"  · change #{ch['id']}: {ch['what'][:90]} — {ch['says']}"
                     + (f" ({ch['note'][:120]})" if ch["note"] else ""))
        for l in o["left"]:
            L.append(f"  → {l}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Every order he gave and what became of it.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--days", type=int, default=DAYS)
    a = ap.parse_args()
    r = report(a.days)
    print(json.dumps(r, default=str) if a.json else as_text(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
