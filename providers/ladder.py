"""
ladder.py — think() and spread(), for the pipelines that ask through them.

There used to be a ladder to climb here: a sector whose API key had died
borrowed a spare model off a shelf, and in the last resort the Director's own,
capped at three borrows a day. It existed because a sector could be left with
no working brain at all.

With the Claude team that cannot happen the same way. Every sector's own call
already steps down the models and over to the backup account
(providers/claude_team.py), and a model is not a seat that one sector takes
from another. So think() is one call, and spread() is several at once.
"""
from concurrent.futures import ThreadPoolExecutor

from . import claude_team, local_cli

OWN = "own"
SECTORS = claude_team.SECTORS

# How many of a sector's independent jobs run side by side. Each is its own
# Claude Code process; three keeps a laptop responsive and a plan's allowance
# from being eaten in one burst.
WORKERS = 3


def think(sector, messages, agent="", reason="", max_tokens=512, timeout=0,
          **_ignored) -> dict:
    """One answer, with {rung, climbed, why} so older callers read it as before."""
    r = claude_team.call(sector, messages, max_tokens, timeout, agent=agent)
    return {**r, "rung": OWN if r.get("ok") else None, "climbed": [],
            "why": (r.get("stepped_down") or
                    f"{claude_team.SECTOR_LABEL.get(claude_team.normalise(sector), sector)}"
                    f" answered on its own model") if r.get("ok") else r.get("error", "")}


def spread(sector, jobs, max_tokens=512, timeout=0, agent="", workers=None) -> dict:
    """
    A pile of INDEPENDENT jobs — twenty subject lines, not the paragraphs of
    one article — done a few at a time. Returns
    {ok, results: {key: result}, done, failed, models_used, rung}.
    """
    jobs = list(jobs or [])
    if not jobs:
        return {"ok": True, "results": {}, "done": 0, "failed": 0,
                "models_used": [], "rung": OWN, "note": "nothing to do"}
    n = max(1, min(int(workers or WORKERS), len(jobs)))
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = {key: pool.submit(claude_team.call, sector, msgs, max_tokens,
                                    timeout, agent=agent)
                   for key, msgs in jobs}
        results = {k: f.result() for k, f in futures.items()}
    done = sum(1 for r in results.values() if r.get("ok"))
    return {"ok": done > 0, "results": results, "done": done,
            "failed": len(results) - done, "rung": OWN,
            "models_used": sorted({r["used"]["model"] for r in results.values()
                                   if r.get("ok")})}


def who_could_answer(sector) -> dict:
    """Could this sector think at all right now? Read-only; spends nothing."""
    s = claude_team.normalise(sector)
    ready, why = local_cli.available()
    known = s in claude_team.DEFAULT_TEAM
    ok = ready and known
    return {"ok": ok, "own": ok, "spare": 0,
            "own_error": "" if ok else (why or f"'{sector}' is not on the Claude team"),
            "why": "" if ok else (why or f"'{sector}' is not on the Claude team")}


def state(sector=None) -> dict:
    """What readiness shows: nothing is borrowed any more; what is struggling is."""
    return {"on_the_directors_brain": [], "borrows_today": 0,
            "struggling": claude_team.health()}


def as_text() -> str:
    rows = claude_team.health()
    if not rows:
        return "Every Claude model on the team is being tried at the top."
    return "\n".join(f"{claude_team.LABEL.get(h['model'], h['model'])}: {h['why']} "
                     f"(tried again in {h['back_in_minutes']} min)" for h in rows)
