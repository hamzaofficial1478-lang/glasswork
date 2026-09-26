"""
local_tests.py — run: python3 -m providers.local_tests

How the agency talks to Claude: the Claude Code command on this machine.

WHAT THIS IS GUARDING

That the command is found where Windows and npm actually put it, that a
half-finished install is named with its fix, that every call is lean (no
tools, our own short system prompt), that nothing above Opus 5 ever runs, and
that a backup account's call really runs as the backup account.

Nothing here shells out to the real CLI except one check that skips itself when
Claude Code is not installed or the suite has switched it off — so the suite is
honest on a machine that has it and never spends the owner's allowance.
"""
import os
import shutil
import sys
from pathlib import Path

from . import local_cli

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


class _Done:
    returncode, stderr = 0, ""
    stdout = ('{"result": "ok", "usage": {"input_tokens": 9, "output_tokens": 2,'
              ' "cache_read_input_tokens": 700, "cache_creation_input_tokens": 100},'
              ' "total_cost_usd": 0.001, "modelUsage": {"claude-sonnet-5": {}}}')


class _Faked:
    """Run _chat_as against a stand-in for the CLI, capturing what it was given."""
    def __init__(self, result=None):
        self.result, self.seen = result or _Done(), []

    def __enter__(self):
        self.real = (local_cli.subprocess.run, local_cli.available, local_cli.cli_path)
        def fake_run(cmd, **kw):
            self.seen.append({"cmd": cmd, **kw})
            r = self.result
            return r(cmd, kw) if callable(r) else r
        local_cli.subprocess.run = fake_run
        local_cli.available = lambda: (True, "")
        local_cli.cli_path = lambda: "/usr/local/bin/claude"
        return self

    def __exit__(self, *a):
        (local_cli.subprocess.run, local_cli.available, local_cli.cli_path) = self.real


ASK = [{"role": "user", "content": "hi"}]


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




def test_one_shape_on_every_path():
    """The Claude team reads any result the same way, success or failure."""
    print("\nEvery result carries the same keys:")
    res = local_cli._chat_as(None, "sonnet", [], timeout=5)        # empty messages
    for key in ("ok", "status", "text", "output", "error", "cause",
                "attempts", "waited", "raw"):
        check(f"'{key}' is present", key in res, True)
    check("it failed", res["ok"], False)
    check("and said why in words", "empty" in res["cause"], True)

    print("\nA missing CLI is a sentence, not a stack trace:")
    real_path, real_off = local_cli.cli_path, os.environ.pop(local_cli.OFF_ENV, None)
    local_cli.cli_path = lambda: ""
    try:
        res = local_cli._chat_as(None, "sonnet", ASK)
        check("refused", res["ok"], False)
        check("and names the fix", "install.ps1" in res["error"], True)
        check("the cause is readable", res["cause"], "claude code is not installed here")
    finally:
        local_cli.cli_path = real_path
        if real_off is not None:
            os.environ[local_cli.OFF_ENV] = real_off

    print("\nA good answer reports what ran and what it cost in tokens:")
    with _Faked():
        res = local_cli._chat_as(None, "sonnet", ASK, exact=True, timeout=30)
    check("it answered", res["ok"], True)
    check("what ran", res["local"]["ran"], ["claude-sonnet-5"])
    check("the tokens", (res["local"]["usage"]["input"], res["local"]["usage"]["cache_read"],
                         res["local"]["usage"]["output"]), (9, 700, 2))


def test_the_cli_s_own_words_become_the_cause():
    print("\nThe CLI's own complaint is what reaches the owner:")
    check("not signed in",
          local_cli._cause_from("Error: not logged in. Run `claude login`."),
          "claude code is not signed in on this machine")
    check("allowance gone",
          local_cli._cause_from("Usage limit reached for your plan"),
          "the Claude plan's usage allowance is used up")
    check("overloaded is its own thing — the model, not the account",
          local_cli._cause_from("API Error: 529 Overloaded"),
          "the model is overloaded right now")
    check("bad model name",
          local_cli._cause_from("Unknown model: gpt-9"),
          "that model name is not one the CLI knows")
    check("anything else is not dressed up",
          local_cli._cause_from("something odd"), "the local model refused")


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
        check("and points at the official installer, which needs no npm",
              "install.ps1" in why, True)
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
        check("it names the one command", "irm https://claude.ai/install.ps1 | iex" in why, True)
        check("and explains why the web app is not this",
              "WEBSITE OR THE DESKTOP APP" in why, True)

        print("\nNo npm at all is no longer a dead end — the installer needs none:")
        local_cli.shutil.which = lambda n: None
        local_cli._run_quietly = lambda argv, timeout=20: ""
        ready, why = local_cli.available()
        check("it says so", "needs neither Node nor npm" in why, True)
    finally:
        local_cli.shutil.which = real_which
        local_cli._run_quietly = real_run
        local_cli.os.path.isfile = real_isfile



def test_a_half_finished_npm_install_is_named_and_the_fix_given():
    """
    What he got from `npm install -g @anthropic-ai/claude-code` on npm 12:

        npm warn install-scripts ... @anthropic-ai/claude-code@2.1.281
          (postinstall: node install.cjs)
        PS> claude
        Program 'claude.exe' failed to run: The specified executable is not a
          valid application for this OS platform.

    Read off the 2.1.281 tarball: bin/claude.exe ships as a 500-byte TEXT FILE
    of echo lines. install.cjs copies the real 237 MB binary over it from the
    platform package, and npm 12 blocked install.cjs. Windows was asked to run
    a text file, and said so in words that point nowhere near npm.
    """
    import os
    import tempfile
    print("\nThe placeholder is recognised by size and content, not by running it:")
    root = tempfile.mkdtemp()
    pkg = os.path.join(root, "@anthropic-ai", "claude-code")
    os.makedirs(os.path.join(pkg, "bin"))
    with open(os.path.join(pkg, "install.cjs"), "w") as fh:
        fh.write("// the step npm refused to run\n")
    stub = os.path.join(pkg, "bin", "claude.exe")
    with open(stub, "w") as fh:
        fh.write('echo "Error: claude native binary not installed." >&2\n'
                 'echo "Either postinstall did not run" >&2\nexit 1\n')

    real, real_path = local_cli._npm_root, local_cli.cli_path
    local_cli._npm_root = lambda: root
    local_cli.cli_path = lambda: ""          # and no working copy anywhere else
    try:
        half = local_cli.half_installed()
        check("half-installed is detected", bool(half), True)
        check("and the installer is found", half["installer"].endswith("install.cjs"), True)
        ready, why = local_cli.available()
        check("it is not called ready", ready, False)
        check("the reason is the real one", "placeholder" in why, True)
        check("the command is quoted, for a user folder with a space in it",
              f'node "{half["installer"]}"' in why, True)
        check("and it says no other copy was found — so the fix is really needed",
              "No other copy of Claude Code was found" in why, True)
        check("the Shelf screen's red line says which problem",
              local_cli._unavailable_cause(why),
              "claude code is half-installed — its setup step was blocked")
        check("the official installer is offered as the other way",
              "install.ps1" in why, True)

        print("\n...but a working Claude Code somewhere else wins — the leftover is no veto:")
        local_cli.cli_path = lambda: r"C:\Users\x\.local\bin\claude.exe"
        ready, why = local_cli.available()
        check("ready", (ready, why), (True, ""))
        local_cli.cli_path = lambda: ""

        print("\nOnce the real binary is in place, it is no longer flagged:")
        with open(stub, "wb") as fh:
            fh.write(b"\0" * (local_cli.PLACEHOLDER_MAX + 1))
        check("the full-size binary passes", local_cli.half_installed(), {})
    finally:
        local_cli._npm_root, local_cli.cli_path = real, real_path

    print("\nWindows' own wording is translated if it ever gets that far:")
    check("'not a valid application'",
          local_cli._cause_from("The specified executable is not a valid "
                                "application for this OS platform."),
          "claude code is half-installed — its setup step was blocked")




def test_the_placeholder_is_walked_past_and_what_works_is_remembered():
    """
    "when i open the setup after pulling the newer code ... Claude Code is
     installed but its last setup step never ran" — on a machine where he had
    signed in to a Claude Code that worked. The check looked at npm's leftover
    first, found the 500-byte placeholder, and never looked any further; and
    the official installer's folder was not on its list at all.
    """
    import tempfile
    print("\nnpm's placeholder and the shim that runs it are both walked past:")
    d = Path(tempfile.mkdtemp())
    npm = d / "npm"
    stub = npm / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    stub.parent.mkdir(parents=True)
    stub.write_text('echo "Error: claude native binary not installed." >&2\nexit 1\n',
                    encoding="utf-8")
    shim = npm / "claude.cmd"
    shim.write_text('@"%dp0%\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe" %*\n',
                    encoding="utf-8")
    real_exe = d / "local-bin" / "claude.exe"
    real_exe.parent.mkdir()
    real_exe.write_bytes(b"MZ" + b"\0" * (local_cli.PLACEHOLDER_MAX + 10))
    check("the placeholder .exe is not the program", local_cli.is_placeholder(str(stub)), True)
    check("nor is the npm shim that only runs it", local_cli.is_placeholder(str(shim)), True)
    check("the real one is", local_cli.is_placeholder(str(real_exe)), False)

    saved = (local_cli._setting, local_cli.shutil.which, local_cli._likely_homes,
             local_cli._npm_installed, local_cli.remembered)
    try:
        local_cli._setting = lambda n: ""
        local_cli.remembered = lambda: ""
        local_cli.shutil.which = lambda n: str(shim) if n in ("claude", "claude.cmd") else None
        local_cli._likely_homes = lambda: [str(real_exe), str(shim)]
        local_cli._npm_installed = lambda: [str(shim)]
        check("PATH finds the npm shim first, and the search goes on to the real one",
              local_cli.cli_path(), str(real_exe))
        local_cli._likely_homes = lambda: [str(shim)]
        check("with only the leftover on the machine, nothing is found",
              local_cli.cli_path(), "")

        print("\nWhere it last worked is tried first, and re-checked every time:")
        local_cli.remembered = lambda: str(real_exe)
        check("the remembered path is used", local_cli.cli_path(), str(real_exe))
        local_cli.remembered = lambda: str(d / "gone" / "claude.exe")
        check("a remembered path that has gone is searched past, not trusted",
              local_cli.cli_path(), "")
    finally:
        (local_cli._setting, local_cli.shutil.which, local_cli._likely_homes,
         local_cli._npm_installed, local_cli.remembered) = saved

    print("\nThe official installer's folder is on the Windows list:")
    real_name = os.name
    try:
        os.name = "nt"
        homes = local_cli._likely_homes()
    finally:
        os.name = real_name
    check("~/.local/bin/claude.exe", any(h.replace("\\", "/").endswith(".local/bin/claude.exe")
                                         for h in homes), True)
    check("and it is looked at before npm's folder",
          [i for i, h in enumerate(homes) if ".local" in h][0]
          < [i for i, h in enumerate(homes) if "npm" in h][0], True)

    print("\nRemembering writes a small file of our own — never while the suite runs:")
    real_file = local_cli.REMEMBERED
    local_cli.REMEMBERED = d / "claude_code.local.json"
    was = os.environ.get("AGENCY_IGNORE_DOTENV")
    try:
        os.environ["AGENCY_IGNORE_DOTENV"] = "1"
        local_cli.remember(str(real_exe))
        check("the suite writes nothing", local_cli.REMEMBERED.exists(), False)
        os.environ.pop("AGENCY_IGNORE_DOTENV")
        real_setting = local_cli._setting
        local_cli._setting = lambda n: ""
        try:
            local_cli.remember(str(real_exe))
        finally:
            local_cli._setting = real_setting
        check("outside it, the path is kept", local_cli.remembered(), str(real_exe))
    finally:
        local_cli.REMEMBERED = real_file
        if was is None:
            os.environ.pop("AGENCY_IGNORE_DOTENV", None)
        else:
            os.environ["AGENCY_IGNORE_DOTENV"] = was


def test_the_floor_and_the_team_s_own_clock():
    """
    A call on its own gets at least the floor — Content's page on Sonnet took 85
    seconds. But the Claude team shares ONE budget across every attempt, so it
    asks for exactly what is left; a second attempt handed a fresh six minutes
    would run past the day's kill.
    """
    print("\nOn its own, a call gets at least the floor:")
    with _Faked() as f:
        local_cli._chat_as(None, "sonnet", ASK, timeout=120)
        check("a 120-second ask still gets the floor", f.seen[-1]["timeout"],
              local_cli.FLOOR_TIMEOUT)
        local_cli._chat_as(None, "sonnet", ASK, timeout=900)
        check("a longer ask keeps its own number", f.seen[-1]["timeout"], 900)
        local_cli._chat_as(None, "sonnet", ASK, timeout=70, exact=True)
        check("the team's clock is taken exactly", f.seen[-1]["timeout"], 70)


def test_never_above_opus_5():
    """
    "for MD, Security we will use opus 5, not higher than this". The alias is
    not the model: --model opus ran Opus 5.5 on this machine.
    """
    print("\nNothing above Opus 5 reaches the command line:")
    for asked, want in (("opus", "claude-opus-5"), ("claude-opus-5-5", "claude-opus-5"),
                        ("claude-opus-6", "claude-opus-5"), ("fable", "claude-opus-5"),
                        ("claude-fable-5-1", "claude-opus-5"),
                        ("claude-mythos-5-1", "claude-opus-5"),
                        ("claude-opus-5", "claude-opus-5"),
                        ("claude-opus-4-8", "claude-opus-4-8"),
                        ("claude-sonnet-5", "claude-sonnet-5"),
                        ("claude-haiku-4-5", "claude-haiku-4-5")):
        check(f"{asked} -> {want}", local_cli.clamp(asked), want)
    with _Faked() as f:
        local_cli._chat_as(None, "claude-opus-5-5", ASK, exact=True, timeout=30,
                           fallback=["claude-fable-5-1", "claude-opus-4-8"])
    cmd = f.seen[-1]["cmd"]
    check("the --model flag is clamped", cmd[cmd.index("--model") + 1], "claude-opus-5")
    check("and so is every fallback",
          cmd[cmd.index("--fallback-model") + 1], "claude-opus-5,claude-opus-4-8")


def test_every_call_is_lean():
    """
    Measured when every sector moved onto Claude, same one-line question:
    19,289 tokens of context -> 3,979 with no built-in tools -> 820 with our
    own short system prompt. Ninety-six per cent of every call was Claude Code
    describing tools to a model only ever asked to write, rank or judge.
    """
    print("\nThe command carries the lean flags:")
    for flag in ("--restricted", "--disallowedTools", "--disable-slash-commands"):
        check(flag, flag in local_cli.LEAN, True)
    check("Bash is denied by name", "Bash" in local_cli.DENY_TOOLS, True)
    check("every built-in tool is off, spelt --tools= so Windows cannot drop the empty value",
          "--tools=" in local_cli.TRIM, True)
    check("no MCP servers", "--strict-mcp-config" in local_cli.TRIM, True)
    check("--bare is NOT used — it would force an API key",
          "--bare" in local_cli.LEAN + local_cli.TRIM, False)
    with _Faked() as f:
        local_cli._chat_as(None, "sonnet", ASK, exact=True, timeout=30)
        cmd = f.seen[-1]["cmd"]
        check("a call with no system prompt gets our plain one, not Claude Code's",
              cmd[cmd.index("--system-prompt") + 1], local_cli.PLAIN_SYSTEM)
        local_cli._chat_as(None, "sonnet", [{"role": "system", "content": "Be terse."}] + ASK,
                           exact=True, timeout=30)
        cmd = f.seen[-1]["cmd"]
        check("the caller's own system prompt wins",
              cmd[cmd.index("--system-prompt") + 1], "Be terse.")
        check("the prompt goes in on stdin, never on the command line",
              (f.seen[-1]["input"], "hi" in cmd), ("hi", False))


def test_an_older_claude_code_still_answers():
    """A Claude Code too old for --tools= must cost more per call, not fail."""
    print("\nAn older CLI that rejects the trim flags is asked again without them:")
    runs = []

    def older(cmd, kw):
        runs.append(cmd)
        if "--tools=" in cmd:
            class _No:
                returncode, stdout, stderr = 1, "", "error: unknown option '--tools='"
            return _No()
        return _Done()
    was = local_cli._trim_ok
    try:
        with _Faked(older):
            res = local_cli._chat_as(None, "sonnet", ASK, exact=True, timeout=30)
        check("it answered", res["ok"], True)
        check("on the second try, without the trim flags",
              ("--tools=" in runs[0], "--tools=" in runs[1]), (True, False))
        check("and it remembers, so the next call does not try again",
              local_cli._trim_ok, False)
    finally:
        local_cli._trim_ok = was


def test_the_backup_account_runs_as_the_backup_account():
    """
    A second Claude account is a second login FOLDER. If a sign-in token in the
    environment were passed down as well, the child would use it and the
    "backup" call would quietly spend the main account.
    """
    print("\nA backup call gets its own folder and none of the main sign-in:")
    real = dict(os.environ)
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "main-account-token"
    try:
        env = local_cli.child_env("/home/x/.claude-backup")
        check("its own login folder", env["CLAUDE_CONFIG_DIR"], "/home/x/.claude-backup")
        check("and the main account's token is not passed on",
              "CLAUDE_CODE_OAUTH_TOKEN" in env, False)
        check("a main-account call keeps the environment as it is",
              local_cli.child_env("").get("CLAUDE_CODE_OAUTH_TOKEN"), "main-account-token")
        with _Faked() as f:
            local_cli._chat_as(None, "sonnet", ASK, exact=True, timeout=30,
                               config_dir="/home/x/.claude-backup", effort="high")
        check("the call is run with that environment",
              f.seen[-1]["env"]["CLAUDE_CONFIG_DIR"], "/home/x/.claude-backup")
        cmd = f.seen[-1]["cmd"]
        check("and the effort flag goes on the command", cmd[cmd.index("--effort") + 1], "high")
    finally:
        os.environ.clear()
        os.environ.update(real)


def test_the_suite_never_spends_his_allowance():
    """run.py --tests sets CLAUDE_TEAM_OFF; nothing may reach the real CLI."""
    print("\nWith the switch on, Claude is simply not available:")
    was = os.environ.get(local_cli.OFF_ENV)
    os.environ[local_cli.OFF_ENV] = "1"
    try:
        ready, why = local_cli.available()
        check("not available", ready, False)
        check("and it says why", local_cli.OFF_ENV in why, True)
        check("with a readable cause", local_cli._unavailable_cause(why),
              "claude is switched off for this run")
    finally:
        if was is None:
            os.environ.pop(local_cli.OFF_ENV, None)
        else:
            os.environ[local_cli.OFF_ENV] = was


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




def test_it_really_answers_on_this_machine():
    """
    The one check that shells out. Skipped, loudly, where Claude Code is not
    installed or the suite has switched it off.
    """
    print("\nEnd to end, against the real CLI:")
    if local_cli.switched_off():
        print("  --  skipped: Claude is switched off for this run (the suite never spends it)")
        return
    if not shutil.which("claude") and not shutil.which("claude.cmd"):
        print("  --  skipped: Claude Code is not installed on this machine")
        return
    res = local_cli._chat_as(None, "claude-haiku-4-5",
                             [{"role": "system", "content": "Answer with one word."},
                              {"role": "user", "content": "Reply with exactly: ALIVE"}],
                             timeout=180, exact=True)
    if not res["ok"] and "signed in" in (res.get("cause") or ""):
        print("  --  skipped: Claude Code is installed but not signed in here")
        return
    check("it answered", res["ok"], True)
    check("with the word asked for", "ALIVE" in res["output"].upper(), True)


def main():
    # These are all stubbed — none reaches the real CLI — but several check
    # what available() says about an install, and the suite's own off switch
    # would answer first. It is put back before the one test that could make
    # a real call.
    off = os.environ.pop(local_cli.OFF_ENV, None)
    try:
        _stubbed()
    finally:
        if off is not None:
            os.environ[local_cli.OFF_ENV] = off
    test_it_really_answers_on_this_machine()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        sys.exit(1)
    print("all local model tests passed")


def _stubbed():
    for t in (test_a_conversation_becomes_one_prompt,
              test_one_shape_on_every_path,
              test_the_cli_s_own_words_become_the_cause,
              test_it_is_found_when_PATH_is_no_help,
              test_npm_is_asked_where_it_puts_things,
              test_a_half_finished_npm_install_is_named_and_the_fix_given,
              test_the_placeholder_is_walked_past_and_what_works_is_remembered,
              test_the_floor_and_the_team_s_own_clock,
              test_never_above_opus_5,
              test_every_call_is_lean,
              test_an_older_claude_code_still_answers,
              test_the_backup_account_runs_as_the_backup_account,
              test_the_suite_never_spends_his_allowance,
              test_a_windows_cmd_shim_is_run_through_cmd_exe):
        t()


if __name__ == "__main__":
    main()
