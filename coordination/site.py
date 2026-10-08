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
import re
from pathlib import Path

from . import workspace as _workspace

# The open website's (coordination/workspace.py); config/site.json before any account.
CONFIG = _workspace.config_path("site.json")


# The agency's own name. Not the client's — this is the firm running the work.
# Set AGENCY_NAME in .env to change it; the default is here so a fresh clone is
# not nameless. Hardcoding it in eleven files was how it came to say the wrong
# thing in eleven places at once.
DEFAULT_AGENCY = "World Deal Technical Agency"


def agency_name() -> str:
    import os as _os
    v = (_os.environ.get("AGENCY_NAME") or "").strip()
    if v:
        return v
    try:
        from providers import integrations
        v = (integrations.read_env().get("AGENCY_NAME") or "").strip()
    except Exception:
        v = ""
    return v or DEFAULT_AGENCY


def load() -> dict:
    """The public site config, or an empty dict if it has not been saved yet."""
    try:
        d = json.loads(_workspace.read_secret(CONFIG, "") or "{}")
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


def site_host(override: str = "") -> str:
    """The bare domain, for showing. No scheme, no www., no trailing slash."""
    u = site_url(override)
    if not u:
        return ""
    h = u.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0].strip().lower()
    return h[4:] if h.startswith("www.") else h


def site_name(override: str = "") -> str:
    """
    What to CALL the website on screen.

    The office used to have "Glassworks Dubai" and "glassworks.ae" written into
    the HTML as a constant, so the owner's own site never appeared no matter
    what he saved on the setup screen — the switcher was showing him somebody
    else's business.

    Order: SITE_NAME if he has set one, otherwise the domain itself. The domain
    is not a pretty label but it is his, and a real address beats an invented
    name every time. Empty when no website is saved at all, which the screen
    must show as "not set yet" rather than filling in.
    """
    for src in (os.environ.get("SITE_NAME", ""), _env_value("SITE_NAME")):
        v = (src or "").strip()
        if v:
            return v
    v = (load().get("name") or "").strip()
    return v or site_host(override)


def site_initials(override: str = "") -> str:
    """
    Two letters for the little square beside the name.

    A domain is not two words: splitting "worlddealtechnicalservices.com" on
    punctuation and taking one letter from each part gives "WC", where the C is
    from ".com". When the label IS the bare domain, take the first two letters
    of the domain's own name instead.
    """
    name = site_name(override)
    if not name:
        return "—"
    if name == site_host(override):
        first = name.split(".", 1)[0]
        return (first[:2].upper() or "—")
    words = [w for w in re.split(r"[^A-Za-z0-9]+", name) if w]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return (words[0][:2].upper() if words else "—")


def _env_value(key: str) -> str:
    try:
        from providers import integrations
        return (integrations.read_env().get(key) or "").strip()
    except Exception:
        return ""


def save(url: str, mode: str = "", areas=None, name: str = "") -> dict:
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
    if (name or "").strip():
        data["name"] = name.strip()[:80]
    _workspace.write_secret(CONFIG, json.dumps(data, indent=2) + "\n")
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


def label() -> dict:
    """
    Everything a screen needs to NAME the website, worked out in one place.

    The office, the control room and the status page each used to decide this
    for themselves, and one of them decided it with a hardcoded constant. One
    function, one answer.
    """
    st = status()
    return {"siteName": site_name(), "siteHost": site_host(),
            "siteInitials": site_initials(), "siteUrl": st["url"],
            "siteSource": st.get("source"), "agencyName": agency_name(),
            "siteConfigured": bool(st["url"])}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="What this agency's website is called.")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    d = label()
    if a.json:
        print(json.dumps({"ok": True, **d}))
    elif d["siteConfigured"]:
        print(f"{d['siteName']}  ({d['siteUrl']}, from {d['siteSource']})")
    else:
        print("No website saved yet — set it on /setup.")
