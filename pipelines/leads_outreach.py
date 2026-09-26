"""
leads_outreach.py — the mail actually goes out.

    python3 -m pipelines.leads_outreach              one pass, as the day runs it
    python3 -m pipelines.leads_outreach --dry-run    draft everything, send nothing
    python3 -m pipelines.leads_outreach --queue      what is waiting on the owner

THE GAP THIS FILLS

Everything needed to send a cold mail existed and nothing drove it. mail.py can
write the copy and has the send boundary — daily cap and sending domain both
enforced inside it. leads_hunt.py collects. Security screens. Nothing joined
them up, and `day_run.py` had no outreach step at all, so the agency has been
collecting leads it never wrote to. The owner asked for "automatic mails
sending system"; this is the missing middle.

WHY mail.compose() IS NOT THE THING THAT RUNS HERE

compose() asks the MD to approve each draft with `request_verification(...,
blocking=True, timeout=120)`. That waits for an answer inside the same call,
and the MD only answers its inbox during its own cycle — so it always times
out, and compose(send=True) could never send. It is fine for a one-off from the
command line; it cannot be the daily path. This runner does the same job with
the gates arranged so they can actually be satisfied.

THE GATES, IN THE ORDER THEY RUN, AND WHY EACH ONE IS HERE

  1. the sector switch    the owner turned Leads off; nothing goes out. First,
                          because it is free and it is his instruction.
  2. a task on the board  this desk works from the board, never on its own
                          initiative. Same rule as every other pipeline.
  3. eligibility          an address, never contacted, stage 'new' or
                          'nurture'. The dedup promise is per BUSINESS: a firm
                          we have written to is not written to again.
  4. Security             scan_lead() on every lead, every time. A lead cleared
                          at collection can have been sitting for a month.
                          Anything with a high finding does not get written to,
                          and the finding is logged.
  5. the Auditor          coordination/auditor.py. The handbook's rule that
                          nothing leaves without passing QA, finally built. A
                          held draft is reported, never quietly dropped.
  6. the owner's tick     every draft is queued for him to read. Only the
                          ones he ticks and sends go out. See below.
  7. cap and domain       inside mail._smtp_send, where they already were, and
                          deliberately not duplicated here — a limit enforced
                          by whoever happens to call is a limit the next caller
                          skips.

EVERY MAIL IS HIS CALL

"it will write mail according to the leads and will show me the mail it
 writes then will use the email i integrated to send the mails to the
 selected mails i will select"

This used to hold the FIRST batch of three for him and treat approving any one
of them as permission to send every later draft on its own. That is not what
he asked for. Now every draft — one per eligible lead, up to BATCH a pass — is
queued, he reads them in the Mail room (/mail), ticks the recipients he
wants, and presses Send. Those go out through his own SMTP, inside the daily
cap, straight away (send_now). Nothing leaves without his tick; the ones he
does not want he discards, and they are never sent.

`--queue` lists what is held.
"""
import argparse
import json
import sys
import time

from coordination import auditor, board, deskwork, dispatch, uae
from . import mail, security_screen, services

AGENT = "leads-outreach"
SECTOR = "Leads"

# How many to draft in one pass. One model call each; he asked the Hunter for
# ten leads and wants a mail written for every one of them, so a pass covers
# that without drafting forty he will never get through.
BATCH = 10


def _company():
    """Who we are, for the signature and the footer. One source, as everywhere."""
    try:
        from coordination import site
        return {"name": site.site_name(), "website": site.site_url(),
                "phone": "", "whatsapp": "",
                "areas": uae.served()}
    except Exception:
        return {"name": "", "website": "", "phone": "", "whatsapp": "",
                "areas": uae.served()}


def eligible(limit=BATCH, only_ids=None, log=True):
    """
    Leads that may be written to, and why each rejected one may not.

    The refusals are the useful half. A pass that says "nothing to send" and
    does not say why is indistinguishable from a broken pass, which is exactly
    how the agency spent weeks collecting leads and writing to nobody.
    """
    import sqlite3
    c = sqlite3.connect(board.DB, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        board._migrate(c)
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM leads ORDER BY found_at DESC LIMIT 400")]
        touched = set()
        for r in c.execute("SELECT DISTINCT lead_id FROM outreach_log"
                           " WHERE direction='sent'"):
            touched.add(r[0])
        queued = set()
        for r in c.execute("SELECT lead_id FROM outreach_queue"
                           " WHERE status IN ('pending','approved','sent')"):
            queued.add(r[0])
    finally:
        c.close()

    can, cannot = [], []
    if only_ids:
        want = {int(i) for i in only_ids}
        rows = [r for r in rows if r["id"] in want]
    for r in rows:
        name = r.get("company") or r.get("name") or f"lead {r['id']}"
        if not (r.get("email") or "").strip():
            cannot.append({"id": r["id"], "company": name,
                           "why": "no address on file"})
            continue
        if r["id"] in touched:
            cannot.append({"id": r["id"], "company": name,
                           "why": "already written to — a firm is approached once"})
            continue
        if r["id"] in queued:
            cannot.append({"id": r["id"], "company": name,
                           "why": "a draft for this one is already queued"})
            continue
        if (r.get("stage") or "new") not in ("new", "nurture"):
            cannot.append({"id": r["id"], "company": name,
                           "why": f"stage is '{r.get('stage')}' — not a cold approach"})
            continue
        # SECURITY, every time. A lead cleared when it was collected may have
        # been sitting for a month, and the screen is cheap.
        findings = security_screen.scan_lead(r)
        bad = [f for f in findings if f["severity"] in ("high", "critical")]
        if bad:
            # Logged when it is a real refusal, not every time a screen that
            # only shows the list asks (log=False) — the Mail room reads this
            # on every refresh.
            if log:
                security_screen.log_events(findings, source=f"lead {r['id']}",
                                           action="refused")
            cannot.append({"id": r["id"], "company": name,
                           "why": f"Security: {bad[0]['why']}"})
            continue
        can.append(r)
        if len(can) >= limit:
            break
    return can, cannot


def draft_for(lead, company, owner_voice=""):
    """Write one mail and put it through the Auditor. No sending here."""
    key, label, _rel = mail.lead_service(lead)
    brief = mail.writing_brief(lead, company, owner_voice)
    written = mail._write_copy(mail._brain(), brief)
    if not written.get("ok"):
        return {"ok": False, "lead": lead, "service": label,
                "error": "could not write it: " + str(written.get("error"))}
    draft = written["draft"]
    v = auditor.check_outreach(draft, lead=lead, company=company,
                               service_label=label)
    subject, body = auditor.split_subject(draft)
    return {"ok": True, "lead": lead, "service": label, "service_key": key,
            "draft": draft, "subject": subject, "body": body,
            "audit": v, "used": written.get("used")}


def run(agent=AGENT, dry_run=False, limit=BATCH, only_ids=None) -> dict:
    """One pass. `only_ids`: write for just these leads — the Mail room's ticks."""
    with board.agent_run(agent) as run_row:
        out = _run(agent, dry_run, limit, only_ids)
        run_row.note(out.get("head") or out.get("error") or "nothing to do")
        return out


def _run(agent, dry_run, limit, only_ids=None) -> dict:
    if not board.sector_enabled(SECTOR):
        return {"ok": False, "error": "Leads is switched off — you turned this "
                                      "sector off, so nothing goes out."}

    # Mail he has already ticked and not yet seen go (the cap was full, or the
    # SMTP server was down when he pressed Send) goes first, task or no task:
    # his yes is the instruction.
    room = max(0, mail.daily_cap() - mail.sent_today())
    sent = [] if dry_run else _send_approved(room)

    task = board.next_open_task(SECTOR, agent)
    if not task:
        if sent:
            return {"ok": True, "agent": agent, "sent": sent, "queued": [],
                    "head": f"{len(sent)} mail(s) you approved were sent"}
        return dispatch.nothing_here(SECTOR, agent)

    company = _company()
    can, cannot = eligible(limit, only_ids=only_ids)
    if not can:
        board.finish_task(task["id"], agent,
                          {"drafted": 0, "why": "nobody eligible"}, status="done")
        return {"ok": False, "agent": agent, "task": task["id"],
                "eligible": 0, "not_eligible": cannot[:20], "sent": sent,
                "error": ("Nobody is eligible to be written to. "
                          + (cannot[0]["why"] if cannot else
                             "there are no leads on file yet"))}

    drafted, held, failed, queued = [], [], [], []

    for lead in can[:limit]:
        d = draft_for(lead, company)
        if not d.get("ok"):
            failed.append({"company": lead.get("company"), "why": d["error"]})
            continue
        line = {"lead_id": lead["id"], "company": lead.get("company"),
                "to": lead.get("email"), "subject": d["subject"],
                "service": d["service"], "words": d["audit"]["words"]}
        drafted.append(line)

        if not d["audit"]["pass"]:
            held.append({**line, "why": d["audit"]["blocking"]})
            continue
        if dry_run:
            continue

        # Every draft waits for him. See EVERY MAIL IS HIS CALL above.
        qid = board.queue_outreach(
            lead["id"], lead.get("company"), lead.get("email"),
            d["subject"], d["body"], service=d["service"],
            audit=d["audit"], by=agent)
        queued.append({**line, "queue_id": qid})

    head = _head(sent, queued, held, failed, drafted, dry_run)
    board.finish_task(task["id"], agent,
                      {"sent": len(sent), "queued": len(queued),
                       "held": len(held), "head": head}, status="done")

    # His phone is told by coordination/notify.py, which reads the queue itself
    # rather than being pushed at from here — one place decides what is worth a
    # buzz, and it groups the batch into one alert instead of three.

    did = board.log_decision(
        "leads-outreach",
        reasoning=(f"{len(can)} lead(s) eligible; drafted {len(drafted)}, "
                   f"Auditor held {len(held)}, queued {len(queued)} for the owner "
                   f"to tick; sent {len(sent)} he had already approved"),
        expectation=("Replies arrive within a week, or the segment is wrong and "
                     "the next pass targets a different one."),
        confidence=0.6,
        evidence={"lead_ids": [l["lead_id"] for l in drafted]})

    return {"ok": True, "agent": agent, "task": task["id"],
            "eligible": len(can), "not_eligible": cannot[:20],
            "drafted": drafted, "sent": sent, "queued": queued,
            "held": held, "failed": failed,
            "cap": mail.daily_cap(), "sent_today": mail.sent_today(),
            "decision_id": did, "head": head}


def _send_approved(room, ids=None, failed=None):
    """
    Mail the owner has said yes to, oldest first, up to `room`. With `ids`,
    only those. Anything that could not go is added to `failed`, if given.

    Security looks again at the moment of sending: a lead cleared when the
    draft was written may have been flagged since. A send that fails because
    the cap is full or the mail server did not answer stays 'approved' and
    goes on the next pass — his yes is not thrown away over a busy server.
    """
    out = []
    failed = failed if failed is not None else []
    import sqlite3
    c = sqlite3.connect(board.DB, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        board._migrate(c)
        q = "SELECT * FROM outreach_queue WHERE status='approved'"
        args = []
        if ids:
            q += " AND id IN (%s)" % ",".join("?" * len(ids))
            args = [int(i) for i in ids]
        rows = [dict(r) for r in c.execute(q + " ORDER BY queued_at", args)]
    finally:
        c.close()
    for q in rows:
        if len(out) >= room:
            failed.append({"id": q["id"], "company": q["company"], "to": q["to_email"],
                           "why": "today's cap is used up — it goes on the next pass"})
            continue
        lead = board.get_lead(q["lead_id"]) if hasattr(board, "get_lead") else None
        lead = lead or {"id": q["lead_id"], "email": q["to_email"],
                        "company": q["company"]}
        bad = [f for f in security_screen.scan_lead(lead)
               if f["severity"] in ("high", "critical")]
        if bad:
            security_screen.log_events(bad, source=f"lead {q['lead_id']}",
                                       action="refused")
            board.mark_outreach_sent(q["id"], False, f"Security: {bad[0]['why']}"[:200])
            failed.append({"id": q["id"], "company": q["company"], "to": q["to_email"],
                           "why": f"Security stopped it: {bad[0]['why']}"})
            continue
        draft = f"Subject: {q['subject']}\n\n{q['body']}"
        r = mail._smtp_send(lead, draft)
        if r.get("ok"):
            mail.log_outreach(q["lead_id"], draft, channel="email")
            _mark_contacted(q["lead_id"])
            board.mark_outreach_sent(q["id"], True, "approved by the owner")
            out.append({"id": q["id"], "lead_id": q["lead_id"], "company": q["company"],
                        "to": q["to_email"], "subject": q["subject"],
                        "service": q["service"], "words": 0})
        else:
            # Left 'approved' so the next pass tries again, with the reason
            # written where the Mail room shows it.
            _note_attempt(q["id"], r.get("error", "the send boundary refused it"))
            failed.append({"id": q["id"], "company": q["company"], "to": q["to_email"],
                           "why": r.get("error", "the send boundary refused it")})
    return out


def _note_attempt(qid, why):
    import sqlite3
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            c.execute("UPDATE outreach_queue SET note=? WHERE id=?",
                      (f"not sent yet: {why}"[:300], qid))
    except Exception:
        pass


def send_now(ids) -> dict:
    """
    The Mail room's Send button: approve exactly these drafts and send them,
    now, through his SMTP, inside the daily cap. Returns {sent, failed}.
    """
    if not board.sector_enabled(SECTOR):
        return {"ok": False, "sent": [], "failed": [],
                "error": "Leads is switched off — turn it back on to send mail."}
    ids = [int(i) for i in (ids or [])]
    if not ids:
        return {"ok": False, "sent": [], "failed": [], "error": "nothing was ticked"}
    for i in ids:
        board.decide_outreach(i, "approved", note="owner ticked it and pressed Send")
    failed = []
    room = max(0, mail.daily_cap() - mail.sent_today())
    sent = _send_approved(room, ids=ids, failed=failed)
    return {"ok": bool(sent) or not failed, "sent": sent, "failed": failed,
            "cap": mail.daily_cap(), "sent_today": mail.sent_today()}


def _mark_contacted(lead_id):
    import sqlite3
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            c.execute("UPDATE leads SET stage='contacted' WHERE id=? AND stage IN"
                      " ('new','nurture')", (lead_id,))
    except Exception:
        pass


def _head(sent, queued, held, failed, drafted, dry_run):
    if dry_run:
        return (f"dry run — drafted {len(drafted)}, "
                f"{len(held)} would be held by the Auditor, nothing sent")
    if queued:
        return (f"{len(queued)} mail(s) written and waiting on you. Tick the "
                "ones you want in the Mail room and press Send."
                + (f" {len(sent)} you approved earlier were sent." if sent else ""))
    if sent:
        return (f"{len(sent)} mail(s) sent"
                + (f", {len(held)} held by the Auditor" if held else "")
                + (f", {len(failed)} failed" if failed else ""))
    if held:
        return f"nothing sent — the Auditor held all {len(held)} draft(s)"
    if failed:
        return f"nothing sent — {len(failed)} draft(s) failed: {failed[0].get('why')}"
    return "nothing to send"


def as_text(r) -> str:
    if not r.get("ok"):
        return "OUTREACH — " + str(r.get("error"))
    L = [f"OUTREACH — {r['head']}"]
    if "cap" in r:
        L.append(f"  cap {r['sent_today']}/{r['cap']} today · every draft waits "
                 "for your tick in the Mail room")
    for s in r.get("sent") or []:
        L.append(f"  sent    {s['company'][:34]:36} {s['to'][:30]:32} {s['subject'][:40]}")
    for q in r.get("queued") or []:
        L.append(f"  waiting {q['company'][:34]:36} {q['to'][:30]:32} {q['subject'][:40]}")
    for h in r.get("held") or []:
        L.append(f"  HELD    {h['company'][:34]:36} {'; '.join(h['why'])[:70]}")
    for f in r.get("failed") or []:
        L.append(f"  failed  {str(f.get('company'))[:34]:36} {str(f.get('why'))[:70]}")
    if r.get("not_eligible"):
        L.append(f"  -- {len(r['not_eligible'])} not eligible --")
        for x in r["not_eligible"][:6]:
            L.append(f"     {x['company'][:34]:36} {x['why']}")
    return "\n".join(L)


def preview(to_email="", lead=None, company=None) -> dict:
    """
    Write ONE mail so the owner can read it before a single real one goes out.

    He asked for exactly this: "send me a mail from the sending agent... i want
    to see how will it write the mail". Fair, and it should never have needed
    asking — he was being invited to arm a system whose output he had never
    read.

    Two things this deliberately does NOT do. It does not count against the
    daily cap or the Mail room, because it goes to him and nobody else; and
    it refuses any address that is not one he typed on the command line, so
    "preview" can never quietly become "send to a lead".
    """
    company = company or _company()
    lead = lead or {
        # A realistic lead, not a lorem-ipsum one: the mail is only judgeable
        # if the reader can picture the business it is going to.
        "company": "Al Noor Cafeteria", "email": to_email or "preview@example.ae",
        "area": "Al Quoz", "segment": "food_stall", "service": "shopfront",
        "website": "", "address": "Al Quoz Industrial Area 3, Dubai",
    }
    d = draft_for(lead, company)
    if not d.get("ok"):
        return {"ok": False, "error": d.get("error"),
                "hint": ("the mail is written by your model, so this needs a "
                         "working LLM key on the shelf. Check: python run.py "
                         "--check")}
    out = {"ok": True, "subject": d["subject"], "body": d["body"],
           "service": d["service"], "audit": d.get("audit"),
           "written_by": d.get("used"), "to": to_email or "(not sent)"}
    if not to_email:
        return out
    # Sent as a plain mail, NOT through the outreach boundary: no cap, no
    # queue, no outreach_log row. This is not outreach — it is him reading
    # his own agency's work.
    r = mail.send_plain(to_email, "[PREVIEW] " + d["subject"],
                        _preview_wrapper(d, lead))
    out["sent"] = bool(r.get("ok"))
    out["send_error"] = r.get("error", "")
    return out


def _preview_wrapper(d, lead) -> str:
    """The draft, with a short note saying what it is and who it was for."""
    return (
        "This is a PREVIEW of the mail your agency would send to a lead.\n"
        "Nobody else received it. It is not counted against your daily cap.\n\n"
        f"Written for : {lead['company']} — {lead.get('segment','')} in "
        f"{lead.get('area','')}\n"
        f"About       : {d['service']}\n"
        f"Written by  : {d.get('used') or 'your model'}\n"
        + "=" * 62 + "\n\n"
        f"Subject: {d['subject']}\n\n{d['body']}\n\n"
        + "=" * 62 + "\n"
        "If the tone is wrong, tell the agency what to change and it will be\n"
        "written differently — the wording is not fixed in the code.\n")


def main():
    ap = argparse.ArgumentParser(description="Write and send the outreach.")
    ap.add_argument("--dry-run", action="store_true", help="draft, send nothing")
    ap.add_argument("--queue", action="store_true", help="what is waiting on the owner")
    ap.add_argument("--preview", action="store_true",
                    help="write one mail and print it — sends nothing")
    ap.add_argument("--to", default="",
                    help="with --preview, also send that one mail to this "
                         "address (yours) so you can see it in an inbox")
    ap.add_argument("--limit", type=int, default=BATCH)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.preview:
        r = preview(a.to)
        if a.json:
            print(json.dumps(r, indent=2))
            return deskwork.exit_code(r)
        if not r.get("ok"):
            print("  --  " + str(r.get("error")))
            if r.get("hint"):
                print("      " + r["hint"])
            return 1
        print(f"\nWritten by {r.get('written_by') or 'your model'}, "
              f"about {r['service']}\n" + "=" * 62)
        print(f"Subject: {r['subject']}\n")
        print(r["body"])
        print("=" * 62)
        v = r.get("audit") or {}
        print(f"Auditor: {'passed' if v.get('ok', True) else 'REFUSED — ' + str(v.get('why'))}")
        if a.to:
            print(f"\nSent to {a.to}: "
                  + ("yes" if r.get("sent") else "no — " + str(r.get("send_error"))))
        else:
            print("\nNothing was sent. Add --to your@email to get it in an inbox.")
        return 0

    if a.queue:
        rows = board.pending_outreach()
        if a.json:
            print(json.dumps({"ok": True, "pending": rows}))
        else:
            print(f"{len(rows)} draft(s) waiting on you"
                  if rows else "nothing waiting on you")
            for q in rows:
                print(f"\n  #{q['id']} to {q['company']} <{q['to_email']}>")
                print(f"  Subject: {q['subject']}")
                for line in (q["body"] or "").splitlines()[:8]:
                    print("    " + line)
        return 0

    r = run(dry_run=a.dry_run, limit=a.limit)
    print(json.dumps(r) if a.json else as_text(r))
    return deskwork.exit_code(r)


if __name__ == "__main__":
    sys.exit(main())
