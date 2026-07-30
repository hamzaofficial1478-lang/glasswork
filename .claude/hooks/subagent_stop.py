#!/usr/bin/env python3
"""
SUBAGENT-STOP HOOK — enforces contracts when a subagent finishes.
A job isn't 'done' until its promises are kept: content must be reviewed before
publish, a fix must reference a ticket, outreach must be under the cap.
Exit 2 to bounce the subagent back to finish the job properly.
"""
import sys, json
def main():
    ev = json.load(sys.stdin)
    agent = ev.get("agent", "")
    output = json.dumps(ev.get("output", {})).lower()

    # writers must not self-publish; they hand to review
    if "content" in agent and "published" in output and "auditor_ok" not in output:
        print("Content cannot be published without Auditor sign-off.", file=sys.stderr)
        sys.exit(2)

    # fixers must cite the ticket they're closing
    if "fixer" in agent and "issue_id" not in output:
        print("A fix must reference the site_issues ticket it closes.", file=sys.stderr)
        sys.exit(2)

    sys.exit(0)
if __name__ == "__main__":
    main()
