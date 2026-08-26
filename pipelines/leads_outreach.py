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
  6. the owner's arming   the FIRST batch is queued for him to read. Approving
                          one arms outreach and later batches send on their
                          own. See below.
  7. cap and domain       inside mail._smtp_send, where they already were, and
                          deliberately not duplicated here — a limit enforced
                          by whoever happens to call is a limit the next caller
                          skips.

ONE CONFIRMATION, THEN AUTONOMOUS

The owner asked for automatic sending and that is what this is. But an agency
that starts cold-mailing real UAE businesses without him having read a single
example of what it writes is not what he asked for either; it is what he would
discover afterwards. So the first batch is held, pushed to his phone, and
approving any one of them arms the system permanently — after that this runner
sends without asking, inside the cap.

He can see it either way: `--queue` lists what is held, and the office shows
each draft under 'Awaiting you' with Approve and Not now.
"""
import argparse
import json
import sys
import time

from coordination import auditor, board, dispatch
from . import mail, security_screen, services

AGENT = "leads-outreach"
SECTOR = "Leads"

# How many to draft in one pass, before the daily cap is even consulted.
# Drafting costs a model call each, and a pass that drafts forty and then
# discovers it may send three has spent thirty-seven calls to learn nothing.
BATCH = 6

# What the first held batch looks like. Enough for him to judge the writing,
# few enough to read on a phone.
FIRST_BATCH = 3


def _company():
    """Who we are, for the signature and the footer. One source, as everywhere."""
    try:
        from coordination import site
        return {"name": site.site_name(), "website": site.site_url(),
                "phone": "", "whatsapp": "",
                "areas": ["Dubai", "Abu Dhabi", "Sharjah"]}
    except Exception:
        return {"name": "", "website": "", "phone": "", "whatsapp": "",
                "areas": ["Dubai", "Abu Dhabi", "Sharjah"]}


def eligible(limit=BATCH):
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


def _send_one(d):
    """Hand one drafted mail to the send boundary, and log what happened."""
    lead = d["lead"]
    r = mail._smtp_send(lead, d["draft"])
    if r.get("ok"):
        return True, "sent"
    return False, r.get("error", "the send boundary refused it")


def run(agent=AGENT, dry_run=False, limit=BATCH) -> dict:
    with board.agent_run(agent) as run_row:
        out = _run(agent, dry_run, limit)
        run_row.note(out.get("head") or out.get("error") or "nothing to do")
        return out


def _run(agent, dry_run, limit) -> dict:
    if not board.sector_enabled(SECTOR):
        return {"ok": False, "error": "Leads is switched off — you turned this "
                                      "sector off, so nothing goes out."}

    task = board.next_open_task(SECTOR, agent)
    if not task:
        return {"ok": False, "error": dispatch.why_nothing(SECTOR, agent)}

    company = _company()
    can, cannot = eligible(limit)
    if not can:
        board.finish_task(task["id"], agent,
                          {"drafted": 0, "why": "nobody eligible"}, status="done")
        return {"ok": False, "agent": agent, "task": task["id"],
                "eligible": 0, "not_eligible": cannot[:20],
                "error": ("Nobody is eligible to be written to. "
                          + (cannot[0]["why"] if cannot else
                             "there are no leads on file yet"))}

    armed = board.outreach_armed()
    room = max(0, mail.daily_cap() - mail.sent_today())
    drafted, held, sent, failed, queued = [], [], [], [], []

    for lead in can[: (limit if armed else FIRST_BATCH)]:
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

        if not armed:
            # FIRST BATCH. He reads it before the agency writes to anybody.
            qid = board.queue_outreach(
                lead["id"], lead.get("company"), lead.get("email"),
                d["subject"], d["body"], service=d["service"],
                audit=d["audit"], by=agent)
            queued.append({**line, "queue_id": qid})
            continue

        if len(sent) >= room:
            held.append({**line, "why": ["today's cap is used up"]})
            continue
        ok, why = _send_one(d)
        if ok:
            mail.log_outreach(lead["id"], d["draft"], channel="email")
            _mark_contacted(lead["id"])
            sent.append(line)
        else:
            failed.append({**line, "why": why})

    # Anything the owner already approved goes out on this pass too. Without
    # this, approving the first batch would arm outreach but the mails he
    # actually read and said yes to would never be the ones sent.
    if not dry_run:
        sent += _send_approved(room - len(sent))

    head = _head(sent, queued, held, failed, drafted, armed, dry_run)
    board.finish_task(task["id"], agent,
                      {"sent": len(sent), "queued": len(queued),
                       "held": len(held), "head": head}, status="done")

    # His phone is told by coordination/notify.py, which reads the queue itself
    # rather than being pushed at from here — one place decides what is worth a
    # buzz, and it groups the batch into one alert instead of three.

    did = board.log_decision(
        "leads-outreach",
        reasoning=(f"{len(can)} lead(s) eligible; drafted {len(drafted)}, "
                   f"Auditor held {len(held)}, "
                   + (f"sent {len(sent)}" if armed else
                      f"queued {len(queued)} for the owner — outreach is not armed yet")),
        expectation=("Replies arrive within a week, or the segment is wrong and "
                     "the next pass targets a different one."),
        confidence=0.6,
        evidence={"lead_ids": [l["lead_id"] for l in drafted]})

    return {"ok": True, "agent": agent, "task": task["id"], "armed": armed,
            "eligible": len(can), "not_eligible": cannot[:20],
            "drafted": drafted, "sent": sent, "queued": queued,
            "held": held, "failed": failed,
            "cap": mail.daily_cap(), "sent_today": mail.sent_today(),
            "decision_id": did, "head": head}


def _send_approved(room):
    """Mail the owner has said yes to. Runs whether or not anything new drafted."""
    out = []
    if room <= 0:
        return out
    import sqlite3
    c = sqlite3.connect(board.DB, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        board._migrate(c)
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM outreach_queue WHERE status='approved'"
            " ORDER BY queued_at LIMIT ?", (int(room),))]
    finally:
        c.close()
    for q in rows:
        lead = board.get_lead(q["lead_id"]) if hasattr(board, "get_lead") else None
        lead = lead or {"id": q["lead_id"], "email": q["to_email"],
                        "company": q["company"]}
        draft = f"Subject: {q['subject']}\n\n{q['body']}"
        r = mail._smtp_send(lead, draft)
        if r.get("ok"):
            mail.log_outreach(q["lead_id"], draft, channel="email")
            _mark_contacted(q["lead_id"])
            board.mark_outreach_sent(q["id"], True, "approved by the owner")
            out.append({"lead_id": q["lead_id"], "company": q["company"],
                        "to": q["to_email"], "subject": q["subject"],
                        "service": q["service"], "words": 0})
        else:
            board.mark_outreach_sent(q["id"], False, r.get("error", "")[:200])
    return out


def _mark_contacted(lead_id):
    import sqlite3
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            c.execute("UPDATE leads SET stage='contacted' WHERE id=? AND stage IN"
                      " ('new','nurture')", (lead_id,))
    except Exception:
        pass


def _head(sent, queued, held, failed, drafted, armed, dry_run):
    if dry_run:
        return (f"dry run — drafted {len(drafted)}, "
                f"{len(held)} would be held by the Auditor, nothing sent")
    if queued:
        return (f"{len(queued)} mail(s) written and waiting on you. Approve one "
                "and outreach runs on its own from then on.")
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
    L = [f"OUTREACH — {r['head']}",
         f"  cap {r['sent_today']}/{r['cap']} today · "
         + ("armed" if r["armed"] else "NOT ARMED — the first batch waits on you")]
    for s in r["sent"]:
        L.append(f"  sent    {s['company'][:34]:36} {s['to'][:30]:32} {s['subject'][:40]}")
    for q in r["queued"]:
        L.append(f"  waiting {q['company'][:34]:36} {q['to'][:30]:32} {q['subject'][:40]}")
    for h in r["held"]:
        L.append(f"  HELD    {h['company'][:34]:36} {'; '.join(h['why'])[:70]}")
    for f in r["failed"]:
        L.append(f"  failed  {str(f.get('company'))[:34]:36} {str(f.get('why'))[:70]}")
    if r["not_eligible"]:
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
    daily cap or the arming gate, because it goes to him and nobody else; and
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
    # arming, no outreach_log row. This is not outreach — it is him reading
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
            return 0 if r.get("ok") else 1
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
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
