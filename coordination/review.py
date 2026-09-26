"""
review.py — what the agency has written for the owner, before any of it leaves.

    python -m coordination.review --json          everything waiting on him
    echo '{"do":"send","ids":[3,4]}' | python -m coordination.review --act
    python -m coordination.review --draft-mails   write a mail for each new lead
    python -m coordination.review --write-page    write the next briefed page
    python -m coordination.review --publish-now   put his approved pages live

THE TWO THINGS HE ASKED TO SEE

  "it will write mail according to the leads and will show me the mail it
   writes then will use the email i integrated to send the mails to the
   selected mails i will select"

  "i asked for 1 post and publish to website i do not know if it writes or
   not made a section where i can see if it write the post to publish and
   mail to sent i will approve both of them"

Both had half an answer already and neither was visible. Drafted mail sat in
outreach_queue and the office showed five of them as paragraphs, with approving
one quietly switching outreach to send the rest on its own. Written pages sat
in site_queue as a filename and a sentence, and pages the checks had held back
(content_queue 'in_review') were on no screen at all — which is why he could
not tell whether his post had been written. It had been, four times, and held.

This module is the one place both are read from and decided on. ui/review.html
draws it; server.js routes /api/review to it.

WHAT EACH BUTTON DOES, AND WHAT STILL GUARDS IT

  send          approves exactly the ticked drafts and sends them now, through
                his SMTP. The daily cap and the sending domain are enforced in
                mail._smtp_send, Security looks at each lead again at the
                moment of sending, and nothing unticked goes.
  discard       those drafts are never sent.
  publish       his yes on a written page. The IT Releaser is still the only
                thing that writes to the live site, and it still runs the test
                suite first (pipelines/it_release). "Put it live now" starts
                that desk rather than waiting for the next day run.
  reject_page   his no. The page is not published.
  rewrite       a page the checks held goes back to the writer, to be written
                again on the next pass. A held page cannot be published past
                the Auditor — that is a handbook red line, not a setting.
  discard_page  a held page is dropped.
"""
import argparse
import json
import sqlite3
import sys
import time

try:
    from . import board
except ImportError:  # pragma: no cover
    import board

# How long a desk's run counts as "still going" for the busy flags. A run
# older than this is either finished or a corpse the Overseer will sweep.
BUSY_SECS = 2 * 3600


def _conn():
    c = sqlite3.connect(board.DB, timeout=30)
    c.row_factory = sqlite3.Row
    board._migrate(c)
    return c


def _rows(c, q, args=()):
    try:
        return [dict(r) for r in c.execute(q, args)]
    except sqlite3.Error:
        return []


def _json(raw, default=None):
    try:
        v = json.loads(raw) if raw else default
        return v if v is not None else default
    except (TypeError, ValueError):
        return default


def busy(agent, c=None) -> bool:
    """Is this desk running right now? A dead process does not count."""
    own = c is None
    c = c or _conn()
    try:
        for r in _rows(c, "SELECT pid, host FROM agent_runs WHERE agent=?"
                          " AND status='running' AND started_at>?",
                       (agent, int(time.time()) - BUSY_SECS)):
            if not r.get("pid"):
                return True
            try:
                if board.process_alive(r["pid"], r.get("host") or ""):
                    return True
            except Exception:
                return True
        return False
    finally:
        if own:
            c.close()


# ---------------------------------------------------------------------------
# WHAT IS WAITING
# ---------------------------------------------------------------------------

def _mail_settings():
    try:
        from pipelines import mail
        host = mail.setting("SMTP_HOST").strip()
        user = mail.setting("SMTP_USER").strip()
        sender = (mail.setting("SMTP_FROM") or user).strip()
        return {"ready": bool(host and user), "from": sender,
                "cap": mail.daily_cap(), "sent_today": mail.sent_today()}
    except Exception as e:
        return {"ready": False, "from": "", "cap": 0, "sent_today": 0,
                "error": str(e)}


def _mails(c):
    waiting = []
    for m in _rows(c, "SELECT q.*, l.name AS lead_name, l.area AS lead_area,"
                      " l.segment AS lead_segment, l.source AS lead_source,"
                      " l.phone AS lead_phone, l.found_at AS lead_found_at"
                      " FROM outreach_queue q LEFT JOIN leads l ON l.id=q.lead_id"
                      " WHERE q.status='pending' ORDER BY q.queued_at, q.id"):
        audit = _json(m.get("audit"), {}) or {}
        waiting.append({
            "id": m["id"], "company": m.get("company") or "", "to": m.get("to_email") or "",
            "subject": m.get("subject") or "", "body": m.get("body") or "",
            "service": m.get("service") or "", "queued_at": m.get("queued_at") or 0,
            "words": audit.get("words"), "warnings": audit.get("warnings") or [],
            "lead": {"name": m.get("lead_name") or "", "area": m.get("lead_area") or "",
                     "segment": m.get("lead_segment") or "",
                     "source": m.get("lead_source") or "",
                     "phone": m.get("lead_phone") or ""}})
    approved = [{"id": m["id"], "company": m.get("company") or "",
                 "to": m.get("to_email") or "", "subject": m.get("subject") or "",
                 "note": m.get("note") or ""}
                for m in _rows(c, "SELECT * FROM outreach_queue WHERE status='approved'"
                                  " ORDER BY decided_at")]
    recent = [{"id": m["id"], "company": m.get("company") or "",
               "to": m.get("to_email") or "", "subject": m.get("subject") or "",
               "status": m.get("status"), "note": m.get("note") or "",
               "at": m.get("sent_at") or m.get("decided_at") or m.get("queued_at") or 0}
              for m in _rows(c, "SELECT * FROM outreach_queue"
                                " WHERE status IN ('sent','rejected')"
                                " ORDER BY COALESCE(sent_at, decided_at) DESC LIMIT 20")]
    # Leads that could be written to and have no draft yet — so the page can
    # say "7 of your leads have no mail yet" rather than looking finished.
    unwritten = 0
    try:
        unwritten = c.execute(
            "SELECT count(*) FROM leads l WHERE COALESCE(l.email,'')!=''"
            " AND COALESCE(l.stage,'new') IN ('new','nurture')"
            " AND l.id NOT IN (SELECT lead_id FROM outreach_queue"
            "                  WHERE lead_id IS NOT NULL)"
            " AND l.id NOT IN (SELECT lead_id FROM outreach_log"
            "                  WHERE direction='sent' AND lead_id IS NOT NULL)"
        ).fetchone()[0]
    except sqlite3.Error:
        pass
    return {"waiting": waiting, "approved": approved, "recent": recent,
            "leads_without_mail": unwritten, "writing": busy("leads-outreach", c),
            **_mail_settings()}


def _page_of(detail):
    d = _json(detail, {}) or {}
    ch = d.get("change") or {}
    return d, ch


def _pages(c):
    briefs = {r["id"]: _json(r.get("brief"), {}) or {}
              for r in _rows(c, "SELECT id, brief FROM content_queue")}
    waiting, approved, recent = [], [], []
    for s in _rows(c, "SELECT * FROM site_queue WHERE kind='new_page'"
                      " ORDER BY queued_at DESC, id DESC LIMIT 60"):
        d, ch = _page_of(s.get("detail"))
        cid = ch.get("content_id")
        b = briefs.get(cid, {}) if cid else {}
        row = {"id": s["id"], "title": d.get("title") or ch.get("page") or "A new page",
               "file": ch.get("page") or "", "why": d.get("why") or "",
               "warnings": d.get("auditor") or [], "content_id": cid,
               "keyword": b.get("primary_keyword") or "",
               "queued_at": s.get("queued_at") or 0, "status": s.get("status"),
               "note": s.get("note") or ""}
        if s.get("status") == "pending":
            waiting.append({**row, "html": ch.get("html") or ""})
        elif s.get("status") == "approved":
            approved.append(row)
        elif len(recent) < 12:
            recent.append(row)

    held = []
    for r in _rows(c, "SELECT id, title, brief, draft, created_at FROM content_queue"
                      " WHERE status='in_review' ORDER BY id DESC LIMIT 20"):
        page = _json(r.get("draft"), {}) or {}
        b = _json(r.get("brief"), {}) or {}
        held.append({"id": r["id"], "title": page.get("title") or r.get("title") or "",
                     "keyword": b.get("primary_keyword") or "",
                     "why": page.get("_held_because") or [],
                     "html": page.get("_page_html") or page.get("html") or "",
                     "at": r.get("created_at") or 0})

    counts = {}
    for r in _rows(c, "SELECT status, count(*) n FROM content_queue GROUP BY status"):
        counts[r["status"] or ""] = r["n"]
    to_write = 0
    try:
        to_write = c.execute("SELECT count(*) FROM content_queue WHERE status='briefed'"
                             " AND (draft IS NULL OR draft='')").fetchone()[0]
    except sqlite3.Error:
        pass
    return {"waiting": waiting, "approved": approved, "recent": recent, "held": held,
            "to_write": to_write,
            "held_for_seo": sum(n for k, n in counts.items() if k.startswith("blocked_on_")),
            "published": counts.get("published", 0),
            "writing": busy("content-writer", c),
            "publishing": busy("it-releaser", c)}


def build() -> dict:
    c = _conn()
    try:
        other = 0
        try:
            other = c.execute("SELECT count(*) FROM site_queue WHERE status='pending'"
                              " AND kind!='new_page'").fetchone()[0]
        except sqlite3.Error:
            pass
        day_running = False
        try:
            r = c.execute("SELECT 1 FROM day_runs WHERE status='running'"
                          " AND started_at>? LIMIT 1",
                          (int(time.time()) - 3 * 3600,)).fetchone()
            day_running = bool(r)
        except sqlite3.Error:
            pass
        return {"ok": True, "mails": _mails(c), "pages": _pages(c),
                "other_site_changes": other, "day_running": day_running,
                "sectors": {s: board.sector_enabled(s) for s in ("Leads", "Content", "IT")}}
    finally:
        c.close()


# ---------------------------------------------------------------------------
# HIS DECISIONS
# ---------------------------------------------------------------------------

def _ids(ids):
    out = []
    for i in ids or []:
        try:
            out.append(int(i))
        except (TypeError, ValueError):
            continue
    return out


def act(do, ids) -> dict:
    ids = _ids(ids)
    if not ids:
        return {"ok": False, "error": "nothing was picked"}
    when = time.strftime("%Y-%m-%d %H:%M")

    if do == "send":
        s = _mail_settings()
        if not s["ready"]:
            return {"ok": False, "error": (
                "Your email is not set up yet, so nothing was sent and nothing was "
                "approved. Add it on Setup → Integrations → Email (SMTP), then "
                "press Send again.")}
        from pipelines import leads_outreach
        r = leads_outreach.send_now(ids)
        sent, failed = r.get("sent") or [], r.get("failed") or []
        head = (f"Sent {len(sent)} mail(s) from {s['from']}."
                if sent else "Nothing was sent.")
        if failed:
            head += f" {len(failed)} did not go: {failed[0].get('why')}"
        if r.get("error"):
            head = r["error"]
        return {"ok": bool(sent) and not r.get("error"), "sent": sent,
                "failed": failed, "head": head}

    if do == "discard":
        n = sum(1 for i in ids
                if board.decide_outreach(i, "rejected", note=f"owner discarded {when}"))
        return {"ok": bool(n), "head": f"Discarded {n} draft(s). They will not be sent."}

    if do in ("publish", "reject_page"):
        decision = "approved" if do == "publish" else "rejected"
        done = []
        with board._conn() as c:
            board._migrate(c)
            for i in ids:
                r = c.execute("SELECT kind, status, detail FROM site_queue WHERE id=?",
                              (i,)).fetchone()
                if not r or r["kind"] != "new_page" or r["status"] != "pending":
                    continue
                c.execute("UPDATE site_queue SET status=?, decided_at=? WHERE id=?",
                          (decision, board._now(), i))
                if decision == "rejected":
                    _d, ch = _page_of(r["detail"])
                    if ch.get("content_id"):
                        c.execute("UPDATE content_queue SET status='rejected' WHERE id=?",
                                  (int(ch["content_id"]),))
                done.append(i)
        if do == "publish":
            head = (f"Approved {len(done)} page(s). The IT Releaser puts them on your "
                    "website after the tests pass — press 'Put it live now' to start "
                    "it, or it happens on the next day run.")
        else:
            head = f"Turned down {len(done)} page(s). They will not be published."
        return {"ok": bool(done), "decided": done, "head": head}

    if do in ("rewrite", "discard_page"):
        done = []
        with board._conn() as c:
            board._migrate(c)
            for i in ids:
                r = c.execute("SELECT status FROM content_queue WHERE id=?", (i,)).fetchone()
                if not r or r["status"] != "in_review":
                    continue
                if do == "rewrite":
                    c.execute("UPDATE content_queue SET status='briefed', draft=NULL"
                              " WHERE id=?", (i,))
                else:
                    c.execute("UPDATE content_queue SET status='rejected' WHERE id=?", (i,))
                done.append(i)
        head = (f"{len(done)} page(s) sent back to be written again — Content picks "
                "them up on its next pass, or press 'Write the next page now'."
                if do == "rewrite" else f"Dropped {len(done)} held page(s).")
        return {"ok": bool(done), "decided": done, "head": head}

    return {"ok": False, "error": f"not something this page does: {do!r}"}


# ---------------------------------------------------------------------------
# THE THREE DESKS HE CAN START FROM HERE
# ---------------------------------------------------------------------------
#
# Each is the ordinary pipeline, run once, exactly as the day run would run
# it. The server starts these in the background so the page stays usable
# while Claude writes, and the page shows "writing…" from the busy flags.

def draft_mails() -> dict:
    if not board.sector_enabled("Leads"):
        return {"ok": False, "error": "Leads is switched off."}
    if busy("leads-outreach"):
        return {"ok": False, "error": "The outreach desk is already writing."}
    board.post_task(
        "Leads", "Write outreach mails for the leads on file",
        "The owner pressed 'Write the mails' on the Review page. Draft one mail "
        "per eligible lead and queue each one for his tick. Send nothing that he "
        "has not ticked.", created_by="owner", desk="leads-outreach")
    from pipelines import leads_outreach
    return leads_outreach.run()


def write_page() -> dict:
    if not board.sector_enabled("Content"):
        return {"ok": False, "error": "Content is switched off."}
    if busy("content-writer"):
        return {"ok": False, "error": "Content is already writing."}
    from pipelines import content_write
    return content_write.run(limit=1)


def publish_now() -> dict:
    if busy("it-releaser"):
        return {"ok": False, "error": "The IT Releaser is already working."}
    if not board.approved_site_changes(limit=1):
        return {"ok": False, "error": "Nothing is approved and waiting to go live."}
    from pipelines import it_release
    return it_release.run()


def main():
    ap = argparse.ArgumentParser(description="What is waiting for the owner to read.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--act", action="store_true", help="read {do, ids} on stdin")
    ap.add_argument("--draft-mails", action="store_true")
    ap.add_argument("--write-page", action="store_true")
    ap.add_argument("--publish-now", action="store_true")
    a = ap.parse_args()
    try:
        if a.act:
            req = json.loads(sys.stdin.read() or "{}")
            out = act(str(req.get("do") or ""), req.get("ids") or [])
        elif a.draft_mails:
            out = draft_mails()
        elif a.write_page:
            out = write_page()
        elif a.publish_now:
            out = publish_now()
        else:
            out = build()
    except Exception as e:
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
