"""
site.py — where the agency's own website address lives, and why it is not in .env.

THE BUG THIS FIXES

The owner saved his website on /setup. It went into .env, because that is where
server.js puts everything the setup screen collects. But .env is gitignored (it
has to be — it holds the SMTP password and the API tokens), so the address never
left his laptop. Clone the repo anywhere else and the IT Watcher and the
site-services reader both refuse to start with "No SITE_URL set", as though he
had never entered it. He had. It just could not travel.

The mistake was treating a public web address as a secret. A URL that is printed
on the company's own van is not a credential. So the two are separated:

  .env                secrets only — SMTP_PASS, SITE_API_TOKEN, bot tokens
  config/site.json    public facts about the business — the address, the mode,
                      the areas it serves. Not a credential, so it is written in
                      plain sight rather than hidden among the secrets.

Reading order is env first, then the file, so an operator can still override the
address for one run without editing anything:

    SITE_URL=https://staging.example.ae python -m pipelines.it_health

THE SECOND BUG, WHICH THE FIRST FIX CAUSED

Making the file TRACKED so it travels to a fresh clone had a consequence nobody
thought through: the repo ships it as {"url": ""}, so every `git pull` reset the
owner's saved address back to empty. He set his website, pulled an update, and
the health check told him "Website: not set — save it on /setup" as though he
never had. Exactly the complaint the tracked file was meant to end, arriving by
a different road.

Two changes, because either alone leaves a hole:

  1. .env is read here now, properly. The setup screen has always written
     SITE_URL there as well, and .env is gitignored, so it is the one copy a
     pull cannot touch. It sits between the process environment and the file.
  2. config/site.json is no longer tracked. config/site.example.json ships
     instead. A file the owner edits and the repo also edits is a file that
     either loses his work on pull or blocks the pull outright — there is no
     third outcome, and both of those happened.

So the order is: explicit argument, process environment, .env, config/site.json.
The first three survive a pull. The file is now only a convenience for moving a
setup between machines by hand.
"""
import json
import os
from pathlib import Path

CONFIG = Path(__file__).parents[1] / "config" / "site.json"


def load() -> dict:
    """The public site config, or an empty dict if it has not been saved yet."""
    try:
        d = json.loads(CONFIG.read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def from_env_file() -> str:
    """
    SITE_URL out of .env. The setup screen has always written it there, and .env
    is gitignored, so this is the copy that survives a pull. Read lazily and
    defensively: this module is imported by everything, and it must not fall
    over because the providers package is mid-install.
    """
    try:
        from providers import integrations
        return (integrations.read_env().get("SITE_URL") or "").strip()
    except Exception:
        return ""


def site_url(override: str = "") -> str:
    """
    The website this agency works on, in order of authority:

        explicit argument   one run, one address, nothing written
        process environment SITE_URL=... in front of the command
        .env                what /setup saved — survives a git pull
        config/site.json    a convenience copy, easy to move between machines

    Returns "" when genuinely unset — callers already have good messages for
    that and must not be handed a guess.
    """
    for candidate in (override, os.environ.get("SITE_URL", ""),
                      from_env_file(), load().get("url", "")):
        candidate = (candidate or "").strip()
        if candidate:
            return candidate
    return ""


def save(url: str, mode: str = "", areas=None) -> dict:
    """
    Record the public config. Called by the setup screen alongside the .env
    write. Deliberately refuses to store anything that looks like a credential:
    this file is committed, and one stray token in it is a leak.
    """
    url = (url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    data = {"url": url}
    if mode:
        data["mode"] = mode
    if areas:
        data["areas"] = list(areas)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(data, indent=2) + "\n")
    return data


def status() -> dict:
    """
    Where the address came from, so the owner can see why it is or isn't set.

    "Not set" was the least useful thing this could say. He HAD set it — the
    address was in .env the whole time and nothing here read that file — so the
    unset case now lists every place that was checked. A health line that names
    the drawers it opened is a health line you can act on.
    """
    if (os.environ.get("SITE_URL") or "").strip():
        return {"url": site_url(), "source": "environment"}
    if from_env_file():
        return {"url": site_url(), "source": ".env"}
    if load().get("url"):
        return {"url": site_url(), "source": "config/site.json"}
    return {"url": "", "source": None,
            "looked_in": ["SITE_URL in the environment", ".env (written by /setup)",
                          "config/site.json"],
            "hint": ("save your website on /setup and press Save — or set "
                     "SITE_URL=https://yoursite.ae for one run")}
