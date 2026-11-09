"""
api.py — thin JSON dispatch the Node server shells into. Keeping every
operation here means the UI and the agents share ONE code path; the server
never builds a client itself.

  echo '<json>' | python3 -m providers.api <command>

  team / team_set / team_test    the Claude team: who thinks with which model
  save_tool / test_tool / ...    the shelf of non-AI tools
"""
import json
import sys
import time

from . import claude_team, client, store

try:  # the cabinet lives in the coordination package; degrade gracefully if absent
    from coordination import cabinet
except Exception:  # pragma: no cover
    cabinet = None


def _in():
    raw = sys.stdin.read()
    return json.loads(raw) if raw.strip() else {}


def _out(obj):
    print(json.dumps(obj))


def cmd_view(_):
    return store.redacted_view()


def cmd_team(_):
    """The Claude team for the Setup page: the table, the accounts, what is struggling."""
    return {"ok": True, **claude_team.status()}


def cmd_team_set(a):
    """One sector's model. Saves that row only; nothing above Opus 5 is accepted."""
    return claude_team.save_model(a.get("sector", ""), a.get("model", ""))


def cmd_team_test(a):
    """
    A real one-line call per model, so a green tick means that model answered
    today — on the main account, and on the backup if there is one.
    """
    from . import engines
    if engines.engine() != engines.CLAUDE_CODE:
        rows = engines.test(only=a.get("model") or None)
        return {"ok": bool(rows) and all(r["ok"] for r in rows), "results": rows}
    models = [claude_team.clamp(a["model"])] if a.get("model") else None
    rows = claude_team.test(models)
    if len(claude_team.accounts()) > 1:
        rows += claude_team.test(models, account="backup")
    return {"ok": all(r["ok"] for r in rows), "results": rows}


def cmd_engine(_):
    """The Engine page: which AI thinks, its models, the cap and today's spend. No keys."""
    from . import engines
    return engines.status()


def cmd_engine_brief(_):
    """Which engine, whether it is ready, today's spend and the cap — for the top bar."""
    from . import engines
    return engines.brief()


def cmd_engine_save(a):
    """Save the Engine page: the engine, a key (stored, never returned), models, the cap."""
    from . import engines
    r = engines.save(a)
    return {**r, "status": engines.status()} if r.get("ok") else r


def cmd_engine_test(a):
    """One tiny real call per model on the chosen engine (or just one model)."""
    from . import engines
    rows = engines.test(only=a.get("model") or None)
    return {"ok": bool(rows) and all(r["ok"] for r in rows), "results": rows,
            "status": engines.status()}


def cmd_team_forget(_):
    """Try every model at the top again — after he signs back in, say."""
    claude_team.clear()
    return {"ok": True}


def cmd_save_tool(a):
    return {"ok": True, "tool": store.upsert_tool(a.get("tool", {}))}


def cmd_delete_tool(a):
    return {"ok": store.delete_tool(a.get("id", ""))}


def cmd_test_tool(a):
    """Test one shelf API for real, against its own endpoint."""
    tool = store.get_tool(a.get("id", ""))
    if not tool:
        return {"ok": False, "error": "no such tool"}
    t0 = time.time()
    res = client.probe_tool(tool)
    res["latency_ms"] = int((time.time() - t0) * 1000)
    store.upsert_tool({**tool, "last_test_result": {
        "ok": res.get("ok"), "status": res.get("status"), "at": int(time.time())}})
    return {"ok": True, "test": res}


def cmd_resolve_shelf(a):
    """The fallback chain for one shelf: primary first, then each fallback."""
    return {"ok": True, "resolved": store.resolve_shelf(a.get("shelf", ""))}


def cmd_shelves(_):
    """
    The shelf board: every shelf, what's on it (catalogue), and its live state
    (holder / queue / today's spend / recent checkouts). Tools only — the
    agency's brains are the Claude team, which is never lent out.
    """
    cat = store.shelf_catalogue()
    names = sorted(set(cat) | set(store.known_shelves()))
    state = cabinet.shelf_state(names) if cabinet else {}
    shelves = []
    for name in names:
        items = store.shelf_items(name)
        # items are already priority-sorted, so item 1 is the primary and the rest
        # are its fallbacks.
        shelves.append({
            "shelf": name,
            "kind": "tool",
            "purpose": store.shelf_kind(name),
            "items": items,
            "primary": (items[0]["name"] if items else None),
            "fallbacks": [i["name"] for i in items[1:]],
            "state": state.get(name, {"holder": None, "queue": [],
                                      "today_spend": 0, "recent": []}),
        })
    return {"ok": True, "shelves": shelves, "cabinet": bool(cabinet),
            "kinds": {s: store.shelf_kind(s) for s in names}}


def cmd_test_integration(a):
    """
    Test one channel (telegram / email), or both with 'all'.
    Nothing is sent unless notify=True is passed, and that only ever posts to the
    owner's own Telegram chat.
    """
    from . import integrations
    which = (a.get("which") or "all").strip().lower()
    notify = bool(a.get("notify"))
    if which == "all":
        return {"ok": True, "results": integrations.test_all(notify=notify)}
    fn = integrations.ALL.get(which)
    if not fn:
        return {"ok": False, "error": f"unknown integration '{which}'"}
    res = fn(notify=notify) if which == "telegram" else fn()
    return {"ok": True, "results": [res]}


def cmd_preflight(a):
    """
    The whole-system sweep: the Claude team, every shelf's fallback chain,
    every channel — and whether the agency is fit to run.
    """
    from . import preflight
    return preflight.sweep(quick=bool(a.get("quick")), notify=bool(a.get("notify")))


DISPATCH = {
    "view": cmd_view,
    "team": cmd_team, "team_set": cmd_team_set, "team_test": cmd_team_test,
    "team_forget": cmd_team_forget,
    "engine": cmd_engine, "engine_save": cmd_engine_save, "engine_test": cmd_engine_test,
    "engine_brief": cmd_engine_brief,
    "save_tool": cmd_save_tool, "delete_tool": cmd_delete_tool, "shelves": cmd_shelves,
    "test_tool": cmd_test_tool, "resolve_shelf": cmd_resolve_shelf,
    "test_integration": cmd_test_integration, "preflight": cmd_preflight,
}


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    fn = DISPATCH.get(cmd)
    if not fn:
        _out({"ok": False, "error": f"unknown command '{cmd}'"})
        return
    try:
        _out(fn(_in()))
    except Exception as e:
        _out({"ok": False, "error": str(e)})


if __name__ == "__main__":
    main()
