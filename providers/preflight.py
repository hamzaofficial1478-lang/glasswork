"""
preflight.py — test everything, once, and say plainly whether the agency can run.

  python3 -m providers.preflight            # the full sweep, human-readable
  python3 -m providers.preflight --quick    # skip the model calls (no tokens spent)
  python3 -m providers.preflight --notify   # also post a real Telegram test message

THE BRAINS ARE ONE QUESTION NOW

Every sector thinks with a Claude model (providers/claude_team.py), so the
brain check is: is Claude Code ready, is each account signed in, and does each
model on the team answer today? A model that does not answer is a warning, not
a stop — the sector steps down to the next model on its own. Only "Claude
cannot answer at all" stops the day.

THE SHELF STILL TESTS EVERY LINK IN EVERY CHAIN

Testing a primary tells you today works. It tells you nothing about the night the
primary rate-limits at 2am. So each API behind a shelf is tested, and the
verdict is the thing that actually matters:

  COVERED   two or more links work; losing the primary costs you nothing
  SINGLE    exactly one link works; it is a single point of failure
  DOWN      nothing works; this sector or shelf cannot run at all

A chain of four dead fallbacks and one live primary reads as SINGLE, not as a
pass, because that is the truth.
"""
import sys
import time

from . import claude_team, client, integrations, store

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


def sweep_team(quick=False) -> dict:
    """
    The Claude team: the command, the accounts, and — unless --quick — one tiny
    real call per model on each signed-in account. Each model is tested once,
    however many sectors use it.
    """
    st = claude_team.status()
    tested = {}
    if st["ready"] and not quick:
        for acc in st["accounts"]:
            if acc.get("ok"):
                for row in claude_team.test(account=acc["id"]):
                    tested[(acc["id"], row["model"])] = row
    brains = []
    for (aid, m), r in sorted(tested.items(), key=lambda kv: (kv[0][0], claude_team.rank(kv[0][1]))):
        brains.append({"credential": f"{aid} Claude account", "model": claude_team.LABEL.get(m, m),
                       "ok": r["ok"], "ms": int((r.get("seconds") or 0) * 1000),
                       "cause": r.get("why") or ""})
    routing = []
    for row in st["team"]:
        chain = []
        for acc in st["accounts"]:
            model = row["model"]
            if acc["id"] == "backup" and not row["main"] and \
                    claude_team.rank(model) < claude_team.rank(claude_team.BACKUP_CAP_FOR_OTHERS):
                model = claude_team.BACKUP_CAP_FOR_OTHERS
            r = tested.get((acc["id"], model))
            ok = None if quick or not st["ready"] else (
                False if not acc.get("ok") else (r["ok"] if r else None))
            cause = ("" if ok else
                     "skipped (--quick): unverified" if quick else
                     acc.get("why") if not acc.get("ok") else (r or {}).get("why", ""))
            chain.append({"role": acc["id"], "name": claude_team.LABEL.get(model, model),
                          "ok": ok, "ms": int(((r or {}).get("seconds") or 0) * 1000),
                          "cause": cause})
        routing.append({"task": row["sector"], "label": row["label"],
                        "model": row["model"], "cover": _verdict(chain),
                        "chain": chain})
    return {"status": st, "brains": brains, "routing": routing}


def sweep_shelf(quick=False) -> list:
    """Every shelf's API chain, each link tested, with the cover verdict."""
    out = []
    cat = store.shelf_catalogue()
    for shelf in sorted(set(store.known_shelves()) | set(cat)):
        tools = store.tools_on_shelf(shelf)
        if not tools:
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


def sweep(quick=False, notify=False) -> dict:
    team = sweep_team(quick=quick)
    st = team["status"]
    shelf = sweep_shelf(quick=quick)
    chan = integrations.test_all(notify=notify)

    blocking, warn = [], []
    signed = [a for a in st["accounts"] if a.get("ok")]
    if not st["ready"]:
        blocking.append("Claude Code is not ready on this machine, so no sector can "
                        "think: " + st["why"].splitlines()[0])
    elif not signed:
        blocking.append("no Claude account is signed in on this machine — run "
                        "`claude` once and sign in")
    else:
        for a in st["accounts"]:
            if not a.get("ok"):
                warn.append(f"the {a['label']} is not signed in — "
                            "`python -m providers.claude_team --login-backup`")
        if len(st["accounts"]) < 2:
            warn.append("no backup Claude account — if the main account's allowance "
                        "runs out, sectors step down to lighter models and then stop. "
                        "`python -m providers.claude_team --login-backup` adds one")
        for r in team["routing"]:
            main = r["chain"][0] if r["chain"] else {}
            if main.get("ok") is False:
                lower = claude_team.below(r["model"])
                warn.append(f"{r['label']}: {claude_team.LABEL.get(r['model'], r['model'])} "
                            f"did not answer ({main.get('cause') or 'no reason given'})"
                            + (f" — it will step down to {claude_team.LABEL[lower[0]]}"
                               if lower else ""))
        if quick:
            warn.append("nothing was actually called, so no model is proven — "
                        "only that the accounts are signed in")

    # Approvals need ONE working route to the owner, not a specific one. ntfy is
    # the route now and Telegram is the fallback an owner mid-switch still has,
    # so this asks "can he be reached at all" rather than naming a product.
    routes = [c for c in chan if c["integration"] in ("ntfy", "telegram")]
    live = sorted(c["integration"] for c in routes if c["ok"] is True)
    broken = sorted(c["integration"] for c in routes if c["ok"] is False)
    if not live:
        blocking.append(
            f"{' and '.join(broken)} failed — you cannot be asked to approve anything"
            if broken else
            "No notification route is set up — set NTFY_TOPIC or approvals have "
            "nowhere to go")

    warn += [f"shelf '{s['shelf']}' has one API and no fallback"
             for s in shelf if s["cover"] == SINGLE]
    warn += [f"shelf '{s['shelf']}' is configured but every API on it failed"
             for s in shelf if s["cover"] == DOWN]
    if live and broken:
        warn.append(f"{' and '.join(broken)} failed, but {' and '.join(live)} "
                    "works — you will still be reached")

    return {"ok": True, "quick": quick, "at": int(time.time()),
            "team": {"accounts": st["accounts"], "ready": st["ready"],
                     "why": st["why"]},
            "brains": team["brains"], "routing": team["routing"], "shelf": shelf,
            "integrations": chan, "ready": not blocking, "blocking": blocking,
            "warnings": warn}


# ---- human-readable report ---------------------------------------------------

_MARK = {True: "✓", False: "✕", None: "·"}


def _line(label, ok, right=""):
    return f"    {_MARK[ok]} {label:44} {right}"


def report(res: dict) -> None:
    print("\n" + "=" * 74)
    print("  AGENCY PREFLIGHT" + ("   (--quick: nothing was actually called)" if res["quick"] else ""))
    print("=" * 74)

    print("\nCLAUDE ACCOUNTS")
    for a in res["team"]["accounts"]:
        who = f" as {a['email']}" if a.get("email") else ""
        print(_line(a["label"] + who, bool(a.get("ok")),
                    "" if a.get("ok") else (a.get("why") or "")[:40]))

    print("\nMODELS — one tiny call each")
    if not res["brains"]:
        print("    (nothing called)")
    for b in res["brains"]:
        right = f'{b.get("ms", 0)}ms' if b["ok"] is True else (b.get("cause") or "")[:44]
        print(_line(f'{b["credential"]} · {b["model"]}', b["ok"], right))

    print("\nTHE TEAM — each sector on the main account, and on the backup")
    for r in res["routing"]:
        print(f'  {r["label"]:18} {r["cover"]}')
        for l in r.get("chain", []):
            right = f'{l.get("ms",0)}ms' if l["ok"] is True else (l.get("cause") or "")[:40]
            print(_line(f'{l["role"]:8} {l["name"]}', l["ok"], right))

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
        print("  READY — Claude can think for every sector and you can be reached.")
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
