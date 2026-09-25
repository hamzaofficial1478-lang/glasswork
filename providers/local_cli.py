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
import re
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

# THE FLOOR IS GIVEN TIME TO FINISH.
#
# A route's timeout is right for an API key: a key that has not answered in
# two minutes is not going to, and the cascade should move on to the next one.
# It is wrong for this provider, because this provider IS the next one — it is
# installed last, so when it runs out of time there is nothing after it and a
# slow answer becomes no answer at all.
#
# Measured, not assumed. The Content writer's real prompt for "Aluminium Doors
# across the UAE", through this provider on sonnet: 85 seconds. The route
# allowed 120, and the first attempt in a full run went over it — every rung
# after it (shelf, the Director's model) was empty, so the page was held with
# "Content's brain did not answer". Opus, which MD and IT use, is slower than
# sonnet, and MD's plan is the one call that decides the whole day.
#
# So a local call gets at least this long, whatever the route says. His API
# keys are unaffected — they are still cut off at the route's timeout and the
# cascade still moves on quickly. This is a ceiling, not a wait: a short
# question still comes back in seconds.
FLOOR_TIMEOUT = 360

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


def _setting(name: str) -> str:
    """
    The environment first, then .env — the same order every other setting in
    this codebase uses, and the reason this needed fixing.

    "Test & detect" runs inside the Python the office server spawns, not inside
    his PowerShell. A Node service does not necessarily hand its child the PATH
    a login shell has, so `claude` can be perfectly installed and perfectly
    invisible to the process that goes looking for it. Reading .env means he
    can write the path down once on the Setup screen and every process finds
    it, whatever its PATH looks like.
    """
    got = (os.environ.get(name) or "").strip()
    if got:
        return got
    try:
        from . import integrations
        return (integrations.read_env().get(name) or "").strip()
    except Exception:
        return ""


def _likely_homes():
    """
    Where Claude Code actually installs itself, for when PATH is no help.

    Not guesswork for its own sake: the npm global bin on Windows is
    %APPDATA%\\npm, which is on an interactive user's PATH and frequently not
    on a service's. Checking the handful of real locations turns "not installed"
    into "found it" without him having to configure anything.
    """
    home = os.path.expanduser("~")
    out = []
    if os.name == "nt":
        appdata = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        out += [
            os.path.join(appdata, "npm", "claude.cmd"),
            os.path.join(appdata, "npm", "claude.exe"),
            os.path.join(local, "Programs", "claude", "claude.exe"),
            os.path.join(home, ".claude", "local", "claude.cmd"),
            os.path.join(home, ".claude", "local", "claude.exe"),
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                         "nodejs", "claude.cmd"),
        ]
    else:
        out += [
            os.path.join(home, ".claude", "local", "claude"),
            os.path.join(home, ".local", "bin", "claude"),
            "/usr/local/bin/claude",
            "/opt/homebrew/bin/claude",
            "/usr/bin/claude",
        ]
    return out


def _run_quietly(argv, timeout=20) -> str:
    """One short command's stdout, or '' — never raises, never blocks for long."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return (p.stdout or "").strip() if p.returncode == 0 else ""


def npm_bin() -> str:
    """
    npm's REAL global bin directory, asked of npm rather than guessed.

    The guessed locations cover a default install. They do not cover a custom
    prefix, a Node version manager, or a machine where npm was configured by
    somebody else years ago — and on a machine that has Node (this one runs the
    office server on it) npm is the only thing that actually knows.
    """
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        return ""
    prefix = _run_quietly(_launcher(npm) + ["prefix", "-g"])
    if not prefix:
        prefix = _run_quietly(_launcher(npm) + ["config", "get", "prefix"])
    if not prefix:
        return ""
    # npm puts binaries in <prefix> on Windows and <prefix>/bin everywhere else.
    return prefix if os.name == "nt" else os.path.join(prefix, "bin")


def toolchain() -> dict:
    """
    What this machine has, so 'not found' can say WHICH problem it is.

    Not installed at all, and installed somewhere unexpected, need completely
    different answers, and the search list alone cannot tell them apart.
    """
    node = shutil.which("node") or shutil.which("node.exe")
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    return {"node": node or "", "npm": npm or "",
            "node_version": _run_quietly(_launcher(node) + ["--version"]) if node else "",
            "npm_bin": npm_bin() if npm else ""}


def find_candidates() -> list:
    """Every place the CLI was looked for, and whether it was there — for --check."""
    rows = []
    named = _setting(CLI_ENV)
    if named:
        rows.append((f"{CLI_ENV}={named}", os.path.isfile(named)
                     or bool(shutil.which(named))))
    for name in ("claude", "claude.cmd", "claude.exe"):
        found = shutil.which(name)
        rows.append((f"PATH: {name}", bool(found)))
    for p in _likely_homes():
        rows.append((p, os.path.isfile(p)))
    for p in _npm_installed():
        rows.append((p + "   (npm's own global folder)", os.path.isfile(p)))
    return rows


def _npm_installed() -> list:
    """Candidate paths inside npm's real global bin, if npm is here at all."""
    binroot = npm_bin()
    if not binroot:
        return []
    names = ("claude.cmd", "claude.exe", "claude") if os.name == "nt" else ("claude",)
    return [os.path.join(binroot, n) for n in names]


def cli_path() -> str:
    """
    Where the CLI is, or '' if this machine really has not got it.

    Four places, in order: what he set, PATH, the usual install locations, and
    nothing. The third is the one that rescues the office server, whose PATH is
    whatever Node was started with.
    """
    named = _setting(CLI_ENV)
    if named:
        if os.path.isfile(named):
            return named
        found = shutil.which(named)
        if found:
            return found
        # He named something that is not there. Say so rather than quietly
        # searching elsewhere and running a different binary than he asked for.
        return ""
    for name in ("claude", "claude.cmd", "claude.exe"):
        found = shutil.which(name)
        if found:
            return found
    for p in _likely_homes():
        if os.path.isfile(p):
            return p
    # Last, because it costs a subprocess: ask npm where it actually puts
    # things. Covers a custom prefix or a version manager, which no list of
    # guessed paths can.
    for p in _npm_installed():
        if os.path.isfile(p):
            return p
    return ""


def _launcher(path: str) -> list:
    """
    The argv prefix that will actually start it.

    On Windows the npm installer leaves a .cmd shim, and CreateProcess — which
    is what subprocess uses — cannot execute a batch file directly. It fails
    with "%1 is not a valid Win32 application" even when the path is exactly
    right, which reads as "not installed" and is nothing of the kind. Batch
    files go through cmd.exe; everything else is run as itself.
    """
    if os.name == "nt" and path.lower().endswith((".cmd", ".bat")):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", path]
    return [path]


# ---------------------------------------------------------------------------
# INSTALLED, BUT HALF-INSTALLED
# ---------------------------------------------------------------------------
#
# What he got from `npm install -g @anthropic-ai/claude-code`:
#
#     npm warn install-scripts 1 package had install scripts blocked because
#       they are not covered by allowScripts:
#       @anthropic-ai/claude-code@2.1.281 (postinstall: node install.cjs)
#     PS> claude
#     Program 'claude.exe' failed to run: The specified executable is not a
#       valid application for this OS platform.
#
# Read off the 2.1.281 tarball itself, not guessed: the package ships
# bin/claude.exe as a FIVE-HUNDRED-BYTE TEXT FILE of `echo` lines saying "claude
# native binary not installed". The real program arrives in a second package,
# @anthropic-ai/claude-code-win32-x64, and install.cjs is the step that copies
# it over the placeholder. npm 12 blocks install scripts that are not on an
# allow-list, so the copy never ran, and Windows was asked to execute a text
# file.
#
# Both halves are on his disk. Only the copy is missing, and the fix is one
# command — the one the placeholder itself prints, once it is given a path.

# A real Claude Code binary is tens of megabytes. The placeholder is 500 bytes.
# Anything under this is not the program, whatever its name says.
PLACEHOLDER_MAX = 64 * 1024


def _npm_root() -> str:
    """npm's global node_modules folder, or ''."""
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        return ""
    return _run_quietly(_launcher(npm) + ["root", "-g"])


def half_installed() -> dict:
    """
    {'installer': path} when the package is there but its binary is still the
    placeholder, else {}.

    Checked by SIZE and CONTENT rather than by trying to run it, because
    running it is exactly the thing that fails, and the failure message is
    Windows' ("not a valid application"), which says nothing about npm.
    """
    root = _npm_root()
    if not root:
        return {}
    pkg = os.path.join(root, "@anthropic-ai", "claude-code")
    installer = os.path.join(pkg, "install.cjs")
    for name in ("claude.exe", "claude"):
        binary = os.path.join(pkg, "bin", name)
        try:
            size = os.path.getsize(binary)
        except OSError:
            continue
        if size > PLACEHOLDER_MAX:
            return {}                          # the real thing is in place
        try:
            with open(binary, "rb") as fh:
                head = fh.read(400).decode("utf-8", "replace").lower()
        except OSError:
            head = ""
        if "not installed" in head or head.startswith("echo") or size < 4096:
            return {"installer": installer if os.path.isfile(installer) else "",
                    "binary": binary, "size": size}
    return {}


def repair_command(info: dict) -> str:
    """The exact line to paste. Quoted, because his user folder has a space in it."""
    return f'node "{info.get("installer") or "install.cjs"}"'


def available() -> tuple:
    """(ready, why_not) — checked before a call so the reason is a sentence."""
    half = half_installed()
    if half:
        return False, (
            "Claude Code is installed but its last setup step never ran, so "
            "the 'claude.exe' on disk is a 500-byte placeholder, not the "
            "program. npm blocked the step because install scripts are not "
            "allowed by default any more.\n\n"
            "Run this once, then check again:\n\n"
            f"    {repair_command(half)}\n\n"
            "Or reinstall with the step allowed:\n\n"
            "    npm install -g @anthropic-ai/claude-code "
            "--allow-scripts=@anthropic-ai/claude-code")
    named = _setting(CLI_ENV)
    if named and not cli_path():
        return False, (f"{CLI_ENV} is set to '{named}' and there is nothing "
                       "there. Correct it, or clear it and let the agency find "
                       "Claude Code itself.")
    if not cli_path():
        kit = toolchain()
        if not kit["npm"]:
            return False, (
                "Claude Code is not on this machine, and neither is npm, so it "
                "cannot be installed the usual way.\n"
                "Install Node.js from nodejs.org (it brings npm with it), then "
                "run:  npm install -g @anthropic-ai/claude-code")
        return False, (
            "Claude Code is not installed on this machine.\n"
            "It was looked for on PATH, in the usual install folders, and in "
            f"npm's own global folder ({kit['npm_bin'] or 'unknown'}). Nothing "
            "there.\n\n"
            "IF YOU USE CLAUDE THROUGH THE WEBSITE OR THE DESKTOP APP, THAT IS "
            "WHY. Those are not this. This needs the command-line tool on this "
            "machine, and installing it does not change how you use the others:"
            "\n\n"
            "    npm install -g @anthropic-ai/claude-code\n"
            "    claude            (once, to sign in — then close it)\n"
            f"    python -m providers.local_cli --check\n\n"
            f"Node {kit['node_version'] or '?'} and npm are already here, so "
            "the install is a couple of minutes and needs nothing else.\n"
            "If it IS installed and you know where, put that path into .env as "
            f"{CLI_ENV}=<path> and restart the server.")
    return True, ""


def _unavailable_cause(why: str) -> str:
    """The one-line cause the Shelf screen shows in red, matched to the reason."""
    if "placeholder" in why:
        return "claude code is half-installed — its setup step was blocked"
    if "is set to" in why:
        return "CLAUDE_CLI points at nothing"
    return "claude code is not installed here"


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


# ---------------------------------------------------------------------------
# WHICH CLAUDE, CALL BY CALL
# ---------------------------------------------------------------------------
#
# "for claude we can use sonnet ... if the tasks are simple program can use
#  sonnet and if tasks are complex the program can use opus 4.8 — not the
#  higher model than opus 4.8"
#
# THE ALIAS WAS THE WRONG MODEL. Checked against the CLI on this machine, by
# what the reply said actually ran, not by what the name suggests:
#
#     --model opus             ran claude-opus-5-5     (higher than he allows)
#     --model claude-opus-4-8  ran claude-opus-4-8
#     --model sonnet           ran claude-sonnet-5
#
# 'opus' follows the newest Opus. The installer had given MD and IT exactly
# that, so both were on a model above his ceiling. The ceiling is now a full,
# pinned name, and anything above it is brought down to it wherever it comes
# from — a shelf entry typed by hand, an old install, a new alias next year.

SIMPLE_MODEL = "sonnet"
COMPLEX_MODEL = "claude-opus-4-8"
CEILING = COMPLEX_MODEL
CEILING_VERSION = (4, 8)

# The shelf entry that means "decide per call".
AUTO = "auto"

# Size signals, used alongside the explicit one (effort.demanding). About 3,000
# tokens of prompt, or an answer asked to run past ~2,000 tokens, is a task a
# person would call heavy whatever it is about. Words are deliberately NOT
# used: guessing difficulty from vocabulary is the trap this codebase has hit
# three times — a rule that fires on ordinary wording is a rule that is wrong.
LONG_PROMPT_CHARS = 12000
LONG_ANSWER_TOKENS = 2000

# Aliases that always mean "the newest of a line above the ceiling".
_ABOVE_CEILING_ALIASES = {"opus", "fable", "best", "opusplan"}


def clamp(model: str) -> str:
    """
    The model as asked for, or the ceiling if it is above it.

    Sonnet and Haiku are below it and pass. Opus 4.x at or under 4.8 passes.
    Any later Opus, any Fable, and the floating aliases for them come down to
    Opus 4.8.
    """
    m = (model or SIMPLE_MODEL).strip()
    low = m.lower()
    if low in _ABOVE_CEILING_ALIASES or low.startswith(("claude-fable", "fable")):
        return CEILING
    hit = re.match(r"claude-opus-(\d+)(?:-(\d+))?", low)
    if hit:
        version = (int(hit.group(1)), int(hit.group(2) or 0))
        if version > CEILING_VERSION:
            return CEILING
    return m


def choose(api_model_id, messages=None, max_tokens=0) -> tuple:
    """
    (model to run, why) for one call.

    A shelf entry of AUTO decides here: Opus 4.8 when the caller marked the
    task demanding (routing.deliberate does, for the MD's allocation and
    Security's clearances) or when the task is simply big; Sonnet otherwise.
    Anything else is the owner's explicit choice, honoured up to the ceiling.
    """
    asked = (api_model_id or "").strip()
    if asked.lower() != AUTO:
        final = clamp(asked or SIMPLE_MODEL)
        why = ("as set on the shelf" if final == (asked or SIMPLE_MODEL)
               else f"{asked} is above the Opus 4.8 ceiling, so {final}")
        return final, why
    from . import effort
    because = effort.why_demanding()
    if because:
        return COMPLEX_MODEL, f"demanding: {because}"
    size = sum(len(str(m.get("content") or "")) for m in (messages or [])
               if isinstance(m, dict))
    if size > LONG_PROMPT_CHARS:
        return COMPLEX_MODEL, f"a long task ({size:,} characters of prompt)"
    if int(max_tokens or 0) >= LONG_ANSWER_TOKENS:
        return COMPLEX_MODEL, f"a long answer asked for ({max_tokens} tokens)"
    return SIMPLE_MODEL, "an ordinary task"


def _model_flag(api_model_id: str) -> str:
    """
    What to pass to --model, never above the ceiling.

    The CLI takes an alias or a full model name, and anything it does not know
    is passed through so its own error — which names the models it does know —
    reaches him. The one thing never passed through is a model above Opus 4.8.
    """
    return clamp((api_model_id or SIMPLE_MODEL).strip())


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

    Which Claude runs is decided per call — see choose(). And if Opus 4.8 has
    used up the plan's allowance, the same call is put to Sonnet once rather
    than failing: "the program can shift according to the usage". A slightly
    lesser answer beats the MD having none, and the step down is written on the
    result so it is never silent.
    """
    name, why = choose((model or {}).get("api_model_id"), messages, max_tokens)
    res = _chat_as(cred, name, messages, max_tokens, timeout)
    stepped = ""
    if (not res["ok"] and name != SIMPLE_MODEL
            and "allowance" in (res.get("cause") or "")):
        stepped = (f"{name} has used up the plan's allowance, so this was put "
                   f"to {SIMPLE_MODEL} instead")
        res = _chat_as(cred, SIMPLE_MODEL, messages, max_tokens, timeout)
        name = SIMPLE_MODEL
    local = dict(res.get("local") or {})
    local.update({"model": name, "why": why})
    if stepped:
        local["stepped_down"] = stepped
    res["local"] = local
    return res


def _chat_as(cred, model_name, messages, max_tokens=512, timeout=DEFAULT_TIMEOUT):
    """
    One call to one named model. max_tokens is accepted and not passed on: the
    CLI has no equivalent flag, and that is named in the note on the way back.
    """
    started = time.perf_counter()
    ready, why = available()
    if not ready:
        return _answer(False, [], error=why, cause=_unavailable_cause(why))

    system, prompt = split_messages(messages)
    if not prompt.strip():
        return _answer(False, [], error="nothing to ask — the messages were empty",
                       cause="empty prompt")

    cmd = _launcher(cli_path()) + [
        "-p", "--output-format", "json",
        "--model", _model_flag(model_name)] + LEAN
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

    # At least FLOOR_TIMEOUT — see its note. The route's number is for keys.
    allowed = max(FLOOR_TIMEOUT, int(timeout or 0))
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            timeout=allowed, cwd=workdir,
            encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return _answer(False, cmd, status=408,
                       error=f"the local model did not answer within {allowed}s",
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
                       # What the CLI says actually ran — the check that caught
                       # 'opus' running Opus 5.5. Claude Code also uses a small
                       # model for its own housekeeping; that is not the answer.
                       "ran": [m for m in (data.get("modelUsage") or {})
                               if "haiku" not in m.lower()]
                              or list(data.get("modelUsage") or {}),
                       "note": ("max_tokens is not enforced on a local call — "
                                "the CLI has no such flag")}})


def _cause_from(detail: str) -> str:
    """Turn the CLI's own words into the short cause the failover trail shows."""
    low = (detail or "").lower()
    # Windows' words for "that file is not a program", and the placeholder's
    # own words. Both mean the npm setup step was blocked, and neither says so.
    if ("not a valid application" in low or "not a valid win32" in low
            or "native binary not installed" in low):
        return "claude code is half-installed — its setup step was blocked"
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
                "cause": _unavailable_cause(why), "output": ""}
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
# MD, IT and Security decide per call (AUTO): Sonnet for ordinary work, Opus
# 4.8 for the judgements that cost money and for big tasks. The rest are on
# Sonnet. This used to say "opus" for the first three, which on this machine
# ran Opus 5.5 — above the owner's ceiling.
SECTOR_MODEL = {
    "md": AUTO, "it": AUTO,
    "content": "sonnet", "seo": "sonnet", "leads": "sonnet",
    "report": "sonnet", "security": AUTO,
}

# Shelf entries an earlier install left that must not stay: a floating alias
# that can land above the ceiling. Their routes move to AUTO and they go.
RETIRED = ("opus",)

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

    # MOVE OFF THE RETIRED ENTRIES FIRST. His shelf has an "opus" entry from
    # the earlier install, routed to MD and IT, and that alias runs Opus 5.5.
    # Any route pointing at it is pointed at AUTO instead — the same place in
    # the chain, so his own keys still go first — and the entry is removed.
    migrated = []
    full = store.get_credential(cid) or {}
    retired = {m["id"]: m["api_model_id"] for m in full.get("models", [])
               if (m.get("api_model_id") or "").lower() in RETIRED}
    if retired:
        auto_id = by_name.get(AUTO)
        if not auto_id:
            auto_id = store.upsert_model(cid, {"api_model_id": AUTO, "category": "LLM",
                                               "enabled": True,
                                               "cost_tier": "included"})["id"]
            by_name[AUTO] = auto_id
        for task, route in list(store.get_routing().items()):
            r = dict(route)
            touched = False
            if r.get("primary") in retired:
                r["primary"], touched = auto_id, True
            if r.get("secondary") in retired:
                r["secondary"], touched = auto_id, True
            chain = [auto_id if x in retired else x for x in (r.get("fallback") or [])]
            chain = list(dict.fromkeys(chain))          # no double entry
            if chain != list(r.get("fallback") or []):
                r["fallback"], touched = chain, True
            if touched:
                store.set_route(task, r)
                migrated.append(task)
        for mid in retired:
            store.delete_model(cid, mid)

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
            "migrated_off_opus": sorted(set(migrated)),
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
        if r.get("migrated_off_opus"):
            print("      moved off 'opus' (it runs Opus 5.5, above your ceiling): "
                  + ", ".join(r["migrated_off_opus"]))
        if AUTO in r["models"]:
            print(f"      'auto' = {SIMPLE_MODEL} for ordinary work, {COMPLEX_MODEL} for "
                  "the MD's day plan, Security's\n"
                  "               clearances and big tasks. Never above Opus 4.8.")
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
        print("\n      Your own keys are still tried first everywhere. This one\n"
              "      answers only when they do not.\n")
        return _where(_store)

    # --check, and the default when nothing else is asked for.
    path = cli_path()
    print(f"Command : {path or '(not found)'}")
    if path:
        print(f"Launched: {' '.join(_launcher(path))}")
    ready, why = available()
    if not ready:
        kit = toolchain()
        print(f"Node    : {kit['node'] or '(not found)'}"
              + (f"  {kit['node_version']}" if kit["node_version"] else ""))
        print(f"npm     : {kit['npm'] or '(not found)'}")
        if kit["npm_bin"]:
            print(f"npm puts global tools in: {kit['npm_bin']}")
        print("\nLooked in:")
        for where, found in find_candidates():
            print(f"   {'FOUND' if found else '  -  '}  {where}")
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
    if "signed in" in (res.get("cause") or ""):
        print("\n      Run  claude  once in a terminal and sign in. The agency\n"
              "      uses that same login; there is nothing separate to set up.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
