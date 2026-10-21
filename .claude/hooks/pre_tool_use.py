#!/usr/bin/env python3
"""
PRE-TOOL-USE HOOK — the seatbelt. Runs BEFORE any agent tool action.
Reads the proposed action on stdin, blocks red-line moves, logs the rest.
Exit 0 = allow, exit 2 = block (Claude Code treats a non-zero blocking hook as a veto).

RED LINE 1's check independently searches for a deploy-word and a
production-word anywhere in the blob (not "deploy-word THEN production-word"
as one ordered pattern) — an ordered match let "our live site needs an
update, so rsync the new build now" straight through, because the words
just happened to sit in the other order. The production-word list uses
exact words, not a wildcard — a wildcard also used to catch ordinary English
like "produced"/"product", which would have blocked completely unrelated
work for no reason.

It also reads the content of any script a Bash command runs by path
(`bash x.sh`, `python x.py`, `./x.sh`, ...) and scans THAT too. Otherwise a
red line is trivially dodged: write the dangerous command to a file across a
couple of small, individually-innocent edits, then run the file with a
command that itself contains none of the trigger words. This closes that
specific hole; it is not a sandbox — a sufficiently indirect trick (an
interpreter reading a command from a variable, a script fetched over the
network at run time) can still get past a regex. Treat this as a tripwire,
not a wall.
"""
import sys, json, time, sqlite3, re
from pathlib import Path

DB = Path(__file__).parents[2] / "coordination" / "agency.db"
try:
    # The open website's cabinet, once accounts exist (coordination/workspace.py).
    sys.path.insert(0, str(Path(__file__).parents[2]))
    from coordination import workspace as _ws
    DB = _ws.db_path()
except Exception:
    pass  # the program's original cabinet; the log is never skipped for this

DEPLOY_WORDS = re.compile(r"\b(deploy\w*|git\s+push|rsync|s?ftp|scp)\b")
PROD_WORDS = re.compile(r"\b(prod|production|live|public_html)\b")

# Word-bounded. Unbounded, the concession words matched INSIDE ordinary ones
# (freeze, freelance, freeform), and the outbound test was a bare substring,
# so a commit note about freezing a schema next to any word containing those
# five letters read as a price offer and blocked real work. A concession is
# the standalone word, never a fragment of a longer one.
MONEY_WORDS = re.compile(r"\b(discount(s|ed|ing)?|free|complimentary|no charge|off the price|special offer)\b")
OUTBOUND_WORDS = re.compile(r"\b(send|sends|sending|sent|email|dm|message[sd]?)\b")
# A command can run a script instead of spelling the dangerous thing out.
# Three shapes used to slip through and now do not:
#   a script on its own line  - the separator class includes a newline and the
#     pattern is MULTILINE. A multi-line Bash block is what people write
#     normally, so this was not an exotic bypass, it was the common case.
#   an interpreter flag first - leading -flags are skipped, so a run written
#     as 'bash -x ship.sh' finds ship.sh instead of stopping at -x.
#   the source builtin        - now listed alongside the '.' form.
#   A SPACE IN THE PATH        - and this one was a real hole, not a theory.
#     The path was matched as [^\s;&|]+, so it stopped at the first space, and
#     a script under 'C:\Users\ammar laptops\...' or '/home/al smith/...' was
#     never opened and never scanned. The owner's own Windows username has a
#     space in it, which is how it surfaced: his hook tests reported the
#     indirection checks as passing-through rather than blocked.
#   A WINDOWS PATH             - the bare-path branch required a leading '/'
#     or './', so 'C:\build\ship.sh' matched nothing at all.
SCRIPT_EXT = r"(?:sh|bash|zsh|py|js|mjs|cjs|rb|pl|ps1|bat|cmd)"
INTERPRETER = r"(?:source|bash|sh|zsh|python3?|node|perl|ruby|pwsh|powershell|\.)"

SCRIPT_RUN = re.compile(
    # an interpreter, optional -flags, then the argument — quoted, or run on
    # until a real shell separator (NOT the first space)
    r"(?:^|[;&|\n]\s*)" + INTERPRETER + r"\s+((?:-\S+\s+)*)"
    r"(?:\"([^\"\n]+)\"|'([^'\n]+)'|([^;&|\n]+))"
    # or a path executed directly, POSIX or Windows, with or without spaces
    r"|(?:^|[;&|\n]\s*)"
    r"(?:\"([^\"\n]+\." + SCRIPT_EXT + r")\"|'([^'\n]+\." + SCRIPT_EXT + r")'"
    r"|((?:[A-Za-z]:[\\/]|\.{0,2}[\\/])[^;&|\n]+?\." + SCRIPT_EXT + r"))(?=\s|$)",
    re.MULTILINE | re.IGNORECASE)

# Inside one argument, the longest prefix that ends in a script extension. This
# is what lets an unquoted path with spaces be recovered: 'ammar laptops\a.sh
# --flag' yields 'ammar laptops\a.sh' rather than 'ammar'.
_ENDS_IN_SCRIPT = re.compile(r"^(.*?\." + SCRIPT_EXT + r")(?=\s|$)", re.IGNORECASE)


# A price concession only matters when it is heading OUT to a client. The old
# test was 'a money word ANYWHERE in the payload and an outbound word ANYWHERE',
# which across a 20KB source file is near-certain coincidence - it refused a
# file whose only sin was a variable named `free` on one line and the word
# `email` four hundred lines below it. Red line 1 was narrowed for exactly this
# reason when its wildcard started catching 'produced' and 'product': a rule
# that cries wolf on ordinary work is a rule that gets switched off.
#
# The two signals now have to sit together, the way they do in any real offer.
# Still a tripwire, not a wall - deliberately spacing them out defeats it, the
# same way a script fetched at run time defeats the scanner above.
CONCESSION_GAP = 60


# ---------------------------------------------------------------------------
# DESCRIBING A THING IS NOT DOING IT
#
# Red line 1 used to read every field of every call, prose included. So a pull
# request whose description WARNED the owner "merging restarts the server" was
# refused — for carrying the warning. That is the third time this file has had
# to learn the same lesson (see the notes above on 'produced'/'product' and on
# 'freeze'), and this time the rule was deleting safety information in order to
# satisfy a safety rule.
#
# These fields are read by people and never executed, so red line 1 skips them.
# NOTHING THAT RUNS IS SKIPPED: a Bash command is not prose, file content is not
# prose, and the scanner still reads any script a command runs by path. Red
# lines 2 and 3 keep reading the whole payload — a price concession lives in
# exactly these fields, and excluding them there would gut that rule.
PROSE_FIELDS = {"body", "message", "title", "description", "detail", "summary",
                "note", "notes", "reasoning", "text", "comment", "commit_message",
                "prompt", "why", "expectation"}


def _scannable(args):
    """
    The parts of a call that DO something, as one string.

    Walks nested structures so a prose field buried in a list is still skipped
    and a command buried in one is still read.
    """
    def walk(v, key=None):
        if isinstance(v, dict):
            return " ".join(walk(x, k) for k, x in v.items())
        if isinstance(v, (list, tuple)):
            return " ".join(walk(x, key) for x in v)
        if key in PROSE_FIELDS:
            return ""
        return f"{key}={v}" if key is not None else str(v)
    return walk(args)


# ---------------------------------------------------------------------------
# THE CALLS THAT LAND CODE ON THE LIVE BRANCH WITHOUT SAYING SO
#
# Found while the rule above was busy refusing a warning. Merging a pull request
# takes {owner, repo, pullNumber}: no watched word anywhere, nothing for a text
# scan to catch. And on a repo whose workflow runs on every push to main, that
# single call is what restarts the running agency. So the red line stopped an
# agent for describing the thing and waved through the call that does it.
#
# Words are a tripwire for shell commands. For a tool whose whole purpose is to
# put code somewhere, the tool and its target are the signal, and no wording can
# hide them. This is a tightening, not a loosening.
MERGE_TOOLS = re.compile(r"(merge_pull_request|enable_pr_auto_merge"
                         r"|actions_run_trigger|create_and_submit_pull_request_review)$", re.I)
WRITE_TOOLS = re.compile(r"(push_files|create_or_update_file|delete_file)$", re.I)
# Branch names a release hangs off. Landing on one of these is production.
LIVE_BRANCHES = {"main", "master", "prod", "production", "live", "release"}
# A shell line putting work onto one of those branches, e.g. '... origin main'.
GIT_TO_LIVE = re.compile(
    r"\bgit\s+push\b[^\n;&|]*\b(" + "|".join(sorted(LIVE_BRANCHES)) + r")\b")


def _lands_on_live(tool, args):
    """(True, why) when this call puts code where the release workflow watches."""
    name = (tool or "").strip()

    if MERGE_TOOLS.search(name):
        # A merge always lands on its base, and we cannot see the base from
        # these arguments. Unknown resolves to 'yes' here: the cost of a wrong
        # refusal is one message to the owner, the cost of a wrong pass is the
        # running agency restarted by an agent that never asked.
        return True, f"{name} lands work on the branch the release watches"

    if WRITE_TOOLS.search(name):
        # No branch given means the repository default, which is the live one.
        branch = str(args.get("branch") or "main").strip().lower()
        if branch in LIVE_BRANCHES:
            return True, f"{name} writes straight to '{branch}'"

    if name == "Bash":
        cmd = str(args.get("command") or "")
        if GIT_TO_LIVE.search(cmd):
            return True, "the command puts work onto a live branch"

    return False, ""


def concession_going_out(blob):
    """The offending text when a money word and an outbound word sit close."""
    money = [m.span() for m in MONEY_WORDS.finditer(blob)]
    if not money:
        return None
    outbound = [m.span() for m in OUTBOUND_WORDS.finditer(blob)]
    for ms, me in money:
        for os_, oe in outbound:
            if max(ms, os_) - min(me, oe) <= CONCESSION_GAP:
                return blob[max(0, min(ms, os_) - 30):max(me, oe) + 30]
    return None


# How far apart the two halves of red line 1 may sit and still be one
# instruction. Wider than CONCESSION_GAP because a real command is longer than
# a real offer: a path, a host, a couple of flags.
REACH_GAP = 140


def reaching_the_site(blob):
    """
    The offending text when an action word and a site word sit CLOSE TOGETHER.

    Red line 3 learned this lesson and red line 1 never did, so it kept firing
    on ordinary English. The second watch list holds a very common verb, and a
    commit message that used it two thousand characters below an action word was
    refused as an attempt to ship. Four false positives now, all the same shape:
    'produced'/'product', 'freeze', a warning written into a pull-request
    description, and this one.

    Two watched words at opposite ends of a long message are a coincidence. Two
    in the same breath are an instruction. The test file carries the real
    examples spelled out — it assembles them at run time, which is why they can
    sit there and not here.

    Still unordered: either word may come first, because the ordered version let
    a genuine instruction through when the words happened to fall the other way.
    Still a tripwire rather than a wall — spacing them apart on purpose defeats
    it, the same way a script fetched at run time defeats the scanner above.
    """
    acts = [m.span() for m in DEPLOY_WORDS.finditer(blob)]
    if not acts:
        return None
    sites = [m.span() for m in PROD_WORDS.finditer(blob)]
    for a_s, a_e in acts:
        for s_s, s_e in sites:
            if max(a_s, s_s) - min(a_e, s_e) <= REACH_GAP:
                return blob[max(0, min(a_s, s_s) - 30):max(a_e, s_e) + 30]
    return None


def log(agent, tool, args, decision, reason):
    try:
        con = sqlite3.connect(DB)
        con.execute("INSERT INTO audit_log(agent,tool,args,decision,reason,at) VALUES(?,?,?,?,?,?)",
                    (agent, tool, json.dumps(args)[:2000], decision, reason, int(time.time())))
        con.commit(); con.close()
    except Exception:
        pass


def _script_contents(command: str) -> str:
    """Read any script this Bash command runs by path, so the checks below see
    what it actually does, not just the one line that launched it."""
    found, seen = [], set()
    for m in SCRIPT_RUN.finditer(command or ""):
        for raw in m.groups()[1:]:          # group 1 is the -flags, skip it
            if not raw:
                continue
            for cand in _candidates(raw):
                if cand in seen:
                    continue
                seen.add(cand)
                try:
                    p = Path(cand)
                    if p.is_file() and p.stat().st_size < 200_000:
                        found.append(p.read_text(encoding="utf-8", errors="ignore"))
                except Exception:
                    pass
    return "\n".join(found)


def _candidates(arg: str):
    """
    The paths one command argument might be, longest first.

    An unquoted argument can hold a path with spaces followed by flags, and
    there is no way to tell them apart by looking — so try the whole thing, the
    prefix that ends in a script extension, and the first word. Whichever is
    actually a file on disk gets read. Trying more is the safe direction: the
    cost of a wrong guess is a stat() that finds nothing.
    """
    arg = (arg or "").strip().strip("\"'")
    if not arg:
        return []
    out = [arg]
    m = _ENDS_IN_SCRIPT.match(arg)
    if m:
        out.append(m.group(1))
    first = arg.split()[0] if arg.split() else ""
    if first:
        out.append(first)
    # de-duplicate, longest first, so the most specific match is read first
    return sorted(dict.fromkeys(out), key=len, reverse=True)


def main():
    ev = json.load(sys.stdin)
    tool = ev.get("tool_name", "")
    args = ev.get("tool_input", {}) or {}
    agent = ev.get("agent", "unknown")
    # Two views of the same call.
    #   blob        everything, prose included — red lines 2 and 3 need it,
    #               because a price concession lives in exactly those fields.
    #   action_blob only the parts that DO something — red line 1 reads this,
    #               so writing ABOUT a release is no longer treated as one.
    # A script run by path is action in both.
    scripts = _script_contents(args.get("command", "")).lower() if tool == "Bash" else ""
    blob = json.dumps(args).lower() + (("\n" + scripts) if scripts else "")
    action_blob = _scannable(args).lower() + (("\n" + scripts) if scripts else "")

    # RED LINE 1a: a call whose target IS the live branch, whatever it is called.
    lands, why = _lands_on_live(tool, args)
    if lands and agent != "it-releaser":
        log(agent, tool, args, "blocked", why)
        print("BLOCKED: only the IT Releaser may touch production.", file=sys.stderr)
        sys.exit(2)

    # RED LINE 1: never deploy/push to the live site unless you are the releaser
    caught = reaching_the_site(action_blob)
    if caught:
        if agent != "it-releaser":
            log(agent, tool, args, "blocked", "only it-releaser may reach production")
            print("BLOCKED: only the IT Releaser may touch production.", file=sys.stderr)
            sys.exit(2)

    # RED LINE 2: destructive commands anywhere near the live site
    if re.search(r"rm\s+-rf|drop\s+table|truncate\s+", blob):
        log(agent, tool, args, "blocked", "destructive command")
        print("BLOCKED: destructive command refused.", file=sys.stderr)
        sys.exit(2)

    # RED LINE 3: a price concession reaching a client needs owner approval first
    if concession_going_out(blob):
        log(agent, tool, args, "blocked", "price concession needs owner approval")
        print("BLOCKED: a discount/offer must be approved by the owner first.", file=sys.stderr)
        sys.exit(2)

    log(agent, tool, args, "allowed", "")
    sys.exit(0)


if __name__ == "__main__":
    main()
