"""
daily.py — the clock. Run by cron on the VPS (e.g. 07:00). Wakes the MD headless.

    0 7 * * *  cd /path/agency && /usr/bin/python3 runner/daily.py >> logs/daily.log 2>&1

It launches Claude Code non-interactively as the MD, on the model the owner chose
for the MD on the Setup page's Claude team table (providers/claude_team.py), with
the same login Claude Code already has. Then the sector leads (also scheduled)
claim and work their tasks.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
from providers import claude_team, local_cli  # noqa: E402

PROMPT = (
    "You are the Managing Director. Run one full daily cycle exactly as CLAUDE.md "
    "describes: sense the filing cabinet, read your recent hit-rate, draft today's "
    "plan grounded in evidence, get it past the critic subagent, then post the "
    "approved tasks to the board and log your decisions. Send the owner anything "
    "amber or low-confidence on Telegram. Do not execute work yourself."
)


def main():
    from providers import engines
    if engines.engine() != engines.CLAUDE_CODE:
        sys.exit("This morning runner drives Claude Code itself, and this account "
                 f"thinks with {engines.NAME.get(engines.engine(), 'no engine yet')}. "
                 "Start the day from the office instead (Run today).")
    ready, why = local_cli.available()
    if not ready:
        sys.exit("Claude Code is not ready on this machine, so the MD cannot "
                 f"start: {why}")
    model = claude_team.model_for("md")
    cmd = local_cli._launcher(local_cli.cli_path()) + [
        "-p", PROMPT,
        "--append-system-prompt", (ROOT / "CLAUDE.md").read_text(encoding="utf-8"),
        "--model", local_cli.clamp(model)]
    subprocess.run(cmd, cwd=ROOT, check=False)


if __name__ == "__main__":
    main()
