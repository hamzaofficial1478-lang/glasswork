"""
daily.py — the clock. Run by cron on the VPS (e.g. 07:00). Wakes the MD headless.

    0 7 * * *  cd /path/agency && /usr/bin/python3 runner/daily.py >> logs/daily.log 2>&1

It launches Claude Code non-interactively with the MD, who runs the daily rhythm
from CLAUDE.md: sense -> reason -> critic -> delegate. Then the sector leads (also
scheduled, or spawned as an Agent Team) claim and work their tasks.
"""
import os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "coordination"))
import ai_keys  # noqa: E402  (resolves the MD's provider/model/key from .env)

PROMPT = (
    "You are the Managing Director. Run one full daily cycle exactly as CLAUDE.md "
    "describes: sense the filing cabinet, read your recent hit-rate, draft today's "
    "plan grounded in evidence, get it past the critic subagent, then post the "
    "approved tasks to the board and log your decisions. Send the owner anything "
    "amber or low-confidence on Telegram. Do not execute work yourself."
)

def main():
    # the MD (and Critic) run on the MD key, falling back to the FALLBACK key
    ai_env, slot = ai_keys.as_claude_env("managing-director")
    if not slot.get("api_key"):
        sys.exit("No MD key set. Open /setup and fill the MD slot (or a Fallback key).")
    env = {**os.environ, **ai_env}

    cmd = ["claude", "-p", PROMPT, "--append-system-prompt", (ROOT / "CLAUDE.md").read_text()]
    if slot.get("model"):
        cmd += ["--model", slot["model"]]

    # headless Claude Code run, pinned to the MD's context and provider
    subprocess.run(cmd, cwd=ROOT, check=False, env=env)

if __name__ == "__main__":
    main()
