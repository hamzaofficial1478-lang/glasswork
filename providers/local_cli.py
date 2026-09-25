"""
local_cli.py — how the agency talks to Claude: the Claude Code command on this
machine, signed in with the owner's own account.

Which sector uses which model is providers/claude_team.py. This file finds the
command, runs one call safely, and reports what happened.

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

  ONE RETURN SHAPE        ok/status/text/cause/attempts on every path, success
                          or failure, so the Claude team can read any result
                          the same way.

THE HONEST LIMITS, BECAUSE HE SHOULD KNOW THEM BEFORE RELYING ON IT

  - It only works on a machine with Claude Code installed AND signed in. On a
    machine with neither, every sector reports that plainly, in one sentence,
    with the command that fixes it.
  - On a Pro or Max subscription it draws on that plan's usage allowance. It is
    not free and it is not unlimited; a heavy day can exhaust it. That is what
    the backup account in claude_team.py is for.
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

# AND THEN MOST OF THE REST.
#
# Measured again when every sector moved onto Claude and the per-call floor
# started to matter ten times a day, same one-line question, Haiku:
#
#     LEAN as above ........................ 19,289 tokens of context
#     + --tools= (no built-in tools at all)   3,979
#     + our own short system prompt ...........   820
#
# Ninety-six per cent of every call was Claude Code describing tools and
# coding habits to a model that is only ever asked to write, rank or judge.
# `--tools=` is written with the equals sign on purpose: an empty "" argument
# can be dropped on its way through claude.cmd on Windows, and then --tools
# would swallow the next flag as its value.
TRIM = ["--tools=", "--strict-mcp-config", "--no-session-persistence"]
_trim_ok = True      # an older Claude Code that rejects them gets LEAN alone

# Used when the caller gave no system prompt of its own. Anything is better
# than Claude Code's default, which tells the model it is a coding agent.
PLAIN_SYSTEM = ("Answer the request directly and completely, in exactly the "
                "format it asks for. Write in British English.")


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


OFF_ENV = "CLAUDE_TEAM_OFF"


def switched_off() -> bool:
    """Set by run.py --tests: the suite must never spend the owner's allowance."""
    return str(os.environ.get(OFF_ENV, "")).strip().lower() in ("1", "true", "yes", "on")


def available() -> tuple:
    """(ready, why_not) — checked before a call so the reason is a sentence."""
    if switched_off():
        return False, (f"Claude is switched off for this run ({OFF_ENV}) — the "
                       "test suite never spends the owner's Claude allowance.")
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
    """The one-line cause shown in red, matched to the reason."""
    if OFF_ENV in why:
        return "claude is switched off for this run"
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
# NEVER ABOVE OPUS 5
# ---------------------------------------------------------------------------
#
# Which model a sector uses is the owner's choice, made on the Setup page and
# kept by providers/claude_team.py. This is only the hard stop under it.
#
# THE ALIAS IS NOT THE MODEL. Checked against the CLI by what the reply said
# actually ran: `--model opus` ran claude-opus-5-5, which is above what he
# allows. So a floating alias for a line above the ceiling, a Fable or Mythos
# name, or a later Opus typed by hand all come down to Opus 5, wherever they
# come from.

SIMPLE_MODEL = "sonnet"
# "for MD, Security we will use opus 5, not higher than this".
CEILING = "claude-opus-5"
CEILING_VERSION = (5, 0)

# Aliases that always mean "the newest of a line above the ceiling".
_ABOVE_CEILING_ALIASES = {"opus", "fable", "best", "opusplan"}


def clamp(model: str) -> str:
    """
    The model as asked for, or the ceiling if it is above it.

    Sonnet and Haiku are below it and pass. Opus at or under 5 passes. Any
    later Opus, any Fable or Mythos, and the floating aliases come down to
    Opus 5.
    """
    m = (model or SIMPLE_MODEL).strip()
    low = m.lower()
    if low in _ABOVE_CEILING_ALIASES or low.startswith(
            ("claude-fable", "fable", "claude-mythos", "mythos")):
        return CEILING
    hit = re.match(r"claude-opus-(\d+)(?:-(\d+))?", low)
    if hit:
        version = (int(hit.group(1)), int(hit.group(2) or 0))
        if version > CEILING_VERSION:
            return CEILING
    return m


def _model_flag(api_model_id: str) -> str:
    """
    What to pass to --model, never above the ceiling. Anything the CLI does not
    know is passed through so its own error — which names the models it does
    know — reaches him.
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


# Sign-in that lives in the environment rather than in a login folder. A backup
# account is a separate login FOLDER (CLAUDE_CONFIG_DIR); if any of these were
# passed down, the child would sign in with them instead and the "backup" call
# would quietly spend the main account.
_ENV_SIGN_IN = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def child_env(config_dir: str = "") -> dict:
    """The environment for one call: this one's, or a backup account's."""
    env = dict(os.environ)
    if config_dir:
        for k in _ENV_SIGN_IN:
            env.pop(k, None)
        env["CLAUDE_CONFIG_DIR"] = config_dir
    return env


def _chat_as(cred, model_name, messages, max_tokens=512, timeout=DEFAULT_TIMEOUT,
             *, config_dir="", fallback=(), effort="", exact=False):
    """
    One call to one named model. max_tokens is accepted and not passed on: the
    CLI has no equivalent flag, and that is named in the note on the way back.

    `config_dir` runs it as the backup account (its own login folder).
    `fallback` is handed to the CLI's own --fallback-model, which steps down
    inside the same call when the model is overloaded or unavailable — no
    second process start. `effort` is the CLI's --effort.
    """
    global _trim_ok
    started = time.perf_counter()
    ready, why = available()
    if not ready:
        return _answer(False, [], error=why, cause=_unavailable_cause(why))

    system, prompt = split_messages(messages)
    if not prompt.strip():
        return _answer(False, [], error="nothing to ask — the messages were empty",
                       cause="empty prompt")

    trimmed = _trim_ok
    cmd = _launcher(cli_path()) + [
        "-p", "--output-format", "json",
        "--model", _model_flag(model_name)] + LEAN + (TRIM if trimmed else [])
    fallback = [_model_flag(m) for m in (fallback or ()) if m]
    if fallback:
        cmd += ["--fallback-model", ",".join(fallback)]
    if effort:
        cmd += ["--effort", effort]
    cmd += ["--system-prompt", system or PLAIN_SYSTEM]

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
    # `exact` is the Claude team's own clock: it shares one budget across
    # every attempt, so the second attempt gets what is left, not a fresh six
    # minutes the day's step does not have.
    allowed = max(1, int(timeout or 0)) if exact else max(FLOOR_TIMEOUT,
                                                           int(timeout or 0))
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            timeout=allowed, cwd=workdir, env=child_env(config_dir),
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
        if trimmed and "unknown option" in detail.lower():
            # An older Claude Code without --tools or --strict-mcp-config.
            # Dearer per call, but it answers; asked once, then remembered.
            _trim_ok = False
            return _chat_as(cred, model_name, messages, max_tokens,
                            max(1, int(timeout or 0) - int(took)) if exact
                            else timeout, config_dir=config_dir,
                            fallback=fallback, effort=effort, exact=exact)
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
                       "ran": ran_models(data.get("modelUsage"), model_name),
                       "usage": usage_of(data),
                       "note": ("max_tokens is not enforced on a local call — "
                                "the CLI has no such flag")}})


def ran_models(model_usage, asked="") -> list:
    """
    What the CLI says actually answered — the check that caught 'opus' running
    Opus 5.5. Claude Code also uses a small model for its own housekeeping, so
    Haiku is ignored UNLESS Haiku is what was asked for; Report runs on it.
    """
    names = list(model_usage or {})
    if "haiku" in (asked or "").lower():
        return names
    return [m for m in names if "haiku" not in m.lower()] or names


def usage_of(data: dict) -> dict:
    """Tokens and the notional cost, from the CLI's own report of the call."""
    u = (data or {}).get("usage") or {}
    return {"input": int(u.get("input_tokens") or 0),
            "output": int(u.get("output_tokens") or 0),
            "cache_read": int(u.get("cache_read_input_tokens") or 0),
            "cache_write": int(u.get("cache_creation_input_tokens") or 0),
            # What the same tokens would cost on the API. On a subscription
            # nothing is billed per call; it is still the fairest way to
            # compare one sector's appetite with another's.
            "api_equivalent_usd": float(data.get("total_cost_usd") or 0.0)}


def _cause_from(detail: str) -> str:
    """Turn the CLI's own words into the short cause the failover trail shows."""
    low = (detail or "").lower()
    # Windows' words for "that file is not a program", and the placeholder's
    # own words. Both mean the npm setup step was blocked, and neither says so.
    if ("not a valid application" in low or "not a valid win32" in low
            or "native binary not installed" in low):
        return "claude code is half-installed — its setup step was blocked"
    if "not logged in" in low or "unauthor" in low or "authenticate" in low \
            or "please run /login" in low or "invalid api key" in low:
        return "claude code is not signed in on this machine"
    if "usage limit" in low or "rate limit" in low or "quota" in low \
            or "limit reached" in low or "limit will reset" in low:
        return "the Claude plan's usage allowance is used up"
    if "overloaded" in low or "529" in low or "capacity" in low:
        return "the model is overloaded right now"
    if "unknown model" in low or "invalid model" in low or "not_found" in low \
            or "not available" in low or "does not have access" in low:
        return "that model name is not one the CLI knows"
    return "the local model refused"


def check(model=SIMPLE_MODEL, timeout=120) -> dict:
    """Is it installed, signed in, and answering? One tiny real call."""
    ready, why = available()
    if not ready:
        return {"ok": False, "error": why, "cause": _unavailable_cause(why),
                "output": ""}
    res = _chat_as(None, model, [{"role": "user", "content": "Reply with exactly: OK"}],
                   16, timeout, exact=True)
    return {"ok": res["ok"], "error": res.get("error", ""),
            "cause": res.get("cause", ""), "output": res.get("output", ""),
            "ran": (res.get("local") or {}).get("ran") or []}


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(
        description="Is the Claude Code command on this machine ready to think "
                    "for the agency?")
    ap.add_argument("--check", action="store_true",
                    help="is it installed, signed in, and answering? (the default)")
    ap.add_argument("--model", default=SIMPLE_MODEL, help="which model to ask")
    a = ap.parse_args(argv)

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
    res = check(a.model)
    if res["ok"]:
        print(f"\n  ok  it answered: {res['output'][:60]!r}")
        print("      Which sector uses which model: python -m providers.claude_team")
        return 0
    print("\n  --  " + (res.get("cause") or "it did not answer"))
    if res.get("error"):
        print("      " + res["error"][:400])
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
