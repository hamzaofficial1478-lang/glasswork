"""
daily.py — the clock. Run by cron on the VPS (e.g. 07:00). Wakes the MD headless.

    0 7 * * *  cd /path/agency && /usr/bin/python3 runner/daily.py >> logs/daily.log 2>&1

It launches Claude Code non-interactively as the MD, on whichever provider the
owner bound to the "md" task on the /setup Routing screen (with fallback), via the
providers module. Then the sector leads (also scheduled) claim and work their tasks.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
import providers  # noqa: E402 — the provider/API module (resolve the MD's provider)

PROMPT = (
    "You are the Managing Director. Run one full daily cycle exactly as CLAUDE.md "
    "describes: sense the filing cabinet, read your recent hit-rate, draft today's "
    "plan grounded in evidence, get it past the critic subagent, then post the "
    "approved tasks to the board and log your decisions. Send the owner anything "
    "amber or low-confidence on Telegram. Do not execute work yourself."
)


def main():
    r = providers.resolve_task("md")
    if not r.get("ok"):
        sys.exit("The MD task has no provider yet. Open /setup → Routing and bind "
                 f"the 'md' task to a model. ({r.get('error', '')})")
    top = r["candidates"][0]
    cred, model = providers.store.find_model(top["model_id"])

    env = {**os.environ}
    if cred.get("api_key"):
        env["ANTHROPIC_API_KEY"] = cred["api_key"]
        env["ANTHROPIC_AUTH_TOKEN"] = cred["api_key"]
    if cred.get("base_url"):
        env["ANTHROPIC_BASE_URL"] = cred["base_url"]
    if model.get("api_model_id"):
        env["ANTHROPIC_MODEL"] = model["api_model_id"]

    cmd = ["claude", "-p", PROMPT, "--append-system-prompt", (ROOT / "CLAUDE.md").read_text()]
    if model.get("api_model_id"):
        cmd += ["--model", model["api_model_id"]]

    subprocess.run(cmd, cwd=ROOT, check=False, env=env)


if __name__ == "__main__":
    main()
