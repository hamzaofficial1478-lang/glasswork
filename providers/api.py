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


DISPATCH = {
    "view": cmd_view, "save_credential": cmd_save_credential,
    "delete_credential": cmd_delete_credential, "save_model": cmd_save_model,
    "delete_model": cmd_delete_model, "fetch_models": cmd_fetch_models,
    "test_model": cmd_test_model, "get_routing": cmd_get_routing,
    "save_route": cmd_save_route, "status": cmd_status,
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
