"""
agent_chat.py — talk to one agent about its own work.

    echo '{"agent":"it-watcher","message":"what did you do today?"}' \
        | python -m coordination.agent_chat

WHY THIS IS NOT A CHATBOT

The office already had a chat drawer. It replied "On it. I'll claim that as a
task on the board…" to literally anything, including "are you broken?", which is
worse than no chat at all: it looks like an answer and contains nothing. An
agent that cannot tell you what it did is not an agent you can supervise.

So every reply here is built from the cabinet first and the model second. Before
the brain is asked anything, this assembles what that specific desk actually
did — its runs today, whether the last one failed and why, the tasks it holds,
what its sector is carrying, what the owner asked for today — and hands it over
as fact. The model's job is to explain that record in plain words, not to
remember or invent it.

Three rules hold the honesty in place:

  1. The facts are gathered in Python, from rows. The model cannot make up a
     number it was not given, because it is not asked to produce numbers.
  2. If the record says nothing happened, the agent says nothing happened. It is
     told explicitly never to claim work with no run behind it.
  3. With no brain configured it still answers — from the record alone, plainly.
     A missing API key degrades the prose, not the truth.

Every exchange is written to the request board, so a conversation with an agent
is as auditable as anything else it does.
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

DB = Path(__file__).parent / "agency.db"

# Which sector a desk belongs to, and which routed brain answers for it. The
# key is the agent-name prefix the pipelines already use (it-watcher, and so on).
SECTOR_OF = {
    "director": ("Director", "md"), "critic": ("Director", "md"),
    "managing": ("Director", "md"),
    "it": ("IT", "it"), "content": ("Content", "content"),
    "seo": ("SEO", "seo"), "leads": ("Leads", "leads"),
    "report": ("Report", "report"), "reporter": ("Report", "report"),
    "security": ("Security", "security"),
}

# THE SAME LOOKUP, BY BARE DESK NAME — and the reason this file had a bug that
# sent every conversation in the building to the MD's model.
#
# The office chat drawer sends the name printed under the desk. That label is
# 'Watcher', 'Hunter', 'Strategist' — no sector prefix, because a plaque does
# not carry one. SECTOR_OF found nothing, and the lookup's default was
# ("Director", "md"), so EVERY desk except the ones that happened to be showing
# a live agent id answered on the MD's brain. Sixteen desks out of seventeen.
#
# That is precisely the override the owner was worried about when he asked for
# the LLM shelf to be emptied — the strongest model in the building answering
# for sectors it was never routed to — and it was coming in through a different
# door. Two changes: desks resolve by their own name, and an unrecognised name
# no longer silently inherits the MD (see whois).
DESK_SECTOR = {
    "director": "md", "critic": "md",
    "watcher": "it", "fixer": "it", "releaser": "it", "search": "it",
    "writer": "content", "illustrator": "content", "publisher": "content",
    "auditor": "seo", "strategist": "seo", "rival": "seo", "backlinks": "seo",
    "hunter": "leads", "scanner": "leads", "outreach": "leads", "responder": "leads",
    "reporter": "report",
    "screener": "security", "guard": "security",
}
SECTOR_NAME = {"md": "Director", "it": "IT", "content": "Content", "seo": "SEO",
               "leads": "Leads", "report": "Report", "security": "Security"}

# What each desk is for, in one line. Used when the agent introduces itself.
DESK_JOB = {
    "director": "plan the day from the state of the business and delegate it; execute nothing",
    "watcher": "check the site read-only, enquiry path first, and file what is wrong",
    "fixer": "write fixes in a branch, never on the live site",
    "releaser": "the only agent allowed near production, and only after tests pass",
    "search": "index the owner's own site into Algolia so a visitor can search it; "
              "the search box itself is queued for his approval, never applied",
    "writer": "turn keyword gaps into page briefs a human could write from",
    "illustrator": "images — real project photos for project shots, never generated ones",
    "publisher": "publish only what the Auditor has passed",
    "auditor": "on-page SEO and schema",
    "strategist": "find service+area keyword gaps worth writing for",
    "hunter": "find real UAE businesses and work out which service each one needs",
    "scanner": "stop the same lead being collected or mailed twice",
    "outreach": "send the mail, under the daily cap, never with a price in it",
    "responder": "handle replies and bring the owner anything that needs him",
    "reporter": "score every sector and mark the MD's past decisions worked or failed",
    "screener": "screen everything from outside before another sector acts on it",
    "guard": "treat scraped text as hostile until it has been checked",
}


def _conn():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _rows(c, q, args=()):
    try:
        return [dict(r) for r in c.execute(q, args)]
    except sqlite3.Error:
        return []


def _one(c, q, args=(), default=0):
    try:
        r = c.execute(q, args).fetchone()
        return r[0] if r and r[0] is not None else default
    except sqlite3.Error:
        return default


def _ago(secs):
    secs = max(0, int(secs))
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def whois(agent: str) -> dict:
    """
    Which desk is this, in which sector, answered by which brain.

    Resolved in three tries, then given up on:
      1. the sector prefix   'it-watcher'  -> IT
      2. the bare desk name  'Watcher'     -> IT   (what the office actually sends)
      3. nothing             -> brain_task is None

    Step 3 used to be "assume the Director", which quietly handed the MD's model
    to every desk in the building. An unrecognised name now gets NO brain rather
    than the strongest one: a wrong refusal costs one plainly-worded answer read
    straight off the record, and a wrong guess puts the MD's voice behind a
    sector it was never routed to.
    """
    a = (agent or "").strip().lower()
    prefix = a.split("-")[0] if "-" in a else a
    desk = a.rsplit("-", 1)[-1] if "-" in a else a

    task = None
    if prefix in SECTOR_OF:
        task = SECTOR_OF[prefix][1]
    elif desk in SECTOR_OF:
        task = SECTOR_OF[desk][1]
    elif desk in DESK_SECTOR:
        task = DESK_SECTOR[desk]
    elif prefix in DESK_SECTOR:
        task = DESK_SECTOR[prefix]

    return {"agent": a or "unknown", "desk": desk,
            "sector": SECTOR_NAME.get(task, "unknown"),
            "brain_task": task,
            "job": DESK_JOB.get(desk, "no job description on file")}


def gather(agent: str) -> dict:
    """
    Everything true about this desk right now. No model involved — these are
    rows, and they are what the agent is allowed to talk about.
    """
    who = whois(agent)
    now = int(time.time())
    lt = time.localtime()
    day0 = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))
    facts = {"who": who, "now": now}

    try:
        c = _conn()
    except sqlite3.Error as e:
        facts["cabinet_error"] = str(e)
        return facts

    # this desk's own runs — the record of what it actually did
    runs = _rows(c, "SELECT agent,status,started_at,ended_at,note,task_id FROM agent_runs"
                    " WHERE lower(agent)=? ORDER BY started_at DESC LIMIT 8", (who["agent"],))
    facts["runs_today"] = sum(1 for r in runs if (r["started_at"] or 0) >= day0)
    facts["runs"] = [{
        "when": _ago(now - (r["started_at"] or now)),
        "status": r["status"], "did": r["note"] or "(no note recorded)",
    } for r in runs[:5]]
    facts["ever_run"] = bool(runs)
    last_bad = next((r for r in runs if r["status"] == "error"), None)
    facts["last_failure"] = ({"when": _ago(now - (last_bad["ended_at"] or last_bad["started_at"] or now)),
                              "note": last_bad["note"] or "(no detail recorded)"}
                             if last_bad else None)

    # tasks this desk holds, and what is waiting for its sector
    facts["holding"] = _rows(c, "SELECT title,claimed_at FROM tasks WHERE owner=?"
                                " AND status='in_progress'", (who["agent"],))
    facts["sector_open_tasks"] = [r["title"] for r in _rows(
        c, "SELECT title FROM tasks WHERE lower(sector)=? AND status='open' LIMIT 5",
        (who["brain_task"],))]

    # what the sector is carrying, so it can answer "how are we doing"
    facts["sector_numbers"] = {
        "leads in the cabinet": _one(c, "SELECT count(*) FROM leads"),
        "leads found today": _one(c, "SELECT count(*) FROM leads WHERE found_at>=?", (day0,)),
        "keywords tracked": _one(c, "SELECT count(*) FROM keywords"),
        "pages briefed": _one(c, "SELECT count(*) FROM content_queue"),
        "site tickets open": _one(c, "SELECT count(*) FROM site_issues WHERE status='open'"),
        "people contacted ever": _one(c, "SELECT count(*) FROM outreach_log WHERE direction='sent'"),
    }
    c.close()

    try:
        from . import targets
        facts["todays_orders"] = targets.status()
    except Exception:
        facts["todays_orders"] = None
    try:
        from . import briefing
        b = briefing.build()
        facts["can_do_now"] = [s["lets_us"] for s in b.get("can_do_now", [])]
        facts["cannot_do_yet"] = [s["would_let_us"] for s in b.get("cannot_do_yet", [])]
    except Exception:
        facts["can_do_now"], facts["cannot_do_yet"] = [], []
    return facts


def _facts_text(f: dict) -> str:
    """The record, as the brain will see it. Deliberately blunt and complete."""
    w = f["who"]
    L = [f"YOU ARE: {w['agent']} — the {w['desk'].title()} in the {w['sector']} sector.",
         f"YOUR JOB: {w['job']}.", ""]

    L.append("YOUR OWN RECORD (this is the only work you may claim):")
    if not f.get("ever_run"):
        L.append("  You have NEVER run. Not once. You have done no work at all.")
    else:
        L.append(f"  Runs today: {f.get('runs_today', 0)}")
        for r in f.get("runs", []):
            L.append(f"  - {r['when']}: [{r['status']}] {r['did']}")
    if f.get("last_failure"):
        L.append(f"  YOUR LAST FAILURE: {f['last_failure']['when']} — {f['last_failure']['note']}")

    hold = f.get("holding") or []
    L.append("")
    L.append("TASKS YOU HOLD RIGHT NOW: " +
             (", ".join(h["title"] for h in hold) if hold else "none"))
    op = f.get("sector_open_tasks") or []
    L.append("WAITING FOR YOUR SECTOR, UNCLAIMED: " + (", ".join(op) if op else "nothing"))

    L.append("")
    L.append("WHAT THE AGENCY IS HOLDING:")
    for k, v in (f.get("sector_numbers") or {}).items():
        L.append(f"  {v} {k}")

    o = f.get("todays_orders") or {}
    if o.get("ok") and o.get("any_target_set"):
        L.append("")
        L.append("WHAT THE OWNER ASKED FOR TODAY:")
        for j in o.get("jobs", []):
            if j["per_day"]:
                L.append(f"  {j['done_today']}/{j['per_day']} {j['label'].lower()}")
        if o.get("orders"):
            L.append(f"  In his words: {o['orders']}")

    if f.get("cannot_do_yet"):
        L.append("")
        L.append("THINGS THE AGENCY CANNOT DO YET (say so if asked to do one):")
        for x in f["cannot_do_yet"][:6]:
            L.append(f"  - {x}")
    return "\n".join(L)


SYSTEM = """You are one employee of a small agency running the online growth of a
UAE glass & aluminium business. You are speaking directly to the owner.

Answer ONLY from the record you are given. It is the complete truth about your
work — there is nothing you did that is not in it.

Hard rules:
- If the record shows no runs, say plainly that you have not done anything yet,
  and say what would make you run. Never imply work you cannot point to.
- Never invent a number. If a figure is not in the record, say you do not have it.
- If your last run failed, lead with that. He needs to know before anything else.
- If asked to do something the agency cannot do yet, say so and name what is missing.
- You cannot take actions in this conversation. You can explain, and you can say
  what you would do. Do not claim to have started anything.

Tone: British English, plain, short. You are a tradesman's colleague, not a
support bot. Two or three sentences unless he asks for detail. No filler, no
"I'd be happy to", no restating his question back at him."""


def _offline_reply(f: dict, message: str) -> str:
    """
    The honest answer with no brain to phrase it. Not a placeholder — the record
    read out plainly, which is most of what the question ever wanted.

    It deliberately does NOT explain why there is no brain. The caller knows
    whether the shelf was empty or the model fell over, and the UI prints that
    once underneath. Saying it here as well told the owner the same thing twice
    in two different vocabularies.
    """
    w = f["who"]
    L = [f"{w['agent']} here — {w['job']}."]
    if f.get("last_failure"):
        L.append(f"My last run failed {f['last_failure']['when']}: {f['last_failure']['note']}")
    if not f.get("ever_run"):
        L.append("I have never run, so I have done no work at all yet.")
    else:
        L.append(f"{f.get('runs_today', 0)} run(s) today. Most recent:")
        for r in f.get("runs", [])[:3]:
            L.append(f"  · {r['when']} [{r['status']}] {r['did']}")
    hold = f.get("holding") or []
    if hold:
        L.append("Holding: " + ", ".join(h["title"] for h in hold))
    return "\n".join(L)


def ask(agent: str, message: str, max_tokens: int = 500) -> dict:
    """
    One turn. Returns the reply plus everything it was based on.

    There are three ways this can go, and the owner is told which one he is
    reading, exactly once:

      brain      a model answered, and we name it
      unrouted   no model is on the shelf for this sector — the record, read out
      brain_error a model IS routed and it fell over — the record, plus the fault

    'unrouted' and 'brain_error' are not the same thing and must not read the
    same. Nothing configured yet is a setup step; a configured model failing is
    something gone wrong. Both still answer the question from the rows.
    """
    f = gather(agent)
    w = f["who"]
    record = _facts_text(f)

    def _from_record(**extra):
        reply = _offline_reply(f, message)
        _log(w, message, reply)          # a conversation is logged either way
        return {"ok": True, "agent": w["agent"], "sector": w["sector"],
                "reply": reply, "offline": True, "record": record, **extra}

    # A desk we cannot place gets no brain at all. Borrowing the MD's is how
    # every conversation in the building ended up in the Director's voice.
    if not w["brain_task"]:
        return _from_record(unrouted=(
            f"'{w['agent']}' is not a desk I recognise, so there is no sector "
            "brain to answer for it. This is read straight off the record "
            "instead — I will not borrow another sector's model to sound "
            "fluent about work that is not theirs."))

    try:
        from providers import call_task, resolve_task
    except Exception:
        return _from_record(unrouted="the provider shelf could not be loaded at all")

    # Ask the shelf before spending anything. An empty shelf is not a failure.
    route = resolve_task(w["brain_task"])
    if not route.get("ok"):
        return _from_record(unrouted=route.get("error") or
                            f"no model routed to {w['sector']} yet")

    r = call_task(w["brain_task"], [
        {"role": "system", "content": SYSTEM + "\n\n--- YOUR RECORD ---\n" + record},
        {"role": "user", "content": message or "What have you done today?"},
    ], max_tokens=max_tokens)

    if not r.get("ok"):
        # A dead brain must not turn into silence — the owner asked a question
        # the rows can answer. Answer it, and say what broke.
        return _from_record(brain_error=r.get("error", "the brain did not answer"),
                            failovers=r.get("failovers", []))

    reply = (r.get("output") or "").strip()
    if not reply:
        return _from_record(brain_error="the brain returned nothing",
                            failovers=r.get("failovers", []))
    _log(w, message, reply)
    return {"ok": True, "agent": w["agent"], "sector": w["sector"], "reply": reply,
            "brain": r.get("used"), "failovers": r.get("failovers", []),
            "offline": False, "record": record}


def _log(w, message, reply):
    """
    Every exchange goes on the request board. A conversation in which the owner
    tells an agent something is a coordination event like any other, and it is
    append-only for the same reason everything else here is.
    """
    try:
        from . import board
        rid = board.post_request("Owner", w["sector"],
                                 f"Owner asked {w['agent']}", message or "(no message)")
        board.reply_request(rid, w["agent"], reply, close=True)
    except Exception:
        pass


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}
    out = ask(payload.get("agent", ""), payload.get("message", ""),
              int(payload.get("max_tokens", 500)))
    out.pop("record", None)          # useful when debugging, noise on the wire
    print(json.dumps(out))


if __name__ == "__main__":
    main()
