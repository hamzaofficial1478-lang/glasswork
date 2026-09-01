"""
preflight.py — test everything, once, and say plainly whether the agency can run.

  python3 -m providers.preflight            # the full sweep, human-readable
  python3 -m providers.preflight --quick    # skip the model calls (no tokens spent)
  python3 -m providers.preflight --notify   # also post a real Telegram test message

WHY THIS EXISTS, AND WHAT IT DOES DIFFERENTLY

Testing a primary tells you today works. It tells you nothing about the night the
primary rate-limits at 2am and the whole sector stops. So this sweep tests EVERY
LINK IN EVERY CHAIN — each model behind a sector's routing, each API behind a
shelf — and then reports the thing that actually matters:

  COVERED   two or more links work; losing the primary costs you nothing
  SINGLE    exactly one link works; it is a single point of failure
  DOWN      nothing works; this sector or shelf cannot run at all

A chain of four dead fallbacks and one live primary reads as SINGLE, not as a
pass, because that is the truth. Each model is tested once and reused across
every sector that routes to it, so a shared brain costs one call, not five.
"""
import sys
import time

from . import client, integrations, routing, store

COVERED, SINGLE, DOWN, EMPTY = "COVERED", "SINGLE", "DOWN", "EMPTY"
# Five, not four. Under --quick nothing is called, so every link comes back
# ok=None — and the old four-way verdict read that as DOWN and announced that
# every sector in the agency had no working brain. "I did not look" and "I
# looked and it is dead" are not the same answer, and reporting the first as
# the second makes the cheap check useless: it can never say ready.
UNKNOWN = "UNKNOWN"


def _cover(n_working: int, n_total: int, n_unknown: int = 0) -> str:
    if n_total == 0:
        return EMPTY
    if n_working >= 2:
        return COVERED
    if n_working == 1:
        return SINGLE
    return UNKNOWN if n_unknown else DOWN


def _verdict(chain) -> str:
    return _cover(sum(1 for l in chain if l["ok"] is True), len(chain),
                  sum(1 for l in chain if l["ok"] is None))


class _ModelCache:
    """A model shared by five sectors should cost one test, not five."""

    def __init__(self, quick=False):
        self.quick, self.seen = quick, {}

    def test(self, cred, model):
        mid = model["id"]
        if mid in self.seen:
            return self.seen[mid]
        if self.quick:
            r = {"ok": None, "status": None, "ms": 0,
                 "cause": "skipped (--quick): no call was made, so this is unverified"}
        else:
            t0 = time.time()
            probe = client.probe_model(cred, model)
            t = probe["test"]
            r = {"ok": t.get("ok"), "status": t.get("status"),
                 "ms": int((time.time() - t0) * 1000), "cause": t.get("cause")}
            # Write the verdict back onto the model. The shelf sweep below has
            # always done this for tools; models were tested and the answer
            # thrown away, so the office had no way to tell a proven brain from
            # a saved one and had to show every sector as unverified forever.
            try:
                store.upsert_model(cred["id"], {**model, "last_test_result": {
                    "ok": r["ok"], "status": r["status"], "at": int(time.time())}})
            except Exception:
                pass
        self.seen[mid] = r
        return r


# ---- the four sweeps ---------------------------------------------------------

def sweep_brains(cache) -> list:
    out = []
    for c in store._load()["credentials"]:
        for m in c.get("models", []):
            if not m.get("enabled", True):
                out.append({"credential": c["name"], "model": m.get("api_model_id", ""),
                            "category": m.get("category", "LLM"), "ok": None,
                            "cause": "disabled — not tested", "ms": 0})
                continue
            r = cache.test(c, m)
            out.append({"credential": c["name"], "model": m.get("api_model_id", ""),
                        "category": m.get("category", "LLM"), "cost_tier": m.get("cost_tier"),
                        **r})
    return out


def sweep_routing(cache) -> list:
    """Every sector's brain chain, each link tested, with the cover verdict."""
    out = []
    for task in sorted(store.get_routing()):
        r = routing.resolve_task(task)
        if not r["ok"]:
            out.append({"task": task, "cover": DOWN, "error": r["error"], "chain": []})
            continue
        chain = []
        for i, cand in enumerate(r["candidates"]):
            cred, model = store.find_model(cand["model_id"])
            res = cache.test(cred, model)
            chain.append({"role": "primary" if i == 0 else f"fallback {i}",
                          "name": f'{cand["credential_name"]} · {cand["api_model_id"]}',
                          **res})
        out.append({"task": task, "chain": chain,
                    "working": sum(1 for l in chain if l["ok"] is True),
                    "cover": _verdict(chain)})
    return out


def sweep_shelf(quick=False) -> list:
    """Every shelf's API chain, each link tested, with the cover verdict."""
    out = []
    cat = store.shelf_catalogue()
    for shelf in sorted(set(store.known_shelves()) | set(cat)):
        tools = store.tools_on_shelf(shelf)
        if not tools:
            models = [i for i in cat.get(shelf, []) if i.get("kind") == "model"]
            if models:  # a model shelf — already covered by sweep_routing
                continue
            out.append({"shelf": shelf, "purpose": store.shelf_kind(shelf),
                        "chain": [], "working": 0, "cover": EMPTY})
            continue
        chain = []
        for i, t in enumerate(tools):
            if quick:
                res = {"ok": None, "status": None, "ms": 0,
                       "cause": "skipped (--quick): unverified"}
            else:
                t0 = time.time()
                p = client.probe_tool(t)
                res = {"ok": p.get("ok"), "status": p.get("status"),
                       "ms": int((time.time() - t0) * 1000), "cause": p.get("cause")}
                store.upsert_tool({**t, "last_test_result": {
                    "ok": res["ok"], "status": res["status"], "at": int(time.time())}})
            chain.append({"role": "primary" if i == 0 else f"fallback {i}",
                          "name": t["name"], **res})
        out.append({"shelf": shelf, "purpose": store.shelf_kind(shelf), "chain": chain,
                    "working": sum(1 for l in chain if l["ok"] is True),
                    "cover": _verdict(chain)})
    return out


def _why_down(r) -> str:
    """
    WHICH brain, and what the provider actually said.

    "sector 'md' has no working brain" is true and useless. It is a live call
    that failed, so there is always a real answer underneath — an expired key,
    a model name the provider does not recognise, no credit left — and the
    owner cannot act on any of it without being told which one. He read that
    line three times over about three sectors and had nothing to go on.

    The detail is already in the chain; it was simply never carried up.
    """
    if r.get("error"):
        return f" — {r['error']}"
    bits = []
    for link in r.get("chain") or []:
        why = (link.get("cause") or "").strip()
        if link.get("status"):
            why = f"HTTP {link['status']}" + (f" — {why}" if why else "")
        bits.append(f"{link.get('name', '?')}: {why or 'no answer'}")
    if not bits:
        return " — nothing is routed to it. Set a model for it on /setup."
    return " — " + "; ".join(bits[:2])


def _single_brain(r) -> str:
    """
    One sector with one working brain — and WHICH of the two cases it is.

    Configured-but-broken and never-configured need opposite things done about
    them, so they get opposite sentences, and the broken one carries the
    provider's own error rather than making him go and look for it.
    """
    task = r["task"]
    chain = r.get("chain") or []
    dead = [l for l in chain if l.get("ok") is False]
    unproven = [l for l in chain if l.get("ok") is None]

    if dead:
        alive = next((l["name"] for l in chain if l.get("ok") is True), "the primary")
        bits = "; ".join(f"{l['name']} — {l.get('cause') or 'no answer'}"
                         for l in dead[:2])
        return (f"sector '{task}' has {len(chain)} brains configured but only "
                f"{alive} is answering. Its fallback is set up and failing, "
                f"which is a different problem from not having one: {bits}")
    if unproven:
        return (f"sector '{task}' has one proven brain; "
                f"{len(unproven)} more configured but not reached this pass, so "
                f"whether it has a fallback is unknown rather than no")
    return (f"sector '{task}' runs on one brain with no fallback — only one "
            f"model is configured for it, so anything that stops that model "
            f"stops the sector")


def sweep(quick=False, notify=False) -> dict:
    cache = _ModelCache(quick=quick)
    brains = sweep_brains(cache)
    route = sweep_routing(cache)
    shelf = sweep_shelf(quick=quick)
    chan = integrations.test_all(notify=notify)

    blocking = []
    for r in route:
        if r["cover"] == DOWN:
            blocking.append(f"sector '{r['task']}' has no working brain"
                            + _why_down(r))
    # Approvals need ONE working route to the owner, not a specific one. ntfy is
    # the route now and Telegram is the fallback an owner mid-switch still has,
    # so this asks "can he be reached at all" rather than naming a product —
    # the old version blocked the whole day over a Telegram bot he had replaced.
    routes = [c for c in chan if c["integration"] in ("ntfy", "telegram")]
    live = sorted(c["integration"] for c in routes if c["ok"] is True)
    broken = sorted(c["integration"] for c in routes if c["ok"] is False)
    if not live:
        blocking.append(
            f"{' and '.join(broken)} failed — you cannot be asked to approve anything"
            if broken else
            "No notification route is set up — set NTFY_TOPIC or approvals have "
            "nowhere to go")

    # "ONE BRAIN WITH NO FALLBACK" WAS A LIE, AND HE CAUGHT IT TWICE.
    #
    # "while in real only leads and Seo sector has no fallback else of it all
    #  sectors have fallback but it said no any single one has any fallback ...
    #  there was no any reason about why fallback not worked"
    #
    # He is right and the bug is one line up: SINGLE means "exactly one link in
    # this chain ANSWERED the probe", not "only one link is configured". A
    # sector with a primary and a fallback, whose fallback happens to be out of
    # credit, comes back SINGLE — and was then told it had never configured a
    # fallback at all. Two completely different problems, one sentence, and the
    # sentence described the one that was not happening. Worse, the actual
    # error — the thing that would let him fix it in a minute — was sitting
    # right there in the chain and was thrown away.
    #
    # A dead fallback is usually one broken key shared by every sector, which
    # is exactly why all seven lit up at once on a machine where five of them
    # were configured perfectly well.
    warn = [_single_brain(r) for r in route if r["cover"] == SINGLE]
    warn += [f"shelf '{s['shelf']}' has one API and no fallback"
             for s in shelf if s["cover"] == SINGLE]
    warn += [f"shelf '{s['shelf']}' is configured but every API on it failed"
             for s in shelf if s["cover"] == DOWN]
    # Under --quick every sector comes back unproven, and seven identical lines
    # is not seven findings — it is one, and repeating it buries the real
    # warnings underneath it. So this is always ONE line, listing the sectors.
    unproven = sorted(r["task"] for r in route if r["cover"] == UNKNOWN)
    if unproven:
        lead = ("nothing was actually called, so no brain is proven"
                if quick else "these brains were not reached, so they are unproven")
        warn.append(f"{lead}: {', '.join(unproven)}")
    if live and broken:
        warn.append(f"{' and '.join(broken)} failed, but {' and '.join(live)} "
                    "works — you will still be reached")

    return {"ok": True, "quick": quick, "at": int(time.time()),
            "brains": brains, "routing": route, "shelf": shelf, "integrations": chan,
            "ready": not blocking, "blocking": blocking, "warnings": warn}


# ---- human-readable report ---------------------------------------------------

_MARK = {True: "✓", False: "✕", None: "·"}


def _line(label, ok, right=""):
    return f"    {_MARK[ok]} {label:44} {right}"


def report(res: dict) -> None:
    print("\n" + "=" * 74)
    print("  AGENCY PREFLIGHT" + ("   (--quick: nothing was actually called)" if res["quick"] else ""))
    print("=" * 74)

    print("\nBRAINS")
    if not res["brains"]:
        print("    (none configured)")
    for b in res["brains"]:
        right = f'{b.get("ms", 0)}ms' if b["ok"] is True else (b.get("cause") or "")[:44]
        print(_line(f'{b["credential"]} · {b["model"]}', b["ok"], right))

    print("\nSECTOR BRAINS — does each survive losing its primary?")
    if not res["routing"]:
        print("    (no routing configured)")
    for r in res["routing"]:
        print(f'  {r["task"]:10} {r["cover"]}' + (f'   {r.get("error","")}' if r.get("error") else ""))
        for l in r.get("chain", []):
            right = f'{l.get("ms",0)}ms' if l["ok"] is True else (l.get("cause") or "")[:40]
            print(_line(f'{l["role"]:12} {l["name"]}', l["ok"], right))

    print("\nSHELF — does each shelf survive losing its primary?")
    for s in res["shelf"]:
        if s["cover"] == EMPTY:
            continue
        print(f'  {s["shelf"]:14} {s["purpose"]:10} {s["cover"]}')
        for l in s["chain"]:
            right = f'{l.get("ms",0)}ms' if l["ok"] is True else (l.get("cause") or "")[:40]
            print(_line(f'{l["role"]:12} {l["name"]}', l["ok"], right))
    empties = [s["shelf"] for s in res["shelf"] if s["cover"] == EMPTY]
    if empties:
        print(f'    (empty shelves, nothing to test: {", ".join(empties)})')

    print("\nCHANNELS — nothing was sent")
    for c in res["integrations"]:
        print(_line(c["integration"], c["ok"], ""))
        print(f'        {c["detail"]}')

    print("\n" + "-" * 74)
    if res["blocking"]:
        print("  NOT READY — these stop the agency running:")
        for b in res["blocking"]:
            print(f"    ✕ {b}")
    else:
        print("  READY — every sector has a working brain and you can be reached.")
    for w in res["warnings"]:
        print(f"    ! {w}")
    print("-" * 74 + "\n")


def main():
    args = sys.argv[1:]
    res = sweep(quick="--quick" in args, notify="--notify" in args)
    if "--json" in args:
        import json
        print(json.dumps(res, indent=2))
    else:
        report(res)
    sys.exit(0 if res["ready"] else 1)


if __name__ == "__main__":
    main()
