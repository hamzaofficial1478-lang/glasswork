"""
notify.py — the things actually worth putting on the owner's phone.

    python -m coordination.notify --preview    what would be sent, sends nothing
    python -m coordination.notify --send       send it for real
    python -m coordination.notify --test       one real message, to prove the pipe

THE GAP THIS FILLS

integrations.send_message() said in its own docstring that it carried "the daily
digest, approval requests, the Overseer". One of those three was true. Grep the
repo before this file existed and exactly one caller turned up: report_daily.py.
So the owner connected ntfy, received a single digest, and reasonably concluded
the notification setup was broken. It was not broken. It was empty.

Everything else the handbook says reaches him — "amber items queue to the
owner's Telegram", the spend cap, a lead replying, the enquiry form going down —
was being written to a table and left there. A row in a database is not a
notification. If he has to open the office to find out the contact form is dead,
the alert has failed at the only job it had.

WHAT GETS THROUGH, AND WHAT DOES NOT

The hard part of notifications is not sending them, it is not sending them. A
phone that buzzes about nothing gets muted, and then the one that mattered is
muted too. So there are two rules:

  1. ONLY THINGS THAT NEED HIM. Something is worth a buzz when the agency is
     BLOCKED on his answer, or when money is at stake, or when a customer is
     waiting. Progress is not news. "SEO wrote 4 briefs" is what the digest is
     for, once a day, at a time he chose.

  2. ONCE. Every alert carries a key, and a key that has been sent is not sent
     again. A pending approval that pings hourly until answered trains him to
     swipe it away, which means the next one goes unread too.

URGENCY, HONESTLY RANKED

  now      a customer or the enquiry path is affected. Minutes matter.
  today    the agency is stopped waiting for him, or money is involved.
  digest   everything else — it goes in the daily summary and nowhere else.
"""
import argparse
import json
import sqlite3
import time

try:
    from . import board
except ImportError:  # pragma: no cover
    import board

NOW, TODAY, DIGEST = "now", "today", "digest"
_RANK = {NOW: 0, TODAY: 1, DIGEST: 2}

# Do not re-send the same alert inside this window even if it is still true.
# A day is deliberate: a thing still unanswered tomorrow morning is worth one
# more nudge, and nothing is worth more than one nudge a day.
REPEAT_AFTER = 20 * 3600


def _agency() -> str:
    try:
        from . import site
        return site.agency_name()
    except Exception:
        return "The agency"


def _conn():
    return board._conn()


def _table(c):
    c.execute("""CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT NOT NULL UNIQUE, urgency TEXT, title TEXT, body TEXT,
        first_seen INTEGER, sent_at INTEGER, route TEXT, detail TEXT)""")


# ---------------------------------------------------------------------------
# WHAT IS WAITING
# ---------------------------------------------------------------------------

def _rows(c, sql, args=()):
    try:
        return [dict(r) for r in c.execute(sql, args)]
    except sqlite3.Error:
        return []


def collect() -> list:
    """
    Everything the agency currently wants the owner to know, newest evidence
    first. Reads only — deciding to send is a separate step on purpose, so
    --preview can show him exactly what would go out.
    """
    out = []
    with _conn() as c:
        board._migrate(c)
        c.row_factory = sqlite3.Row

        # 1. The enquiry path. A dead contact form is the only thing in this
        #    system that is losing money by the hour.
        for r in _rows(c, "SELECT id, kind, detail, url FROM site_issues"
                          " WHERE status='open' AND severity='critical'"
                          " ORDER BY found_at DESC LIMIT 5"):
            out.append({"key": f"site:{r['id']}", "urgency": NOW,
                        "title": "Site problem — enquiries may not be reaching you",
                        "body": f"{r['detail']} ({r['url'] or 'site-wide'})"})

        # 2. A human is waiting for a reply. Nothing else in the agency has a
        #    person on the other end of it.
        #
        #    THE REPLY ITSELF DOES NOT GO IN THE ALERT. A topic on the public
        #    ntfy.sh is readable by anyone who guesses the name, and pasting a
        #    customer's message into it would publish their words and their
        #    company to a channel with no access control — to save the owner one
        #    tap. He gets told who wrote and roughly what kind of message it is;
        #    the words stay in the office. Self-hosting ntfy changes the risk,
        #    not the principle, so this does not read a setting.
        for r in _rows(c, "SELECT o.id, o.body, o.reply_class, l.company"
                          " FROM outreach_log o LEFT JOIN leads l ON l.id=o.lead_id"
                          " WHERE o.direction='reply' AND o.needs_owner=1"
                          " ORDER BY o.at DESC LIMIT 5"):
            kind = {"interested": "They sound interested.",
                    "question": "They asked a question.",
                    "not_now": "Not right now, they said.",
                    "no": "A no."}.get((r["reply_class"] or "").strip(), "")
            out.append({"key": f"reply:{r['id']}", "urgency": NOW,
                        "title": f"{r['company'] or 'A lead'} replied",
                        "body": (kind + " Open the office to read it and answer."
                                 ).strip()})

        # 3. The agency is stopped until he answers.
        for r in _rows(c, "SELECT id, kind, detail FROM site_queue"
                          " WHERE status='pending' ORDER BY queued_at LIMIT 5"):
            out.append({"key": f"change:{r['id']}", "urgency": TODAY,
                        "title": "A website change is waiting for your yes or no",
                        "body": f"{r['kind']}: {(r['detail'] or '')[:240]}"})
        for r in _rows(c, "SELECT id, title, sector FROM tasks"
                          " WHERE status IN ('open','in_progress')"
                          " AND risk IN ('amber','red') LIMIT 5"):
            out.append({"key": f"task:{r['id']}", "urgency": TODAY,
                        "title": f"{r['sector']} needs your approval",
                        "body": r["title"] or ""})

        # 4. Money.
        try:
            from . import cabinet
            cap, spent = cabinet.daily_spend_cap(), cabinet.spent_today()
            if cap and spent >= cap:
                out.append({"key": f"cap:{time.strftime('%Y-%m-%d')}", "urgency": TODAY,
                            "title": "Daily spend cap reached",
                            "body": (f"About ${spent:.2f} of ${cap:.2f} spent today. "
                                     f"Paid work is refused until midnight or until "
                                     f"you raise the cap on /setup.")})
        except Exception:
            pass

        # 5. A plan allowance running out. Not urgent, but it becomes a bill.
        try:
            from . import usage
            for m in usage.status():
                if m["verdict"] == "over":
                    out.append({"key": f"quota:{m['provider']}:{m['kind']}:"
                                       f"{time.strftime('%Y-%m')}",
                                "urgency": TODAY,
                                "title": f"{m['label']} — plan allowance gone",
                                "body": (f"{m['used']:,} of {m['allowance']:,}. "
                                         f"From here: {m['then']}.")})
        except Exception:
            pass

        # 6. An agent that started and never came back. The Overseer's job, and
        #    until now it had no way to say so out loud.
        stalled = _rows(c, "SELECT agent, note, started_at FROM agent_runs"
                           " WHERE status='running' AND started_at < ?"
                           " ORDER BY started_at LIMIT 3",
                        (int(time.time()) - 1800,))
        for r in stalled:
            out.append({"key": f"stall:{r['agent']}:{r['started_at']}",
                        "urgency": DIGEST,
                        "title": f"{r['agent']} has been running for over 30 minutes",
                        "body": (r["note"] or "no note") + " — probably stuck."})

    out.sort(key=lambda a: _RANK.get(a["urgency"], 9))
    return out


# ---------------------------------------------------------------------------
# SENDING, ONCE
# ---------------------------------------------------------------------------

def unsent(alerts=None, repeat_after=REPEAT_AFTER) -> list:
    """The ones he has not already been told about recently."""
    alerts = collect() if alerts is None else alerts
    cutoff = int(time.time()) - repeat_after
    fresh = []
    with _conn() as c:
        board._migrate(c)
        _table(c)
        for a in alerts:
            row = c.execute("SELECT sent_at FROM notifications WHERE key=?",
                            (a["key"],)).fetchone()
            if row and row[0] and int(row[0]) > cutoff:
                continue
            fresh.append(a)
    return fresh


def _text(a):
    return f"{a['title']}\n{a['body']}".strip()


def send(alerts=None, dry_run=False, include_digest=False) -> dict:
    """
    Push what is waiting. Digest-level items are held back by default — they
    belong in the daily summary, not on his lock screen.
    """
    alerts = unsent(alerts)
    if not include_digest:
        alerts = [a for a in alerts if a["urgency"] != DIGEST]
    if not alerts:
        return {"ok": True, "sent": 0, "alerts": [], "note": "nothing needs him"}
    if dry_run:
        return {"ok": True, "sent": 0, "dry_run": True, "alerts": alerts}

    try:
        from providers import integrations
    except Exception as e:
        return {"ok": False, "sent": 0, "error": f"cannot load the channel: {e}"}

    sent, results = 0, []
    now = int(time.time())
    with _conn() as c:
        board._migrate(c)
        _table(c)
        for a in alerts:
            r = integrations.send_message(_text(a))
            good = bool(r.get("ok"))
            sent += 1 if good else 0
            results.append({**a, "delivered": good,
                            "detail": r.get("detail") or r.get("error") or ""})
            c.execute(
                "INSERT INTO notifications(key,urgency,title,body,first_seen,sent_at,"
                " route,detail) VALUES(?,?,?,?,?,?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET sent_at=excluded.sent_at,"
                " route=excluded.route, detail=excluded.detail",
                (a["key"], a["urgency"], a["title"], a["body"], now,
                 now if good else None, r.get("integration") or "",
                 (r.get("detail") or r.get("error") or "")[:200]))
    return {"ok": True, "sent": sent, "alerts": results}


def push_activity(limit=40, dry_run=False) -> dict:
    """
    Send what the agency has DONE since the last time this ran.

    The owner asked for activity on his phone, and this file argues elsewhere
    that progress is not news. Both are honoured by BATCHING: one message
    summarising everything new, at most once per run, instead of a buzz per
    event. Forty things that happened is one notification, and it is readable.

    Problems are named individually inside it; routine work is counted. A
    message you can skim in three seconds is one you keep reading; forty
    separate ones is a muted app by Tuesday.
    """
    try:
        from . import activity
    except ImportError:  # pragma: no cover
        import activity

    last = 0
    try:
        with _conn() as c:
            board._migrate(c)
            _table(c)
            r = c.execute("SELECT sent_at FROM notifications WHERE key='activity:last'"
                          ).fetchone()
            last = int(r[0]) if r and r[0] else 0
    except sqlite3.Error:
        pass

    f = activity.feed(ts=last, limit=limit)
    fresh = [e for e in f["events"] if e["at"] > last] if last else f["events"]
    if not fresh:
        return {"ok": True, "sent": 0, "note": "nothing new since the last push"}

    bad = [e for e in fresh if e["level"] == "bad"]
    rooms = {}
    for e in fresh:
        rooms[e["room"] or "—"] = rooms.get(e["room"] or "—", 0) + 1
    lines = [f"{len(fresh)} things happened."]
    if bad:
        lines.append("")
        lines.append(f"{len(bad)} went wrong:")
        for e in bad[:5]:
            lines.append(f"  • {e['who']}: {e['what']}"[:160])
    lines.append("")
    lines.append("By room: " + ", ".join(f"{k} {v}" for k, v in sorted(
        rooms.items(), key=lambda kv: -kv[1])))
    lines.append("Open the office to read any of it.")
    text = "\n".join(lines)

    if dry_run:
        return {"ok": True, "sent": 0, "dry_run": True, "events": len(fresh),
                "text": text}

    try:
        from providers import integrations
        r = integrations.send_message(text)
    except Exception as e:
        return {"ok": False, "sent": 0, "error": str(e)}

    now = int(time.time())
    try:
        with _conn() as c:
            board._migrate(c)
            _table(c)
            c.execute(
                "INSERT INTO notifications(key,urgency,title,body,first_seen,sent_at,"
                " route,detail) VALUES('activity:last',?,?,?,?,?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET sent_at=excluded.sent_at,"
                " body=excluded.body, detail=excluded.detail",
                (DIGEST, "Activity", text[:400], now, now if r.get("ok") else last,
                 r.get("integration") or "", (r.get("detail") or r.get("error") or "")[:200]))
    except sqlite3.Error:
        pass
    return {"ok": bool(r.get("ok")), "sent": 1 if r.get("ok") else 0,
            "events": len(fresh), "detail": r.get("detail") or r.get("error") or ""}


def preview_text() -> str:
    alerts = collect()
    waiting = unsent(alerts)
    L = ["WHAT WOULD REACH YOUR PHONE", "=" * 60]
    if not alerts:
        L += ["", "  Nothing. That is a good sign, not a broken pipe —",
              "  this only fires when the agency is stopped waiting for you,",
              "  a customer is waiting, or money is involved.", ""]
    for a in alerts:
        mark = {NOW: "!!", TODAY: " !", DIGEST: "  "}[a["urgency"]]
        again = "" if a in waiting else "   (already sent — will not repeat today)"
        L.append(f"  {mark} [{a['urgency']}] {a['title']}{again}")
        L.append(f"       {a['body'][:150]}")
    try:
        from providers import integrations
        env = integrations.read_env()
        route = ("ntfy → " + env["NTFY_TOPIC"]) if (env.get("NTFY_TOPIC") or "").strip() \
            else ("Telegram" if (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
                  else "NOTHING CONFIGURED — set a topic on /setup")
    except Exception:
        route = "unknown"
    L += ["", f"Route: {route}",
          "",
          "WHAT YOU GET, AND WHAT YOU DO NOT",
          "  You get: a dead contact form, a lead replying, anything waiting on",
          "  your approval, the spend cap, a plan allowance running out.",
          "  You do not get: progress. Briefs written, pages audited and leads",
          "  found go in the one daily digest, so the phone stays quiet enough",
          "  that you still look when it does buzz."]
    return "\n".join(L)


def test() -> dict:
    """One real message, so he can watch it land."""
    try:
        from providers import integrations
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return integrations.send_message(
        _agency() + " — test.\nIf you can read this, your notifications work. "
        "You will get: site problems, lead replies, approvals, and the spend cap. "
        "Nothing else.")


def main():
    ap = argparse.ArgumentParser(description="What reaches the owner's phone.")
    ap.add_argument("--preview", action="store_true", help="show it, send nothing")
    ap.add_argument("--send", action="store_true", help="actually send")
    ap.add_argument("--test", action="store_true", help="one real message, to prove the pipe")
    ap.add_argument("--activity", action="store_true",
                    help="push a batched summary of what has happened since last time")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    board.ensure_schema()
    if a.test:
        print(json.dumps(test(), indent=2))
        return
    if a.activity:
        print(json.dumps(push_activity(dry_run=not a.send), indent=2, ensure_ascii=False))
        return
    if a.send:
        print(json.dumps(send(), indent=2, ensure_ascii=False))
        return
    if a.json:
        print(json.dumps({"ok": True, "alerts": collect(),
                          "waiting": unsent()}, indent=2, ensure_ascii=False))
        return
    print(preview_text())


if __name__ == "__main__":
    main()
