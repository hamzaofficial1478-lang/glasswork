"""
ntfy_replies.py — approving and refusing from the phone.

THE PROBLEM

The owner gets the amber queue on ntfy and cannot do anything about it. He has
to be at the machine, open the control room, and click. A notification you can
only read is half a notification, and it is the half that does not help at
eleven at night.

WHY NOT ntfy's ACTION BUTTONS

ntfy can put Approve and Deny buttons on a notification, and they work by having
the PHONE call an HTTP endpoint. That endpoint would have to be this server —
and this server binds to 127.0.0.1 on purpose, because it holds every API key
the agency has. Nothing outside the machine can reach it, which is exactly the
property we want to keep. Opening a port to the internet so a button works is a
bad trade, and it is not one to make quietly on somebody's behalf.

So the buttons are offered ONLY when the owner has already chosen to expose the
server himself (BIND_HOST is set and he has given a reachable address). By
default there are no buttons, and this works instead:

WHAT ACTUALLY HAPPENS

ntfy topics are two-way. The phone app has a send box on every topic, and
anything published there sits in the topic's JSON feed. So the agency asks a
question and reads the topic for the answer:

    agency  ->  "AMBER #3 — Apply queued change: add_search_widget.
                 Reply  ok 3  to approve, or  no 3  to refuse."
    owner   ->  "ok 3"
    agency  ->  "#3 approved. Applied."

No open port, no exposed key, nothing new to install. The message is already
going to a private topic only he is subscribed to; this reads the same topic
back. The trade-off is the one that already applies to ntfy and is stated
wherever it appears: anybody who guesses the topic name can read it — and now,
also write to it. That matters more than it did, so make the topic long and
random, and self-host if these decisions are worth money to somebody else.

WHAT IT WILL NOT DO

Approve anything it is not certain about. "ok" on its own, with two items
waiting, is ambiguous — it asks which rather than guessing, because guessing
wrong here means applying a change to the live site nobody agreed to. And it
never acts on a message the agency itself published, or it would approve its
own requests the moment it printed them.
"""
import argparse
import json
import re
import time
import urllib.parse
import urllib.request

try:
    from . import board, notify
except ImportError:  # pragma: no cover
    import board, notify

UA = "glasswork-agency/1.0"

# What counts as yes and no. Deliberately short — this is typed on a phone, one
# handed, probably in a hurry.
YES = r"(?:ok|okay|yes|y|approve|approved|go|do it|sure)"
NO = r"(?:no|n|nope|deny|denied|refuse|reject|stop|don'?t)"
_RE = re.compile(rf"^\s*({YES}|{NO})\b[\s:#-]*(\d+)?\s*$", re.I)

# Only look at the last day of the topic. An "ok 3" from last week is not a
# decision about today's item 3.
WINDOW = 24 * 3600


def _feed(env=None, since=WINDOW, timeout=20):
    """Everything published to the topic recently. A read; publishes nothing."""
    from providers import integrations as ig
    env = env if env is not None else ig.read_env()
    server, topic = ig._ntfy_conf(env)
    if not topic:
        return [], "no ntfy topic set"
    url = (f"{server.rstrip('/')}/{urllib.parse.quote(topic)}/json"
           f"?poll=1&since={int(since)}s")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    tok = (env.get("NTFY_TOKEN") or "").strip()
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception as e:
        return [], f"could not read the topic: {e}"
    out = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("event") == "message":
            out.append(d)
    return out, ""


def _ours(msg):
    """
    Did the agency publish this? Its own messages must never be read as answers,
    or it approves whatever it just asked about.

    The agency always sends a Title; a reply typed into the phone's send box has
    none. That is the tell, and it is checked before anything else.
    """
    return bool((msg.get("title") or "").strip())


def parse(text):
    """('approved'|'rejected'|None, number or None) from one message."""
    m = _RE.match(text or "")
    if not m:
        return None, None
    word = m.group(1).lower()
    n = int(m.group(2)) if m.group(2) else None
    return ("rejected" if re.fullmatch(NO, word, re.I) else "approved"), n


def pending():
    """The amber queue, numbered the way the notification numbers it."""
    try:
        from . import state as _state
        items = (_state.build() or {}).get("approvals") or []
    except Exception:
        items = []
    return [{"n": i + 1, **a} for i, a in enumerate(items)]


def apply_replies(dry_run=False, env=None) -> dict:
    """
    Read the topic, act on what the owner said, and tell him what happened.

    Every decision is echoed back to the topic. Silence after a reply is
    indistinguishable from the message never arriving, and on a control the
    handbook makes him the tie-breaker for, that is not good enough.
    """
    msgs, err = _feed(env)
    if err:
        return {"ok": False, "error": err, "acted": [], "ignored": []}

    items = pending()
    by_n = {a["n"]: a for a in items}
    acted, ignored, seen = [], [], _already_acted()

    for m in sorted(msgs, key=lambda d: d.get("time") or 0):
        if _ours(m):
            continue
        mid = str(m.get("id") or "")
        if mid in seen:
            continue                     # decided on a previous pass
        text = (m.get("message") or "").strip()
        decision, n = parse(text)
        if not decision:
            continue

        if n is None:
            if len(items) == 1:
                n = items[0]["n"]        # only one thing waiting: no ambiguity
            else:
                ignored.append({"id": mid, "said": text,
                                "why": (f"{len(items)} items are waiting, so "
                                        f"'{text}' does not say which. Reply with "
                                        "the number, e.g. 'ok 2'.")})
                _remember(mid)
                _say(f"Which one? {len(items)} are waiting — reply 'ok <number>'.")
                continue

        a = by_n.get(n)
        if not a:
            ignored.append({"id": mid, "said": text,
                            "why": f"there is no item {n} waiting any more"})
            _remember(mid)
            _say(f"Nothing is waiting as #{n} any more — it may already be decided.")
            continue

        if dry_run:
            acted.append({"n": n, "decision": decision, "item": a.get("text"),
                          "dry_run": True})
            continue

        try:
            r = board.decide_approval(a.get("kind"), a.get("id"), decision)
            ok = bool(r.get("ok", True))
            note = r.get("error") or ""
        except Exception as e:
            ok, note = False, str(e)
        _remember(mid)
        acted.append({"n": n, "decision": decision, "item": a.get("text"),
                      "ok": ok, "note": note})
        _say(f"#{n} {decision}"
             + (f" — {a.get('text','')}"[:90] if a.get("text") else "")
             + ("" if ok else f". It did not stick: {note}"))

    return {"ok": True, "acted": acted, "ignored": ignored,
            "waiting": len(items),
            "head": (f"{len(acted)} decision(s) applied" if acted else
                     "nothing new from the phone")}


# ---- remembering what has already been acted on -----------------------------
# A reply stays in the topic's feed for as long as the server keeps it, so
# without this every pass would re-apply the same "ok 3" — and after the item
# was decided and a new one took its number, re-apply it to the WRONG thing.

def _remember(msg_id):
    try:
        with board._conn() as c:
            board._migrate(c)
            c.execute("INSERT OR IGNORE INTO read_marks (what, seen_to, at)"
                      " VALUES (?,?,?)", (f"ntfy:{msg_id}", int(time.time()),
                                          int(time.time())))
    except Exception:
        pass


def _already_acted():
    try:
        with board._conn() as c:
            board._migrate(c)
            return {r[0].split(":", 1)[1] for r in c.execute(
                "SELECT what FROM read_marks WHERE what LIKE 'ntfy:%'")}
    except Exception:
        return set()


def _say(line):
    """Confirm back on the topic. Best effort — never breaks the decision."""
    try:
        from providers import integrations as ig
        ig.send_message(line, title="Agency")
    except Exception:
        pass


def ask_text(items=None) -> str:
    """The message that tells him what is waiting and how to answer it."""
    items = items if items is not None else pending()
    if not items:
        return "Nothing is waiting on you."
    L = [f"{len(items)} waiting on you:"]
    for a in items:
        L.append(f"  {a['n']}. [{a.get('lvl', 'amber')}] {(a.get('text') or '')[:70]}"
                 + (f"  ({a['why']})" if a.get("why") else ""))
    L.append("")
    L.append("Reply  ok <number>  to approve, or  no <number>  to refuse.")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(
        description="Approve or refuse the amber queue by replying on ntfy.")
    ap.add_argument("--apply", action="store_true", help="read the topic and act")
    ap.add_argument("--ask", action="store_true",
                    help="send him what is waiting, and how to answer")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    # Running it with no arguments DOES the work, the same as every other step
    # the day runner calls.
    if not (a.ask or a.apply):
        a.apply = True

    if a.ask:
        from providers import integrations as ig
        items = pending()
        out = {"ok": True, "waiting": len(items), "text": ask_text(items)}
        if items and not a.dry_run:
            out["sent"] = ig.send_message(ask_text(items),
                                          title="Waiting on you")
    else:
        out = apply_replies(dry_run=a.dry_run)

    if a.json:
        print(json.dumps(out, default=str))
    elif out.get("ok"):
        print(out.get("head") or out.get("text") or "done")
        for x in out.get("acted", []):
            print(f"  #{x['n']} {x['decision']}: {x.get('item', '')}")
        for x in out.get("ignored", []):
            print(f"  ignored '{x['said']}' — {x['why']}")
    else:
        print(out.get("error"))


if __name__ == "__main__":
    main()
