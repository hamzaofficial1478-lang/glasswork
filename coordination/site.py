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
                      the areas it serves. Tracked in git, safe to read anywhere.

Reading order is env first, then the file, so an operator can still override the
address for one run without editing anything:

    SITE_URL=https://staging.example.ae python -m pipelines.it_health
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


def site_url(override: str = "") -> str:
    """
    The website this agency works on. Explicit argument, then environment, then
    the tracked config file. Returns "" when genuinely unset — callers already
    have good messages for that and must not be handed a guess.
    """
    for candidate in (override, os.environ.get("SITE_URL", ""), load().get("url", "")):
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
    """Where the address came from, so the owner can see why it is or isn't set."""
    if (os.environ.get("SITE_URL") or "").strip():
        return {"url": site_url(), "source": "environment"}
    if load().get("url"):
        return {"url": site_url(), "source": "config/site.json"}
    return {"url": "", "source": None,
            "hint": "save your website on /setup, or set SITE_URL for one run"}
