"""
connector_update.py — keeps the connector on his website up to date, by itself.

    python -m pipelines.connector_update          update if this copy has a newer one
    python -m pipelines.connector_update --json

"the api auto update not on every update to uplaod the api file in the
 website backend that will make the owner hectic"

First in IT's lane every day, before anything reads or writes his site, so
every desk after it talks to the newest connector. Run as the IT Releaser —
the connector sits on his live website, and only the Releaser changes that.
The careful part happens on his server (agency-api.php, /self-update); see
site_api.update_connector for what this side does and why.

A connector too old to update itself is said once, plainly, with what to do —
it is not an error that stops the day.
"""
import argparse
import json
import sys

from coordination import board
from . import site_api

AGENT = "it-releaser"


def run() -> dict:
    with board.agent_run(AGENT) as r:
        out = site_api.update_connector(agent=AGENT)
        r.note(out.get("head") or out.get("error") or "")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Keep the website connector up to date.")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    board.ensure_schema()
    out = run()
    print(json.dumps(out, indent=2) if a.json else (out.get("head") or out.get("error") or ""))
    # Too old to update itself, or the site resting: said, not a failed step.
    return 0 if out.get("ok") or out.get("manual") or out.get("unreachable") else 1


if __name__ == "__main__":
    sys.exit(main())
