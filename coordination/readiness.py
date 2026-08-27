"""
readiness.py — can this sector actually work right now?

WHAT THE OWNER ASKED FOR

"when each sector is having apis and working then there should be a sign showing
that the sector has working apis."

Fair, and it was missing. The office floor showed what each sector HAD DONE
(bulbs, task feed) but nothing about whether it COULD do anything. A sector with
no brain and no key looks identical to one that simply had a quiet morning: both
dark, both silent. That is the worst kind of dashboard — it hides the difference
between "nothing needed doing" and "nothing can be done".

FOUR STATES, AND WHY NOT TWO

A green/red light would be a lie in both directions, so this carries four:

  ready       a brain answers, every key it needs is on the shelf, and the last
              check passed. Press Run and this sector does real work.
  unchecked   everything is configured but nothing has been tested since it was
              saved. A saved key is not a working key. This is NOT green, because
              the whole point of the light is that green means proven.
  thin        it will run, but one thing dying stops it — one brain and no
              fallback, or one API on a shelf it depends on. Runs today, silently
              stops the night that API rate-limits.
  blocked     it cannot run. No brain, or a shelf it must have is empty.

CONFIGURED IS NOT WORKING. That distinction is the entire module. Reading the
config is free and instant, so this runs on every dashboard poll; proving the
APIs costs money and time, so that only happens on a preflight and the result is
remembered here.

WHAT EACH SECTOR NEEDS, AND WHY

Not invented — read off what the pipelines actually borrow and read:

  seo      seo_data   OPTIONAL now, and that is a deliberate change. It used to
                      be required because seo_gap.py wanted volumes, and it made
                      the whole SEO room go dark for an owner who could not
                      justify a DataForSEO subscription. pipelines/seo_intel.py
                      does the same job from Search Console, autocomplete, his
                      own enquiries and rival sitemaps — all free — so a paid key
                      now buys volume figures and rank tracking on top of a
                      sector that already works. A note, not a blackout.
           crawler    OPTIONAL, and now actually used: pipelines/fetcher.py reads
                      a key off it to render JavaScript pages and to reach rivals
                      that block plain Python. Both callers work without it, so a
                      missing key is a note rather than a blackout.
  leads    maps       pipelines/leads_hunt.py needs a Places key or it finds nobody
           email      OPTIONAL. pipelines/mail.py borrows this shelf for its LOCK
                      — that is what serialises outreach and makes the daily cap
                      enforceable — but the mail ROUTE is SMTP, in .env. Marking
                      the shelf required meant an owner who had configured SMTP,
                      tested it and seen it log in was still told outreach had no
                      way to send. The route is checked below, in ENV_NEEDS.
  content  stock_images — Pexels and its fallback, the only picture source now
           that image generation is gone. Optional: a brief still writes without
           a picture, so a missing key is a note, not a blackout.
  it       SITE_URL. There is nothing to check the health of without one.
  report   somewhere to send the digest, or the day's score is written to a
           table nobody opens.

MD AND SECURITY WANT TWO BRAINS, NOT ONE

Both put hard calls to two models and treat disagreement as the signal (see
providers/routing.deliberate and coordination/clearance.py). With one model
configured they still run, but the cross-check silently stops happening — so one
brain reads as 'thin' for those two, and only for those two.
"""
import json
import sqlite3
import time

try:
    from . import board
except ImportError:  # pragma: no cover
    import board

# Where the cabinet lives is board's to say — see the note in state.py.
def _db():
    return board.DB

READY, UNCHECKED, THIN, BLOCKED = "ready", "unchecked", "thin", "blocked"
_RANK = {BLOCKED: 0, THIN: 1, UNCHECKED: 2, READY: 3}

# sector key -> (routing task, office room key, label)
SECTORS = [
    ("md", "Director", "Director's Office"),
    ("it", "IT", "IT & Site Health"),
    ("content", "Content", "Content & Social"),
    ("seo", "SEO", "SEO & Rankings"),
    ("leads", "Leads", "Leads & Outreach"),
    ("report", "Report", "Reports"),
    ("security", "Security", "Security"),
]

# how many brains the sector's own design assumes
WANTS_TWO = {"md", "security"}

# shelves each sector borrows, and whether it can work without them
SHELVES = {
    # crawler is now genuinely read — pipelines/fetcher.py takes a key off this
    # shelf and uses it to render JavaScript sites and to reach rivals that
    # block plain Python. It stays OPTIONAL because both callers work without
    # it: the site indexer reads static pages fine, and the rival watch reads
    # sitemaps fine. A key buys the pages they currently cannot see, which is a
    # real gain and not a precondition — so this is a note on the sector light,
    # never a blackout.
    "seo": {"seo_data": False, "crawler": False},
    # The email SHELF is not required, and demanding it was wrong. SMTP is the
    # mail route and it lives in .env — the owner set it up, tested it, saw
    # "logged in successfully", and Leads still told him outreach had no way to
    # send because a shelf he does not need was empty. The shelf exists for its
    # LOCK (turn-taking and the outreach cap) and for an optional second
    # provider, and readiness reads .env for the route it actually uses.
    # maps is OPTIONAL now, and that is a real change rather than a softening.
    # It was required because search_places was the only way the Hunter found
    # anybody — no key, no leads, and the room went dark honestly. There is now
    # a second collector, pipelines/leads_osm.py, which finds UAE businesses on
    # OpenStreetMap with no key and no charge. It is thinner than Google in the
    # UAE and does not pretend otherwise; both collectors feed the same dedup
    # gate, so a Places key still buys real coverage. What it no longer buys is
    # the difference between a working sector and a dark one.
    "leads": {"maps": False, "email": False},
    # Pexels (and whatever second stock provider sits under it) is Content's
    # only picture source now — image generation was dropped. Still optional at
    # SECTOR level, and that is deliberate: content_briefs.py writes briefs and
    # never touches an image, so a missing stock key must not black out a room
    # that can still do its day's work. It shows as a note, not a blocker.
    "content": {"stock_images": False},
}

# .env keys a sector cannot work without, with the plain reason
ENV_NEEDS = {
    "it": [("SITE_URL", "there is no site to check the health of")],
    "leads": [("SMTP_HOST", "outreach has no way to send")],
}

# a soft need: the sector runs, but the output goes nowhere useful
ENV_WANTS = {
    "report": [("NTFY_TOPIC", "the daily digest is written to a table nobody opens")],
    "leads": [("OUTREACH_DOMAIN", "outreach stays off until a sending domain is set")],
    # The AEO/GEO audit reads our OWN pages, so without an address it has
    # nothing to audit. SEO still does keyword gaps, rivals and the link list
    # without it, so this is a note on the light rather than a blackout.
    "seo": [("SITE_URL", "the AEO/GEO audit has no pages of ours to read")],
}


def _read_env() -> dict:
    try:
        from providers import integrations
        return integrations.read_env()
    except Exception:
        return {}


def _brains(task):
    """The sector's model chain, and how much of it has been proven to answer."""
    try:
        from providers import routing, store
    except Exception as e:
        return {"n": 0, "verified": 0, "names": [], "error": str(e)}
    try:
        r = routing.resolve_task(task)
    except Exception as e:
        return {"n": 0, "verified": 0, "names": [], "error": str(e)}
    if not r.get("ok"):
        return {"n": 0, "verified": 0, "names": [], "error": r.get("error")}

    names, verified, tested = [], 0, 0
    for cand in r.get("candidates", []):
        names.append(f'{cand.get("credential_name", "?")} · {cand.get("api_model_id", "?")}')
        try:
            _cred, model = store.find_model(cand["model_id"])
            last = (model or {}).get("last_test_result") or {}
        except Exception:
            last = {}
        if last.get("ok") is True:
            verified += 1
        if "ok" in last:
            tested += 1
    return {"n": len(names), "verified": verified, "tested": tested, "names": names}


def _shelf_state(names):
    """{shelf: {items, names}} for the shelves asked about. Cheap, config only."""
    out = {}
    try:
        from providers import store
        items_for = store.shelf_items          # the ONE reader — includes SMTP
    except Exception:
        items_for = lambda _n: []
    for n in names:
        # shelf_items, not shelf_catalogue: SMTP is the mail route and lives in
        # .env. Counting only the catalogue made this report "no email API" on a
        # machine with working, tested mail.
        try:
            items = items_for(n)
        except Exception:
            items = []
        working = sum(1 for i in items
                      if i.get("kind") == "route"
                      or ((i.get("last_test_result") or {}).get("ok") is True))
        out[n] = {"items": len(items),
                  "names": [i.get("name", "") for i in items],
                  "working": working}
    return out


def _worst(*states):
    return min(states, key=lambda s: _RANK[s])


# WHAT EACH SECTOR'S MODEL ACTUALLY DOES, which is not the same for all seven
# and was nowhere on any screen.
#
# "double check each sector had modules integrated"
#
# Fair question, and the honest answer is more interesting than yes. A model is
# routed per sector and all seven use one to WORD the requests they send each
# other — that is coordination/ask.py, and it is why a sector with no brain
# falls back to a template and says so. But only two sectors put a model to
# work producing anything:
#
#   Content   writes the pages. Without a model it writes nothing at all, on
#             purpose — see pipelines/content_write.
#   Leads     writes the outreach mail — pipelines/mail.
#
# The other five are deterministic code. IT reads his site and compares it to
# rules; SEO counts and ranks what it fetched; Report adds up rows; Security
# matches patterns; the MD plans from the cabinet. Their model writes the
# wording of a request and answers him in the office drawer, and that is all it
# is for. That is a design choice rather than a gap — a number you can check is
# worth more than a model's opinion of it — but he should be able to see which
# is which rather than assuming every sector is thinking.
BRAIN_DOES = {
    "content": "writes the pages. No model routed means no page is written",
    "leads": "writes the outreach mail",
    "md": "plans the day and challenges the plan",
    "it": "wording only — the site checks themselves are code, not judgement",
    "seo": "wording only — the rankings and gaps are counted, not guessed",
    "report": "wording only — the digest is added up from rows",
    "security": "wording only — the screening is pattern matching",
}


def sector(key, task, env=None, shelf=None) -> dict:
    """One sector's verdict, with the reason spelled out in plain words."""
    env = _read_env() if env is None else env
    wanted = list(SHELVES.get(key, {}))
    shelf = _shelf_state(wanted) if shelf is None else shelf

    b = _brains(task)
    notes, blocking, thin = [], [], []
    state = READY

    # ---- the brain -----------------------------------------------------
    if b["n"] == 0:
        state = BLOCKED
        blocking.append("no brain routed — this sector cannot reason at all")
    else:
        if b["verified"] == 0:
            state = _worst(state, UNCHECKED)
            notes.append(f"{b['n']} brain{'s' if b['n'] > 1 else ''} configured, "
                         "none proven yet — run the check")
        elif b["verified"] == 1 and b["n"] == 1:
            state = _worst(state, THIN)
            thin.append("one brain and no fallback")
        if key in WANTS_TWO and b["n"] < 2:
            state = _worst(state, THIN)
            thin.append("this sector puts hard calls to two models; with one, "
                        "the cross-check silently stops happening")

    # ---- the shelves it borrows ----------------------------------------
    for name, required in SHELVES.get(key, {}).items():
        s = shelf.get(name, {"items": 0, "working": 0})
        if s["items"] == 0:
            if required:
                state = BLOCKED
                blocking.append(f"the {name} shelf is empty and this sector needs it")
            else:
                notes.append(f"no {name} API — optional, the sector works without it")
        elif s["working"] == 0:
            state = _worst(state, UNCHECKED)
            notes.append(f"{name}: {s['items']} API saved, not proven yet")
        elif s["items"] == 1 and required:
            state = _worst(state, THIN)
            thin.append(f"one API on the {name} shelf and no fallback")

    # ---- keys in .env ---------------------------------------------------
    for k, why in ENV_NEEDS.get(key, []):
        if not (env.get(k) or "").strip():
            state = BLOCKED
            blocking.append(f"{k} is not set — {why}")
    for k, why in ENV_WANTS.get(key, []):
        if not (env.get(k) or "").strip():
            state = _worst(state, THIN)
            thin.append(f"{k} is not set — {why}")

    says = (blocking[0] if blocking else
            thin[0] if thin else
            notes[0] if notes and state == UNCHECKED else
            "brain answers, every key it needs is on the shelf")

    return {"sector": key, "state": state, "says": says,
            "brains": b, "shelves": shelf,
            "brain_does": BRAIN_DOES.get(key, ""),
            "blocking": blocking, "thin": thin, "notes": notes}


def all_sectors() -> dict:
    """Every sector's light, plus the one-line summary of the floor."""
    env = _read_env()
    wanted = sorted({n for m in SHELVES.values() for n in m})
    shelf = _shelf_state(wanted)
    out = []
    for key, room, label in SECTORS:
        r = sector(key, key, env=env,
                   shelf={n: shelf[n] for n in SHELVES.get(key, {})})
        r["room"] = room
        r["label"] = label
        out.append(r)

    counts = {s: sum(1 for r in out if r["state"] == s)
              for s in (READY, UNCHECKED, THIN, BLOCKED)}
    if counts[BLOCKED]:
        head = f"{counts[BLOCKED]} sector{'s' if counts[BLOCKED] > 1 else ''} cannot run"
    elif counts[UNCHECKED]:
        head = f"{counts[UNCHECKED]} not proven yet — run the check"
    elif counts[THIN]:
        head = f"{counts[THIN]} running with no fallback"
    else:
        head = "all sectors ready"

    return {"ok": True, "at": int(time.time()), "sectors": out,
            "counts": counts, "head": head,
            "can_run": counts[BLOCKED] == 0, "checked": last_check()}


# ---------------------------------------------------------------------------
# THE REMEMBERED CHECK
#
# Proving an API costs a call. Doing that on every dashboard poll would burn the
# spend cap on a screen nobody is looking at, so the sweep runs when the owner
# asks for it and the result is kept here for the lights to read.
# ---------------------------------------------------------------------------

def _slim(payload: dict) -> dict:
    """
    What is worth keeping from a check, and what must not be kept.

    The full sweep carries the ntfy topic and the credential names in its
    channel details — the topic IS the secret on ntfy, anyone holding it reads
    the owner's notifications. It is fine in the reply to his own browser; it
    is not fine written into a row that any later dump reads back. So only the
    verdicts are stored, and only the shape the lights need.
    """
    return {
        "at": payload.get("at"), "quick": payload.get("quick"),
        "ready": bool(payload.get("ready")),
        "blocking": list(payload.get("blocking") or []),
        "warnings": list(payload.get("warnings") or []),
        "sectors": [{"sector": s.get("sector"), "state": s.get("state"),
                     "says": s.get("says")}
                    for s in ((payload.get("sectors") or {}).get("sectors") or [])],
    }


def record_check(payload: dict) -> None:
    board.ensure_schema()
    try:
        with sqlite3.connect(_db(), timeout=30) as c:
            c.execute("INSERT INTO preflight_runs(at, ready, blocking, payload)"
                      " VALUES(?,?,?,?)",
                      (int(time.time()), 1 if payload.get("ready") else 0,
                       json.dumps(payload.get("blocking") or []),
                       json.dumps(_slim(payload))[:200000]))
            # keep the last 30; this is a light, not an archive
            c.execute("DELETE FROM preflight_runs WHERE id NOT IN"
                      " (SELECT id FROM preflight_runs ORDER BY at DESC LIMIT 30)")
    except Exception:
        pass


def last_check() -> dict:
    board.ensure_schema()
    try:
        with sqlite3.connect(_db(), timeout=30) as c:
            r = c.execute("SELECT at, ready, blocking FROM preflight_runs"
                          " ORDER BY at DESC LIMIT 1").fetchone()
    except Exception:
        return {}
    if not r:
        return {}
    try:
        blocking = json.loads(r[2] or "[]")
    except Exception:
        blocking = []
    return {"at": r[0], "ready": bool(r[1]), "blocking": blocking,
            "ago": int(time.time()) - int(r[0] or 0)}


def as_text() -> str:
    d = all_sectors()
    mark = {READY: "●", UNCHECKED: "○", THIN: "◐", BLOCKED: "✕"}
    L = [f"SECTOR READINESS — {d['head']}"]
    for s in d["sectors"]:
        L.append(f"  {mark[s['state']]} {s['label']:22} {s['state']:9} {s['says']}")
        if s.get("brain_does"):
            L.append(f"      its model {s['brain_does']}")
    ck = d["checked"]
    L.append("")
    L.append(f"  last proven: {ck['ago'] // 60} min ago" if ck
             else "  never proven — nothing here has been tested against a live API")
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    if "--json" in sys.argv:
        print(json.dumps(all_sectors()))
    else:
        print(as_text())
