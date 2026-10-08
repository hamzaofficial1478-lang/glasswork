"""
prove.py — "is this thing actually working, or is the dashboard just pretty?"

Run this any time to see HARD EVIDENCE from the filing cabinet: what the agents
really did, when, from which source, and what they wrote. Every line here is read
straight out of agency.db — nothing is decorative, nothing is invented.

    python3 -m coordination.prove

It also prints, honestly, which parts of the system are still placeholders, so
you always know the difference between real work and scaffolding.
"""
import sqlite3
import time
from pathlib import Path

DB = __import__("coordination.workspace", fromlist=["db_path"]).db_path()   # the open website's


def _ago(ts):
    if not ts:
        return "—"
    d = int(time.time()) - int(ts)
    if d < 60:
        return f"{d}s ago"
    if d < 3600:
        return f"{d // 60}m ago"
    if d < 86400:
        return f"{d // 3600}h ago"
    return f"{d // 86400}d ago"


def _stamp(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "—"


def main():
    if not DB.exists():
        print("No filing cabinet yet. Run: python3 coordination/init_db.py")
        return
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    q = lambda s, a=(): c.execute(s, a).fetchall()
    one = lambda s, a=(): (c.execute(s, a).fetchone() or [0])[0]

    print("=" * 66)
    print("  IS THE AGENCY REALLY WORKING?  — evidence from the filing cabinet")
    print("=" * 66)

    # 1. shelf checkouts — proof an agent borrowed a real capability
    print("\n1. SHELF CHECKOUTS  (did an agent actually borrow an API?)")
    rows = q("SELECT * FROM borrows ORDER BY granted_at DESC LIMIT 5")
    if not rows:
        print("   nothing borrowed yet — no agent has run.")
    for b in rows:
        print(f"   [{_stamp(b['granted_at'])}] {b['agent']} took '{b['shelf']}' "
              f"({b['approval']}) → used: {b['item'] or '—'}  ${b['usd_cost']}  [{b['status']}]")
        print(f"        reason: {b['reason']}")

    # 2. what a real agent produced
    print("\n2. REAL DATA PRODUCED  (keywords the SEO agent actually fetched)")
    # seeds are the hand-written service+area terms; anything lowercase with a
    # buy/info intent came from a real fetch, so count those separately and honestly.
    total = one("SELECT count(*) FROM keywords")
    fetched = one("SELECT count(*) FROM keywords WHERE phrase = lower(phrase)")
    print(f"   {total} keywords in the cabinet "
          f"(~{fetched} pulled from a live source, the rest are your seed terms)")
    for k in q("SELECT phrase,intent,area FROM keywords WHERE intent='buy' "
               "ORDER BY id DESC LIMIT 6"):
        print(f"     · {k['phrase']}   [{k['intent']}{'/' + k['area'] if k['area'] else ''}]")

    # 3. sector-to-sector traffic — proof the board is being used
    print("\n3. BOARD TRAFFIC  (are sectors actually talking to each other?)")
    rows = q("SELECT * FROM requests ORDER BY created_at DESC LIMIT 3")
    if not rows:
        print("   no requests posted yet.")
    for r in rows:
        print(f"   [{_ago(r['created_at'])}] {r['from_sector']} → {r['to_sector']}: "
              f"{r['subject']}  [{r['status']}]")
        m = c.execute("SELECT body FROM request_msgs WHERE request_id=? ORDER BY id LIMIT 1",
                      (r["id"],)).fetchone()
        if m:
            first = (m["body"] or "").splitlines()[0][:88]
            print(f"        \"{first}…\"")

    # 4. the MD's scorecard
    print("\n4. DECISION LOG  (is the MD keeping score?)")
    tot = one("SELECT count(*) FROM decisions")
    scored = one("SELECT count(*) FROM decisions WHERE outcome IS NOT NULL")
    print(f"   {tot} decisions logged, {scored} scored by Report")
    for d in q("SELECT * FROM decisions ORDER BY created_at DESC LIMIT 2"):
        print(f"   [{_ago(d['created_at'])}] confidence {d['confidence']} → "
              f"{d['outcome'] or 'not yet scored'}")
        print(f"        {(d['reasoning'] or '')[:96]}")

    # 5. the board of work
    print("\n5. TASK BOARD")
    for r in q("SELECT status,count(*) c FROM tasks GROUP BY status"):
        print(f"   {r['status']}: {r['c']}")

    # 6. guard rail activity
    print("\n6. GUARD RAILS  (are the hooks actually firing?)")
    blocked = one("SELECT count(*) FROM audit_log WHERE decision='blocked'")
    allowed = one("SELECT count(*) FROM audit_log WHERE decision='allowed'")
    print(f"   {allowed} actions allowed, {blocked} blocked by a red line")
    for a in q("SELECT * FROM audit_log WHERE decision='blocked' ORDER BY at DESC LIMIT 3"):
        print(f"   [{_ago(a['at'])}] blocked {a['agent']}/{a['tool']}: {a['reason']}")

    # 7. honesty section — what is NOT real yet
    print("\n" + "=" * 66)
    print("  STILL SCAFFOLDING (not real work yet) — so you're never misled:")
    print("=" * 66)
    print("   · /api/chat in the Office  → echoes your message, not wired to a model")
    print("   · runner/service.py        → a sketch; the always-on crew isn't looping yet")
    print("   · Content / Leads / IT / Report / Security agents → written as role")
    print("     definitions in .claude/agents/, but only SEO has runnable code so far")
    print("   · outreach email sending   → deliberately switched off until you add SMTP")
    print("   · the six room 'scores'    → computed from real counts, but they are a")
    print("     heuristic, not a measured KPI")
    print()
    c.close()


if __name__ == "__main__":
    main()
