"""
triage.py — which request gets dealt with first, and why.

THE QUESTION THIS ANSWERS

The owner asked: if SEO sends the MD two requests, IT sends two more, and
something else arrives at the same time, how does the MD decide what to do
first? And what stops something genuinely urgent sitting behind something
trivial that merely arrived earlier?

The honest answer before this file existed was: it did not decide. The queue was
`ORDER BY created_at` — pure arrival order — and the MD did not read its inbox
at all. Twenty-six requests had been sitting in it for fifty-three hours,
including every escalation the Overseer had raised about a stalled agent.

TWO FAILURES, PULLING OPPOSITE WAYS

  URGENT WAITS      first-come-first-served puts a dead enquiry form behind a
                    question about a keyword, because the keyword asked first.
  TRIVIAL STARVES   pure priority is worse: a low-priority request never rises
                    and is never answered, and "we will get to it" becomes a lie
                    the system tells for ever.

You need both fixed at once, so the score has two halves: WEIGHT, which is what
the thing is worth, and AGE, which climbs the whole time it waits. A low-weight
request that has waited long enough WILL overtake a high-weight one that just
arrived. That is deliberate. It is the only thing that makes a queue honest.

WHO DECIDES THE WEIGHT — NOT THE ASKER

Every asker believes its own request is urgent; a priority field the sender
fills in becomes a field every sender sets to "high". So weight is measured by
the RECEIVER from things it can check for itself:

  is a sector actually blocked        the asker said it cannot continue. This is
                                      the one thing the asker genuinely knows
                                      better than anyone, so it is taken — but
                                      it is one factor, not the whole score.
  is a person waiting                 a real customer or lead is on the other
                                      end. Nothing in this agency outranks that.
  is the enquiry path broken          money is leaving by the hour.
  is it an Overseer escalation        the Overseer only escalates on evidence,
                                      and it has already been routed once and
                                      ignored.
  how many times has it been asked    the same question five times unanswered is
                                      a blockage, whatever the subject line says.
  how long has it waited              see above.

Every one of those is a row somebody can go and look at, and score() returns the
reasons alongside the number so the ordering can be argued with.

LOAD

A queue of twenty-six is not a reason to answer twenty-six. queue() takes a
limit: the top N to deal with now, and everything else stays queued WITH its
position, so "not yet" is visible rather than silent. A sector that is behind
should look behind.
"""
import json
import sqlite3
import time

try:
    from . import board
except ImportError:  # pragma: no cover
    import board

# ---- what a thing is worth, before it has waited at all ---------------------
# Spread out deliberately: one step of weight should not be overtaken by an
# hour of waiting, or ageing would be the only thing that mattered.
W_PERSON_WAITING = 100   # a customer or lead is on the other end
W_ENQUIRY_PATH   = 90    # the way people reach the business is broken
W_BLOCKED        = 70    # the asking sector cannot continue without an answer
W_ESCALATION     = 55    # the Overseer raised it, on evidence, after routing
W_REPEAT         = 8     # per extra time the same question has been asked
W_NORMAL         = 30    # an ordinary ask
W_FYI            = 12    # useful to know, nobody is waiting

# ---- ageing -----------------------------------------------------------------
# One point per hour, so an ordinary request (30) overtakes an escalation (55)
# after about a day of being ignored, and overtakes a blocked one (70) after
# nearly two. Capped so a forgotten request cannot climb above a person waiting
# — a three-week-old question about a sitemap must never outrank a customer.
AGE_PER_HOUR = 1.0
AGE_CAP = 60

# How many a sector should try to clear in one pass. More than this and it is
# not triaging, it is thrashing.
DEFAULT_LIMIT = 5


def _conn():
    c = sqlite3.connect(board.DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _rows(c, q, args=()):
    try:
        return [dict(r) for r in c.execute(q, args)]
    except Exception:
        return []


def _ago(secs):
    secs = max(0, int(secs))
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def score(req, now=None, repeats=1, person_waiting=False, enquiry_broken=False):
    """
    (points, reasons[]) for one request. Pure — every input is passed in, so it
    is trivial to test and impossible for it to reach out and surprise anyone.
    """
    now = int(now or time.time())
    why = []
    weight = W_NORMAL
    label = "an ordinary ask"

    if person_waiting:
        weight, label = W_PERSON_WAITING, "a person is waiting for an answer"
    elif enquiry_broken:
        weight, label = W_ENQUIRY_PATH, "the enquiry path is broken — money is leaving"
    elif req.get("blocking"):
        weight, label = W_BLOCKED, f"{req.get('from_sector')} says it cannot continue"
    elif (req.get("from_sector") or "").strip().lower() == "overseer":
        weight, label = W_ESCALATION, "the Overseer escalated it after routing failed"
    elif (req.get("urgency") or "").strip().lower() in ("fyi", "low"):
        weight, label = W_FYI, "marked as background, nobody is waiting"
    why.append({"points": weight, "label": label})

    if repeats > 1:
        pts = W_REPEAT * (repeats - 1)
        weight += pts
        why.append({"points": pts,
                    "label": f"asked {repeats} times and still unanswered"})

    waited = max(0, now - int(req.get("created_at") or now))
    age = min(AGE_CAP, (waited / 3600.0) * AGE_PER_HOUR)
    if age >= 1:
        weight += age
        why.append({"points": round(age, 1),
                    "label": f"waiting {_ago(waited)} — nothing is left to starve"})

    return round(weight, 1), why


# Which tables a request may point at, and what makes that row urgent. The
# request names a row; this reads it. Nothing is inferred from the subject line.
#
# The first version DID guess from the subject — it looked for "lead", "reply",
# "form" and so on — and immediately ranked "Escalating: leads-outreach claimed
# ..." as a customer waiting for an answer, because the agent is called
# leads-outreach. A word in a sentence is not evidence. A row is.
ABOUT_TABLES = {
    "outreach_log": ("SELECT 1 FROM outreach_log WHERE id=? AND direction='reply'"
                     " AND needs_owner=1",
                     "person", "a real reply is sitting unanswered (outreach_log:{id})"),
    "site_issues": ("SELECT 1 FROM site_issues WHERE id=? AND status='open'"
                    " AND severity='critical'",
                    "enquiry", "a critical site fault is open (site_issues:{id})"),
    "leads": ("SELECT 1 FROM leads WHERE id=? AND stage='replied'",
              "person", "a lead has replied and is waiting (leads:{id})"),
    # A fault the Overseer escalated. Only counts while it is still open and
    # still blocking — an escalation about something already fixed is history.
    "faults": ("SELECT 1 FROM faults WHERE id=? AND status!='resolved'"
               " AND severity='blocking'",
               "enquiry", "a blocking fault is still open (faults:{id})"),
}


def _about(c, ref):
    """
    Read the row a request says it is about. Returns (kind, note) or (None, "").

    A reference to a row that does not exist, or no longer meets the condition —
    the reply was answered, the fault was fixed — earns nothing. An urgency that
    was true yesterday is not urgency today.
    """
    ref = (ref or "").strip()
    if ":" not in ref:
        return None, ""
    table, _, rid = ref.partition(":")
    spec = ABOUT_TABLES.get(table.strip().lower())
    if not spec or not rid.strip().isdigit():
        return None, ""
    sql, kind, note = spec
    try:
        if c.execute(sql, (int(rid),)).fetchone():
            return kind, note.format(id=rid)
    except Exception:
        pass
    return None, ""


def queue(sector, limit=DEFAULT_LIMIT, now=None) -> dict:
    """
    This sector's inbox, worst first, with the reasoning and the rest still
    visible.

    Returns {sector, now, next, waiting, total, head}. `next` is what to deal
    with in this pass; `waiting` is everything else, in order, each with the
    position it is in — a queue that hides its own length is how twenty-six
    requests went unnoticed for two days.
    """
    now = int(now or time.time())
    c = _conn()
    try:
        board._migrate(c)
        rows = _rows(c, "SELECT * FROM requests WHERE to_sector=?"
                        " AND status IN ('open','claimed') ORDER BY created_at",
                     (sector,))

        # The same question asked repeatedly is one blockage, and counts as one
        # — otherwise five copies of a trivial ask crowd out one real problem.
        seen = {}
        for r in rows:
            k = ((r.get("from_sector") or ""), (r.get("subject") or "")[:80])
            seen.setdefault(k, []).append(r)

        scored = []
        for k, group in seen.items():
            first = min(group, key=lambda r: r.get("created_at") or now)
            # The request names the row it is about; that row is read. A
            # request that names nothing gets neither boost, which is right —
            # "this might be about a customer" is not evidence that it is.
            kind, note = _about(c, first.get("about"))
            pts, why = score(first, now, repeats=len(group),
                             person_waiting=(kind == "person"),
                             enquiry_broken=(kind == "enquiry"))
            if note:
                why[0]["label"] += f" — {note}"
            scored.append({
                "id": first["id"], "from": first.get("from_sector"),
                "to": first.get("to_sector"), "subject": first.get("subject"),
                "status": first.get("status"),
                # The row this is about, carried through — the MD routes from it
                # rather than reading a sector name out of the subject line.
                "about": first.get("about") or "",
                "blocking": bool(first.get("blocking")),
                "created_at": first.get("created_at"),
                "waited": _ago(now - int(first.get("created_at") or now)),
                "copies": len(group), "ids": [r["id"] for r in group],
                "points": pts, "why": why,
            })
        # TWO THINGS THAT SCORE THE SAME.
        #
        # The owner asked what happens when two requests are equally urgent, and
        # "whatever sort() happens to do" is not an answer — an order nobody can
        # predict is an order nobody can check. Three tie-breaks, in order, each
        # one a fact rather than a preference:
        #
        #   1. BLOCKED FIRST. Two things matter the same, but one has a sector
        #      standing still behind it and the other does not. Clearing the
        #      blocked one puts a whole sector back to work; clearing the other
        #      puts one request to bed.
        #   2. THEN THE OLDER. It has been waited on longer, and between two
        #      equal claims the one that has already paid more is served.
        #   3. THEN THE ID. Never a coin-flip: the same inbox must produce the
        #      same order twice, or nobody can reproduce a complaint about it.
        #
        # Note this makes ties rare rather than common — the ageing bonus is
        # continuous, so two requests genuinely level are two that arrived in
        # the same minute with the same weight.
        scored.sort(key=lambda r: (-r["points"],
                                   0 if r.get("blocking") else 1,
                                   r["created_at"] or 0,
                                   r["id"]))
        for i, r in enumerate(scored):
            r["position"] = i + 1

        nxt, rest = scored[:max(0, int(limit))], scored[max(0, int(limit)):]
        head = (f"nothing waiting" if not scored else
                f"{len(scored)} waiting"
                + (f", dealing with {len(nxt)} this pass" if rest else ""))
        return {"ok": True, "sector": sector, "now": now, "next": nxt,
                "waiting": rest, "total": len(scored), "head": head}
    finally:
        c.close()


def as_text(sector, limit=DEFAULT_LIMIT) -> str:
    q = queue(sector, limit)
    L = [f"{sector.upper()} INBOX — {q['head']}"]
    for r in q["next"]:
        L.append(f"  {r['position']:>2}. [{r['points']:>5}] {r['from']:>9} → "
                 f"{r['subject'][:56]}  ({r['waited']} waited"
                 + (f", ×{r['copies']}" if r["copies"] > 1 else "") + ")")
        for w in r["why"]:
            L.append(f"          +{w['points']:<5} {w['label']}")
    if q["waiting"]:
        L.append(f"  -- {len(q['waiting'])} more, not this pass --")
        for r in q["waiting"][:8]:
            L.append(f"  {r['position']:>2}. [{r['points']:>5}] {r['from']:>9} → "
                     f"{r['subject'][:56]}  ({r['waited']} waited)")
    if not q["total"]:
        L.append("  (nothing — no sector is waiting on this one)")
    return "\n".join(L)


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Which request a sector should deal with first, and why.")
    ap.add_argument("sector", nargs="?", default="Director")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.json:
        print(json.dumps(queue(a.sector, a.limit)))
    else:
        print(as_text(a.sector, a.limit))


if __name__ == "__main__":
    main()
