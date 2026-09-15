"""
local_tests.py — run: python3 -m providers.local_tests

The model that runs on this machine instead of over an API key.

WHAT THIS IS GUARDING

"each time one or 2 keys remains unanswerable that making program failure."

A cascade needs somewhere to fall to. These checks are about the floor under
it: that a dead key lands on the local model instead of failing the step, that
the local model is LAST so his paid keys are still tried first, and that it
never pretends to be something it is not.

Nothing here shells out to the real CLI except one check that skips itself when
Claude Code is not installed — so the suite is honest on a machine that has it
and still passes on one that does not.
"""
import shutil
import sys

from . import client, local_cli, routing
from . import store as _store

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


CRED = {"id": "c-local", "name": local_cli.NAME,
        "base_url": local_cli.MARKER, "api_key": ""}
MODEL = {"id": "m-local", "api_model_id": "sonnet", "category": "LLM",
         "enabled": True, "cost_tier": "included"}


def test_it_is_recognised_without_being_called():
    print("\nA local credential is told apart by its base URL alone:")
    check("the marker is local", local_cli.is_local(CRED), True)
    check("a real endpoint is not",
          local_cli.is_local({"base_url": "https://api.openai.com/v1"}), False)
    check("a trailing slash makes no difference",
          local_cli.is_local({"base_url": local_cli.MARKER + "/"}), True)
    check("case makes no difference",
          local_cli.is_local({"base_url": local_cli.MARKER.upper()}), True)
    check("an empty credential is not local", local_cli.is_local({}), False)
    check("and neither is nothing at all", local_cli.is_local(None), False)


def test_a_conversation_becomes_one_prompt():
    """
    The CLI takes one system prompt and one prompt, not a messages array. The
    labels matter: strip them and a two-turn exchange reads as one run-on
    paragraph, and the model answers the wrong half of it.
    """
    print("\nSystem turns are separated from the conversation:")
    sysmsg, prompt = local_cli.split_messages([
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "First answer"},
        {"role": "user", "content": "Second question"}])
    check("the system prompt is lifted out", sysmsg, "You are terse.")
    check("the turns keep their labels",
          prompt, "User: First question\n\nAssistant: First answer\n\nUser: Second question")

    print("\n...but a single question is asked plainly, not as a transcript:")
    sysmsg, prompt = local_cli.split_messages(
        [{"role": "user", "content": "What is 2+2?"}])
    check("no label on a lone question", prompt, "What is 2+2?")
    check("and no system prompt invented", sysmsg, "")

    print("\nTwo system turns are joined, not the last one kept:")
    sysmsg, _ = local_cli.split_messages([
        {"role": "system", "content": "Be terse."},
        {"role": "system", "content": "Answer in British English."},
        {"role": "user", "content": "hello"}])
    check("both survive", sysmsg, "Be terse.\n\nAnswer in British English.")

    print("\nThe multimodal shape does not crash it — the text is kept:")
    _, prompt = local_cli.split_messages([{"role": "user", "content": [
        {"type": "text", "text": "what colour is this?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}])
    check("the words come through", prompt, "what colour is this?")

    print("\nEmpty and malformed messages are dropped, not passed on:")
    _, prompt = local_cli.split_messages(
        [None, {"role": "user", "content": ""}, "nonsense",
         {"role": "user", "content": "  real  "}])
    check("only the real one survives", prompt, "real")


def test_it_fails_like_an_api_so_the_cascade_understands_it():
    """
    routing.call_task, load.py and the failover trail all read the same keys off
    a result. A local call that returned a different shape would break the
    cascade at the moment it is most needed — when something else has failed.
    """
    print("\nEvery failure carries the keys the cascade reads:")
    res = local_cli.chat(CRED, MODEL, [], timeout=5)        # empty messages
    for key in ("ok", "status", "text", "output", "error", "cause",
                "attempts", "waited", "raw"):
        check(f"'{key}' is present", key in res, True)
    check("it failed", res["ok"], False)
    check("and said why in words", "empty" in res["cause"], True)

    print("\nprobe_model reads res['raw'] on every path, so it is always there:")
    check("raw has a url", res["raw"]["url"], local_cli.MARKER)
    check("and a body", "response_body" in res["raw"], True)

    print("\nA missing CLI is a sentence, not a stack trace:")
    real = local_cli.cli_path
    local_cli.cli_path = lambda: ""
    try:
        res = local_cli.chat(CRED, MODEL, [{"role": "user", "content": "hi"}])
        check("refused", res["ok"], False)
        check("and names the fix", "npm install" in res["error"], True)
        check("the cause is readable",
              res["cause"], "claude code is not installed here")
    finally:
        local_cli.cli_path = real


def test_the_provider_words_are_the_cli_s_own():
    print("\nThe CLI's own complaint is what reaches the failover trail:")
    check("not signed in",
          local_cli._cause_from("Error: not logged in. Run `claude login`."),
          "claude code is not signed in on this machine")
    check("allowance gone",
          local_cli._cause_from("Usage limit reached for your plan"),
          "the Claude plan's usage allowance is used up")
    check("bad model name",
          local_cli._cause_from("Unknown model: gpt-9"),
          "that model name is not one the CLI knows")
    check("anything else is not dressed up",
          local_cli._cause_from("something odd"), "the local model refused")


def test_a_dead_key_falls_through_to_it():
    """
    THE WHOLE POINT. A key that will not answer must cost a slower step, not a
    failed one — and the local model must be the thing underneath.
    """
    print("\nA key that cannot answer falls through to the local model:")
    calls = {"n": 0}

    def fake_chat(cred, model, messages, max_tokens=512, timeout=180, extra=None,
                  tries=3):
        calls["n"] += 1
        if local_cli.is_local(cred):
            return {"ok": True, "status": 200, "text": "Dubai", "output": "Dubai",
                    "attempts": 1, "waited": [1.2], "raw": {"url": local_cli.MARKER}}
        return {"ok": False, "status": 429, "text": "", "output": "",
                "cause": "429 — rate limited or out of credit.",
                "attempts": 3, "waited": [1.0, 2.0], "raw": {"url": "x"}}

    real = client.chat
    client.chat = fake_chat
    try:
        with _store.isolated():
            dead = _store.upsert_credential({"name": "Rate-limited key",
                                             "base_url": "https://api.example.com/v1",
                                             "api_key": "sk-x"})
            _store.upsert_model(dead["id"], {"api_model_id": "their-model",
                                             "category": "LLM", "enabled": True,
                                             "cost_tier": "free"})
            # The dead key is his primary FIRST, then the local model is
            # installed under it. Order matters: install only takes the primary
            # slot for a sector that has nothing, so a sector with a real key
            # keeps it and gets this appended.
            dead_m = _store.get_credential(dead["id"])["models"][0]["id"]
            _store.set_route("md", {"primary": dead_m, "enabled": True})
            local_cli.install(_store, tasks=["md"])

            r = routing.call_task("md", [{"role": "user", "content": "one emirate"}])
            check("the task still succeeded", r["ok"], True)
            check("on the local model", r["used"]["credential"], local_cli.NAME)
            check("and the dead key is named in the trail",
                  r["failovers"][0]["cause"].startswith("429"), True)
            check("both were tried", calls["n"], 2)
    finally:
        client.chat = real


def test_his_own_keys_are_still_tried_first():
    """
    A safety net that jumped the queue would spend his Claude allowance on work
    a key he already pays for could have done — and it is slower per call.
    """
    print("\nInstalling puts it LAST, never first:")
    with _store.isolated():
        paid = _store.upsert_credential({"name": "His paid key",
                                         "base_url": "https://api.example.com/v1",
                                         "api_key": "sk-x"})
        _store.upsert_model(paid["id"], {"api_model_id": "good-model",
                                         "category": "LLM", "enabled": True,
                                         "cost_tier": "paid"})
        paid_m = _store.get_credential(paid["id"])["models"][0]["id"]
        for task in local_cli.SECTOR_MODEL:
            _store.set_route(task, {"primary": paid_m, "enabled": True})

        local_cli.install(_store, tasks=sorted(local_cli.SECTOR_MODEL))
        routes = _store.get_routing()
        check("his key is still primary everywhere",
              {t: routes[t]["primary"] for t in local_cli.SECTOR_MODEL},
              {t: paid_m for t in local_cli.SECTOR_MODEL})
        check("and the local model is the last fallback, in every sector",
              all(routes[t]["fallback"] and routes[t]["fallback"][-1] != paid_m
                  for t in local_cli.SECTOR_MODEL), True)

        print("\n  ...and the two he named get the strongest model:")
        cid = [c for c in _store.redacted_view()["credentials"]
               if c["base_url"] == local_cli.MARKER][0]["id"]
        names = {m["id"]: m["api_model_id"]
                 for m in _store.get_credential(cid)["models"]}
        check("MD", names[routes["md"]["fallback"][-1]], "opus")
        check("IT", names[routes["it"]["fallback"][-1]], "opus")

        print("\nRunning it twice changes nothing:")
        before = _store.get_routing()["md"]["fallback"]
        local_cli.install(_store, tasks=sorted(local_cli.SECTOR_MODEL))
        check("no duplicate in the chain",
              _store.get_routing()["md"]["fallback"], before)
        check("and no second credential",
              len([c for c in _store.redacted_view()["credentials"]
                   if c["base_url"] == local_cli.MARKER]), 1)


def test_by_default_it_goes_to_md_and_it_and_nowhere_else():
    """
    "i only want to assign the claude to MD and IT the rest of sectors will use
     the modules already added."

    Right, and not only because he said so: every sector that falls to this
    spends the same Claude allowance, and spread across seven it runs out
    before the two that matter have finished. MD and IT are where a failed call
    costs the whole day — no plan posted, or no release.
    """
    print("\nOut of the box it touches two sectors:")
    with _store.isolated():
        r = local_cli.install(_store)
        check("exactly MD and IT", sorted(r["routed"]), ["it", "md"])
        routes = _store.get_routing()
        others = [s for s in local_cli.SECTOR_MODEL if s not in ("md", "it")]
        check("every other sector is left completely alone",
              [s for s in others if routes.get(s)], [])

        print("  ...and only the model those two need is put on the shelf:")
        check("no sonnet entry nobody asked for", sorted(r["models"]), ["opus"])

    print("\nHe can widen it if he wants to:")
    with _store.isolated():
        r = local_cli.install(_store, tasks=["md", "it", "security"])
        check("three sectors", sorted(r["routed"]), ["it", "md", "security"])

    print("\nA typo names the sectors rather than silently doing nothing:")
    with _store.isolated():
        try:
            local_cli.install(_store, tasks=["md", "marketing"])
            check("it refused", False, True)
        except ValueError as exc:
            check("it refused", True, True)
            check("and named the bad one", "marketing" in str(exc), True)
            check("and listed the real ones", "leads" in str(exc), True)


def test_running_it_again_with_fewer_sectors_takes_it_off_the_rest():
    """
    HE CAUGHT THIS ONE FROM THE OUTPUT ALONE.

        python -m providers.local_cli --install
          ok  added 'Claude Code (this machine)' to the shelf
                opus
              left alone, still on your own keys: content, it, leads, md, ...

    Every sector listed as being on his own keys, including the two he had just
    asked to be on Claude — and no "last-resort model for" line at all. He had
    run the earlier version, which defaulted to all seven; the second run added
    nothing because they were already there, and the report was built from what
    it had just DONE rather than from the routing.

    Two faults. The report described an action as though it were a state. And
    asking for md,it did not take it off the other five, so he had seven
    sectors quietly spending one Claude allowance when he had asked for two.
    """
    print("\nHis exact sequence: all seven, then 'only MD and IT':")
    with _store.isolated():
        paid = _store.upsert_credential({"name": "His key",
                                         "base_url": "https://api.example.com/v1",
                                         "api_key": "sk-x"})
        _store.upsert_model(paid["id"], {"api_model_id": "his-model",
                                         "category": "LLM", "enabled": True,
                                         "cost_tier": "paid"})
        his = _store.get_credential(paid["id"])["models"][0]["id"]
        for s in local_cli.SECTOR_MODEL:
            _store.set_route(s, {"primary": his, "enabled": True})

        local_cli.install(_store, tasks=sorted(local_cli.SECTOR_MODEL))
        check("all seven on it to begin with",
              all(local_cli.on_board(_store, s) for s in local_cli.SECTOR_MODEL),
              True)

        r = local_cli.install(_store)           # the default: md, it
        check("MD and IT are on it", sorted(r["routed"]), ["it", "md"])
        check("and they are reported as already on, not newly added",
              sorted(r["already"]), ["it", "md"])
        check("the other five were taken back off",
              r["removed"], ["content", "leads", "report", "security", "seo"])

        print("\n  ...and the ROUTING agrees, which is the thing that matters:")
        on = sorted(s for s in local_cli.SECTOR_MODEL
                    if local_cli.on_board(_store, s))
        check("exactly two sectors on the Claude brain", on, ["it", "md"])
        check("his own key is still primary in all seven",
              all((_store.get_routing()[s]["primary"] == his)
                  for s in local_cli.SECTOR_MODEL), True)


def test_it_will_not_switch_a_sector_off_to_tidy_up():
    """
    Pruning must not leave a sector with no model at all. A sector that had
    nothing got the local model as its PRIMARY; taking that away because it is
    no longer in --sectors would silently stop the sector working.
    """
    print("\nA sector whose only model is this one keeps it, loudly:")
    with _store.isolated():
        local_cli.install(_store, tasks=sorted(local_cli.SECTOR_MODEL))
        r = local_cli.install(_store)           # narrow to md, it
        check("the rest are named as stranded, not silently emptied",
              r["stranded"], ["content", "leads", "report", "security", "seo"])
        check("and none of them was actually emptied",
              all((_store.get_routing()[s] or {}).get("primary")
                  for s in r["stranded"]), True)

    print("\nBut with a real key behind them, pruning promotes that key:")
    with _store.isolated():
        paid = _store.upsert_credential({"name": "His key",
                                         "base_url": "https://api.example.com/v1",
                                         "api_key": "sk-x"})
        _store.upsert_model(paid["id"], {"api_model_id": "his-model",
                                         "category": "LLM", "enabled": True})
        his = _store.get_credential(paid["id"])["models"][0]["id"]
        # the awkward shape: local model primary, his key only a fallback
        local_cli.install(_store, tasks=["seo"])
        route = dict(_store.get_routing()["seo"])
        route["fallback"] = [his]
        _store.set_route("seo", route)

        r = local_cli.install(_store, tasks=["md"])
        check("seo came off it", "seo" in r["removed"], True)
        check("nothing was stranded", r["stranded"], [])
        check("and his key was promoted into the empty primary slot",
              _store.get_routing()["seo"]["primary"], his)


def test_keep_others_adds_without_taking_anything_away():
    print("\n--keep-others widens without narrowing:")
    with _store.isolated():
        local_cli.install(_store, tasks=["md", "it"])
        local_cli.install(_store, tasks=["seo"], prune=False)
        on = sorted(s for s in local_cli.SECTOR_MODEL
                    if local_cli.on_board(_store, s))
        check("all three are on it", on, ["it", "md", "seo"])


def test_it_is_found_when_PATH_is_no_help():
    """
    HIS "Test & detect" SAID IT WAS NOT INSTALLED. IT WAS.

        X no HTTP response (couldn't connect) — claude code is not installed here
        "command": "(not started)"

    He runs Claude Code every day. The lookup was shutil.which() and nothing
    else, and the process doing the looking is the Python the office server
    spawns — a Node service whose PATH is whatever it was started with, not the
    one his PowerShell has. On Windows the npm shim lives in %APPDATA%\\npm,
    which is on an interactive user's PATH and often not on a service's.

    So: what he set, then PATH, then the real install locations.
    """
    print("\nThe search order is his setting, then PATH, then the usual places:")
    import os
    real_setting, real_which, real_isfile, real_run = (
        local_cli._setting, local_cli.shutil.which, local_cli.os.path.isfile,
        local_cli._run_quietly)
    try:
        # 1. what he set wins over everything
        local_cli._setting = lambda n: "/somewhere/claude" if n == local_cli.CLI_ENV else ""
        local_cli.os.path.isfile = lambda p: p == "/somewhere/claude"
        local_cli.shutil.which = lambda n: "/on/path/claude"
        check("his setting beats PATH", local_cli.cli_path(), "/somewhere/claude")

        # 2. a setting pointing at nothing is an error, not a reason to guess
        local_cli.os.path.isfile = lambda p: False
        local_cli.shutil.which = lambda n: None
        check("a wrong setting finds nothing", local_cli.cli_path(), "")
        ready, why = local_cli.available()
        check("and says so by name", "is set to" in why, True)

        # 3. nothing set: PATH is used
        local_cli._setting = lambda n: ""
        local_cli.shutil.which = lambda n: "/on/path/claude" if n == "claude" else None
        check("PATH is next", local_cli.cli_path(), "/on/path/claude")

        # 4. PATH empty: the usual install locations rescue it
        local_cli.shutil.which = lambda n: None
        homes = local_cli._likely_homes()
        local_cli.os.path.isfile = lambda p: p == homes[0]
        check("the install locations are the safety net",
              local_cli.cli_path(), homes[0])

        # 5. genuinely absent. With which() stubbed to None there is no npm
        #    either, so this is the "you have not got Node" answer — checked
        #    properly in test_npm_is_asked_where_it_puts_things.
        local_cli.os.path.isfile = lambda p: False
        local_cli._run_quietly = lambda argv, timeout=20: ""
        check("and absent is still absent", local_cli.cli_path(), "")
        ready, why = local_cli.available()
        check("it does not pretend to be ready", ready, False)
        check("and points at the thing that has to come first",
              "nodejs.org" in why, True)
    finally:
        local_cli._setting = real_setting
        local_cli.shutil.which = real_which
        local_cli.os.path.isfile = real_isfile
        local_cli._run_quietly = real_run

    print("\nEvery place looked in is listed, so the answer is checkable:")
    rows = local_cli.find_candidates()
    check("PATH is named", any("PATH:" in w for w, _ in rows), True)
    check("and so are the install locations", len(rows) > 3, True)


def test_npm_is_asked_where_it_puts_things():
    """
    His --check came back empty on every guessed path AND on PATH:

        -    C:\\Users\\ammar laptops\\AppData\\Roaming\\npm\\claude.cmd
        -    C:\\Program Files\\nodejs\\claude.cmd
        ...

    A list of guessed folders covers a default install and nothing else — not a
    custom npm prefix, not a Node version manager. On a machine that has Node
    (his runs the office server on it) npm is the only thing that actually
    knows where it puts global tools, so it gets asked.

    And the answer has to tell two situations apart, because they need opposite
    advice: installed somewhere unexpected, versus not installed at all.
    """
    print("\nnpm's own global folder is searched, not just the guesses:")
    real_which, real_run, real_isfile = (
        local_cli.shutil.which, local_cli._run_quietly, local_cli.os.path.isfile)
    try:
        local_cli.shutil.which = lambda n: "/usr/bin/npm" if n == "npm" else None
        local_cli._run_quietly = lambda argv, timeout=20: "/custom/prefix"
        found = "/custom/prefix/bin/claude"
        local_cli.os.path.isfile = lambda p: p == found
        check("it is found where npm actually put it", local_cli.cli_path(), found)
        check("and the folder is named in the search list",
              any("npm's own global folder" in w for w, _ in
                  local_cli.find_candidates()), True)

        print("\n'not installed' says so, and does not send him hunting:")
        local_cli.os.path.isfile = lambda p: False
        ready, why = local_cli.available()
        check("it is plain about it", "not installed on this machine" in why, True)
        check("it names the one command", "npm install -g" in why, True)
        check("and explains why the web app is not this",
              "WEBSITE OR THE DESKTOP APP" in why, True)

        print("\nNo npm at all is a different problem and says so:")
        local_cli.shutil.which = lambda n: None
        local_cli._run_quietly = lambda argv, timeout=20: ""
        ready, why = local_cli.available()
        check("Node comes first", "nodejs.org" in why, True)
    finally:
        local_cli.shutil.which = real_which
        local_cli._run_quietly = real_run
        local_cli.os.path.isfile = real_isfile


def test_a_windows_cmd_shim_is_run_through_cmd_exe():
    """
    npm on Windows installs a .cmd shim. CreateProcess — which is what
    subprocess uses — cannot execute a batch file directly: it fails with
    "%1 is not a valid Win32 application" even when the path is exactly right.
    That reads as "not installed" and is nothing of the kind.
    """
    print("\nA .cmd shim goes through cmd.exe; anything else runs as itself:")
    import os
    real = os.name
    try:
        os.name = "nt"
        launched = local_cli._launcher(r"C:\Users\x\AppData\Roaming\npm\claude.cmd")
        check("cmd.exe is the launcher", launched[0].lower().endswith("cmd.exe"), True)
        check("with /c", launched[1], "/c")
        check("and the shim last", launched[2].endswith("claude.cmd"), True)
        check("a .exe is run directly",
              local_cli._launcher(r"C:\claude.exe"), [r"C:\claude.exe"])
        os.name = "posix"
        check("and so is a unix binary",
              local_cli._launcher("/usr/local/bin/claude"), ["/usr/local/bin/claude"])
    finally:
        os.name = real


def test_every_tool_is_off_not_just_the_dangerous_ones():
    """
    --restricted removes the code-running tools, which is the safety half. The
    speed half is that a tool the model can never use still ships its
    description on every call. Measured on this machine, same prompt, same
    model: 28,473 tokens -> 22,738 with tools denied -> 20,324 with slash
    commands off. Nearly a third off the floor of every call.
    """
    print("\nThe command carries the lean flags, not just --restricted:")
    for flag in ("--restricted", "--disallowedTools", "--disable-slash-commands"):
        check(flag, flag in local_cli.LEAN, True)
    check("Bash is denied by name", "Bash" in local_cli.DENY_TOOLS, True)
    check("and so is WebFetch", "WebFetch" in local_cli.DENY_TOOLS, True)

    print("\n--bare is NOT used, and must not be:")
    check("it would force an API key and break the whole point",
          "--bare" in local_cli.LEAN, False)


def test_a_sector_with_nothing_gets_it_as_its_only_model():
    """A fallback chain under an empty primary never runs."""
    print("\nA sector with no model at all gets this as its primary:")
    with _store.isolated():
        local_cli.install(_store)                 # MD and IT, with no keys set
        route = _store.get_routing()["md"]
        check("it has a primary now", bool(route["primary"]), True)
        check("and is not left only in the fallback list",
              route["primary"] in (route["fallback"] or []), False)


def test_it_never_claims_to_see_pictures():
    """
    The image probe puts a data: URI in the message content. split_messages
    keeps the text and drops the picture, so the model would answer from the
    wording alone and the shelf would record vision it has not got — and
    something later would route an image at it.
    """
    print("\nVision is reported as absent, not guessed:")
    real = client.chat
    client.chat = lambda *a, **k: {"ok": True, "status": 200, "text": "ok",
                                   "output": "ok", "raw": {}, "attempts": 1,
                                   "waited": []}
    try:
        out = client.probe_model(CRED, MODEL, timeout=5)
        check("chat works", out["capabilities"]["chat"], True)
        check("multimodal is False, not None and not True",
              out["capabilities"]["multimodal"], False)
    finally:
        client.chat = real


def test_it_really_answers_on_this_machine():
    """
    The one check that shells out. Skipped, loudly, where Claude Code is not
    installed — a suite that silently passes on a machine that cannot run the
    thing is worse than one that says it did not try.
    """
    print("\nEnd to end, against the real CLI:")
    if not shutil.which("claude") and not shutil.which("claude.cmd"):
        print("  --  skipped: Claude Code is not installed on this machine")
        return
    res = local_cli.chat(CRED, {"api_model_id": "haiku"},
                         [{"role": "system", "content": "Answer with one word."},
                          {"role": "user", "content": "Reply with exactly: ALIVE"}],
                         timeout=180)
    if not res["ok"] and "signed in" in (res.get("cause") or ""):
        print("  --  skipped: Claude Code is installed but not signed in here")
        return
    check("it answered", res["ok"], True)
    check("with the word asked for", "ALIVE" in res["output"].upper(), True)
    check("and reported how long it took", bool(res["waited"]), True)


def main():
    test_it_is_recognised_without_being_called()
    test_a_conversation_becomes_one_prompt()
    test_it_fails_like_an_api_so_the_cascade_understands_it()
    test_the_provider_words_are_the_cli_s_own()
    test_a_dead_key_falls_through_to_it()
    test_his_own_keys_are_still_tried_first()
    test_by_default_it_goes_to_md_and_it_and_nowhere_else()
    test_running_it_again_with_fewer_sectors_takes_it_off_the_rest()
    test_it_will_not_switch_a_sector_off_to_tidy_up()
    test_keep_others_adds_without_taking_anything_away()
    test_it_is_found_when_PATH_is_no_help()
    test_npm_is_asked_where_it_puts_things()
    test_a_windows_cmd_shim_is_run_through_cmd_exe()
    test_every_tool_is_off_not_just_the_dangerous_ones()
    test_a_sector_with_nothing_gets_it_as_its_only_model()
    test_it_never_claims_to_see_pictures()
    test_it_really_answers_on_this_machine()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        sys.exit(1)
    print("all local model tests passed")


if __name__ == "__main__":
    main()
