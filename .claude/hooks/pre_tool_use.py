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
SCRIPT_RUN = re.compile(
    r"(?:^|[;&|\n]\s*)"
    r"(?:source|bash|sh|zsh|python3?|node|perl|ruby|\.)\s+"
    r"((?:-\S+\s+)*)([^\s;&|]+)"
    r"|(?:^|[;&|\n]\s*)(\.{0,2}/[^\s;&|]+\.(?:sh|py|js|rb|pl))\b",
    re.MULTILINE)


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
    found = []
    for m in SCRIPT_RUN.finditer(command or ""):
        path = m.group(2) or m.group(3)
        if not path:
            continue
        try:
            p = Path(path)
            if p.is_file() and p.stat().st_size < 200_000:
                found.append(p.read_text(errors="ignore"))
        except Exception:
            pass
    return "\n".join(found)


def main():
    ev = json.load(sys.stdin)
    tool = ev.get("tool_name", "")
    args = ev.get("tool_input", {}) or {}
    agent = ev.get("agent", "unknown")
    blob = json.dumps(args).lower()
    if tool == "Bash":
        blob += "\n" + _script_contents(args.get("command", "")).lower()

    # RED LINE 1: never deploy/push to the live site unless you are the releaser
    if DEPLOY_WORDS.search(blob) and PROD_WORDS.search(blob):
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
