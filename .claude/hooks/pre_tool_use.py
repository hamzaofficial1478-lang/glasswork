#!/usr/bin/env python3
"""
PRE-TOOL-USE HOOK — the seatbelt. Runs BEFORE any agent tool action.
Reads the proposed action on stdin, blocks red-line moves, logs the rest.
Exit 0 = allow, exit 2 = block (Claude Code treats a non-zero blocking hook as a veto).
"""
import sys, json, time, sqlite3, re
from pathlib import Path

DB = Path(__file__).parents[2] / "coordination" / "agency.db"

def log(agent, tool, args, decision, reason):
    try:
        con = sqlite3.connect(DB)
        con.execute("INSERT INTO audit_log(agent,tool,args,decision,reason,at) VALUES(?,?,?,?,?,?)",
                    (agent, tool, json.dumps(args)[:2000], decision, reason, int(time.time())))
        con.commit(); con.close()
    except Exception:
        pass

def main():
    ev = json.load(sys.stdin)
    tool = ev.get("tool_name", "")
    args = ev.get("tool_input", {}) or {}
    agent = ev.get("agent", "unknown")
    blob = json.dumps(args).lower()

    # RED LINE 1: never deploy/push to the live site unless you are the releaser
    if re.search(r"\b(deploy\w*|git push|rsync|s?ftp|scp)\b.*\b(prod\w*|live|public_html)\b", blob):
        if agent != "it-releaser":
            log(agent, tool, args, "blocked", "only it-releaser may reach production")
            print("BLOCKED: only the IT Releaser may touch production.", file=sys.stderr)
            sys.exit(2)

    # RED LINE 2: destructive commands anywhere near the live site
    if re.search(r"rm\s+-rf|drop\s+table|truncate\s+", blob):
        log(agent, tool, args, "blocked", "destructive command")
        print("BLOCKED: destructive command refused.", file=sys.stderr)
        sys.exit(2)

    # RED LINE 3: sending money-offer / discount language to a client -> needs owner
    if re.search(r"(discount|free|off the price|special offer)", blob) and "send" in blob:
        log(agent, tool, args, "blocked", "price concession needs owner approval")
        print("BLOCKED: a discount/offer must be approved by the owner first.", file=sys.stderr)
        sys.exit(2)

    log(agent, tool, args, "allowed", "")
    sys.exit(0)

if __name__ == "__main__":
    main()
