"""
api.py — thin JSON dispatch the Node server shells into, plus the
`check-providers` CLI. Keeping every provider operation here means the UI and the
agents share ONE code path (this module); the server never builds an API client.

  echo '<json>' | python3 -m providers.api <command>
  python3 -m providers.api check-providers
"""
import json
import sys
import time

from . import auth, client, routing, store

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


def cmd_save_credential(a):
    return {"ok": True, "credential": store.upsert_credential(a)}


def cmd_delete_credential(a):
    return {"ok": store.delete_credential(a.get("id", ""))}


def cmd_save_model(a):
    return {"ok": True, "model": store.upsert_model(a.get("credential_id", ""), a.get("model", {}))}


def cmd_delete_model(a):
    return {"ok": store.delete_model(a.get("credential_id", ""), a.get("model_id", ""))}


def cmd_fetch_models(a):
    cred = store.get_credential(a.get("credential_id", ""))
    if not cred:
        return {"ok": False, "error": "no such credential"}
    return client.fetch_models(cred)


def cmd_test_model(a):
    cred = store.get_credential(a.get("credential_id", ""))
    if not cred:
        return {"ok": False, "error": "no such credential"}
    model = next((m for m in cred.get("models", []) if m["id"] == a.get("model_id")), None)
    if not model:
        return {"ok": False, "error": "no such model"}
    t0 = time.time()
    probe = client.probe_model(cred, model)
    probe["latency_ms"] = int((time.time() - t0) * 1000)
    # persist what we detected
    store.upsert_model(cred["id"], {**model,
                                    "detected_capabilities": probe["capabilities"],
                                    "last_test_result": {"ok": probe["test"].get("ok"),
                                                         "at": int(time.time()),
                                                         "status": probe["test"].get("status")}})
    return {"ok": True, **probe}


def cmd_get_routing(_):
    return {"ok": True, "routing": store.get_routing()}


def cmd_save_route(a):
    return {"ok": True, "route": store.set_route(a.get("task", ""), a.get("route", {}))}


def cmd_status(_):
    """Readiness summary for the /status page: which tasks can resolve a provider."""
    data = store.redacted_view()
    tasks = {}
    for task in data["routing"]:
        r = routing.resolve_task(task)
        cand = r["candidates"][0] if r.get("candidates") else None
        tasks[task] = {
            "ok": r["ok"],
            "detail": (f'{cand["credential_name"]} · {cand["api_model_id"]}' if cand
                       else r.get("error", "no provider")),
        }
    return {"ok": True, "credentials": len(data["credentials"]),
            "models": sum(len(c["models"]) for c in data["credentials"]),
            "tasks": tasks, "usage": data["usage"]}


def cmd_save_tool(a):
    return {"ok": True, "tool": store.upsert_tool(a.get("tool", {}))}


def cmd_delete_tool(a):
    return {"ok": store.delete_tool(a.get("id", ""))}


def cmd_test_tool(a):
    """Test one shelf API for real, the same way the model tester works."""
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


def _smtp_configured() -> bool:
    """Is there a real mail route, even though it does not live on the shelf?"""
    try:
        from . import integrations
        env = integrations.read_env()
        return all((env.get(k) or "").strip()
                   for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"))
    except Exception:
        return False


def cmd_shelves(_):
    """
    The shelf board: every shelf, what's on it (catalogue), and its live state
    (holder / queue / today's spend / recent checkouts). Model shelves come from
    model categories; tool shelves from the tools list.
    """
    cat = store.shelf_catalogue()
    # Model categories are no longer forced onto this list. 'image' and
    # 'embedding' were injected here and appeared as permanently-empty shelves
    # nobody could fill, and 'llm' appeared as a SHELF full of the owner's
    # routed brains — which reads as "any agent can take the MD's model". None
    # of them can: brains are routed, never borrowed. A category still shows if
    # a model is filed under it, and it is labelled a brain, not a shelf.
    names = sorted(set(cat) | set(store.known_shelves()))
    state = cabinet.shelf_state(names) if cabinet else {}
    shelves = []
    for name in names:
        items = store.shelf_items(name)
        # items are already priority-sorted, so item 1 is the primary and the rest
        # are its fallbacks — the same shape the routing screen uses for brains.
        shelves.append({
            "shelf": name,
            "kind": "model" if name in store.MODEL_SHELF_KINDS
                    or all(i.get("kind") == "model" for i in items) and items
                    else "tool",
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
    The whole-system sweep: every brain, every sector's fallback chain, every
    shelf's fallback chain, every channel — and whether the agency is fit to run.
    """
    from . import preflight
    return preflight.sweep(quick=bool(a.get("quick")), notify=bool(a.get("notify")))


DISPATCH = {
    "view": cmd_view, "save_credential": cmd_save_credential,
    "delete_credential": cmd_delete_credential, "save_model": cmd_save_model,
    "delete_model": cmd_delete_model, "fetch_models": cmd_fetch_models,
    "test_model": cmd_test_model, "get_routing": cmd_get_routing,
    "save_route": cmd_save_route, "status": cmd_status,
    "save_tool": cmd_save_tool, "delete_tool": cmd_delete_tool, "shelves": cmd_shelves,
    "test_tool": cmd_test_tool, "resolve_shelf": cmd_resolve_shelf,
    "test_integration": cmd_test_integration, "preflight": cmd_preflight,
}


def check_providers():
    """Human-readable audit of every configured provider/model."""
    data = store._load()  # full (server-side CLI only)
    if not data["credentials"]:
        print("No providers configured yet. Open /setup to add one.")
        return
    for c in data["credentials"]:
        print(f"\n● {c['name']}  [{c['base_url']}]  auth={c['auth_style']}  key={auth.redact(c.get('api_key',''))}")
        note = c.get("credit_note")
        if note:
            print(f"    credit: {note}")
        for m in c.get("models", []):
            t0 = time.time()
            probe = client.probe_model(c, m)
            ms = int((time.time() - t0) * 1000)
            caps = " ".join(f"{k}:{'✓' if v is True else '✗' if v is False else '?'}"
                            for k, v in probe["capabilities"].items())
            test = probe["test"]
            verdict = "PASS" if test.get("ok") else ("?" if test.get("ok") is None else "FAIL")
            print(f"    - {m['api_model_id']:28} {m['category']:9} {m['cost_tier']:7} "
                  f"{verdict:4} {ms:5}ms  [{caps}]")
            if test.get("ok") is False:
                print(f"        cause: {test.get('cause')}")


def main():
    args = sys.argv[1:]
    if args and args[0] == "check-providers":
        check_providers()
        return
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
