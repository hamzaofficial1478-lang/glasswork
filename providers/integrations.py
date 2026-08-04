"""
integrations.py — test the channels the agency talks through: Telegram (how the
owner is asked to approve) and SMTP.

These live in .env rather than the provider store, so they were the one part of
the setup with no test at all. You could have a dead Telegram token and not find
out until the night an agent needed a decision from you.

Telegram is NOT a customer channel — it is the owner's approval line, the way an
agent stops and asks before doing anything amber. Five agents depend on it (MD,
Overseer, Reporter, Security, Leads-Responder), and every red line in the
handbook ends in "owner approval", which arrives here.

THE RULE HERE: prove the credential, send nothing. Every check below is a
read-only or auth-only call:
  Telegram  getMe + getChat   — reads the bot and the chat, posts no message
  SMTP      connect + login   — authenticates and hangs up, no mail leaves

A test that spams the owner (or worse, a customer) to prove it works is a test
you stop running. The one exception is Telegram, where you can explicitly ask
for a real message — you have to pass notify=True, and it only ever goes to the
owner's own chat id.

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


def read_env(path=None) -> dict:
    """Parse .env into a dict. Mirrors the reader in server.js so both agree."""
    env = {}
    p = Path(path) if path else ENV_PATH
    if not p.exists():
        return env
    try:
        for line in p.read_text().splitlines():
            m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
            if m:
                env[m.group(1)] = m.group(2).strip()
    except Exception:
        return {}
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


ALL = {"telegram": test_telegram, "email": test_email}


def test_all(env=None, notify=False) -> list:
    """Every channel, in the order the agency depends on them."""
    env = env if env is not None else read_env()
    out = []
    for name, fn in ALL.items():
        try:
            out.append(fn(env=env, notify=notify) if name == "telegram" else fn(env=env))
        except Exception as e:  # a broken check must never take the sweep down
            out.append(_result(name, False, f"The check itself errored: {e}"))
    return out
