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

On a WordPress connection the same step keeps the Botix helper plugin up to
date (site_api.update_wp_helper): "every time when you will make any change or
will update that should also be updated and build for the wordpress too".

A connector too old to update itself is said once, plainly, with what to do —
it is not an error that stops the day; so is a helper not installed yet.
"""
import argparse
import json
import sys

from coordination import board
from . import site_api

AGENT = "it-releaser"


def run() -> dict:
    with board.agent_run(AGENT) as r:
        if site_api.settings().get("kind") == "wordpress":
            out = site_api.update_wp_helper(agent=AGENT)
        else:
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
    return 0 if out.get("ok") or out.get("manual") or out.get("unreachable") or out.get("missing") else 1


if __name__ == "__main__":
    sys.exit(main())
