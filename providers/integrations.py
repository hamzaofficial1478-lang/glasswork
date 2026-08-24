"""
integrations.py — the channels the agency talks through: ntfy and Telegram for
reaching the owner, SMTP for reaching customers.

These live in .env rather than the provider store, so they were the one part of
the setup with no test at all. You could have a dead token and not find out
until the night an agent needed a decision from you.

NONE OF THESE IS A CUSTOMER CHANNEL EXCEPT SMTP. ntfy and Telegram are the
owner's own approval line — the way an agent stops and asks before doing
anything amber. Five agents depend on it (MD, Overseer, Reporter, Security,
Leads-Responder), and every red line in the handbook ends in "owner approval",
which arrives here. Everything routes through one function, send_message(), so
switching the owner's channel is a config change and not a code change.

ntfy is the default when NTFY_TOPIC is set: no bot to create, no token to leak,
no chat id to hunt down. Telegram stays as the fallback so an existing setup
does not go silent. The ntfy trade-off is stated where it lives — a topic on
the public server is readable by anyone who guesses the name.

THE RULE HERE: prove the credential, send nothing. Every check below is a
read-only or auth-only call:
  ntfy      poll the topic     — reads the feed, publishes nothing
  Telegram  getMe + getChat    — reads the bot and the chat, posts no message
  SMTP      connect + login    — authenticates and hangs up, no mail leaves

A test that spams the owner (or worse, a customer) to prove it works is a test
you stop running. You can ask for a real notification with notify=True, and it
only ever goes to the owner's own topic or chat.

(The WhatsApp Cloud API used to live here. It was removed because the owner
takes WhatsApp enquiries through the widget on the site and answers them
himself — no agent ever read or wrote that channel, so it was setup surface
with nothing behind it. It is in git history if agent-driven replies are ever
wanted. The company's WhatsApp *number* still appears on the email banner;
that is contact detail, not this API.)
"""
import os
import re
import smtplib
import socket
from pathlib import Path

import requests

from . import auth

ENV_PATH = Path(__file__).parents[1] / ".env"
_TELEGRAM = "https://api.telegram.org"


# The settings the agency reads by name. Used to decide which environment
# variables may override the file — copying the whole environment in would put
# PATH, HOME and every CI variable into a dict the office renders as the
# owner's settings.
ENV_KEYS = {
    "SITE_URL", "SITE_MODE", "SITE_NAME", "AGENCY_NAME",
    "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_FROM",
    "OUTREACH_DOMAIN", "OUTREACH_DAILY_CAP", "WHATSAPP_DAILY_CAP",
    "NTFY_SERVER", "NTFY_TOPIC", "NTFY_TOKEN",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "ALGOLIA_APP_ID", "ALGOLIA_SEARCH_KEY", "ALGOLIA_WRITE_KEY", "ALGOLIA_INDEX",
    "GOOGLE_PLACES_API_KEY", "GSC_PROPERTY",
    "DAILY_SPEND_CAP_USD", "ZYTE_COST_PER_FETCH", "ZYTE_COST_PER_RENDER",
    "CRAWLER_ON_OWN_SITE", "LEADS_ALLOW_INDIVIDUALS",
    "SEO_SEEDS", "SEO_AREAS", "SEO_STRIKING_TO", "SEO_W_AREA", "SEO_W_ENQUIRIES",
    "PORT",
}


def read_env(path=None) -> dict:
    """
    The agency's settings: .env, with the real environment on top.

    THE ENVIRONMENT USED TO BE IGNORED, AND THE PROGRAM ADVERTISED OTHERWISE.

    `run.py --check` tells the owner, in as many words, "set
    SITE_URL=https://yoursite.ae for one run". coordination/site.py honours
    that and reports the source as 'environment'. This function did not read
    the environment at all — so the same setting was set and not set at the
    same time, depending on who asked, and the preflight that decides whether
    the day may start was the one saying not set. Doing exactly what the
    program told you to do left IT and Leads blocked with no way to see why.

    Precedence is the environment, because exporting a variable for one run is
    a deliberate act and the saved file is the standing default. Everything
    that reads a setting directly — mail.py, whatsapp.py, fetcher.py — already
    used os.environ, so this brings the odd one out into line rather than
    inventing a new rule.

    Mirrored in server.js's readEnv() so the two never disagree again.
    """
    env = {}
    p = Path(path) if path else ENV_PATH
    try:
        if p.exists():
            for line in p.read_text().splitlines():
                m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
                if m:
                    env[m.group(1)] = m.group(2).strip()
    except Exception:
        env = {}
    # Only keys the file already knows about, plus the ones the agency reads by
    # name. Copying the WHOLE environment in would drag PATH, HOME and every
    # CI variable into a dict the office renders as the owner's settings.
    for k in set(env) | ENV_KEYS:
        v = os.environ.get(k)
        if v is not None and str(v).strip():
            env[k] = str(v).strip()
    return env


def _missing(env, keys):
    return [k for k in keys if not (env.get(k) or "").strip()]


def _result(name, ok, detail, **extra):
    return {"integration": name, "ok": ok, "detail": detail, **extra}


# ---- Telegram ---------------------------------------------------------------

def test_telegram(env=None, notify=False, timeout=20) -> dict:
    """
    getMe proves the bot token. getChat proves the chat id is one this bot can
    actually reach — a valid token pointed at a chat the bot was never added to
    is the failure people hit, and it looks identical to 'working' until it
    matters. Both are reads. Pass notify=True to also post a real message.
    """
    env = env if env is not None else read_env()
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (env.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token:
        return _result("telegram", None, "Not configured — no TELEGRAM_BOT_TOKEN in .env.")
    token = re.sub(r"^bot", "", token)  # tolerate a pasted "bot<token>"

    try:
        r = requests.get(f"{_TELEGRAM}/bot{token}/getMe", timeout=timeout)
    except Exception as e:
        return _result("telegram", False, f"Could not reach Telegram: {e}",
                       key=auth.redact(token))
    if r.status_code == 401:
        return _result("telegram", False,
                       "401 — Telegram rejected the bot token. Re-copy it from @BotFather.",
                       status=401, key=auth.redact(token))
    if not (200 <= r.status_code < 300):
        return _result("telegram", False, f"getMe returned {r.status_code}.",
                       status=r.status_code, key=auth.redact(token))
    me = (r.json() or {}).get("result", {})
    bot = "@" + me.get("username", "?")

    if not chat:
        return _result("telegram", False,
                       f"Bot token is valid ({bot}) but TELEGRAM_CHAT_ID is empty — "
                       "the agency has no one to report to. Message your bot, then read "
                       "the id from /getUpdates.", bot=bot)

    try:
        c = requests.get(f"{_TELEGRAM}/bot{token}/getChat",
                         params={"chat_id": chat}, timeout=timeout)
    except Exception as e:
        return _result("telegram", False, f"Bot valid ({bot}) but the chat check failed: {e}",
                       bot=bot)
    if not (200 <= c.status_code < 300):
        why = ""
        try:
            why = " — " + (c.json() or {}).get("description", "")
        except Exception:
            pass
        return _result("telegram", False,
                       f"Bot token is valid ({bot}), but chat {chat} is unreachable{why}. "
                       "Usually: you haven't messaged the bot yet, or the chat id is wrong.",
                       bot=bot, status=c.status_code)

    detail = f"Bot {bot} is live and chat {chat} is reachable. Nothing was posted."
    if notify:
        s = requests.post(f"{_TELEGRAM}/bot{token}/sendMessage", timeout=timeout,
                          json={"chat_id": chat,
                                "text": "Agency check: your Telegram route is working. "
                                        "This is the channel you'll be asked to approve on."})
        if 200 <= s.status_code < 300:
            detail = f"Bot {bot} is live — a test message has just arrived in chat {chat}."
        else:
            return _result("telegram", False,
                           f"Bot {bot} reads fine but posting failed ({s.status_code}).",
                           bot=bot, status=s.status_code)
    return _result("telegram", True, detail, bot=bot, chat=chat)


def send_telegram(text, env=None, timeout=20) -> dict:
    """
    Post agency-generated text to the owner's Telegram chat.

    Deliberately separate from test_telegram(): that one proves the credential
    and only ever posts a fixed, clearly-labelled test string. This one posts
    whatever the caller built, so it stays a one-line function with no test-vs-
    real ambiguity baked into it.
    """
    env = env if env is not None else read_env()
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (env.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        return _result("telegram", False,
                       "Not configured — no TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID in .env. "
                       "Nothing went out.")
    token = re.sub(r"^bot", "", token)
    try:
        r = requests.post(f"{_TELEGRAM}/bot{token}/sendMessage", timeout=timeout,
                          json={"chat_id": chat, "text": (text or "")[:4000]})
    except Exception as e:
        return _result("telegram", False, f"Could not reach Telegram: {e}")
    if not (200 <= r.status_code < 300):
        return _result("telegram", False, f"Telegram rejected the message ({r.status_code}).",
                       status=r.status_code)
    return _result("telegram", True, "Delivered.")


# ---- ntfy -------------------------------------------------------------------
# The owner's chosen notification route. Simpler than Telegram in the way that
# matters here: a topic is just a name, so there is no bot to create, no token
# to leak and no chat id to hunt down in /getUpdates.
#
# The trade-off is real and worth stating: a topic on the public ntfy.sh is
# readable by ANYONE who guesses its name. Nothing secret should go through it.
# Pick a long unguessable topic, and self-host ntfy if the digest ever carries
# customer detail.

_NTFY_DEFAULT = "https://ntfy.sh"


def _agency_title() -> str:
    """Whose notification this is. Set AGENCY_NAME in .env to change it."""
    try:
        from coordination import site
        return site.agency_name()
    except Exception:
        return "World Deal Technical Agency"


def _ntfy_conf(env):
    server = (env.get("NTFY_SERVER") or _NTFY_DEFAULT).strip().rstrip("/")
    topic = (env.get("NTFY_TOPIC") or "").strip().lstrip("/")
    return server, topic


def _ntfy_headers(env, title=None, priority=None, tags=None, actions=None):
    """
    ntfy takes its options as headers. Values must be latin-1 clean, because a
    header carrying an em-dash raises inside requests and the notification is
    lost — which is exactly the moment you needed it.
    """
    def _ascii(v):
        return (str(v).replace("—", "-").replace("–", "-").replace("’", "'")
                .encode("ascii", "replace").decode("ascii"))

    h = {}
    if title:
        h["Title"] = _ascii(title)[:200]
    if priority:
        h["Priority"] = _ascii(priority)
    if tags:
        h["Tags"] = _ascii(",".join(tags) if isinstance(tags, (list, tuple)) else tags)
    if actions:
        # each action: "http, Approve, https://host/api/approve/12, method=POST"
        h["Actions"] = _ascii("; ".join(actions))[:1900]
    token = (env.get("NTFY_TOKEN") or "").strip()
    if token:                      # only needed for a protected/self-hosted topic
        h["Authorization"] = f"Bearer {token}"
    return h


def test_ntfy(env=None, notify=False, timeout=20) -> dict:
    """
    Prove the topic is reachable WITHOUT publishing to it. Polling the topic's
    json feed is a read: it returns whatever is cached and posts nothing. Pass
    notify=True to send a real, clearly-labelled test notification.
    """
    env = env if env is not None else read_env()
    server, topic = _ntfy_conf(env)
    if not topic:
        return _result("ntfy", None, "Not configured — no NTFY_TOPIC in .env.")
    if len(topic) < 8:
        return _result("ntfy", False,
                       f"Topic {topic!r} is too short to be private. Anyone who guesses "
                       "the name reads your notifications — use a long random topic.",
                       server=server, topic=topic)
    try:
        r = requests.get(f"{server}/{topic}/json", params={"poll": "1"}, timeout=timeout,
                         headers=_ntfy_headers(env))
    except Exception as e:
        return _result("ntfy", False, f"Could not reach {server}: {e}", server=server)
    if not (200 <= r.status_code < 300):
        return _result("ntfy", False,
                       f"{server} answered {r.status_code} for topic {topic}.",
                       server=server, topic=topic, status=r.status_code)

    detail = (f"Topic {topic} on {server} is reachable. Nothing was published. "
              "Subscribe on your phone: install ntfy, add this topic.")
    if notify:
        s = send_ntfy("Agency check: this is the channel you'll be asked to approve on.",
                      title=_agency_title(), tags=["white_check_mark"], env=env,
                      timeout=timeout)
        if not s["ok"]:
            return _result("ntfy", False,
                           f"Topic reads fine but publishing failed: {s['detail']}",
                           server=server, topic=topic)
        detail = f"Topic {topic} is live — a test notification has just been sent."
    return _result("ntfy", True, detail, server=server, topic=topic)


def send_ntfy(text, title=None, priority=None, tags=None, actions=None,
              env=None, timeout=20) -> dict:
    """Publish one notification to the owner's topic."""
    env = env if env is not None else read_env()
    server, topic = _ntfy_conf(env)
    if not topic:
        return _result("ntfy", False,
                       "Not configured — no NTFY_TOPIC in .env. Nothing went out.")
    try:
        r = requests.post(f"{server}/{topic}",
                          data=(text or "").encode("utf-8")[:4000],
                          headers=_ntfy_headers(env, title, priority, tags, actions),
                          timeout=timeout)
    except Exception as e:
        return _result("ntfy", False, f"Could not reach {server}: {e}", server=server)
    if not (200 <= r.status_code < 300):
        return _result("ntfy", False, f"{server} rejected it ({r.status_code}).",
                       server=server, status=r.status_code)
    return _result("ntfy", True, "Delivered.", server=server, topic=topic)


def send_message(text, env=None, timeout=20, **kw) -> dict:
    """
    The agency's one way of reaching the owner. Everything that notifies him
    goes through here — the daily digest, approval requests, the Overseer.

    ntfy wins when a topic is set, because that is the owner's stated choice;
    Telegram is kept as the fallback so an existing setup does not go silent the
    day this lands. If neither is configured it says so plainly and sends
    nothing, rather than failing quietly and leaving him uninformed.
    """
    env = env if env is not None else read_env()
    _server, topic = _ntfy_conf(env)
    if topic:
        return send_ntfy(text, env=env, timeout=timeout, **kw)
    if (env.get("TELEGRAM_BOT_TOKEN") or "").strip():
        return send_telegram(text, env=env, timeout=timeout)
    return _result("none", False,
                   "No notification route configured — set NTFY_TOPIC in .env "
                   "(or Telegram). Nothing went out, and nobody was told.")


# ---- SMTP -------------------------------------------------------------------

def test_email(env=None, timeout=20) -> dict:
    """
    Connect, secure the channel, and log in — then quit. This proves the exact
    thing that breaks (the password, the port, the TLS mode) without a single
    message leaving the building.
    """
    env = env if env is not None else read_env()
    gone = _missing(env, ["SMTP_HOST", "SMTP_USER", "SMTP_PASS"])
    if len(gone) == 3:
        return _result("email", None, "Not configured — no SMTP details in .env.")
    if gone:
        return _result("email", False, f"Half configured — still missing: {', '.join(gone)}.")

    host = env["SMTP_HOST"].strip()
    user = env["SMTP_USER"].strip()
    pw = env["SMTP_PASS"]
    try:
        port = int((env.get("SMTP_PORT") or "587").strip() or 587)
    except ValueError:
        return _result("email", False, f"SMTP_PORT is not a number: {env.get('SMTP_PORT')!r}")

    try:
        if port == 465:  # implicit TLS
            srv = smtplib.SMTP_SSL(host, port, timeout=timeout)
        else:
            srv = smtplib.SMTP(host, port, timeout=timeout)
            srv.ehlo()
            if srv.has_extn("starttls"):
                srv.starttls()
                srv.ehlo()
        try:
            srv.login(user, pw)
            return _result("email", True,
                           f"SMTP accepted the login for {user} on {host}:{port}. "
                           "No mail was sent.", host=host, port=port, user=user)
        finally:
            try:
                srv.quit()
            except Exception:
                pass
    except smtplib.SMTPAuthenticationError as e:
        hint = ""
        if "gmail" in host.lower():
            hint = (" Gmail needs an App Password, not your normal one — "
                    "an account password always fails here.")
        return _result("email", False,
                       f"The server rejected the login for {user}.{hint} ({e.smtp_code})",
                       host=host, port=port, key=auth.redact(pw))
    except (socket.timeout, TimeoutError):
        return _result("email", False,
                       f"Timed out connecting to {host}:{port}. Usually a wrong port or a "
                       "firewall — try 587 (STARTTLS) or 465 (SSL).", host=host, port=port)
    except Exception as e:
        return _result("email", False, f"Could not connect to {host}:{port} — {e}",
                       host=host, port=port)


# ---- Algolia (search on the owner's own website) ----------------------------

def test_algolia(env=None, timeout=15) -> dict:
    """
    Prove the account and the index answer, WITHOUT writing anything.

    The read is a query with the search key, which is the same call the widget
    makes from a visitor's browser — so a pass here means the search box will
    work, not merely that a key exists.

    The write key is deliberately NOT exercised. The only way to test it is to
    write to the index, and a connector test that quietly mutates the owner's
    data is not a test. It is reported as saved or not saved, and the first real
    index run is what proves it.
    """
    env = env if env is not None else read_env()
    app = (env.get("ALGOLIA_APP_ID") or "").strip()
    search = (env.get("ALGOLIA_SEARCH_KEY") or "").strip()
    write = (env.get("ALGOLIA_WRITE_KEY") or "").strip()
    index = (env.get("ALGOLIA_INDEX") or "glasswork_site").strip()

    if not app and not search:
        return _result("algolia", None, "Not configured — no ALGOLIA_APP_ID in .env.")
    if not app or not search:
        return _result("algolia", False,
                       "Half configured — both the application ID and the search "
                       "key are needed before the search box can query anything.")
    if search == write and write:
        return _result("algolia", False,
                       "The search key and the write key are set to the same value. "
                       "The search key goes into the page where anyone can read it, "
                       "so that would publish write access to your whole index. "
                       "Copy the SEARCH-ONLY key into ALGOLIA_SEARCH_KEY.")
    try:
        r = requests.post(
            f"https://{app}-dsn.algolia.net/1/indexes/{index}/query",
            headers={"X-Algolia-API-Key": search, "X-Algolia-Application-Id": app,
                     "Content-Type": "application/json"},
            json={"query": "", "hitsPerPage": 0}, timeout=timeout)
    except Exception as e:
        return _result("algolia", False, f"Could not reach Algolia: {e}", app=app, index=index)

    if r.status_code == 403:
        return _result("algolia", False,
                       "403 — Algolia rejected the search key for this index. Check it "
                       "is the Search-Only key and that the index name matches.",
                       app=app, index=index)
    if r.status_code == 404:
        return _result("algolia", None,
                       f"Reachable, but the index '{index}' does not exist yet. It is "
                       "created the first time the site is indexed — run "
                       "`python -m pipelines.site_search`.", app=app, index=index)
    if r.status_code >= 400:
        return _result("algolia", False, f"Algolia returned {r.status_code}.",
                       app=app, index=index)

    try:
        n = int((r.json() or {}).get("nbHits", 0))
    except Exception:
        n = 0
    detail = (f"Index '{index}' answers — {n} page(s) searchable. Nothing was written. "
              + ("The write key is saved, and is only used server-side."
                 if write else
                 "No write key saved yet, so the site cannot be indexed."))
    return _result("algolia", True, detail, app=app, index=index, pages=n)


ALL = {"ntfy": test_ntfy, "telegram": test_telegram, "email": test_email,
       "algolia": test_algolia}


def test_all(env=None, notify=False) -> list:
    """Every channel, in the order the agency depends on them."""
    env = env if env is not None else read_env()
    out = []
    for name, fn in ALL.items():
        try:
            out.append(fn(env=env, notify=notify)
                       if name in ("ntfy", "telegram") else fn(env=env))
        except Exception as e:  # a broken check must never take the sweep down
            out.append(_result(name, False, f"The check itself errored: {e}"))
    return out
