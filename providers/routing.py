"""
routing.py — the doors every sector knocks on for an answer.

Behind them is the Claude team (providers/claude_team.py): each sector thinks
with the Claude model the owner chose for it, steps down a model or over to
the backup account when it has to, and says so on the result.

This file used to be a cascade over API keys from any provider — primary,
secondary, fallbacks, a cost-tier guard and a load balancer. The owner retired
all of it: "each time one or 2 models always give message of failure, so we
will remove all of them and will integrate the Claude". The function names and
the shapes they return are kept exactly, because two dozen callers ask through
them and none of them needed to know what answers.
"""


def _team():
    # Imported when first asked, not when the package loads: `python -m
    # providers.local_cli` would otherwise find itself already imported and
    # print a RuntimeWarning at the top of the owner's terminal.
    from . import claude_team, local_cli
    return claude_team, local_cli


def resolve_task(task: str) -> dict:
    """
    Who would answer for this sector, in order — without asking anything.
    Never returns a secret; there are none to return.
    """
    claude_team, local_cli = _team()
    s = claude_team.normalise(task)
    if s not in claude_team.DEFAULT_TEAM:
        return {"ok": False, "task": s, "candidates": [],
                "error": (f"'{task}' is not a sector the Claude team has. "
                          f"It has: {', '.join(claude_team.SECTORS)}")}
    ready, why = local_cli.available()
    if not ready:
        return {"ok": False, "task": s, "error": why, "candidates": []}
    labels = {a["id"]: a["label"] for a in claude_team.accounts()}
    return {
        "ok": True, "task": s,
        "route": {"primary": claude_team.model_for(s), "enabled": True,
                  "timeout": claude_team.BUDGET},
        "candidates": [
            {"credential_id": f"claude:{aid}", "credential_name": labels[aid],
             "model_id": m, "api_model_id": m, "cost_tier": "plan"}
            for aid, m in claude_team.candidates(s)],
    }


def call_task(task: str, messages, max_tokens: int = 512, timeout: int = 0) -> dict:
    """Ask this sector's Claude. See claude_team.call for the full story."""
    return _team()[0].call(task, messages, max_tokens, timeout)


def deliberate(task: str, messages, max_tokens: int = 512, verdict_of=None) -> dict:
    """
    Put one question to two different Claude models — the sector's own and the
    Critic's — and report whether they agree. For the handful of judgements
    where being wrong costs real money: how the MD allocates the day, and
    whether Security clears something. The value is the DISAGREEMENT; a split
    goes to the owner, never to a vote.

    Every call in here is marked demanding (providers/effort.py), so it thinks
    harder than an ordinary one.
    """
    from . import effort
    with effort.demanding(f"a judgement where being wrong costs money ({task})"):
        return _team()[0].deliberate(task, messages, max_tokens, verdict_of)
