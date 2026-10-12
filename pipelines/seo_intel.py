"""
seo_intel.py — keyword intelligence without a paid keyword tool.

    python -m pipelines.seo_intel              build the picture
    python -m pipelines.seo_intel --report     what it found, ranked
    python -m pipelines.seo_intel --wins       the free money: striking distance

WHY THIS EXISTS

DataForSEO, Ahrefs and Semrush all sell the same two numbers: monthly search
volume, and a difficulty score. For a Dubai glass and aluminium firm those cost
more per month than a decent lead is worth, and the owner said plainly that he
cannot afford them. Fair. So the question is not "which cheaper tool" — it is
"what do those numbers actually buy, and can we get the same decision without
them".

WHAT VOLUME IS ACTUALLY FOR

You use volume to decide which page to write next. That is the whole job. And
for a local trade it is a poor way to decide, because the numbers are wrong at
exactly the scale that matters: "glass partition Dubai price" might report 30 a
month and bring two site visits a week, while "glass" reports fifty thousand and
brings nothing but students. Every one of those tools is modelling anyway — none
of them has Google's real figures either.

So this does not estimate volume, and never prints a number pretending to be
one. It ranks by EVIDENCE WE CAN ACTUALLY PROVE:

  1. GOOGLE SEARCH CONSOLE — the real queries people already typed to reach this
     website, with real impressions and real positions. Free, and it is the
     owner's own property. No paid tool has data this good about his site,
     because no paid tool can see it. It was already on the setup screen and
     nothing in the code read it, which is the most expensive line in this file.

  2. AUTOCOMPLETE, PROPERLY WORKED — Google and Bing suggest, expanded by area,
     by question word, by buying word, and by letter. Free and keyless. A phrase
     that spawns thirty completions is a phrase people finish typing in thirty
     ways. That is demand BREADTH, not volume, and it is labelled as such.

  3. HIS OWN ENQUIRIES — the words real customers used when they wrote in. Every
     rival is guessing at this from a keyword tool. He has the actual sentences.

  4. WHAT RIVALS BUILT PAGES FOR — a competitor who built and kept a page for
     "frameless shower screen Abu Dhabi" is a competitor telling you it pays.
     That is a market signal, gathered from sitemaps we already read.

THE ONE THING WORTH MORE THAN ALL OF IT

Striking distance. Search Console shows queries where the site already ranks
somewhere between 5th and 20th: Google has decided the page is relevant and has
put it just below where anybody clicks. Fixing those is the cheapest traffic in
SEO and it needs no keyword tool at all — only the free data he already has a
login for. `--wins` prints exactly those.

WHAT THIS IS NOT

It is not search volume, it does not approximate search volume, and if anyone
reads a number here as "searches per month" the file has failed. Where Search
Console gives a real impression count it says so and names the source. Where the
signal is autocomplete breadth or rival coverage it says that instead. A ranked
list built from four honest signals beats a precise-looking number that is a
model's guess.

HOW ACCURATE IS THIS, HONESTLY

The owner asked, and he is right to. The answer depends entirely on which
question you mean, so here are both.

"HOW MANY PEOPLE SEARCH THIS PER MONTH?" — we do not answer it at all. A paid
tool does, and its answer is worth less than it looks. DataForSEO's own docs say
the search_volume field comes from the Google Ads API and is a "monthly average
search volume rate", and that "Google Ads provides combined search volume values
for groups of similar keywords". Google's own Keyword Planner help says plainly:
"Your search volume statistics are rounded." So the number a subscription buys
is a rounded, twelve-month-averaged figure for a GROUP of keywords, produced for
advertisers deciding bids. Nobody is measuring your market. It is a real number
about a real thing, but it is not the precise instrument the price implies.

"WHICH PAGE SHOULD I WRITE NEXT?" — this is the decision volume is bought to
make, and here the accuracy is good, because three of the four sources are
measurements rather than models:

  Search Console impressions   EXACT. It is Google reporting on your own
                               property. Not an estimate at any point. The one
                               caveat is Google's own: very rare queries are
                               omitted for privacy, so the list is complete for
                               anything with real traffic and silent on the tail.
  Autocomplete completions     EXACT as an observation — these are genuinely the
                               completions Google and Bing offer, today, for the
                               UAE. They tell you WHAT people type. They do not
                               tell you HOW MANY, and nothing here pretends they
                               do. Ordering by breadth is an inference, and it is
                               the weakest link in the file.
  Your own enquiries           EXACT. They are your emails.
  Rival pages                  EXACT as an observation, inferred as a signal.
                               That a competitor keeps a page live is a fact;
                               that it therefore earns money is a reasonable
                               guess, and it is weighted as one.

So: the inputs are facts, and the ranking built on them is a judgement — stated
as such, with the evidence printed beside every row so it can be argued with. A
paid tool inverts that: the input is a model and the output looks like a fact.

WHERE A PAID KEY STILL EARNS ITS MONEY

Two things this cannot do, and they are worth naming rather than papering over:
tracking your daily rank for a fixed keyword list without you looking, and
telling you a phrase is worth chasing BEFORE anybody has ever searched it on
your site. If either becomes worth the subscription, put the key on the seo_data
shelf and both start working alongside this, not instead of it.
"""
import argparse
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

from coordination import board, cabinet, deskwork

try:
    from . import seo_gap, services
except ImportError:  # pragma: no cover
    import seo_gap, services

AGENT = "seo-strategist"
UA = "Mozilla/5.0 (compatible; glasswork-seo/1.0)"

# ---------------------------------------------------------------------------
# NOTHING BELOW IS A DECISION THIS FILE GETS TO MAKE ON ITS OWN
# ---------------------------------------------------------------------------
#
# The owner's point: this is the main part of his SEO, so it must not be
# hardcoded — not the work it does, not its limits, not its conditions, not the
# reasoning behind its scores. Every one of those was a literal in this file.
#
# So they are DEFAULTS now, in three layers, and the layer nearest him wins:
#
#   1. .env             one line, no code, survives an update
#   2. the live cabinet what his site actually sells, what his customers wrote
#   3. these defaults   a sensible start for a UAE trade, and nothing more
#
# The seeds are the sharpest example. They used to be a fixed list of twelve
# phrases written into seo_gap. They are now read from the service catalogue
# the rest of the agency already uses, PLUS whatever his own website turns out
# to sell that the catalogue has not got yet — so adding a service to the site
# changes what SEO researches, without anybody editing Python.

DEFAULT_AREAS = ["Dubai", "Abu Dhabi", "Sharjah", "Al Quoz", "Business Bay",
                 "Jumeirah", "Deira", "Ajman", "Dubai Marina"]
# ...for the owner's website. Any other's are its own (coordination/profile.py).
try:
    from coordination import profile as _pf, uae as _uae
    if not _pf.is_starter():
        DEFAULT_AREAS = _uae.search_areas(9)
except Exception:
    pass

# How a person finishes a search when they are close to paying somebody.
DEFAULT_BUY_WORDS = ["price", "cost", "near me", "company", "supplier",
                     "installation", "repair", "contractor", "fixing", "quotation"]

# How a person searches when they are still working out what they want. Worth
# less as a lead, worth more for AEO — these are the ones an answer engine
# intercepts, so they feed straight into seo_answers.
DEFAULT_ASK_WORDS = ["how much", "what is", "how to", "can you", "best", "which"]


def _env(name, default=""):
    import os as _os
    v = (_os.environ.get(name) or "").strip()
    if v:
        return v
    try:
        from providers import integrations
        return (integrations.read_env().get(name) or "").strip() or default
    except Exception:
        return default


def _list(name, default):
    """A comma-separated .env override, or the built-in list."""
    raw = _env(name)
    if not raw:
        return list(default)
    got = [x.strip() for x in raw.split(",") if x.strip()]
    return got or list(default)


def _num(name, default):
    try:
        return float(_env(name) or default)
    except (TypeError, ValueError):
        return default


def areas_list():
    """
    Where he actually works. From SEO_AREAS in .env, else the areas saved with
    his website, else the emirates. A firm that moves into Al Ain changes one
    line and the research follows it.
    """
    override = _list("SEO_AREAS", [])
    if override:
        return override
    try:
        from coordination import site
        saved = site.load().get("areas") or []
        if saved:
            return list(saved)
    except Exception:
        pass
    return list(DEFAULT_AREAS)


def buy_words():
    return _list("SEO_BUY_WORDS", DEFAULT_BUY_WORDS)


def ask_words():
    return _list("SEO_ASK_WORDS", DEFAULT_ASK_WORDS)


def seeds_from_catalogue(con=None):
    """
    What to research, taken from what the business actually sells.

    Three sources, in order of authority: the service catalogue every other
    sector already uses; anything his own WEBSITE says he sells that the
    catalogue has not caught up with (site_services files those as
    'uncatalogued', and they are real words real customers read); and finally
    SEO_SEEDS in .env if he wants to force the list.
    """
    forced = _list("SEO_SEEDS", [])
    if forced:
        return forced
    out = []
    try:
        for key, svc in services.SERVICES.items():
            names = list(svc.get("names") or ())
            if names:
                out.append(names[0])            # the plainest name for the thing
    except Exception:
        pass
    # Whatever the site sells that the catalogue does not know about yet.
    if con is not None:
        try:
            for (phrase,) in con.execute(
                    "SELECT DISTINCT phrase FROM keywords WHERE intent='site'"
                    " ORDER BY id DESC LIMIT 12"):
                if phrase and phrase.lower() not in [o.lower() for o in out]:
                    out.append(phrase)
        except sqlite3.Error:
            pass
    return out or list(seo_gap.SERVICES)


# The scoring weights. They are a JUDGEMENT, not a law, and a judgement the
# owner is entitled to disagree with — so each one is a line in .env. Raise
# SEO_W_ENQUIRIES if the words his own customers use should outrank everything;
# drop SEO_W_RIVALS if he thinks his competitors are guessing too.
def weights():
    return {
        "impressions": _num("SEO_W_IMPRESSIONS", 0.20),   # per impression, capped
        "striking":    _num("SEO_W_STRIKING", 40),
        "breadth":     _num("SEO_W_BREADTH", 1.5),        # per completion, capped
        "rivals":      _num("SEO_W_RIVALS", 6),           # per rival page, capped
        "intent":      _num("SEO_W_INTENT", 18),
        "area":        _num("SEO_W_AREA", 12),
        "enquiries":   _num("SEO_W_ENQUIRIES", 25),
        "covered":     _num("SEO_W_ALREADY_COVERED", -20),
    }


# ---------------------------------------------------------------------------
# 1. GOOGLE SEARCH CONSOLE — free, and it is his own data
# ---------------------------------------------------------------------------

GSC_SA = "config/gsc-service-account.local.json"
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"


def gsc_ready():
    """(ok, why_not) — is Search Console actually usable right now?"""
    try:
        from providers import integrations
        prop = (integrations.read_env().get("GSC_PROPERTY") or "").strip()
    except Exception:
        prop = ""
    if not prop:
        return False, ("no GSC_PROPERTY set. Search Console is free and it is the "
                       "single best keyword source you own — add it on /setup → "
                       "Integrations.")
    if not _sa_path().exists():
        return False, (f"GSC_PROPERTY is set but {GSC_SA} is missing. Upload the "
                       f"service-account JSON on /setup → Integrations, and share "
                       f"the property with that account's email in Search Console.")
    try:
        import google.oauth2.service_account  # noqa: F401
    except Exception:
        return False, ("the Google auth library is not installed. One command, "
                       "once: pip install google-auth requests")
    return True, ""


# THE LAST TIME GOOGLE WAS ASKED, AND WHAT IT SAID — so Setup can say
# "working" or "Google refused: …" instead of "Connected" the moment two fields
# are filled in. A git-ignored file beside the key, written by every real ask.
GSC_STATUS = "config/gsc-status.local.json"


def _gsc_status_path():
    from pathlib import Path
    p = Path(GSC_STATUS)
    if p.is_absolute():
        return p
    return _config_file(p)


def _config_file(rel):
    """A config/ file of the open website's (coordination/workspace.py), or the program's own."""
    from pathlib import Path
    from coordination import workspace
    rel = Path(rel)
    if workspace.legacy() or rel.parts[:1] != ("config",):
        return Path(__file__).parents[1] / rel
    return workspace.config_path(rel.name)


def _sa_path():
    """The Search Console key of the open website."""
    return _config_file(GSC_SA)


def _sa_json() -> dict:
    """The key file's contents, opened with the account's key when it is sealed."""
    from coordination import workspace
    return json.loads(workspace.read_secret(_sa_path(), "") or "{}")


def _sa_credentials(service_account, scopes):
    """
    The Search Console sign-in. A sealed key file (an encrypted account) is
    opened in memory and never written out in the clear; a plain one is read
    from its path as before.
    """
    from coordination import vault
    p = _sa_path()
    if p.exists() and vault.is_sealed(p.read_bytes()):
        return service_account.Credentials.from_service_account_info(_sa_json(), scopes=scopes)
    return service_account.Credentials.from_service_account_file(str(p), scopes=scopes)


def gsc_note(ok, why, rows=0):
    """Write down what Google said, and settle his to-do either way."""
    try:
        from providers import integrations
        prop = (integrations.read_env().get("GSC_PROPERTY") or "").strip()
    except Exception:
        prop = ""
    try:
        p = _gsc_status_path()
        # The test suite runs with his .env ignored; it must never overwrite
        # what Setup shows about his real Search Console.
        if (__import__("os").environ.get("AGENCY_IGNORE_DOTENV") == "1"
                and GSC_STATUS == "config/gsc-status.local.json"):
            raise RuntimeError("not during the test run")
        from coordination import workspace
        workspace.write_secret(p, json.dumps({"ok": bool(ok), "why": (why or "")[:600],
                                              "rows": int(rows or 0), "property": prop,
                                              "at": int(time.time())}))
    except Exception:
        pass
    try:
        from coordination import inbox
        if ok:
            inbox.gsc_done(f"{int(rows or 0)} search(es) came back.")
        elif prop:
            # He has set it up and Google said no: his to-do says what Google
            # said. (Never set up at all is the plain "set it up" one, which
            # SEO's own hand-off files.)
            inbox.gsc_todo(why, email=_service_account_email())
    except Exception:
        pass


GSC_WRITE_SCOPE = "https://www.googleapis.com/auth/webmasters"


def _gsc_session(scope=GSC_SCOPE):
    """(credentials with a fresh token, property, why_not)."""
    ok, why = gsc_ready()
    if not ok:
        return None, "", why
    import google.auth.transport.requests
    from google.oauth2 import service_account
    from providers import integrations
    prop = (integrations.read_env().get("GSC_PROPERTY") or "").strip()
    try:
        creds = _sa_credentials(service_account, [scope])
        creds.refresh(google.auth.transport.requests.Request())
    except Exception as e:
        return None, prop, _login_failed(e)
    return creds, prop, ""


def gsc_submit_sitemap(url) -> dict:
    """
    Give Search Console the sitemap, the way his Submit button does. Needs the
    write scope — the agency's account is a Full user on his property.
    """
    creds, prop, why = _gsc_session(GSC_WRITE_SCOPE)
    if not creds:
        return {"ok": False, "why": why}
    import requests
    path = ("/webmasters/v3/sites/" + urllib.parse.quote(prop, safe="")
            + "/sitemaps/" + urllib.parse.quote(url, safe=""))
    try:
        r = requests.put("https://www.googleapis.com" + path, timeout=30,
                         headers={"Authorization": "Bearer " + creds.token})
    except Exception as e:
        return {"ok": False, "why": f"Search Console did not answer ({type(e).__name__})"}
    if r.status_code in (200, 204):
        return {"ok": True}
    off = _api_switched_off(r.text or "")
    return {"ok": False, "why": off or f"Search Console said {r.status_code}: {(r.text or '')[:160]}"}


def gsc_inspect(url, creds=None, prop=""):
    """
    What Google itself says about one page: indexed or not, and why. The same
    answer as the URL Inspection box in Search Console. {ok, verdict, coverage,
    last_crawl, google_canonical, user_canonical, robots, fetch} or {ok: False, why}.
    """
    if creds is None:
        creds, prop, why = _gsc_session()
        if not creds:
            return {"ok": False, "why": why}
    import requests
    try:
        r = requests.post("https://searchconsole.googleapis.com/v1/urlInspection/index:inspect",
                          timeout=45, headers={"Authorization": "Bearer " + creds.token},
                          json={"inspectionUrl": url, "siteUrl": prop, "languageCode": "en-GB"})
    except Exception as e:
        return {"ok": False, "why": f"Search Console did not answer ({type(e).__name__})"}
    if r.status_code != 200:
        off = _api_switched_off(r.text or "")
        if not off and "not part of this property" in (r.text or ""):
            off = (f"{url} is not inside your Search Console property ({prop}) — "
                   "Google answers only for addresses the property covers")
        return {"ok": False, "status": r.status_code,
                "why": off or f"Search Console said {r.status_code}: {(r.text or '')[:200]}"}
    st = ((r.json().get("inspectionResult") or {}).get("indexStatusResult") or {})
    return {"ok": True, "verdict": st.get("verdict") or "",
            "coverage": st.get("coverageState") or "",
            "indexing": st.get("indexingState") or "",
            "robots": st.get("robotsTxtState") or "",
            "fetch": st.get("pageFetchState") or "",
            "last_crawl": st.get("lastCrawlTime") or "",
            "google_canonical": st.get("googleCanonical") or "",
            "user_canonical": st.get("userCanonical") or "",
            "sitemaps": st.get("sitemap") or []}


def gsc_check() -> dict:
    """
    Ask Google now: can the agency read this property? The Test button on Setup.

    The same call the keyword pass makes — log in, ask for the searches that
    reached the site — so a pass cannot disagree with it. It fixes a property
    Google knows under another spelling as it goes, and says what to do when
    Google refuses.
    """
    rows, why = gsc_queries(days=28, limit=10)
    ok = not why
    gsc_note(ok, why, len(rows))
    try:
        from providers import integrations
        prop = (integrations.read_env().get("GSC_PROPERTY") or "").strip()
    except Exception:
        prop = ""
    return {"ok": ok, "property": prop, "rows": len(rows),
            "email": _service_account_email(),
            "why": why or (f"Search Console is working — Google answered for {prop}"
                           + (f" with {len(rows)} search(es) that reached your site."
                              if rows else ", and no searches have reached the site "
                              "in the last 28 days yet (normal for a new property)."))}


def _service_account_email() -> str:
    """The address he has to add as a user in Search Console."""
    try:
        return _sa_json().get("client_email") or ""
    except Exception:
        return ""


def _project_id() -> str:
    """The Google Cloud project the service-account key belongs to."""
    try:
        return _sa_json().get("project_id") or ""
    except Exception:
        return ""


def _api_switched_off(text) -> str:
    """
    The fix, when Google's 403 means the Search Console API is not switched on
    in the key's Google Cloud project — or '' when it means something else.

    "Search Console refused (403) ... Could not list what the service account
    can see (HTTP 403)". Listing what the account can see answers with an empty
    list when the account has simply not been added anywhere; a 403 THERE means
    Google will not take the call at all, and the body says why. Reading only
    the status code sent him to the permissions screen for an API that was off.
    """
    t = (text or "").lower()
    if not any(w in t for w in ("service_disabled", "accessnotconfigured",
                                "has not been used in project", "it is disabled",
                                "api has not been used", "searchconsole.googleapis.com")):
        return ""
    pid = _project_id()
    link = ("https://console.cloud.google.com/apis/library/searchconsole.googleapis.com"
            + (f"?project={pid}" if pid else ""))
    who = _service_account_email()
    return ("Google says the Search Console API is switched off in your Google Cloud "
            f"project{' ' + pid if pid else ''}. Open {link}, press Enable, wait a "
            "minute, then press Test again."
            + (f" If it then says the account cannot see your site, add {who} as a "
               "user in Search Console (Settings → Users and permissions → Add user)."
               if who else ""))


def _which_403(creds, host, prop, get=None) -> str:
    """
    Ask Google which of the two causes it actually is.

    A 403 from Search Console means either "you are not a user on that
    property" or "no such property as you typed it", and Google uses the same
    code for both because a property you cannot see is indistinguishable from
    one that does not exist.

    But /sites lists every property this service account CAN see, and that
    settles it. No list at all: it has been added to nothing. A list without
    GSC_PROPERTY in it: the string is wrong, and the right one is right there.
    """
    email = _service_account_email()
    who = f" The address to add is: {email}" if email else ""
    try:
        if get is None:
            import requests as _rq
            get = _rq.get
        r = get(host + "/webmasters/v3/sites", timeout=30,
                headers={"Authorization": "Bearer " + creds.token})
        if r.status_code != 200:
            off = _api_switched_off(getattr(r, "text", "") or "")
            if off:
                return off
            said = ""
            try:
                said = ((r.json().get("error") or {}).get("message") or "")[:200]
            except Exception:
                said = ""
            raise RuntimeError(f"HTTP {r.status_code}" + (f" — Google said: {said}" if said else ""))
        seen = [s.get("siteUrl") for s in (r.json().get("siteEntry") or [])
                if s.get("siteUrl")]
    except Exception as e:
        return ("Could not list what the service account can see "
                f"({e}), so both causes are still open: either it is "
                "not a user on the property, or GSC_PROPERTY does not match "
                "exactly." + who)
    if not seen:
        return ("Google says this service account can see NO properties at "
                "all, so this is a permissions problem, not a typo. In Search "
                "Console open Settings, then Users and permissions, and add it "
                "as a Full or Restricted user." + who)
    if prop in seen:
        return ("Odd one: Google lists this property for the service account "
                "and still refused. Give it a minute — a permission just "
                "granted takes a moment — then try again.")
    # The usual case, and the useful one.
    close = [s for s in seen if _bare_site(s) == _bare_site(prop)]
    tip = (f" That is the same site as yours written differently — set "
           f"GSC_PROPERTY to exactly: {close[0]}" if close else
           " Set GSC_PROPERTY to exactly one of those.")
    return (f"GSC_PROPERTY is {prop!r}, which is not one of the "
            f"{len(seen)} property(ies) this service account can see: "
            + ", ".join(repr(s) for s in seen[:6]) + "." + tip)


def _bare_site(prop) -> str:
    """'sc-domain:x.com', 'https://www.x.com/' and 'http://x.com' are one site."""
    s = (prop or "").strip().lower()
    for pre in ("sc-domain:", "https://", "http://", "www."):
        if s.startswith(pre):
            s = s[len(pre):]
    return s.rstrip("/")


def _same_site_property(creds, host, prop, get=None) -> str:
    """
    The property Google says this account CAN see that is the same site as
    GSC_PROPERTY, written differently — or ''.

    Search Console treats 'https://www.site.com/' and 'sc-domain:site.com' as
    different properties. When he registered one and Setup holds the other,
    every pull came back 403 and an IT ticket said "service account fix
    needed" — when the account already had access and only the spelling was
    off.
    """
    try:
        if get is None:
            import requests as _rq
            get = _rq.get
        r = get(host + "/webmasters/v3/sites", timeout=30,
                headers={"Authorization": "Bearer " + creds.token})
        if r.status_code != 200:
            return ""
        seen = [x.get("siteUrl") for x in (r.json().get("siteEntry") or [])
                if x.get("siteUrl")]
    except Exception:
        return ""
    same = [x for x in seen if x != prop and _bare_site(x) == _bare_site(prop)]
    # A domain property covers every form of the site; prefer it.
    same.sort(key=lambda x: 0 if x.startswith("sc-domain:") else 1)
    return same[0] if same else ""


def _dropped(e) -> bool:
    """A connection that fell over on the way, rather than Google saying no."""
    t = f"{type(e).__name__}: {e}".lower()
    return any(w in t for w in ("connection aborted", "remotedisconnected",
                                "connection reset", "timed out", "timeout",
                                "temporarily unavailable", "name resolution",
                                "max retries", "connectionerror", "eof occurred"))


def _login_failed(e) -> str:
    """Why the Search Console login did not happen, in words he can act on."""
    raw = f"{type(e).__name__}: {e}"
    if _dropped(e):
        return ("Google did not answer the Search Console login — the connection "
                f"was closed before a reply ({raw[:120]}). It was tried three times. "
                "This is nearly always a short drop on this computer's internet or "
                "at Google, not a problem with your setup; it is tried again on the "
                "next run, and the free keyword sources carried on without it.")
    if "invalid_grant" in raw.lower():
        return ("Google refused the service-account key (invalid_grant). Either the "
                "key was deleted in Google Cloud, or this computer's clock is wrong "
                "by more than a few minutes. Check the clock first; if it is right, "
                "create a new key and upload it on /setup → Integrations.")
    return (f"The Search Console login failed ({raw[:160]}). The free keyword "
            "sources carried on without it.")


def gsc_queries(days=90, limit=500):
    """
    Real queries that brought impressions to this site, with position.

    Returns [] and a reason rather than raising, because every other source here
    works without it and a missing optional login must not stop the pass.
    """
    ok, why = gsc_ready()
    if not ok:
        return [], why
    import google.auth.transport.requests
    from google.oauth2 import service_account
    import requests

    from providers import integrations
    prop = (integrations.read_env().get("GSC_PROPERTY") or "").strip()

    # THE LOGIN WAS THE ONE STEP NOT GUARDED, and it took the whole desk down:
    # "seo-strategist crashed: FAILED: TransportError: ('Connection aborted.',
    # RemoteDisconnected)". Google's token server — or the line to it — dropped
    # the connection while this fetched a login, the exception escaped, and
    # the pass died with every other source unread, then set off a fault, an
    # IT ticket and a string of escalations about one dropped connection. The
    # promise above is "returns [] and a reason rather than raising"; this is
    # that promise kept for the login too. A dropped connection is tried again
    # twice, a little later each time; a refusal is not, because asking again
    # does not change Google's mind.
    try:
        creds = _sa_credentials(service_account, [GSC_SCOPE])
    except Exception as e:
        return [], (f"the Search Console key file could not be read ({e}). Upload "
                    "the service-account JSON again on /setup → Integrations.")
    err = None
    for wait in (0, 2, 5):
        if wait:
            time.sleep(wait)
        try:
            creds.refresh(google.auth.transport.requests.Request())
            err = None
            break
        except Exception as e:
            err = e
            if not _dropped(e):
                break
    if err is not None:
        return [], _login_failed(err)

    end = time.strftime("%Y-%m-%d", time.localtime())
    start = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    # The documented host is www.googleapis.com. An earlier version of this used
    # searchconsole.googleapis.com — that host serves the newer URL-inspection
    # API, not webmasters/v3, so the very first real call would have come back
    # 404 and read like a permissions problem. Both are tried, documented one
    # first, because this path cannot be tested without a live property.
    hosts = ("https://www.googleapis.com", "https://searchconsole.googleapis.com")
    path = ("/webmasters/v3/sites/" + urllib.parse.quote(prop, safe="")
            + "/searchAnalytics/query")
    body = {"startDate": start, "endDate": end,
            "dimensions": ["query"], "rowLimit": limit}
    last = ""
    rows = None
    for host in hosts:
        try:
            r = requests.post(host + path, timeout=45,
                              headers={"Authorization": "Bearer " + creds.token},
                              json=body)
        except Exception as e:
            last = f"Search Console call failed: {e}"
            continue
        if r.status_code == 200:
            rows = r.json().get("rows") or []
            break
        # 403 and 404 mean different things and the fix is different for each,
        # so neither is reported as a bare status code.
        if r.status_code == 403 and _api_switched_off(r.text or ""):
            return [], _api_switched_off(r.text or "")
        if r.status_code == 403:
            # THE SAME SITE, WRITTEN THE WAY GOOGLE KNOWS IT. If the account can
            # see this site under another spelling, use that — and keep it, so
            # the next pass does not trip over the same thing.
            other = _same_site_property(creds, host, prop)
            if other:
                try:
                    r2 = requests.post(
                        host + "/webmasters/v3/sites/" + urllib.parse.quote(other, safe="")
                        + "/searchAnalytics/query", timeout=45, json=body,
                        headers={"Authorization": "Bearer " + creds.token})
                except Exception:
                    r2 = None
                if r2 is not None and r2.status_code == 200:
                    try:
                        integrations.write_env({"GSC_PROPERTY": other})
                    except Exception:
                        pass
                    rows = r2.json().get("rows") or []
                    break
            # Google returns 403 for BOTH "you are not a user here" and "that
            # property does not exist as you typed it", because a property you
            # cannot see is indistinguishable from one that is not there. Naming
            # only the first sent the owner to the permissions screen for a
            # property string that was missing its trailing slash.
            slash = "" if prop.startswith("sc-domain:") or prop.endswith("/") else (
                f" Note: {prop!r} has no trailing slash. A URL-prefix property "
                f"must be given exactly as Search Console shows it, "
                f"'{prop}/' — without it Google does not recognise the property.")
            # DO NOT MAKE HIM GUESS BETWEEN THE TWO CAUSES.
            #
            # Google answers this itself: /sites lists every property this
            # service account can actually see. An empty list means it has
            # been added to nothing, and the fix is the permissions screen. A
            # non-empty list that does not contain GSC_PROPERTY means the
            # string is wrong, and the right one is sitting in that list.
            #
            # The old message named both causes and left him to work out
            # which. On his own site the answer is almost certainly the www:
            # he verified one form in Search Console and set the other here.
            return [], (f"Search Console refused (403) for {prop}." + slash + " "
                        + _which_403(creds, host, prop))
        if r.status_code == 404 and host is hosts[-1]:
            return [], (f"Search Console has no property called {prop!r} (404). It "
                        f"must match exactly: 'sc-domain:yoursite.ae' for a domain "
                        f"property, or 'https://yoursite.ae/' with the trailing "
                        f"slash for a URL-prefix one.")
        last = f"Search Console said {r.status_code}: {r.text[:160]}"
    if rows is None:
        return [], last or "Search Console did not answer"

    return [{"phrase": (row.get("keys") or [""])[0].strip().lower(),
             "impressions": int(row.get("impressions") or 0),
             "clicks": int(row.get("clicks") or 0),
             "position": round(float(row.get("position") or 0), 1)}
            for row in rows if (row.get("keys") or [""])[0].strip()], ""


# ---------------------------------------------------------------------------
# 2. AUTOCOMPLETE, WORKED PROPERLY
# ---------------------------------------------------------------------------

def _bing_suggest(q):
    """A second free opinion. Bing's suggestions differ enough to be worth it."""
    url = "https://api.bing.com/osjson.aspx?" + urllib.parse.urlencode({"query": q})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=SUGGEST_TIMEOUT) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        return [s.strip().lower() for s in (d[1] if len(d) > 1 else []) if s.strip()]
    except Exception:
        return []


def _suggest(q, deadline=None):
    """
    Both engines, deduped. seo_gap owns the Google one; do not duplicate it.

    The clock is checked BETWEEN the two, not only between probes. Each is a
    network call, and a probe that starts a tick inside the deadline used to be
    allowed to spend both of its timeouts running past it.
    """
    out = []
    try:
        out += seo_gap._suggest(q)
    except Exception:
        pass
    if deadline and time.time() > deadline:
        return out
    out += _bing_suggest(q)
    seen, uniq = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


# THE CLOCK, AND WHY IT RAN OUT ANYWAY.
#
# The daily runner kills a step at 420 seconds. This pass works to a budget so
# that a step dying at 419 does not throw away every phrase it gathered —
# partial and honest beats complete and killed.
#
# It timed out anyway, on his machine, at 420 seconds. Three reasons, and the
# budget was only guarding against one of them:
#
#   THE BUDGET ONLY COVERED THE GATHERING LOOP. Everything before it — taking
#   the shelf lock, reading Search Console — and everything after it — scoring,
#   writing to the cabinet, posting the task, handing pages to Content — ran on
#   no clock at all. 300 seconds of gathering plus all of that does not fit in
#   420.
#
#   TAKING THE SHELF LOCK COULD BLOCK FOR 120 SECONDS on its own, before a
#   single phrase was gathered. 120 + 300 is 420 exactly, with nothing left for
#   the work.
#
#   ONE PROBE COULD TAKE 22 SECONDS. Google's autocomplete had a 12-second
#   timeout and Bing's 10, and the clock was only checked BETWEEN probes — so
#   the last probe of the pass could start a tick inside the deadline and run
#   22 seconds past it. Four seconds each now: an autocomplete endpoint that has
#   not answered in four seconds is not going to say anything useful, and this
#   makes hundreds of the calls.
#
# So: one clock for the whole pass, started before anything else, derived from
# the runner's own ceiling rather than from a number typed here that can drift
# away from it.
SUGGEST_TIMEOUT = 4     # per autocomplete call, per engine

# What fraction of the step's ceiling this pass will use. The rest is headroom
# for the process to start, the cabinet to open and the step to be recorded.
OF_THE_CEILING = 0.80

# Held back from gathering for the write-up: scoring every phrase, the cabinet
# writes, the task, the handover to Content. Measured at about eight seconds on
# a normal pass; thirty is room for a bad one.
WRITE_RESERVE = 30

# The longest this will wait for the SEO shelf before giving up. Waiting two
# minutes and then having no time left to work is worse than saying the shelf
# was busy and coming back tomorrow.
BORROW_WAIT = 30

TIME_BUDGET = 300      # only used if the runner's ceiling cannot be read


def step_ceiling() -> int:
    """
    The runner's own step timeout, read from the runner.

    Not copied. A second copy of that number is a second thing to forget when
    the first one changes, and this file has the scars: the budget said 300
    against a ceiling of 420 and nobody noticed the sum did not work.
    """
    try:
        from . import day_run
        # THIS STEP'S ceiling, not the day's default. seo_intel is one of the
        # two steps given longer than everything else, and reading the default
        # meant the pass budgeted itself for seven minutes inside a fifteen
        # minute slot and cut its own gathering short for no reason.
        for_this = getattr(day_run, "step_timeout_for", None)
        if callable(for_this):
            return int(for_this("pipelines.seo_intel")) or TIME_BUDGET
        return int(getattr(day_run, "STEP_TIMEOUT", 0)) or TIME_BUDGET
    except Exception:
        return TIME_BUDGET


def time_budget(explicit=None):
    """How long the WHOLE pass gets, gathering and writing up together."""
    if explicit:
        return int(explicit)
    env = _num("SEO_TIME_BUDGET", 0)
    if env:
        return int(env)
    return max(60, int(step_ceiling() * OF_THE_CEILING))


def expand(seed, areas=None, deep=False, pause=0.1, deadline=None):
    """
    One seed, worked from every angle a customer would type it.

    Autocomplete only answers the prefix you give it, so asking once for
    "glass partition" and stopping is how you get eight results and conclude
    there is no demand. Asking it the way people actually type — with the area,
    with the price question, with the letter they are part-way through — is what
    turns one seed into forty real phrases for nothing.
    """
    areas = list(areas or areas_list()[:3])
    prefixes = [seed] + [f"{w} {seed}" for w in ask_words()]
    suffixes = [f"{seed} {a}" for a in areas] + [f"{seed} {w}" for w in buy_words()]
    probes = prefixes + suffixes
    if deep:
        probes += [f"{seed} {c}" for c in "abcdefghijklmnopqrstuvwxyz"]

    found, stopped = {}, False
    for p in probes:
        if deadline and time.time() > deadline:
            stopped = True
            break
        for s in _suggest(p, deadline=deadline):
            found.setdefault(s, 0)
            found[s] += 1
        # Free endpoints, so be a good guest — but nineteen seeds at twenty
        # probes each spent seventy-six seconds of a three-hundred-second
        # budget doing nothing but sleeping. A tenth of a second is still
        # polite and buys back half a seed.
        time.sleep(pause)
    return found, stopped


# ---------------------------------------------------------------------------
# 3. HIS OWN ENQUIRIES — the words nobody else can see
# ---------------------------------------------------------------------------

_WORD = re.compile(r"[a-z][a-z\-]{2,}")
_STOP = set("""the and for you your our with from that this have has are was were
will would can could please dear hello hi thanks thank regards kindly need want
about into out get got any all one two some more most very just like also than
then them they their there here what when where which who how why""".split())


def from_enquiries(con, limit=400):
    """
    What real customers called it, taken from what they actually wrote in.

    This is the one source in the file that no competitor and no paid tool can
    reproduce, because it is his own inbox. If nine people wrote "office
    partition" and nobody wrote "glass division wall", that settles a wording
    argument no keyword tool can.
    """
    words, phrases = {}, []
    try:
        rows = con.execute(
            "SELECT body FROM outreach_log WHERE direction='reply'"
            " AND body IS NOT NULL ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
    except sqlite3.Error:
        rows = []
    try:
        rows += con.execute(
            "SELECT COALESCE(segment,'') || ' ' || COALESCE(company,'') AS body"
            " FROM leads ORDER BY found_at DESC LIMIT ?", (limit,)).fetchall()
    except sqlite3.Error:
        pass
    for r in rows:
        text = (r[0] or "").lower()
        if not text.strip():
            continue
        phrases.append(text[:200])
        for w in _WORD.findall(text):
            if w not in _STOP:
                words[w] = words.get(w, 0) + 1
    return words, phrases


# ---------------------------------------------------------------------------
# 4. WHAT RIVALS BUILT PAGES FOR
# ---------------------------------------------------------------------------

_SLUG = re.compile(r"[^a-z0-9]+")


def from_rivals(con, limit=600):
    """
    A rival's page slugs, read as a market signal.

    Nobody keeps a page live for two years about something that never sold. A
    phrase three competitors have each built a page for is a phrase with money
    behind it, and this costs nothing beyond the sitemaps already in the cabinet.
    """
    topics = {}
    try:
        rows = con.execute(
            "SELECT url FROM competitor_pages ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
    except sqlite3.Error:
        try:
            rows = con.execute(
                "SELECT url FROM competitor_changes ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        except sqlite3.Error:
            return {}
    for (u,) in rows:
        path = urllib.parse.urlparse(u or "").path
        slug = _SLUG.sub(" ", (path.rsplit("/", 1)[-1] or "").lower()).strip()
        if len(slug) > 4 and not slug.isdigit():
            topics[slug] = topics.get(slug, 0) + 1
    return topics


# ---------------------------------------------------------------------------
# SCORING — evidence only, never a volume estimate
# ---------------------------------------------------------------------------

# WHERE "STRIKING DISTANCE" STARTS AND STOPS.
# 5th to 20th is the usual rule of thumb, not a law of nature — a site with
# very little traffic may want to chase to 30, one with plenty may only care
# about 5 to 10. Both ends move from .env.
def striking_band():
    return _num("SEO_STRIKING_FROM", 4.5), _num("SEO_STRIKING_TO", 20.5)


def striking_min_impressions():
    return int(_num("SEO_STRIKING_MIN_IMPRESSIONS", 10))


STRIKING_LOW, STRIKING_HIGH = 4.5, 20.5      # read via striking_band() at call time


def score(row):
    """
    One number, built only from things we can point at. Weights in plain terms:

      real impressions      heaviest, because it is the only PROVEN demand here
      striking distance     a bonus, because it is the cheapest traffic there is
      autocomplete breadth  moderate — demand shape, explicitly not volume
      rival pages           moderate — somebody is paying to keep that page up
      buying intent         moderate — a quote request beats a curiosity
      names a UAE area      required, effectively; this business is local
    """
    w = weights()
    s = 0.0
    s += min(row.get("impressions", 0), 500) * w["impressions"]
    if row.get("striking"):
        s += w["striking"]
    s += min(row.get("suggest_breadth", 0), 30) * w["breadth"]
    s += min(row.get("rival_pages", 0), 5) * w["rivals"]
    if row.get("intent") == "buy":
        s += w["intent"]
    if row.get("area"):
        s += w["area"]
    if row.get("in_enquiries"):
        s += w["enquiries"]
    if row.get("we_have_page"):
        s += w["covered"]      # already covered; the gap is elsewhere
    return round(s, 1)


def evidence_line(row):
    """Why this phrase is on the list, in words the owner can check."""
    bits = []
    if row.get("impressions"):
        bits.append(f"{row['impressions']} real impressions in Search Console"
                    + (f", average position {row['position']}" if row.get("position") else ""))
    if row.get("striking"):
        bits.append("sitting just below the clicks — position 5 to 20, the cheapest "
                    "traffic you will ever buy")
    if row.get("suggest_breadth"):
        bits.append(f"{row['suggest_breadth']} autocomplete completions "
                    f"(demand breadth, NOT a volume figure)")
    if row.get("rival_pages"):
        bits.append(f"{row['rival_pages']} competitor(s) keep a page for it")
    if row.get("in_enquiries"):
        bits.append("customers used this wording when they wrote to you")
    if row.get("area"):
        bits.append(f"names {row['area']}")
    return "; ".join(bits) or "autocomplete only — thin evidence, treat with care"


# ---------------------------------------------------------------------------
# THE PASS
# ---------------------------------------------------------------------------

def _table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS keyword_intel (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phrase TEXT NOT NULL UNIQUE, area TEXT, intent TEXT,
        impressions INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0,
        position REAL DEFAULT 0, striking INTEGER DEFAULT 0,
        suggest_breadth INTEGER DEFAULT 0, rival_pages INTEGER DEFAULT 0,
        in_enquiries INTEGER DEFAULT 0, we_have_page INTEGER DEFAULT 0,
        score REAL DEFAULT 0, evidence TEXT, sources TEXT, seen_at INTEGER)""")
    con.commit()


def _cursor(con, value=None):
    """
    Which seed the next pass should start on. Read with no value, write with one.

    Kept in the cabinet rather than in memory because the point is to survive
    between runs — the whole bug was a pass that forgot where it got to.
    """
    try:
        con.execute("CREATE TABLE IF NOT EXISTS seo_cursor ("
                    "id INTEGER PRIMARY KEY CHECK (id = 1), seed_index INTEGER,"
                    " updated_at INTEGER)")
        if value is None:
            r = con.execute("SELECT seed_index FROM seo_cursor WHERE id=1").fetchone()
            return int(r[0]) if r and r[0] is not None else 0
        con.execute("INSERT INTO seo_cursor(id, seed_index, updated_at) VALUES(1,?,?)"
                    " ON CONFLICT(id) DO UPDATE SET seed_index=excluded.seed_index,"
                    " updated_at=excluded.updated_at", (int(value), int(time.time())))
        con.commit()
    except sqlite3.Error:
        return 0
    return value


def build(con, seeds=None, deep=False, our_paths=(), budget=None,
          deadline=None) -> dict:
    seeds = list(seeds or seeds_from_catalogue(con))
    gsc, gsc_why = gsc_queries()
    gsc_note(not gsc_why, gsc_why, len(gsc))
    _lo, _hi = striking_band()
    _minimp = striking_min_impressions()
    rows = {}

    def touch(phrase):
        phrase = " ".join((phrase or "").lower().split())
        if not phrase or len(phrase) < 4:
            return None
        if not seo_gap._in_scope(phrase):
            return None
        return rows.setdefault(phrase, {"phrase": phrase, "sources": set()})

    for g in gsc:
        r = touch(g["phrase"])
        if r is None:
            continue
        r.update({"impressions": g["impressions"], "clicks": g["clicks"],
                  "position": g["position"],
                  "striking": int(_lo <= g["position"] <= _hi
                                  and g["impressions"] >= _minimp)})
        r["sources"].add("search-console")

    # WHERE TODAY STARTS.
    #
    # The pass stops when the clock runs out and the summary said "the rest go
    # on tomorrow's pass". It did not: the loop began at seeds[0] every time, so
    # a run that reached six of twelve seeds would reach the same six tomorrow
    # and the other six would never be gathered at all. Six services invisible
    # for ever, and the summary reassuring him about it.
    #
    # A cursor fixes it. Tomorrow genuinely begins where today gave up, and the
    # whole list gets covered over as many passes as it takes.
    start = _cursor(con)
    seeds = seeds[start:] + seeds[:start]
    # THE DEADLINE COMES FROM THE CALLER NOW. It used to be worked out here,
    # which meant the gathering loop got the whole budget and the write-up that
    # follows it got none — see the note on time_budget. Called on its own, it
    # still gets a sensible clock of its own.
    if deadline is None:
        deadline = time.time() + time_budget(budget)
    # And gathering stops early enough to write down what it gathered.
    gather_until = deadline - WRITE_RESERVE
    seeds_done, ran_out = 0, False
    for seed in seeds:
        if time.time() > gather_until:
            ran_out = True
            break
        got, stopped = expand(seed, deep=deep, deadline=gather_until)
        seeds_done += 1
        ran_out = ran_out or stopped
        for phrase, hits in got.items():
            r = touch(phrase)
            if r is None:
                continue
            r["suggest_breadth"] = r.get("suggest_breadth", 0) + hits
            r["sources"].add("autocomplete")

    words, _phrases = from_enquiries(con)
    rivals = from_rivals(con)
    for r in rows.values():
        toks = [w for w in _WORD.findall(r["phrase"]) if w not in _STOP]
        if toks and sum(words.get(w, 0) for w in toks) >= 2:
            r["in_enquiries"] = 1
            r["sources"].add("your own enquiries")
        hits = sum(n for slug, n in rivals.items()
                   if slug and slug in r["phrase"].replace(" ", " "))
        if hits:
            r["rival_pages"] = hits
            r["sources"].add("rival pages")
        r["area"] = seo_gap._area_of(r["phrase"]) or ""
        r["intent"] = seo_gap._intent(r["phrase"])
        r["we_have_page"] = int(any(
            all(t in p for t in toks[:2]) for p in our_paths)) if toks else 0
        r["score"] = score(r)
        r["evidence"] = evidence_line(r)

    now = int(time.time())
    _table(con)
    for r in rows.values():
        con.execute(
            "INSERT INTO keyword_intel(phrase,area,intent,impressions,clicks,position,"
            " striking,suggest_breadth,rival_pages,in_enquiries,we_have_page,score,"
            " evidence,sources,seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(phrase) DO UPDATE SET area=excluded.area,intent=excluded.intent,"
            " impressions=excluded.impressions,clicks=excluded.clicks,position=excluded.position,"
            " striking=excluded.striking,suggest_breadth=excluded.suggest_breadth,"
            " rival_pages=excluded.rival_pages,in_enquiries=excluded.in_enquiries,"
            " we_have_page=excluded.we_have_page,score=excluded.score,"
            " evidence=excluded.evidence,sources=excluded.sources,seen_at=excluded.seen_at",
            (r["phrase"], r.get("area", ""), r.get("intent", ""),
             r.get("impressions", 0), r.get("clicks", 0), r.get("position", 0.0),
             r.get("striking", 0), r.get("suggest_breadth", 0), r.get("rival_pages", 0),
             r.get("in_enquiries", 0), r.get("we_have_page", 0), r.get("score", 0.0),
             r.get("evidence", ""), ",".join(sorted(r["sources"])), now))
    con.commit()
    _cursor(con, (start + seeds_done) % max(len(seeds), 1))
    return {"phrases": len(rows), "gsc_rows": len(gsc), "gsc_why": gsc_why,
            "striking": sum(1 for r in rows.values() if r.get("striking")),
            "from_enquiries": sum(1 for r in rows.values() if r.get("in_enquiries")),
            "seeds_done": seeds_done, "seeds_total": len(seeds),
            "started_at_seed": start, "ran_out_of_time": ran_out}


def striking_distance(con, limit=15):
    """Ranked 5th to 20th with real impressions. Free money, no tool required."""
    _table(con)
    return [dict(r) for r in con.execute(
        "SELECT * FROM keyword_intel WHERE striking=1"
        " ORDER BY impressions DESC LIMIT ?", (limit,))]


def ask_for_help(con, built, top) -> list:
    """
    When this sector cannot do its job alone, it asks — it does not shrug.

    The handbook is explicit: a sector that needs something outside its own
    expertise posts a request and waits, rather than guessing. SEO kept hitting
    exactly those walls and swallowing them: Search Console refused and the pass
    just reported zero, the site sold things the catalogue had never heard of
    and they were silently dropped from the research, rivals were never read and
    nobody was told the signal was missing.

    Each one below is a real dependency on another sector, with the evidence
    attached. Nothing is invented: it only asks when the cabinet shows it
    actually needs the answer.

    WHAT CHANGED, AND WHY. The trigger and the facts were always real, but the
    wording and the choice of who to ask were fixed text written months ago —
    the agent filled in a form. Each one now goes through coordination/ask.py:
    SEO's own brain is shown the situation and decides whether it is worth
    another sector's time, which sector actually owns it, and how to put it. The
    facts below are still gathered here, from rows, and attached to whatever it
    writes; a model may reason, it may not supply evidence. With no brain routed
    to SEO the original wording is used and the request says so.
    """
    asked = []
    try:
        from coordination import ask as _ask
    except Exception:
        _ask = None

    def _send(to, situation, facts, subject, body, about=""):
        if _ask is None:
            return board.post_request("SEO", to, subject, body, about=about)
        r = _ask.send("SEO", to, situation, facts, subject, body, about=about)
        return r.get("id")

    # 1. Search Console is IT's door — it is a property and a service account,
    #    not a keyword question, and SEO cannot fix either.
    why = built.get("gsc_why") or ""
    if why and not built.get("gsc_rows"):
        asked.append(_send(
            "IT",
            "Search Console returned nothing this pass, so the keyword work ran "
            "without the only source that knows which searches already reach us. "
            "It is a property and a service account, which SEO cannot fix.",
            [f"Google's exact words: {why}",
             f"striking-distance queries available this pass: "
             f"{built.get('gsc_rows') or 0}"],
            "Search Console is not answering, and it is our best source",
            "The keyword pass ran without the one source that knows which searches "
            "already reach us. Until this works there is no striking-distance list "
            "— the queries we rank 5th to 20th for, which are the cheapest traffic "
            "available. Everything else in the pass carried on and is on file.",
            about="handoff:gsc_down"))

    # 2. Things the SITE sells that the catalogue has never heard of. Content
    #    owns the service list; SEO must not quietly widen it on its own.
    try:
        unknown = [r[0] for r in con.execute(
            "SELECT DISTINCT phrase FROM keywords WHERE intent='site' LIMIT 20")]
    except sqlite3.Error:
        unknown = []
    if unknown:
        asked.append(_send(
            "Content",
            f"{len(unknown)} thing(s) appear on our own website but have no entry "
            "in the service catalogue, so nothing targets them and nothing is "
            "written about them. Content owns the service list; SEO must not "
            "widen it on its own.",
            [f"found on the site, not in the catalogue: {u}" for u in unknown[:20]],
            f"{len(unknown)} thing(s) on our site are not in the service catalogue",
            "These appear on our own website but the agency has no service entry "
            "for them, so nothing targets them and nothing writes about them. If "
            "they are real services, catalogue them and they join the research "
            "automatically. If they are not, say so and I will stop reading them "
            "off the site."))

    # 3. No rival signal at all. Knowing what competitors keep a page for is one
    #    of the four things this ranks on, and it comes from the rival watch.
    try:
        rivals = con.execute("SELECT COUNT(*) FROM competitors").fetchone()[0]
    except sqlite3.Error:
        rivals = 0
    if not rivals:
        asked.append(board.post_request(
            "SEO", "SEO", "No competitors on file, so one of four signals is blank",
            "The keyword ranking uses 'how many rivals keep a page for this' as a "
            "market signal — nobody maintains a page for two years about something "
            "that never sold. There are no competitors in the cabinet, so that "
            "signal is contributing nothing and the ranking is running on three "
            "legs instead of four. Add rivals and run the watch."))

    # 4. Nothing from his own enquiries. That is the one source no competitor
    #    has, and it is empty until Leads has replies on file.
    if top and not built.get("from_enquiries"):
        asked.append(_send(
            "Leads",
            "The keyword ranking uses the words real customers write to us, which "
            "is the one signal no competitor can copy. There are no replies on "
            "file, so that part of the ranking is idle. This may simply be where "
            "the business is, in which case it is not worth anyone's time.",
            ["replies on file to read: 0",
             f"phrases shortlisted without any customer wording: {len(top)}"],
            "No customer wording on file yet",
            "The strongest signal we have that nobody else can copy is the words "
            "customers use when they write in. There are no replies in the cabinet "
            "to read, so that part of the ranking is idle. Once outreach starts "
            "getting answers this sharpens on its own — no action needed if that "
            "is simply where we are."))

    return [a for a in asked if a]


def critic_check(built, top):
    reasons = []
    if not built.get("phrases"):
        reasons.append("nothing was gathered, so there is nothing to conclude")
    if built.get("phrases") and not top:
        reasons.append("phrases were gathered but none scored — the ranking is broken")
    thin = [t for t in top if not (t["impressions"] or t["suggest_breadth"]
                                   or t["rival_pages"] or t["in_enquiries"])]
    if thin:
        reasons.append(f"{len(thin)} shortlisted phrase(s) carry no evidence at all")
    if built.get("gsc_rows") and not any(t["impressions"] for t in top):
        reasons.append("Search Console answered but no real query reached the "
                       "shortlist — the scope filter is probably too tight")
    return (len(reasons) == 0, reasons)


def run(agent=AGENT, deep=False, budget=None) -> dict:
    with board.agent_run(agent) as run_row:
        with deskwork.at_the_desk("SEO", agent, run_row) as job:
            out = _work(agent, deep, budget)
            deskwork.settle(job, out, made=out.get("phrases"),
                            what="phrase scored|phrases scored")
        run_row.note(out.get("summary") or out.get("error") or "keyword intel pass")
        return out


def _work(agent, deep, budget=None) -> dict:
    board.ensure_schema()
    # THE CLOCK STARTS HERE, BEFORE ANYTHING ELSE — including the shelf lock.
    # It used to start inside build(), so taking the lock could burn two
    # minutes and the gathering would still believe it had its full budget.
    deadline = time.time() + time_budget(budget)

    b = cabinet.borrow("seo_data", agent, "SEO",
                       reason="free keyword intelligence pass (no paid API)",
                       data_class="external", wait=True, timeout=BORROW_WAIT)
    borrow_id = b.get("borrow_id")
    if not borrow_id:
        # Busy is not broken. Saying so and coming back tomorrow beats waiting
        # out the lock and then being killed with nothing gathered.
        return {"ok": False, "agent": agent,
                "error": ("the SEO shelf was busy — another agent is using it. "
                          "Nothing was gathered; the next pass picks it up."),
                "shelf_busy": True}
    try:
        con = sqlite3.connect(board.DB, timeout=30)
        con.row_factory = sqlite3.Row

        our_paths = []
        try:
            our_paths = [r[0].lower() for r in con.execute(
                "SELECT DISTINCT url FROM aeo_findings")]
        except sqlite3.Error:
            pass

        built = build(con, deep=deep, our_paths=our_paths, budget=budget,
                      deadline=deadline)
        top = [dict(r) for r in con.execute(
            "SELECT * FROM keyword_intel ORDER BY score DESC LIMIT 20")]
        ok, reasons = critic_check(built, top)
        result = {"ok": True, "agent": agent, **built,
                  "top": [{"phrase": t["phrase"], "score": t["score"],
                           "why": t["evidence"], "sources": t["sources"]}
                          for t in top[:10]],
                  "wins": [{"phrase": w["phrase"], "position": w["position"],
                            "impressions": w["impressions"]}
                           for w in striking_distance(con)]}
        if not ok:
            con.close()
            result.update({"critic": "rejected", "critic_reasons": reasons,
                           "summary": "critic rejected: " + "; ".join(reasons)})
            return result
        result["critic"] = "passed"

        wins = result["wins"]
        if wins:
            board.try_post_task(
                "SEO", f"{len(wins)} page(s) are one push from the first page",
                "Google already ranks these 5th to 20th with real impressions. "
                "They need better answers on the page, not new pages:\n- "
                + "\n- ".join(f"{w['phrase']} — position {w['position']}, "
                              f"{w['impressions']} impressions" for w in wins)
                + "\n\nThis came from Search Console, which is free. No keyword "
                  "tool was used or needed.",
                risk="green", created_by="SEO")

        writeable = [t for t in top if not t["we_have_page"]][:6]
        if writeable:
            board.post_request(
                "SEO", "Content", "Pages worth writing, with the evidence",
                "Ranked on evidence we can point at — real Search Console "
                "impressions, autocomplete breadth, rival pages, and the wording "
                "your own customers used. None of these is a search-volume "
                "estimate and none of them cost anything:\n\n"
                + "\n".join(f"- {t['phrase']}\n    {t['evidence']}" for t in writeable),
                about="handoff:pages")

        # Ask for what it cannot get itself, before logging the decision — so
        # the decision can honestly say the ranking ran short and who was asked.
        helped = ask_for_help(con, built, top)
        result["asked_for_help"] = helped

        did = board.log_decision(
            "seo-intel",
            reasoning=(f"Gathered {built['phrases']} in-scope phrases with no paid "
                       f"keyword API. Search Console contributed {built['gsc_rows']} "
                       f"real queries"
                       + ("" if built["gsc_rows"] else f" (unavailable: {built['gsc_why']})")
                       + f"; {built['striking']} are in striking distance. Ranked on "
                       f"provable evidence, never on an estimated volume."),
            expectation=("The striking-distance pages are improved and move onto page "
                         "one, which is measurable in Search Console within weeks "
                         "without buying anything."),
            confidence=0.75 if built["gsc_rows"] else 0.55,
            evidence={"phrases": built["phrases"], "gsc_rows": built["gsc_rows"],
                      "striking": [w["phrase"] for w in wins]})
        result["decision_id"] = did
        con.close()
        partial = ("" if not built.get("ran_out_of_time") else
                   f" (stopped at {built['seeds_done']} of {built['seeds_total']} "
                   f"seeds — out of time, the rest go on tomorrow's pass)")
        # "(-413s of the budget unspent)" — printed on the same line as "out
        # of time", which is the same fact said twice, once as nonsense.
        # `left` is deadline minus now, so a pass that OVERRAN comes out
        # negative and the sentence claimed it had negative seconds spare.
        #
        # There are three real cases and they read differently:
        #   overran   it went past its slot. Say by how much; never "unspent".
        #   stopped   it hit the deadline and put the rest back. The seeds line
        #             above already says that, so adding a spare-time figure to
        #             it is noise at best and a contradiction at worst.
        #   finished  it got through everything with time to spare, which is
        #             the only case where "unspent" means anything.
        left = int(deadline - time.time())
        result["seconds_left"] = left
        if left < 0:
            timing = f" ({abs(left)}s over its budget)"
        elif built.get("ran_out_of_time"):
            timing = ""
        else:
            timing = f" ({left}s of the budget unspent)"
        result["summary"] = (
            f"{built['phrases']} phrases from free sources, {built['striking']} in "
            f"striking distance, {len(writeable)} pages handed to Content" + partial
            + timing)
        return result
    finally:
        if borrow_id:
            cabinet.release("seo_data", agent, borrow_id=borrow_id, usd_cost=0.0)


def _nearly(con):
    """
    Rows that were close but did not qualify, so an empty answer still says
    something useful about where you actually are.
    """
    out = {"rows": 0, "with_impressions": 0, "best": []}
    try:
        out["rows"] = con.execute("SELECT COUNT(*) FROM keyword_intel").fetchone()[0]
        out["with_impressions"] = con.execute(
            "SELECT COUNT(*) FROM keyword_intel WHERE impressions > 0").fetchone()[0]
        out["best"] = [dict(r) for r in con.execute(
            "SELECT phrase, position, impressions FROM keyword_intel"
            " WHERE impressions > 0 AND position > 0"
            " ORDER BY position ASC LIMIT 8")]
    except sqlite3.Error:
        pass
    return out


def _why_no_wins(near) -> str:
    """
    Which link in the chain is missing — not a list of things it might be.

    "Either the pass has not run, or Search Console is not connected" was true
    and useless: it handed back the diagnosis as homework. Every step below is
    checkable from here, so it gets checked.
    """
    L = ["NOTHING IN STRIKING DISTANCE YET", "=" * 60, ""]
    ok, why = gsc_ready()

    # gsc_ready() only checks CONFIGURATION — property set, file on disk,
    # library importable. All three can be true while Google still refuses the
    # call. That happened: the pass got a clean 403 and said so, and then this
    # function skipped straight past it and offered two guesses, one of them
    # wrong. So when the config looks fine, ASK GOOGLE. It is one free call and
    # it replaces a guess with what actually came back.
    if ok:
        _rows, live = gsc_queries(days=30, limit=1)
        if live:
            L += ["  Search Console is configured, but Google refused the call.",
                  "  This is what it said, verbatim:", "",
                  "    " + live, ""]
            if "403" in live:
                L += ["  A 403 has two causes and BOTH are worth checking, because",
                      "  Google returns the same code for each:", "",
                      "    1. The service account is not a user on the property.",
                      "       Open the service-account JSON, copy client_email",
                      "       (it ends @...gserviceaccount.com), and add it in",
                      "       Search Console under Settings, Users and",
                      "       permissions. Uploading the file here does NOT grant",
                      "       access — Google has to be told separately.",
                      "",
                      "    2. GSC_PROPERTY is not character-for-character what",
                      "       Search Console shows. A property you do not own",
                      "       reads as one you may not see, so it 403s rather",
                      "       than 404s. A URL-prefix property needs the TRAILING",
                      "       SLASH: 'https://www.example.com/' — without it,",
                      "       Google does not recognise the property at all.", ""]
            return "\n".join(L)

    if not ok:
        L += ["  Search Console is not usable yet, and it is the ONLY source for",
              "  this list — striking distance means 'Google already ranks you",
              "  5th to 20th', and nothing but Google can tell you that.", "",
              "  What is missing:", "    " + why, ""]
        try:
            import google.oauth2.service_account  # noqa: F401
        except Exception:
            L += ["  Start here:", "    pip install -r requirements.txt", ""]
        return "\n".join(L)

    if not near["rows"]:
        L += ["  Search Console is connected, but the keyword pass has never run,",
              "  so there is nothing on file to read.", "",
              "  Run it once (a few minutes, no cost):",
              "    python -m pipelines.seo_intel", ""]
        return "\n".join(L)

    if not near["with_impressions"]:
        L += [f"  The pass has run — {near['rows']:,} phrases on file — but not one",
              "  carries a Search Console impression. Search Console answered and",
              "  gave back nothing for your property. Two usual causes:", "",
              "    1. The property does not match. GSC_PROPERTY must be exactly",
              "       what Search Console shows: 'sc-domain:yoursite.ae' for a",
              "       domain property, or 'https://yoursite.ae/' WITH the slash",
              "       for a URL-prefix one. They are not interchangeable.",
              "    2. The service account is not a user on that property. Add its",
              "       email in Search Console under Settings -> Users and",
              "       permissions, then run the pass again.", "",
              "  A third, less likely: the site is too new and genuinely has no",
              "  impressions in the last 90 days yet.", ""]
        return "\n".join(L)

    L += [f"  The pass has run and Search Console is answering: "
          f"{near['with_impressions']:,} of {near['rows']:,} phrases carry real",
          "  impressions. None of them sit between position 5 and 20 with enough",
          "  impressions to count, which is a real answer, not a fault.", "",
          "  Your best positions right now:"]
    for r in near["best"]:
        L.append(f"    {r['position']:>5}  {r['impressions']:>6} impressions  {r['phrase']}")
    L += ["", "  Anything above position 5 is already winning. Anything past 20 needs",
          "  a better page, not a nudge — see `--report` for what to write.", ""]
    return "\n".join(L)


def as_text(wins_only=False) -> str:
    board.ensure_schema()
    con = sqlite3.connect(board.DB, timeout=30)
    con.row_factory = sqlite3.Row
    _table(con)
    if wins_only:
        rows = striking_distance(con, 25)
        if not rows:
            near = _nearly(con)
            con.close()
            return _why_no_wins(near)
        L = ["ALREADY RANKING, JUST BELOW THE CLICKS", "=" * 60,
             "Google puts you 5th to 20th for these and people are seeing you.",
             "Improving an existing page is cheaper than writing a new one.", ""]
        for r in rows:
            L.append(f"  {r['position']:>5}  {r['impressions']:>6} impressions  {r['phrase']}")
        return "\n".join(L)

    rows = [dict(r) for r in con.execute(
        "SELECT * FROM keyword_intel ORDER BY score DESC LIMIT 30")]
    con.close()
    if not rows:
        return "No keyword intel yet — run `python -m pipelines.seo_intel`."
    ok, why = gsc_ready()
    L = [f"KEYWORD INTELLIGENCE — {len(rows)} shown, ranked on evidence", "=" * 60,
         "Not search volume. Nothing here estimates searches per month.", ""]
    for r in rows:
        flag = "★" if r["striking"] else " "
        L.append(f" {flag} {r['score']:>6}  {r['phrase']}")
        L.append(f"          {r['evidence']}")
    L += ["", "SOURCES USED", "  " + ("Search Console: connected" if ok
                                      else f"Search Console: OFF — {why}"),
          "  Autocomplete (Google + Bing): free, no key",
          "  Your own enquiries: free, and nobody else has them",
          "  Rival pages: from sitemaps already in the cabinet",
          "", "None of this costs anything. No DataForSEO, Ahrefs or Semrush "
          "subscription is required for any line above."]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(
        description="Keyword intelligence with no paid keyword tool.")
    ap.add_argument("--report", action="store_true", help="what it found, ranked")
    ap.add_argument("--wins", action="store_true",
                    help="striking distance — already ranking 5th to 20th")
    ap.add_argument("--deep", action="store_true",
                    help="also run the a-z expansion (slower, many more phrases)")
    ap.add_argument("--budget", type=int, default=0,
                    help="seconds to spend gathering (default: 900 by hand, 300 "
                         "inside the daily run, which has a step timeout)")
    ap.add_argument("--check-gsc", action="store_true",
                    help="ask Google now whether Search Console works, and say why not")
    a = ap.parse_args()
    if a.check_gsc:
        print(json.dumps(gsc_check(), ensure_ascii=False))
        return
    if a.wins:
        print(as_text(wins_only=True))
        return
    if a.report:
        print(as_text())
        return
    # Run by hand there is no 420-second step timeout to duck under, so
    # the budget is three times the one the daily runner uses.
    print(json.dumps(run(deep=a.deep, budget=a.budget or 900),
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
