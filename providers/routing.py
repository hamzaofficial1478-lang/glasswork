"""
routing.py — per-task provider binding. There is NO global "AI provider": each
task binds independently to primary / secondary / fallback models, with a hard
cost-tier guard and visible failover.
"""
from . import client, store


def _ordered_ids(route: dict):
    ids = []
    if route.get("primary"):
        ids.append(route["primary"])
    # secondary counts as a fallback here unless the task marks it as fusion
    if route.get("secondary") and route.get("secondary_mode", "fallback") == "fallback":
        ids.append(route["secondary"])
    ids += list(route.get("fallback") or [])
    seen, out = set(), []
    for i in ids:
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _candidates(task: str):
    route = store.get_routing().get(task)
    if not route:
        return [], None
    out = []
    for mid in _ordered_ids(route):
        cred, model = store.find_model(mid)
        if cred and model and model.get("enabled", True):
            out.append((cred, model))
    return out, route


def resolve_task(task: str) -> dict:
    """
    Ordered, usable (credential, model) candidates for a task, after the enable
    toggle and the cost-tier guard. This is the clean read interface other code
    uses; it never returns a secret.
    """
    cands, route = _candidates(task)
    if route is None:
        return {"ok": False, "error": f"no routing configured for task '{task}'", "candidates": []}
    if not route.get("enabled", True):
        return {"ok": False, "error": f"task '{task}' is disabled", "candidates": []}
    paid_allowed = route.get("paid_allowed", True)
    usable = [(c, m) for c, m in cands if paid_allowed or m.get("cost_tier") != "paid"]
    if not usable:
        if cands and not paid_allowed:
            return {"ok": False,
                    "error": f"task '{task}' allows only free providers, but only paid ones are configured",
                    "candidates": []}
        return {"ok": False, "error": f"task '{task}' has no usable provider yet", "candidates": []}
    return {
        "ok": True, "task": task, "route": route,
        "candidates": [
            {"credential_id": c["id"], "credential_name": c["name"], "model_id": m["id"],
             "api_model_id": m["api_model_id"], "cost_tier": m.get("cost_tier")}
            for c, m in usable
        ],
    }


def call_task(task: str, messages, max_tokens: int = 512) -> dict:
    """
    Run a task: try its candidates in order, cascade on failure/timeout/rate-limit,
    and return the output plus a visible failover trail. The cost-tier guard is
    hard — if paid isn't allowed and only paid providers exist, it refuses rather
    than spending.
    """
    r = resolve_task(task)
    if not r["ok"]:
        return {"ok": False, "task": task, "error": r["error"], "failovers": []}
    route = r["route"]
    timeout = int(route.get("timeout", 120))
    failovers = []
    for cand in r["candidates"]:
        cred, model = store.find_model(cand["model_id"])
        res = client.chat(cred, model, messages, max_tokens=max_tokens, timeout=timeout)
        if res["ok"]:
            store.record_usage(task, cred["id"], model["id"], calls=1, spend=0.0)
            return {"ok": True, "task": task,
                    "used": {"credential": cred["name"], "model": model["api_model_id"]},
                    "output": res["output"], "failovers": failovers}
        failovers.append({"model": model["api_model_id"], "status": res["status"],
                          "cause": res.get("cause")})
    return {"ok": False, "task": task, "error": "all providers for this task failed",
            "failovers": failovers}


# ---------------------------------------------------------------------------
# TWO MINDS ON ONE QUESTION
# ---------------------------------------------------------------------------
#
# call_task() is a CASCADE: try the primary, fall back only when it fails. That
# is right for ordinary work — a second opinion on drafting an email is money
# spent for nothing.
#
# deliberate() is different. It asks the primary AND the second model the same
# question and compares them, for the handful of judgements where being wrong
# costs real money: how the MD allocates the day, and whether Security clears
# something to be acted on. The value is not a better answer, it is the
# DISAGREEMENT. Two models reaching the same verdict is weak evidence it is
# right; two models splitting is strong evidence the question is genuinely hard
# and a human should see it.
#
# What it deliberately does NOT do:
#
#   it does not vote. Two opinions cannot outvote anything, and picking the
#   "majority" of two is just picking the first one with extra steps. On a
#   split it reports the split and lets the caller decide, which for Security
#   means failing safe and for the MD means asking the owner.
#
#   it does not run when only one model is configured. It says so — agreed=None
#   — rather than asking the same model twice and calling that agreement, which
#   would be the most expensive way possible of learning nothing.
#
# Cost: two calls instead of one, on a small number of decisions. The daily
# spend cap in coordination/cabinet.py is the backstop.

def deliberate(task: str, messages, max_tokens: int = 512, verdict_of=None) -> dict:
    """
    Put one question to two different models. Returns both answers and whether
    they agree.

    `verdict_of` reduces an answer to the thing worth comparing — for a
    clear/refuse decision that is the word, not the prose around it. Without it
    the whole trimmed output is compared, which is only meaningful for short
    structured replies.
    """
    r = resolve_task(task)
    if not r["ok"]:
        return {"ok": False, "task": task, "error": r["error"], "consulted": 0,
                "agreed": None, "answers": []}

    route = r["route"]
    timeout = int(route.get("timeout", 120))
    pair = r["candidates"][:2]

    answers, failovers = [], []
    for cand in pair:
        cred, model = store.find_model(cand["model_id"])
        res = client.chat(cred, model, messages, max_tokens=max_tokens, timeout=timeout)
        if res["ok"]:
            store.record_usage(task, cred["id"], model["id"], calls=1, spend=0.0)
            answers.append({"model": model["api_model_id"],
                            "credential": cred["name"],
                            "output": (res["output"] or "").strip()})
        else:
            failovers.append({"model": model["api_model_id"], "status": res["status"],
                              "cause": res.get("cause")})

    if not answers:
        return {"ok": False, "task": task, "error": "no model answered",
                "consulted": 0, "agreed": None, "answers": [], "failovers": failovers}

    def _v(a):
        raw = a["output"]
        return (verdict_of(raw) if verdict_of else raw).strip().lower()

    if len(answers) < 2:
        # One model available, or the second failed. Say so plainly: a single
        # answer is not a cross-check, and pretending otherwise is worse than
        # not having one, because it buys false confidence.
        return {"ok": True, "task": task, "consulted": len(answers), "agreed": None,
                "answers": answers, "verdict": _v(answers[0]),
                "why": ("only one model is configured for this task, so this was "
                        "not cross-checked" if len(pair) < 2
                        else "the second model failed, so this was not cross-checked"),
                "failovers": failovers}

    v1, v2 = _v(answers[0]), _v(answers[1])
    agreed = v1 == v2
    return {"ok": True, "task": task, "consulted": 2, "agreed": agreed,
            "answers": answers, "verdict": v1 if agreed else None,
            "split": None if agreed else [v1, v2],
            "why": ("both models reached the same verdict" if agreed else
                    f"the two models disagreed: {v1!r} vs {v2!r}"),
            "failovers": failovers}
