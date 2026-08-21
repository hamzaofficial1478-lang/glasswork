"""
site_services.py — read the owner's own website and learn what he actually sells.

    python -m pipelines.site_services              (uses SITE_URL)
    python -m pipelines.site_services --url https://glassworks.ae

THE PROBLEM THIS SOLVES

services.py holds fourteen services that I wrote down from the trade and the
keyword work. That list is a guess, however educated. The owner's website is
the actual answer — it is where he has already decided what the business does,
in his own words, for his own market. An agency that pitches from my list
instead of his will eventually offer something he does not do, or miss
something he does, and both are embarrassing in front of a customer.

So this reads the site and reconciles the two. It reports three things:

  CONFIRMED   on the site and in the catalogue — safe to pitch
  MISSING     in the catalogue but nowhere on the site — do we still do this?
  UNCATALOGUED  named on the site but not in the catalogue — the interesting
                one. This is a service the agency currently cannot target or
                write about, and it is invisible until you look.

It changes nothing on its own. Adding a service is a decision, so the finding
goes to the owner and the catalogue is edited deliberately.

Read-only: GETs the owner's own pages and nothing else.
"""
import argparse
import json
import os
import re
import time
import urllib.parse
import urllib.request
from html import unescape as _unescape

from coordination import board, site as _site
from . import services

UA = "Mozilla/5.0 (compatible; glasswork-agency/1.0; reading our own site)"

# Where a UAE trade site usually lists what it does.
LIKELY_PATHS = ("", "/services", "/our-services", "/what-we-do", "/products",
                "/solutions", "/about", "/about-us")

# Words that mean "this is a service we offer", used to pull candidate phrases
# out of headings and nav links rather than out of the whole page.
TRADE_WORDS = ("glass", "alumin", "door", "window", "partition", "railing",
               "balustrade", "shower", "mirror", "pergola", "canopy", "cladding",
               "facade", "façade", "curtain wall", "skylight", "joinery",
               "shopfront", "shop front", "storefront", "handrail", "screen",
               "louver", "louvre", "awning", "gazebo", "shade", "fabrication",
               "installation", "repair", "maintenance", "glazing", "wooden",
               "timber", "steel", "stainless", "acp", "upvc")

# Headings, nav links and list items. NOT <title> — "Glassworks Dubai | Glass &
# Aluminium" contains the trade words and is the company name, not a service,
# and letting it in puts the business's own name on the list of things it sells.
_TAGS = re.compile(r"<(h1|h2|h3|h4|a|li)[^>]*>(.*?)</\1>", re.I | re.S)
_STRIP = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _get(url, timeout=15):
    """
    One page, through the shared reader.

    This used to open its own urllib connection. Three things came free by
    routing it through fetcher instead, and the third is why it changed:

      - the address gets checked (no private or loopback hosts), which this
        had no protection against at all;
      - a JavaScript shell is reported as a shell rather than filed as a page
        with no services on it;
      - and it joins fetcher.cache(), so a full site_check reads each page ONCE
        instead of once per pipeline. That mattered: a single test made forty-
        odd requests in a burst, the owner's server started returning a
        73-character page for about one in ten of them, and the run reported
        "0 services found" on a site that lists nine.

    Still returns "" on failure — every caller expects a string.
    """
    try:
        from . import fetcher
    except ImportError:  # pragma: no cover
        import fetcher
    return fetcher.fetch(url, timeout=timeout, allow_paid=False)["html"] or ""


def phrases_from(html):
    """
    Candidate service names: the text of headings, nav links and list items that
    mention the trade. Deliberately not the whole page — body copy is full of
    the same words in sentences, and a sentence is not a service name.
    """
    out = []
    for _tag, inner in _TAGS.findall(html or ""):
        # unescape first: "Windows &amp; Doors" is one service, and leaving the
        # entity in there means it never matches anything and shows up in the
        # report looking like something we do not sell.
        txt = _WS.sub(" ", _unescape(_STRIP.sub(" ", inner))).strip()
        if not (2 < len(txt) <= 70):
            continue
        low = txt.lower()
        if any(w in low for w in TRADE_WORDS):
            out.append(txt)
    # de-duplicate case-insensitively, keeping the first spelling seen
    seen, uniq = set(), []
    for p in out:
        k = p.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def _internal_links(html, base):
    """Same-site links only. We read the owner's site, not the whole web."""
    host = urllib.parse.urlparse(base).netloc.lower()
    found = set()
    for href in re.findall(r'href=["\']([^"\'#]+)', html or "", re.I):
        u = urllib.parse.urljoin(base, href)
        p = urllib.parse.urlparse(u)
        if p.netloc.lower() != host:
            continue
        path = p.path.rstrip("/").lower()
        if any(w in path for w in ("service", "product", "what-we-do", "solution")):
            found.add(f"{p.scheme}://{p.netloc}{p.path}")
    return sorted(found)[:8]


def discover(url, timeout=15, max_pages=10):
    """Read the site and return what it says the business does."""
    if not url:
        return {"ok": False, "error": "no website set — save it on /setup, or pass --url"}
    if "://" not in url:
        url = "https://" + url
    base = url.rstrip("/")

    pages, phrases = [], []
    to_read = [base + p for p in LIKELY_PATHS]
    home = _get(base, timeout)
    if home:
        pages.append(base)
        phrases += phrases_from(home)
        to_read += _internal_links(home, base)

    for u in to_read:
        if u in pages or len(pages) >= max_pages:
            continue
        html = _get(u, timeout)
        if not html:
            continue
        pages.append(u)
        phrases += phrases_from(html)
        time.sleep(0.3)                    # our own server, but still be polite

    if not pages:
        return {"ok": False, "error": f"could not read anything at {base} — is the "
                                      "site up, and is the URL right?"}

    # Match each candidate against the service NAMES, not the buyer signals.
    # The signals answer "who needs this" — a hotel signals shower glass — and
    # using them here fails on "Frameless Shower Enclosures", which is the name
    # of the job and mentions no buyer at all. match_by_name returns nothing
    # rather than guessing, which is what makes 'uncatalogued' meaningful.
    confirmed, unmatched = {}, []
    for p in phrases:
        key, _matched = services.match_by_name(p)
        if key:
            confirmed.setdefault(key, []).append(p)
        else:
            unmatched.append(p)

    missing = [k for k in services.SERVICES if k not in confirmed]
    return {
        "ok": True, "url": base, "pages_read": pages,
        "phrases_found": len(phrases),
        "confirmed": {k: {"label": services.label(k), "seen_as": v[:4]}
                      for k, v in confirmed.items()},
        "missing_from_site": [{"key": k, "label": services.label(k)} for k in missing],
        "uncatalogued": unmatched[:25],
        "summary": (f"{len(confirmed)} of {len(services.SERVICES)} catalogued services "
                    f"found on {base}; {len(unmatched)} phrase(s) on the site match "
                    f"nothing we can currently target"),
    }


def critic_check(d):
    """The failure here is a confident answer from a page that never loaded."""
    reasons = []
    if not d.get("pages_read"):
        reasons.append("no page was actually read — there is nothing to conclude")
    if d.get("pages_read") and not d.get("phrases_found"):
        reasons.append("pages loaded but no trade words at all — either the wrong "
                       "site, or the services are in images and cannot be read")
    if len(d.get("confirmed") or {}) == len(services.SERVICES) and not d.get("uncatalogued"):
        reasons.append("every service matched and nothing was left over — that is "
                       "suspiciously tidy, check the matcher is not waving things through")
    return (not reasons, reasons)


def run(agent="seo-auditor", url=None, timeout=15):
    with board.agent_run(agent) as run_row:
        site = _site.site_url(url)
        d = discover(site, timeout)
        if not d.get("ok"):
            run_row.note(d.get("error", "could not read the site"))
            return d

        ok, reasons = critic_check(d)
        if not ok:
            d.update({"critic": "rejected", "critic_reasons": reasons})
            run_row.note("critic rejected: " + "; ".join(reasons))
            return d
        d["critic"] = "passed"

        # An uncatalogued service is a gap in what the agency can sell. That is
        # the owner's call to make, so it goes to him rather than being silently
        # bolted into services.py by an agent.
        if d["uncatalogued"]:
            d["queued_for_owner"] = board.queue_site_change(
                agent, kind="catalogue_services",
                detail=("These appear on the website but are not in the agency's "
                        "service catalogue, so no agent can target or write about "
                        "them yet: " + "; ".join(d["uncatalogued"][:12])
                        + ". Add the ones that are real services to "
                          "pipelines/services.py."))

        d["decision_id"] = board.log_decision(
            "site-services",
            reasoning=(f"Read {len(d['pages_read'])} page(s) of {d['url']}: "
                       f"{len(d['confirmed'])} catalogued services confirmed, "
                       f"{len(d['missing_from_site'])} not found on the site, "
                       f"{len(d['uncatalogued'])} phrases we cannot target."),
            expectation="The agency sells what the owner actually sells, in his words.",
            confidence=0.6,
            evidence={"uncatalogued": d["uncatalogued"],
                      "missing_from_site": d["missing_from_site"]})
        run_row.note(d["summary"])
        return d


def as_text(d) -> str:
    if not d.get("ok"):
        return d.get("error", "unavailable")
    L = [f"WHAT {d['url']} SAYS WE DO", f"  read {len(d['pages_read'])} page(s)", ""]
    L.append("CONFIRMED — on the site and targetable")
    for k, v in d["confirmed"].items():
        L.append(f"  + {v['label']}")
        L.append(f"      on the site as: {', '.join(v['seen_as'][:2])}")
    if not d["confirmed"]:
        L.append("  (none — check the URL)")
    L += ["", "IN THE CATALOGUE BUT NOT ON THE SITE"]
    for m in d["missing_from_site"]:
        L.append(f"  ? {m['label']}")
    if not d["missing_from_site"]:
        L.append("  (none)")
    L += ["", "ON THE SITE BUT NOT IN THE CATALOGUE — cannot be targeted yet"]
    for p in d["uncatalogued"]:
        L.append(f"  ! {p}")
    if not d["uncatalogued"]:
        L.append("  (none)")
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Read our own site and list what we sell.")
    ap.add_argument("--url", help="defaults to SITE_URL")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    out = run(url=a.url)
    print(json.dumps(out, indent=2) if a.json else as_text(out))
