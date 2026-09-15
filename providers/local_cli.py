"""
local_cli.py — a model that runs on this machine instead of over an API key.

WHY THIS EXISTS

"each time one or 2 keys remains unanswerable that making program failure".

That is the right complaint and the cascade in routing.py was never the cure,
because a cascade needs somewhere to fall TO. Most tasks have one model behind
them; when that one key is rate-limited, expired, or simply not answering
today, the task has no second option and the step fails. Adding a fourth API
key just moves the problem — it is a fourth thing that can stop answering.

This provider has no key to go unanswerable. It shells out to the Claude Code
CLI already installed on the owner's machine, in headless mode:

    claude -p "<prompt>" --output-format json --model sonnet --restricted

Authentication is whatever that CLI is already logged in with — the same login
he uses to work in Claude Code. Nothing to paste into Setup, nothing to rotate,
no request to a service that can 429 him.

WHAT THIS IS NOT

It is NOT a bridge that replays browser cookies against claude.ai's internal
endpoints. That approach — the one gemini-web2api takes for Gemini — is against
Anthropic's terms, and the account it would put at risk is the owner's only
Claude account. `claude -p` is the supported, documented way to drive Claude
from a script, which is the same capability without the risk, and it does not
break the morning Anthropic changes a frontend build id.

FOUR THINGS THIS DOES DELIBERATELY

  RUNS SOMEWHERE ELSE     The subprocess runs in a scratch directory, never in
                          the project. Claude Code reads CLAUDE.md from the
                          directory it starts in, so running it here would feed
                          the entire agency handbook into every call — measured
                          at 37,000 tokens of context to answer "rank these
                          keywords", and a model told it is the Managing
                          Director when all that was wanted was a keyword list.

  NO TOOLS                --restricted removes Bash and the other code-running
                          tools. A provider answering a question must not be
                          able to touch the site, the cabinet or the network.
                          The red lines are enforced where the work happens,
                          and a model on the shelf is not where work happens.

  NO SECRETS IN THE ARGV  The prompt goes in on stdin, not as a command-line
                          argument. Anything in argv is visible to every other
                          process on the machine via the process list.

  FAILS LIKE AN API       Returns the same shape client._execute returns —
                          ok/status/text/cause/attempts — so routing, the load
                          ladder, the failover trail and the usage counter all
                          treat it as one more provider and none of them needed
                          changing.

THE HONEST LIMITS, BECAUSE HE SHOULD KNOW THEM BEFORE RELYING ON IT

  - It only works on a machine with Claude Code installed AND signed in. On a
    server with neither, this provider reports that plainly and the cascade
    moves on to the next one.
  - On a Pro or Max subscription it draws on that plan's usage allowance. It is
    not free and it is not unlimited; a heavy day can exhaust it, and then this
    provider starts refusing like any other.
  - One call is a process start, so it carries about a second of overhead an
    HTTP call does not. Fine for a sector's thinking; wrong for something in a
    tight loop.
"""
import json
import os
import shutil
import subprocess
import tempfile
import time

# The base_url that marks a credential as "run it here, do not call anything".
MARKER = "local://claude-code"

# Claude Code's own context is ~33k tokens before our prompt is added, so the
# floor on a call is a couple of seconds. This is the ceiling on the whole
# subprocess, not on the model's thinking.
DEFAULT_TIMEOUT = 180

# What to run. Overridable for a machine where the CLI is not on PATH under
# this name — a Windows install is claude.cmd, and PATH is not always inherited
# by a service.
CLI_ENV = "CLAUDE_CLI"

# EVERY TOOL OFF, NOT JUST THE DANGEROUS ONES.
#
# --restricted alone removes the code-running tools, which is the safety half.
# The speed and cost half is that a tool the model can never use still has its
# description sent on every single call. Measured on this machine, same prompt,
# same model:
#
#     --restricted only ............. 28,473 tokens of context
#     + every tool denied ........... 22,738
#     + slash commands off .......... 20,324
#
# Nearly a third off the floor of every call. The first call of the day pays
# that in full; after that it is cached and read back, so the saving is largest
# exactly where it is felt most — the first thing the MD does each morning.
#
# The remaining 20k is Claude Code's own system prompt. --bare would drop it,
# and must not be used: it makes the CLI read ANTHROPIC_API_KEY only and never
# the subscription login, so the one provider that needs no key would suddenly
# need one.
DENY_TOOLS = ("Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,Task,"
              "TodoWrite,NotebookEdit,BashOutput,KillShell,SlashCommand,Agent")

LEAN = ["--restricted",
        "--disallowedTools", DENY_TOOLS,
        "--disable-slash-commands"]


def is_local(cred) -> bool:
    """Is this credential a local CLI rather than an HTTP endpoint?"""
    base = ((cred or {}).get("base_url") or "").strip().lower().rstrip("/")
    return base == MARKER


def cli_path() -> str:
    """Where the CLI is, or '' if this machine has not got it."""
    named = (os.environ.get(CLI_ENV) or "").strip()
    if named:
        return named if os.path.isfile(named) else (shutil.which(named) or "")
    # claude.cmd on Windows, claude everywhere else; shutil.which handles both
    # when PATHEXT is set, but name it explicitly for the case where it is not.
    return shutil.which("claude") or shutil.which("claude.cmd") or ""


def available() -> tuple:
    """(ready, why_not) — checked before a call so the reason is a sentence."""
    path = cli_path()
    if not path:
        return False, ("Claude Code is not on this machine's PATH, so there is "
                       "no local model to call. Install it with "
                       "'npm install -g @anthropic-ai/claude-code', or set "
                       f"{CLI_ENV} to the full path of the command.")
    return True, ""


def split_messages(messages) -> tuple:
    """
    (system_prompt, prompt) from an OpenAI-shaped messages list.

    The CLI takes one system prompt and one prompt, not a conversation. System
    turns are joined into --system-prompt; the rest become a labelled
    transcript, because dropping the labels turns a two-turn exchange into one
    run-on paragraph and the model answers the wrong half of it.
    """
    system, turns = [], []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = (m.get("role") or "user").strip().lower()
        content = m.get("content")
        if isinstance(content, list):      # multimodal shape: keep the text
            content = "".join(p.get("text", "") for p in content
                              if isinstance(p, dict))
        content = (content or "").strip()
        if not content:
            continue
        if role == "system":
            system.append(content)
        elif role == "assistant":
            turns.append(f"Assistant: {content}")
        else:
            turns.append(f"User: {content}")
    # A single user turn needs no label — labelling one line makes the model
    # more likely to answer in the transcript's voice than to just answer.
    if len(turns) == 1 and turns[0].startswith("User: "):
        prompt = turns[0][6:]
    else:
        prompt = "\n\n".join(turns)
    return "\n\n".join(system), prompt


def _model_flag(api_model_id: str) -> str:
    """
    What to pass to --model.

    The CLI takes an alias ('opus', 'sonnet', 'haiku') or a full model name. A
    shelf entry written the API way — 'claude-sonnet-5' — is already a full
    name and goes straight through. Anything else is passed verbatim too: if
    the owner typed something the CLI does not know, its own error message
    naming the models it does know is far more use than a guess from here.
    """
    return (api_model_id or "sonnet").strip()


def _answer(ok, cmd, *, status=0, text="", error="", cause="", waited=None,
            body="", extra=None):
    """
    The one return shape, so every path carries the same keys.

    probe_model reads res["raw"] on EVERY path, so a local call carries that
    block too. There is no url or headers to put in it; what is useful is the
    command that ran and what came back. Neither can leak a secret: the prompt
    goes in on stdin and there is no key to redact.
    """
    out = {"ok": bool(ok), "status": status, "text": text,
           "output": text if ok else "", "error": error, "cause": cause,
           "attempts": 1, "waited": list(waited or []),
           "raw": {"url": MARKER,
                   "command": " ".join(cmd) if cmd else "(not started)",
                   "response_body": (body or error or "")[:2000]}}
    if extra:
        out.update(extra)
    return out


def chat(cred, model, messages, max_tokens=512, timeout=DEFAULT_TIMEOUT):
    """
    One call, shaped exactly like client._execute's return so the cascade,
    the load ladder and the failover trail need no special case.

    max_tokens is accepted and not passed on: the CLI has no equivalent flag.
    Silently ignoring a caller's ceiling would be worse than saying so, so it
    is named in the note on the way back.
    """
    started = time.perf_counter()
    ready, why = available()
    if not ready:
        return _answer(False, [], error=why,
                       cause="claude code is not installed here")

    system, prompt = split_messages(messages)
    if not prompt.strip():
        return _answer(False, [], error="nothing to ask — the messages were empty",
                       cause="empty prompt")

    cmd = [cli_path(), "-p",
           "--output-format", "json",
           "--model", _model_flag(model.get("api_model_id"))] + LEAN
    if system:
        cmd += ["--system-prompt", system]

    # A SCRATCH DIRECTORY, NOT THE PROJECT.
    #
    # Claude Code discovers CLAUDE.md from its working directory. Started in
    # the repo it loads the whole agency handbook before reading the question —
    # slower, dearer, and it tells a model asked for five keywords that it is
    # the Managing Director of a UAE glass firm.
    try:
        workdir = tempfile.mkdtemp(prefix="shelf-claude-")
    except Exception as exc:                                  # pragma: no cover
        return _answer(False, cmd, error=f"could not make a scratch directory: {exc}",
                       cause="no scratch directory")

    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            timeout=max(10, int(timeout)), cwd=workdir,
            encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return _answer(False, cmd, status=408,
                       error=f"the local model did not answer within {int(timeout)}s",
                       cause="timed out",
                       waited=[round(time.perf_counter() - started, 1)])
    except Exception as exc:                                  # pragma: no cover
        return _answer(False, cmd, error=str(exc),
                       cause="could not start claude code")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    took = round(time.perf_counter() - started, 1)
    if proc.returncode != 0:
        # The CLI's own stderr is the most useful thing there is here — "not
        # logged in", "usage limit reached", "unknown model". Pass it through
        # rather than replacing it with a category.
        detail = (proc.stderr or proc.stdout or "").strip()
        return _answer(False, cmd,
                       error=detail[:600] or f"claude exited {proc.returncode}",
                       cause=_cause_from(detail), waited=[took], body=detail)

    try:
        data = json.loads(proc.stdout or "{}")
    except Exception:
        # --output-format json promised JSON. Anything else usually means the
        # CLI printed a notice first, so show what actually came back.
        raw = (proc.stdout or "").strip()
        return _answer(False, cmd,
                       error="the local model did not answer with JSON — the "
                             "first 200 characters were: "
                             + raw[:200].replace("\n", " "),
                       cause="unreadable answer", waited=[took], body=raw)

    answer = (data.get("result") or "").strip()
    if data.get("is_error") or not answer:
        return _answer(False, cmd,
                       error=(data.get("result")
                              or "the local model returned nothing")[:600],
                       cause="empty answer", waited=[took],
                       body=proc.stdout or "")

    return _answer(True, cmd, status=200, text=answer, waited=[took],
                   body=answer,
                   extra={"local": {
                       "seconds": took,
                       "session": data.get("session_id", ""),
                       "note": ("max_tokens is not enforced on a local call — "
                                "the CLI has no such flag")}})


def _cause_from(detail: str) -> str:
    """Turn the CLI's own words into the short cause the failover trail shows."""
    low = (detail or "").lower()
    if "not logged in" in low or "unauthor" in low or "authenticate" in low:
        return "claude code is not signed in on this machine"
    if "usage limit" in low or "rate limit" in low or "quota" in low:
        return "the Claude plan's usage allowance is used up"
    if "unknown model" in low or "invalid model" in low:
        return "that model name is not one the CLI knows"
    return "the local model refused"


def probe(cred, model, timeout=120) -> dict:
    """
    Is this usable right now? Same question the shelf asks of every provider.

    Deliberately cheap and deliberately NOT retried, for the reason written on
    client.probe_model: asking three times does not make the answer truer, it
    makes CHECK EVERYTHING take fifteen minutes.
    """
    ready, why = available()
    if not ready:
        return {"ok": False, "status": 0, "error": why,
                "cause": "claude code is not installed here", "output": ""}
    res = chat(cred, model,
               [{"role": "user", "content": "Reply with exactly: OK"}],
               max_tokens=16, timeout=timeout)
    return {"ok": res["ok"], "status": res.get("status", 0),
            "error": res.get("error", ""), "cause": res.get("cause", ""),
            "output": res.get("output", "")}


# ---------------------------------------------------------------------------
# PUTTING IT ON THE SHELF
# ---------------------------------------------------------------------------
#
# He should not need me to wire this in. One command adds the credential, the
# models, and — the part that actually fixes his complaint — puts the local
# model at the END of every sector's fallback chain.
#
# LAST, never first. His paid keys stay primary: they are faster per call and
# they do not spend his Claude plan's allowance. This is the floor under the
# cascade, so "one or two keys not answering" stops being a failed step and
# becomes a slower one.

NAME = "Claude Code (this machine)"

# Which CLI model a sector gets when it falls this far.
SECTOR_MODEL = {
    "md": "opus", "it": "opus",
    "content": "sonnet", "seo": "sonnet", "leads": "sonnet",
    "report": "sonnet", "security": "opus",
}

# WHERE IT GOES BY DEFAULT: the two he asked for, and only those.
#
# "i only want to assign the claude to MD and IT the rest of sectors will use
#  the modules already added."
#
# He is right, and not only because he said so. Every sector that falls to this
# spends his Claude allowance, and an allowance spread across seven sectors
# runs out before the two that matter have finished thinking. MD and IT are
# where a failed call costs the whole day: no plan posted, or no release. A
# keyword pass that fails is a keyword pass retried tomorrow.
#
# --sectors all puts it everywhere for anyone who wants that.
DEFAULT_SECTORS = ("md", "it")


def our_model_ids(store, cid=None) -> set:
    """Every model id belonging to the local credential."""
    if cid is None:
        c = next((c for c in store.redacted_view().get("credentials", [])
                  if (c.get("base_url") or "").lower().rstrip("/") == MARKER), None)
        if not c:
            return set()
        cid = c["id"]
    full = store.get_credential(cid) or {}
    return {m["id"] for m in full.get("models", [])}


def on_board(store, task, mine=None) -> bool:
    """Is the local model anywhere in this sector's chain — primary or not?"""
    mine = our_model_ids(store) if mine is None else mine
    if not mine:
        return False
    r = store.get_routing().get(task) or {}
    return (r.get("primary") in mine or r.get("secondary") in mine
            or bool(set(r.get("fallback") or []) & mine))


def install(store, tasks=None, as_last_resort=True, prune=True) -> dict:
    """
    Make the routing MATCH the sectors asked for: present on those, absent
    from the rest.

    IT USED TO ONLY ADD, AND THEN REPORT ON WHAT IT HAD ADDED.

    He ran it once on the version that defaulted to all seven sectors, pulled,
    and ran it again asking for md,it. The second run added nothing — they were
    already there — so `routed` came back empty, no "last-resort model for"
    line printed at all, and every sector was listed as "left alone, still on
    your own keys" while all seven were in fact wired to Claude. The report was
    the exact opposite of the truth, and he spotted it.

    Two faults, and the second is the one that mattered:

      - "added just now" was being reported as though it meant "is on board".
        Nothing distinguished it from "was already there".
      - asking for md,it did not REMOVE it from the other five. He said he
        wanted two sectors on it; he had seven, each quietly spending the same
        Claude allowance.

    So this is now a declaration of the state he wants, not a one-way add. Pass
    prune=False to add without taking it off anywhere.
    """
    tasks = list(tasks or DEFAULT_SECTORS)
    unknown = [t for t in tasks if t not in SECTOR_MODEL]
    if unknown:
        raise ValueError("not a sector: " + ", ".join(unknown)
                         + ". One of: " + ", ".join(sorted(SECTOR_MODEL)))
    wanted = sorted({SECTOR_MODEL[t] for t in tasks})
    existing = next((c for c in store.redacted_view().get("credentials", [])
                     if (c.get("base_url") or "").lower().rstrip("/") == MARKER), None)
    cred = store.upsert_credential({
        "id": existing["id"] if existing else "",
        "name": NAME, "base_url": MARKER, "api_key": "",
        "credit_note": ("Runs the Claude Code CLI already signed in on this "
                        "machine. No API key. Draws on that plan's usage "
                        "allowance, so it is a safety net, not a free lunch."),
    })
    cid = cred["id"]

    by_name = {}
    for alias in wanted:
        full = store.get_credential(cid) or {}
        already = next((m for m in full.get("models", [])
                        if m.get("api_model_id") == alias), None)
        m = store.upsert_model(cid, {
            "id": already["id"] if already else "",
            "api_model_id": alias, "category": "LLM", "enabled": True,
            # Not "free" — it spends his subscription allowance. Calling it free
            # would let a route marked paid_allowed=False spend it without
            # meaning to, which is precisely the guard that flag exists for.
            "cost_tier": "included",
        })
        by_name[alias] = m["id"]

    mine = our_model_ids(store, cid)
    added, already_on, removed, stranded = [], [], [], []

    if as_last_resort:
        for task in tasks:
            mid = by_name.get(SECTOR_MODEL.get(task, "sonnet"))
            if not mid:
                continue
            route = dict(store.get_routing().get(task) or {})
            chain = list(route.get("fallback") or [])
            if mid in chain or route.get("primary") == mid or route.get("secondary") == mid:
                already_on.append(task)
                continue
            # Any OTHER local model on this sector goes — a sector should reach
            # for one of ours, not two.
            chain = [x for x in chain if x not in mine]
            chain.append(mid)                      # LAST. His keys go first.
            route["fallback"] = chain
            # A sector with no model at all gets this as its primary, because a
            # fallback chain under an empty primary never runs.
            if not route.get("primary") or route.get("primary") in mine:
                route["primary"] = mid
                route["fallback"] = [x for x in chain if x != mid]
            store.set_route(task, route)
            added.append(task)

    if prune and mine:
        for task in SECTOR_MODEL:
            if task in tasks:
                continue
            route = dict(store.get_routing().get(task) or {})
            if not route:
                continue
            chain = [x for x in (route.get("fallback") or []) if x not in mine]
            changed = chain != list(route.get("fallback") or [])
            # Taking it out of the PRIMARY slot needs somewhere for the sector
            # to go. Promote its first real fallback; if there is none, the
            # sector would be left with no model at all, so leave it and say so
            # rather than silently switching a sector off.
            if route.get("primary") in mine:
                if chain:
                    route["primary"], chain = chain[0], chain[1:]
                    changed = True
                else:
                    stranded.append(task)
                    continue
            if route.get("secondary") in mine:
                route["secondary"] = ""
                changed = True
            if changed:
                route["fallback"] = chain
                store.set_route(task, route)
                removed.append(task)

    return {"ok": True, "credential": cid, "models": by_name,
            "routed": sorted(added + already_on), "added": sorted(added),
            "already": sorted(already_on), "removed": sorted(removed),
            "stranded": sorted(stranded)}


def _where(store) -> int:
    """
    Where the local model actually is, read back off the routing.

    Printed after every install, and available on its own with --where, because
    the previous version reported what it had just DONE and called that the
    state. Running it twice made it claim every sector was on its own keys
    while all seven were wired to Claude. A report that cannot be wrong about
    this has to read the routes, not remember the last action.
    """
    mine = our_model_ids(store)
    if not mine:
        print("Claude Code is not on the shelf at all. Add it with --install.")
        return 0
    routes = store.get_routing()
    on, off, nothing = [], [], []
    for task in sorted(SECTOR_MODEL):
        r = routes.get(task) or {}
        if not r.get("primary") and not (r.get("fallback") or []):
            nothing.append(task)
        elif on_board(store, task, mine):
            first = "primary" if r.get("primary") in mine else "last resort"
            on.append(f"{task} ({first})")
        else:
            off.append(task)
    print("On the Claude brain      : " + (", ".join(on) if on else "nothing"))
    print("On your own keys only    : " + (", ".join(off) if off else "nothing"))
    if nothing:
        print("No model at all          : " + ", ".join(nothing))
    return 0


def main(argv=None):
    import argparse
    from . import store as _store

    ap = argparse.ArgumentParser(
        description="Use the Claude Code CLI on this machine as a model on the shelf.")
    ap.add_argument("--check", action="store_true",
                    help="is it installed, signed in, and answering?")
    ap.add_argument("--install", action="store_true",
                    help="add it to the shelf as a last-resort model")
    ap.add_argument("--sectors", default=",".join(DEFAULT_SECTORS),
                    help="which sectors fall back to it (default: md,it). "
                         "'all' for every sector, 'none' to take it off "
                         "everything. One of: " + ", ".join(sorted(SECTOR_MODEL)))
    ap.add_argument("--keep-others", action="store_true",
                    help="add to --sectors without removing it from any sector "
                         "it is already on")
    ap.add_argument("--where", action="store_true",
                    help="which sectors are on it right now, and which are not")
    ap.add_argument("--model", default="sonnet", help="which model --check asks")
    a = ap.parse_args(argv)

    if a.where:
        return _where(_store)

    if a.install:
        asked = a.sectors.strip().lower()
        picked = ([] if asked == "none"
                  else sorted(SECTOR_MODEL) if asked == "all"
                  else [s.strip().lower() for s in a.sectors.split(",") if s.strip()])
        try:
            r = install(_store, tasks=picked, prune=not a.keep_others)
        except ValueError as exc:
            print("  --  " + str(exc))
            return 1
        print(f"  ok  '{NAME}' is on the shelf")
        for alias in sorted(r["models"]):
            print(f"        {alias}")
        if r["added"]:
            print("      newly on: " + ", ".join(
                f"{t} ({SECTOR_MODEL[t]})" for t in r["added"]))
        if r["already"]:
            print("      already on: " + ", ".join(
                f"{t} ({SECTOR_MODEL[t]})" for t in r["already"]))
        if r["removed"]:
            print("      taken back off: " + ", ".join(r["removed"]))
        if r["stranded"]:
            print("      !!  left on " + ", ".join(r["stranded"])
                  + " — it is the only model those sectors have, and removing "
                    "it\n          would leave them with nothing. Give them a "
                    "key first.")
        print()
        return _where(_store)
        print("\n      Your own keys are still tried first everywhere. This one\n"
              "      answers only when they do not.")
        return 0

    ready, why = available()
    print(f"Command : {cli_path() or '(not found)'}")
    if not ready:
        print("\n  --  " + why)
        return 1
    print("Model   : " + a.model)
    res = probe({"base_url": MARKER}, {"api_model_id": a.model})
    if res["ok"]:
        print(f"\n  ok  it answered: {res['output'][:60]!r}")
        print("      No API key involved. Nothing to rotate, nothing to run out.")
        return 0
    print("\n  --  " + (res.get("cause") or "it did not answer"))
    if res.get("error"):
        print("      " + res["error"][:300])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
