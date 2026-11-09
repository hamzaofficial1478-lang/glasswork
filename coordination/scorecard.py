"""
scorecard.py — how the MD is judged, and by whom.

    python -m coordination.scorecard          this week's card
    python -m coordination.scorecard --days 1

WHO SCORES THE MD

Not the MD. This module is computed by the Report sector, from rows the MD did
not write, and md_cycle.py must never import it — an agent that grades its own
management is not being graded. md_tests.py asserts that separation.

WHY FIVE NUMBERS AND NOT ONE

"Did the tasks get done" is an outcome measure, and outcome alone judges the MD
on the sectors' work rather than on its own. A manager who orders one safe job a
day and sees it finished scores 100% and is doing a bad job. So the card
separates the things a manager is actually accountable for:

  DIRECTION   of the tasks it posted, how many did a sector actually claim?
              Nobody claiming means it is ordering work no one wants or no one
              can do. This is the purest measure of bad direction, because the
              sector saw the task and walked past it.

  DELIVERY    of those claimed, how many finished? Claimed-then-abandoned is a
              different failure from never-claimed: the work was wanted but was
              wrong, too big, or blocked.

  GROUNDING   of everything it proposed, how much survived the Critic? A plan
              that arrives half-rejected was half-guessed. This is the only
              measure of its REASONING rather than its results, and it is the
              one a lucky day cannot flatter.

  COVERAGE    how many of the sectors it can direct got work? A manager quietly
              neglecting a sector for a week is failing at management even while
              everything it did order went perfectly.

  JUDGEMENT   the hit-rate of the decisions its work produced across the whole
              agency. Slowest to move, hardest to fake, and the closest thing
              here to "was it right".

Each is a fraction with the raw counts kept beside it, because 1/1 and 40/40 are
both "100%" and only one of them means anything. A measure with too little data
reports `None`, not a score — the same rule the decision scorer lives by.
"""
import argparse
import json
import sqlite3
import time

try:
    from . import board
except ImportError:  # pragma: no cover — direct-script fallback
    import board

# Sectors the MD can actually put a task on. Posting to a sector that does not
# claim from the board would be an instruction into a void, and COVERAGE would
# punish the MD for not doing something impossible.
DIRECTABLE = ("IT", "Leads", "SEO")

# Below this many samples a percentage is noise, so it is not reported as one.
MIN_SAMPLE = 3


def _pct(n, d):
    return None if not d else round(n / d, 2)


def _grade(fraction, floor, good):
    if fraction is None:
        return "not enough yet"
    if fraction >= good:
        return "good"
    if fraction >= floor:
        return "fair"
    return "poor"


def build(days=7, con=None):
    """The MD's management card for the last `days`. Pure read."""
    own = con is None
    con = con or sqlite3.connect(board.DB)
    since = int(time.time()) - days * 86400
    try:
        rows = con.execute(
            "SELECT id, evidence, created_at FROM decisions"
            " WHERE cycle='md-cycle' AND created_at>=?", (since,)).fetchall()

        task_ids, proposed, rejected, sectors_touched = [], 0, 0, set()
        blocked = set()
        for _did, raw, _at in rows:
            try:
                ev = json.loads(raw) if raw else {}
            except (TypeError, ValueError):
                ev = {}
            task_ids += [t for t in (ev.get("task_ids") or []) if t]
            proposed += int(ev.get("proposed") or 0)
            rejected += int(ev.get("rejected") or 0)
            sectors_touched |= set(ev.get("sectors") or [])
            blocked |= set(ev.get("blocked_sectors") or [])

        claimed = done = 0
        if task_ids:
            q = ",".join("?" * len(task_ids))
            for (status,) in con.execute(
                    f"SELECT status FROM tasks WHERE id IN ({q})", task_ids):
                if status in ("in_progress", "done", "blocked"):
                    claimed += 1
                if status == "done":
                    done += 1

        scored = con.execute(
            "SELECT outcome FROM decisions WHERE outcome IS NOT NULL"
            " AND scored_at>=?", (since,)).fetchall()
        wins = sum(1 for (o,) in scored if o == "worked")

        posted = len(task_ids)
        reachable = set(DIRECTABLE) - blocked or set(DIRECTABLE)
        card = {
            "days": days,
            "cycles_run": len(rows),
            "direction": {
                "value": _pct(claimed, posted) if posted >= MIN_SAMPLE else None,
                "of": f"{claimed}/{posted} posted tasks were claimed by a sector"},
            "delivery": {
                "value": _pct(done, claimed) if claimed >= MIN_SAMPLE else None,
                "of": f"{done}/{claimed} claimed tasks were finished"},
            "grounding": {
                "value": _pct(proposed - rejected, proposed) if proposed >= MIN_SAMPLE else None,
                "of": f"{proposed - rejected}/{proposed} proposals survived the Critic"},
            # A sector whose shelf is empty was never the MD's to direct. Marking
            # it down for that would blame it for a missing API key, and a metric
            # nobody can move is one everybody learns to ignore.
            "coverage": {
                "value": _pct(len(sectors_touched & reachable), len(reachable)),
                "of": (f"{len(sectors_touched & reachable)}/{len(reachable)} "
                       f"reachable sectors given work"
                       + (f" ({', '.join(sorted(blocked))} had no key)" if blocked else ""))},
            "judgement": {
                "value": _pct(wins, len(scored)) if len(scored) >= MIN_SAMPLE else None,
                "of": f"{wins}/{len(scored)} scored decisions worked"},
        }
        for key, floor, good in (("direction", .5, .8), ("delivery", .4, .75),
                                 ("grounding", .5, .8), ("coverage", .5, .99),
                                 ("judgement", .4, .7)):
            card[key]["grade"] = _grade(card[key]["value"], floor, good)

        card["verdict"] = _verdict(card)
        return card
    finally:
        if own:
            con.close()


def _verdict(card) -> str:
    """
    One plain sentence. Named after the WEAKEST measure, not the average — an
    average lets a manager hide a sector it has ignored for a week behind four
    numbers that look fine.
    """
    if not card["cycles_run"]:
        return "The MD has not run in this window, so there is nothing to judge."
    scored = [(k, card[k]) for k in ("direction", "delivery", "grounding",
                                     "coverage", "judgement")
              if card[k]["value"] is not None]
    if not scored:
        return ("The MD has run, but not enough has happened yet to judge it. "
                "That is an honest 'too early', not a pass.")
    worst_key, worst = min(scored, key=lambda kv: kv[1]["value"])
    if worst["grade"] == "good":
        return f"Managing well across the board — weakest is still {worst_key} ({worst['of']})."
    return (f"Weakest link is {worst_key}: {worst['of']}. "
            + {"direction": "It is ordering work the sectors walk past.",
               "delivery": "The work is wanted but is not getting finished.",
               "grounding": "Too much of the plan is being rejected — it is guessing.",
               "coverage": "A sector is being neglected.",
               "judgement": "Its calls are not coming good."}[worst_key])


def as_text(card) -> str:
    L = [f"THE MD'S CARD — last {card['days']} days, {card['cycles_run']} cycle(s)", ""]
    for k in ("direction", "delivery", "grounding", "coverage", "judgement"):
        m = card[k]
        val = "  —  " if m["value"] is None else f"{m['value']:>5.0%}"
        L.append(f"  {k.upper():<10} {val}  {m['grade']:<14} {m['of']}")
    L += ["", "  " + card["verdict"]]
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="How the MD is managing.")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    c = build(days=a.days)
    print(json.dumps(c, indent=2) if a.json else as_text(c))
