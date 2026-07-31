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
