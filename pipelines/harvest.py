"""
harvest.py — read a business's own website and take the contact details it
published there.

    python3 -m pipelines.harvest https://somerestaurant.ae
    python3 -m pipelines.harvest https://somerestaurant.ae --json

WHY THIS EXISTS

The owner wants the name, the number and the address for every local business
worth approaching, and asked about building our own crawler off the back of a
crawl4ai-style library. Two thirds of that already existed and the last third
was the part actually costing us leads:

  Google Places gives a name, a category, an area, a phone and a website. It
  gives no address for writing to and no WhatsApp number, and it never will —
  those are not fields Maps holds.

  pipelines/fetcher.py already reads a page properly: address checked before
  the request, plain HTTP first because it is free, a rendering fetch behind it
  for JavaScript sites, cached, and metered against the daily spend cap.

  Between them sat `emails_from_site` in leads_hunt: raw urllib, no rendering,
  no cache, no spend guard, guessing at eight fixed paths. On a UAE small
  business built in Wix or Squarespace — which is most of them — it read the
  empty shell and returned nothing, and the lead was filed phone-only.

This module is that middle piece done properly: follow the site's OWN links to
its contact page, read the pages through the fetcher, and pull out every way of
reaching the business that the business itself put on the internet.

WHAT IT TAKES, AND WHAT IT REFUSES TO INVENT

  emails      from `mailto:` links first — an address in a mailto is one
              somebody deliberately published — then from the visible text.
  phones      from `tel:` links first, then UAE dialling formats in the text.
              Normalised to +971… so the same number found twice is one number.
  whatsapp    from wa.me / api.whatsapp.com / web.whatsapp.com links. A
              click-to-chat button on a company's own website is an invitation
              to message that business, which is a different thing from a
              number scraped off a person, and it is recorded as its own
              channel so the two can never be confused.
  people      ONLY from structured data the site published — schema.org
              JSON-LD. A name is personal data; guessing one out of prose
              ("ask for Ahmed") would be inventing personal data, and this
              agency does not do that. No JSON-LD, no name, and the lead is a
              company lead. That is an honest outcome.
  address     JSON-LD PostalAddress, or the og: tags.
  socials     the profile links in the footer, deduplicated by network.

Every single finding carries WHERE it was found and HOW. A number with no
provenance is a rumour, and six months from now nobody can tell a real contact
from a parsing accident.

WHAT IT WILL NOT DO

It does not contact anybody. It does not touch the cabinet. It reads pages on
the same host it was pointed at and nowhere else, at most `max_pages` of them,
politely. Everything it returns goes through the Scanner's dedup gate and
Security's clearance before a word is written to anyone — see the handbook.
"""
import argparse
import json
import re
import sys
import time
import urllib.parse

from . import fetcher

try:
    from . import leads_sources as ls
except ImportError:                                          # pragma: no cover
    ls = None

# How many pages of one site are worth reading. The contact details are on the
# home page or one click from it; a fourth page has never yet been where the
# address was, and every page is either a request to somebody else's server or
# a metered render.
MAX_PAGES = 4

# Fallback paths, tried only when the site's own links did not offer a contact
# page. Guessing is the second-best way to find it and it used to be the only
# way — a site that links "Reach us" from its nav is now followed properly.
GUESS_PATHS = ("/contact", "/contact-us", "/contactus", "/about",
               "/about-us", "/get-in-touch", "/enquiry", "/reach-us")

# Anchor text and hrefs worth a click, best first. Matched on the link, not on
# the whole page — the page saying the word "contact" somewhere is not a link.
WORTH_A_CLICK = ("contact", "reach us", "get in touch", "enquir", "inquir",
                 "about", "connect", "locations", "branches", "find us",
                 "book", "appointment", "اتصل", "تواصل")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# UAE numbers, and only UAE numbers.
#
#   +971 4 xxx xxxx     landline, Dubai (2 Abu Dhabi, 6 Sharjah, 7 RAK, 9 Fuj)
#   +971 5x xxx xxxx    mobile — 50, 52, 54, 55, 56, 58
#   04-xxx xxxx         the same landline written locally
#   05x xxx xxxx        the same mobile written locally
#
# Deliberately not a general international matcher. A loose one picks up
# licence numbers, PO boxes, price lists and dates, and a lead list full of
# those is worse than a short one.
PHONE_PATTERNS = (
    re.compile(r"(?:\+|00)\s?971[\s\-.()]*([1-9])[\s\-.()]*((?:[\s\-.()]*\d){7,8})"),
    re.compile(r"\b0([1-9])[\s\-.()]*((?:[\s\-.()]*\d){7,8})\b"),
)

WA_PATTERNS = (
    re.compile(r"(?:wa\.me|api\.whatsapp\.com/send|web\.whatsapp\.com/send)"
               r"[/?][^\"'\s<>]*?(?:phone=)?(\+?\d[\d\s\-]{7,17})", re.I),
    re.compile(r"whatsapp[^\"'<>]{0,40}?(\+?971[\d\s\-]{7,14})", re.I),
    # anywhere else: a number with its + code beside the word
    re.compile(r"whatsapp[^\"'<>]{0,40}?(\+[1-9][\d\s\-]{7,16})", re.I),
)
WA_GROUP_RE = re.compile(r"chat\.whatsapp\.com/([A-Za-z0-9]{8,})", re.I)

SOCIAL_HOSTS = {
    "instagram.com": "instagram", "facebook.com": "facebook",
    "linkedin.com": "linkedin", "x.com": "x", "twitter.com": "x",
    "tiktok.com": "tiktok", "youtube.com": "youtube", "snapchat.com": "snapchat",
}

# Addresses belonging to the agency that built the site, to analytics, or to
# nobody. Kept in step with leads_hunt.JUNK_EMAIL — same job, same list.
JUNK_EMAIL = ("example.com", "sentry.io", "wordpress", "wixpress", "godaddy",
              "squarespace", "shopify", "yourdomain", "domain.com", "email.com",
              "sample", "test@", "noreply", "no-reply", "donotreply",
              "@sentry", "wix.com", "cloudflare")
JUNK_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js",
            ".woff", ".woff2", ".ico")

_A_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.I | re.S)
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_LD_RE = re.compile(r'<script[^>]+type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                    re.I | re.S)
_META_RE = re.compile(r"""<meta\b[^>]*?(?:property|name)\s*=\s*["']([^"']+)["'][^>]*?"""
                      r"""content\s*=\s*["']([^"']*)["']""", re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


# ---------------------------------------------------------------------------
# NORMALISING, SO THE SAME THING FOUND TWICE IS ONE THING
# ---------------------------------------------------------------------------

def _here():
    """The open website's country and its dialling code — the UAE for the owner's."""
    from coordination import countries, profile
    cc = profile.country("AE") or "AE"
    return cc, countries.calling_code(cc) or "971"


def _phones_in(text):
    """Every number written in this text, for a country that is not the UAE."""
    try:
        import phonenumbers
    except Exception:
        return []
    cc, _code = _here()
    out = []
    try:
        for m in phonenumbers.PhoneNumberMatcher(text or "", cc):
            out.append(phonenumbers.format_number(m.number, phonenumbers.PhoneNumberFormat.E164))
    except Exception:
        pass
    return out


def normalise_phone(raw, default_cc=None):
    """
    A number in one shape: +<code><number>, for the open website's country —
    +971XXXXXXXXX for the owner's. Returns "" if it is not one.

    Anywhere but the UAE the country's own rules come from
    coordination/countries.py (Google's numbering library); the UAE keeps the
    rules below, which are what every number in his cabinet was made with.

    Everything downstream depends on this. '04 385 1122', '+971 4 385 1122'
    and '0097143851122' are one number, and a lead list that holds all three
    is a lead list that will ring the same business three times.
    """
    cc, code = _here()
    if default_cc is None:
        default_cc = code
    if default_cc != "971":
        from coordination import countries
        e = countries.normalise_phone(raw, cc)
        return e if e.startswith("+" + default_cc) else ""
    s = re.sub(r"[^\d+]", "", str(raw or ""))
    if not s:
        return ""
    if s.startswith("00"):
        s = "+" + s[2:]
    if s.startswith("+"):
        digits = s[1:]
    elif s.startswith(default_cc) and len(s) >= 11:
        digits = s
    elif s.startswith("0"):
        digits = default_cc + s[1:]
    else:
        digits = default_cc + s
    if not digits.startswith(default_cc):
        return ""                        # not a UAE number; out of scope
    rest = digits[len(default_cc):]
    # 9 digits for a mobile (5x + 7), 8 for a landline (single-digit area + 7)
    if len(rest) not in (8, 9) or not rest.isdigit() or rest.startswith("0"):
        return ""
    return "+" + digits


def phone_kind(e164):
    """mobile, landline, or "". A mobile is what WhatsApp can reach."""
    if not e164.startswith("+971"):
        try:
            import phonenumbers
            t = phonenumbers.number_type(phonenumbers.parse(e164, None))
        except Exception:
            return ""
        return {phonenumbers.PhoneNumberType.MOBILE: "mobile",
                phonenumbers.PhoneNumberType.FIXED_LINE: "landline"}.get(t, "")
    rest = e164[4:]
    if rest.startswith("5") and len(rest) == 9:
        return "mobile"
    if len(rest) == 8 and rest[0] in "234679":
        return "landline"
    return ""


# Labels that are a suffix rather than a name. Enough to find the brand in a
# UAE host, which is all this is for — not a public-suffix list.
_SUFFIXES = {"com", "net", "org", "co", "ae", "uk", "au", "io", "me", "info",
             "biz", "gov", "edu", "sa", "om", "qa", "bh", "kw", "eu", "shop",
             "online", "store", "site", "agency", "app", "dev", "xyz"}


def brand(host):
    """
    The name in a hostname. 'www.acmeglass.ae' and 'acmeglass.com' are both
    'acmeglass'.

    THIS IS NOT PEDANTRY, IT COST A REAL LEAD. The owner's own site is on .com
    and publishes info@…​.ae, and the "is this address theirs?" test compared
    whole hostnames — so it decided the business's own published address
    belonged to a stranger and threw it away. UAE firms split their site and
    their mail across .com and .ae constantly.
    """
    h = (host or "").lower().strip().strip(".")
    h = h[4:] if h.startswith("www.") else h
    parts = [x for x in h.split(".") if x]
    while len(parts) > 1 and parts[-1] in _SUFFIXES:
        parts.pop()
    return parts[-1] if parts else ""


def _unescape(s):
    import html as _html
    return _html.unescape(s or "")


def usable_email(addr):
    a = (addr or "").strip().lower().strip(".,;:")
    if not a or len(a) > 120 or "@" not in a:
        return ""
    if any(j in a for j in JUNK_EMAIL):
        return ""
    if any(a.endswith(x) for x in JUNK_EXT):
        return ""
    dom = a.rpartition("@")[2]
    if "." not in dom or dom.endswith("."):
        return ""
    return a


# ---------------------------------------------------------------------------
# WHICH PAGES TO READ — the site's own links, not a list of guesses
# ---------------------------------------------------------------------------

def links(html, base):
    """[(absolute url, anchor text)] on the same host, in document order."""
    try:
        host = urllib.parse.urlparse(base).netloc.lower()
    except Exception:
        return []
    out, seen = [], set()
    for attrs, inner in _A_RE.findall(html or ""):
        m = _HREF_RE.search(attrs)
        if not m:
            continue
        href = m.group(1).strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        try:
            url = urllib.parse.urljoin(base, href)
            p = urllib.parse.urlparse(url)
        except Exception:
            continue
        if p.scheme not in ("http", "https") or p.netloc.lower() != host:
            continue
        url = p._replace(fragment="").geturl()
        if url in seen:
            continue
        seen.add(url)
        out.append((url, _TAG_RE.sub(" ", inner).strip()[:80]))
    return out


def worth_a_click(url, text):
    """How promising this link is as a route to the contact details. 0 = no."""
    hay = f"{text} {urllib.parse.urlparse(url).path}".lower()
    for i, w in enumerate(WORTH_A_CLICK):
        if w in hay:
            return len(WORTH_A_CLICK) - i
    return 0


# Locale prefixes a UAE site puts in front of the same page. /ar/uae/contact-us
# is /uae/contact-us in Arabic, and reading both spends a page of the budget —
# a metered render, on a big site — to learn nothing. Talabat did exactly that
# on the first real run.
_LOCALE_RE = re.compile(r"^/(?:ar|en|ru|zh|fr|de|hi|ur|fa|tr)(?:-[a-z]{2})?(?=/|$)",
                        re.I)


def page_identity(url):
    """
    What page this IS, ignoring the language it is in and how it is decorated.

    Two URLs with the same identity are the same page, so the second one is not
    worth fetching.
    """
    try:
        p = urllib.parse.urlparse(url)
    except Exception:
        return url
    path = _LOCALE_RE.sub("", p.path or "/") or "/"
    path = path.rstrip("/").lower() or "/"
    for ext in (".html", ".htm", ".php", ".aspx"):
        if path.endswith(ext):
            path = path[: -len(ext)]
            break
    return f"{p.netloc.lower()}{path}"


# ---------------------------------------------------------------------------
# PULLING THE DETAILS OUT OF ONE PAGE
# ---------------------------------------------------------------------------

def _jsonld(html):
    """Every JSON-LD object on the page, flattened. Bad JSON is skipped."""
    out = []

    def walk(o):
        if isinstance(o, dict):
            out.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    for blob in _LD_RE.findall(html or ""):
        try:
            walk(json.loads(blob.strip()))
        except Exception:
            continue
    return out


def _metas(html):
    return {k.lower(): v for k, v in _META_RE.findall(html or "")}


def _address_of(node):
    a = node.get("address")
    if isinstance(a, str):
        return a.strip()
    if isinstance(a, dict):
        bits = [a.get(k) for k in ("streetAddress", "addressLocality",
                                   "addressRegion", "addressCountry")]
        return ", ".join(str(b).strip() for b in bits if b)
    return ""


def read_page(url, html) -> dict:
    """
    Everything one page has to say about how to reach this business.

    Every list holds {value, where, how} — never a bare string. The `how` is
    the difference between "they published this in a mailto link" and "this
    matched a regex in the footer", and it is what makes the list arguable.
    """
    found = {"emails": [], "phones": [], "whatsapp": [], "people": [],
             "socials": [], "address": "", "name": "", "groups": []}
    html = html or ""
    text = fetcher.visible_text(html)

    # ---- the strongest evidence first: links they wrote on purpose ---------
    #
    # Read out of the href ATTRIBUTE, not with a regex that stops at the first
    # space. Half the sites in the UAE write `tel:+971 56 510 0471` with the
    # number spaced for humans, and a whitespace-terminated match harvested the
    # string "+971" from every one of them — which normalises to nothing, so
    # the phone silently vanished on exactly the sites that had one.
    for href in _HREF_RE.findall(html):
        h = _unescape(href).strip()
        low = h.lower()
        if low.startswith("mailto:"):
            a = usable_email(urllib.parse.unquote(h[7:].split("?")[0]))
            if a:
                found["emails"].append({"value": a, "where": url, "how": "mailto link"})
        elif low.startswith("tel:"):
            p = normalise_phone(urllib.parse.unquote(h[4:]))
            if p:
                found["phones"].append({"value": p, "where": url, "how": "tel link"})
    for pat in WA_PATTERNS:
        for m in pat.findall(html):
            p = normalise_phone(m)
            if p:
                found["whatsapp"].append({"value": p, "where": url,
                                          "how": "click-to-chat link on their site"})
    for g in WA_GROUP_RE.findall(html):
        # A group invite is not a way to reach the business privately. Recorded
        # so it is visible, never treated as a contact.
        found["groups"].append({"value": g[:24], "where": url,
                                "how": "group invite — not a contact"})

    # ---- then structured data they published for machines to read ---------
    for node in _jsonld(html):
        t = node.get("@type") or ""
        t = " ".join(t) if isinstance(t, list) else str(t)
        if node.get("email"):
            a = usable_email(str(node["email"]).replace("mailto:", ""))
            if a:
                found["emails"].append({"value": a, "where": url,
                                        "how": "schema.org data"})
        tel = node.get("telephone")
        for one in (tel if isinstance(tel, list) else [tel]):
            p = normalise_phone(one) if one else ""
            if p:
                found["phones"].append({"value": p, "where": url,
                                        "how": "schema.org data"})
        if "Person" in t and node.get("name"):
            found["people"].append({
                "name": str(node["name"])[:80],
                "role": str(node.get("jobTitle") or "")[:60],
                "where": url, "how": "schema.org Person"})
        if not found["address"]:
            found["address"] = _address_of(node)[:200]
        if not found["name"] and any(k in t for k in
                                     ("Organization", "LocalBusiness", "Store",
                                      "Restaurant", "Hotel", "Corporation")):
            found["name"] = str(node.get("name") or "")[:120]

    metas = _metas(html)
    if not found["name"]:
        found["name"] = _unescape(metas.get("og:site_name") or "")[:120]
    if not found["name"]:
        m = _TITLE_RE.search(html)
        found["name"] = (_unescape(_TAG_RE.sub(" ", m.group(1))).strip()[:120]
                         if m else "")
    found["name"] = re.sub(r"\s+", " ", found["name"]).strip()
    found["address"] = re.sub(r"\s+", " ", _unescape(found["address"])).strip()

    # ---- last and weakest: what is merely written on the page -------------
    for a in EMAIL_RE.findall(text):
        a = usable_email(a)
        if a:
            found["emails"].append({"value": a, "where": url, "how": "printed on the page"})
    printed = []
    if _here()[1] == "971":
        for pat in PHONE_PATTERNS:
            printed += ["".join(m) if isinstance(m, tuple) else m for m in pat.findall(text)]
    else:
        printed = _phones_in(text)          # the country's own formats
    for m in printed:
        p = normalise_phone(m)
        if p:
            found["phones"].append({"value": p, "where": url,
                                    "how": "printed on the page"})

    # Socials are the one thing we deliberately look for OFF the host.
    for href in _HREF_RE.findall(html):
        try:
            full = urllib.parse.urljoin(url, _unescape(href))
            p = urllib.parse.urlparse(full)
        except Exception:
            continue
        h = p.netloc.lower()
        h = h[4:] if h.startswith("www.") else h
        net = SOCIAL_HOSTS.get(h)
        # A bare https://facebook.com/ in the footer is a template placeholder
        # nobody filled in, not a profile. It has to point AT something.
        if net and len(p.path.strip("/")) >= 2:
            found["socials"].append({"value": full[:200], "network": net,
                                     "where": url, "how": "link"})

    # Two patterns can match one link — wa.me is caught by the URL rule and
    # again by the "the word whatsapp near a +971" rule. That is one sighting,
    # not two, and letting it through would inflate the 'seen' count that says
    # how many places a detail was found in.
    for k, key in (("emails", "value"), ("phones", "value"), ("whatsapp", "value"),
                   ("socials", "value"), ("groups", "value"), ("people", "name")):
        seen, out = set(), []
        for row in found[k]:
            sig = (row.get(key), row.get("how"))
            if sig in seen:
                continue
            seen.add(sig)
            out.append(row)
        found[k] = out
    return found


# ---------------------------------------------------------------------------
# MERGING — best evidence wins, and the runner-up is not thrown away
# ---------------------------------------------------------------------------

# How much each route to a finding is worth. A mailto is somebody choosing to
# publish an address; a regex hit in body text might be a customer's address in
# a testimonial. Both are kept, in this order.
HOW_RANK = {"mailto link": 0, "tel link": 0, "click-to-chat link on their site": 0,
            "schema.org data": 1, "schema.org Person": 1,
            "printed on the page": 2, "link": 3}


def _merge(rows, key="value"):
    """One row per distinct value, keeping the best-evidenced sighting."""
    best = {}
    for r in rows:
        v = r.get(key)
        if not v:
            continue
        cur = best.get(v)
        if cur is None or HOW_RANK.get(r["how"], 9) < HOW_RANK.get(cur["how"], 9):
            best[v] = dict(r, seen=(cur or {}).get("seen", 0) + 1)
        else:
            cur["seen"] = cur.get("seen", 1) + 1
    return sorted(best.values(), key=lambda r: (HOW_RANK.get(r["how"], 9), r[key]))


def rank_emails(rows, host):
    """
    Best address first, and drop the ones that are not theirs.

    Same rule leads_hunt has always used and the reason is worth restating:
    half the small-business sites in the UAE carry a "site by <agency>" line in
    the footer with the agency's address beside it. Writing to that wastes the
    slot and tells the agency we read their client's site.

    The order is leads_hunt's too: their own domain first, then who the address
    reaches (leads_sources.address_tier — projects@ before info@ before sales@,
    a named person last), and only then how it was found.
    """
    theirname = brand(host)
    public = ("gmail.", "hotmail.", "yahoo.", "outlook.", "live.", "icloud.")
    roles = ("info", "sales", "contact", "admin", "office", "hello")

    def theirs(a):
        dom = a.partition("@")[2]
        if not theirname:
            return True
        return brand(dom) == theirname or any(p in dom for p in public)

    def tier(a):
        if ls:
            return ls.address_tier(a)
        local = a.partition("@")[0]      # the old two-way split, if ls is gone
        return 1 if any(local == x or local.startswith(x) for x in roles) else 3

    def score(r):
        a = r["value"]
        dom = a.partition("@")[2]
        return (0 if theirname and brand(dom) == theirname else 1,
                tier(a),
                1 if any(p in dom for p in public) else 0,
                HOW_RANK.get(r["how"], 9),
                len(a))
    return sorted((r for r in rows if theirs(r["value"])), key=score)


# ---------------------------------------------------------------------------
# THE CRAWL
# ---------------------------------------------------------------------------

def harvest(url, max_pages=MAX_PAGES, timeout=20, allow_paid=True,
            pause=0.4) -> dict:
    """
    Read a business's site and return every published way of reaching it.

    Home page first, then the links IT offers towards its contact details, then
    the guessed paths only if the site offered nothing. Stops as soon as it has
    an address to write to and a number to ring, because a fourth page costs
    somebody else's bandwidth and possibly our money for nothing.
    """
    if not (url or "").strip():
        return {"ok": False, "error": "no website to read"}
    if "://" not in url:
        url = "https://" + url.strip()
    try:
        p = urllib.parse.urlparse(url)
        host = (p.netloc or "").lower()
        home = f"{p.scheme}://{p.netloc}" + (p.path if p.path not in ("", "/") else "")
    except Exception as e:
        return {"ok": False, "error": f"not a readable address: {e}"}

    pages, notes, spend = [], [], 0.0
    bag = {"emails": [], "phones": [], "whatsapp": [], "people": [],
           "socials": [], "groups": []}
    name, address = "", ""
    queue = [(home, "the home page")]
    # Tracked by IDENTITY, not by URL, so /ar/uae/contact-us does not spend a
    # second page of the budget re-reading /uae/contact-us in Arabic.
    done, offered = set(), False

    while queue and len(pages) < max_pages:
        u, why = queue.pop(0)
        ident = page_identity(u)
        if ident in done:
            continue
        done.add(ident)
        r = fetcher.fetch(u, timeout=timeout, allow_paid=allow_paid)
        spend += r.get("cost", 0.0) or 0.0
        if not r.get("ok") or not r.get("html"):
            notes.append(f"{u} — {r.get('error') or 'nothing came back'}")
            continue
        html = r["html"]
        if fetcher.looks_like_challenge(html):
            notes.append(f"{u} — a bot check, not the page")
            continue
        pages.append({"url": u, "why": why, "how": r.get("how"),
                      "cost": r.get("cost", 0.0)})

        got = read_page(u, html)
        for k in bag:
            bag[k] += got[k]
        name = name or got["name"]
        address = address or got["address"]

        # Follow the site's OWN route to its contact page before guessing at
        # one. A business that links "Reach us" from the nav has told us where
        # to look, and a guessed /contact on a site that calls it /reach-us is
        # a 404 we paid for.
        if len(pages) < max_pages:
            cands = [(worth_a_click(lu, lt), lu, lt)
                     for lu, lt in links(html, u)]
            cands = sorted((c for c in cands if c[0] > 0), reverse=True)
            for _s, lu, lt in cands[:max_pages]:
                if (page_identity(lu) not in done
                        and all(page_identity(lu) != page_identity(q[0])
                                for q in queue)):
                    queue.append((lu, f"linked from {u} as {lt or 'a link'!r}"))
                    offered = True

        # Enough. An address to write to and a number to ring is the job.
        if _merge(bag["emails"]) and _merge(bag["phones"]) and len(pages) >= 2:
            break
        if r.get("how") == "plain":
            time.sleep(pause)            # only the polite path needs manners

    if not offered and len(pages) < max_pages:
        for path in GUESS_PATHS:
            if len(pages) >= max_pages:
                break
            u = f"{p.scheme}://{p.netloc}{path}"
            if page_identity(u) in done:
                continue
            done.add(page_identity(u))
            r = fetcher.fetch(u, timeout=timeout, allow_paid=False)
            if not r.get("ok") or not r.get("html"):
                continue
            pages.append({"url": u, "why": "guessed — the site linked nowhere useful",
                          "how": r.get("how"), "cost": r.get("cost", 0.0)})
            got = read_page(u, r["html"])
            for k in bag:
                bag[k] += got[k]
            name = name or got["name"]
            address = address or got["address"]
            time.sleep(pause)

    emails = rank_emails(_merge(bag["emails"]), host)
    phones = _merge(bag["phones"])
    for ph in phones:
        ph["kind"] = phone_kind(ph["value"])
    wa = _merge(bag["whatsapp"])
    # A mobile they published is reachable on WhatsApp in practice, but only a
    # click-to-chat button is EVIDENCE that they want to be messaged there. The
    # two are kept apart on purpose: outreach.py may use the first list; the
    # second is a maybe, and it is labelled as one.
    wa_numbers = {w["value"] for w in wa}
    maybe_wa = [dict(ph, how="a mobile they published — no chat button seen")
                for ph in phones
                if ph.get("kind") == "mobile" and ph["value"] not in wa_numbers]

    return {
        "ok": bool(pages), "site": home, "host": host,
        "name": name, "address": address,
        "emails": emails, "phones": phones,
        "whatsapp": wa, "whatsapp_maybe": maybe_wa,
        "people": _merge(bag["people"], key="name"),
        "socials": _merge(bag["socials"]),
        "groups": _merge(bag["groups"]),
        "pages": pages, "spend": round(spend, 4), "notes": notes,
        "head": _head(pages, emails, phones, wa, notes),
    }


def _head(pages, emails, phones, wa, notes):
    if not pages:
        return "could not read the site — " + (notes[0] if notes else "no reason given")
    bits = []
    if emails:
        bits.append(f"{len(emails)} address(es)")
    if phones:
        bits.append(f"{len(phones)} number(s)")
    if wa:
        bits.append("a chat button")
    return (f"read {len(pages)} page(s), found " + ", ".join(bits)) if bits else \
           f"read {len(pages)} page(s) — they published no way of reaching them"


def as_text(r) -> str:
    if not r.get("ok"):
        return f"{r.get('site', '?')} — {r.get('error') or r.get('head')}"
    L = [f"{r['name'] or r['host']}  ({r['head']})"]
    if r["address"]:
        L.append(f"  address   {r['address']}")
    for e in r["emails"][:4]:
        L.append(f"  mail      {e['value']:38} {e['how']}")
    for ph in r["phones"][:4]:
        L.append(f"  phone     {ph['value']:38} {ph['kind'] or '?'}, {ph['how']}")
    for w in r["whatsapp"][:3]:
        L.append(f"  whatsapp  {w['value']:38} {w['how']}")
    for w in r["whatsapp_maybe"][:2]:
        L.append(f"  whatsapp? {w['value']:38} {w['how']}")
    for pn in r["people"][:3]:
        L.append(f"  person    {pn['name']:38} {pn.get('role') or ''} ({pn['how']})")
    for s in r["socials"][:5]:
        L.append(f"  {s['network']:9} {s['value'][:60]}")
    for pg in r["pages"]:
        L.append(f"    read {pg['url'][:64]}  — {pg['why']}")
    for n in r["notes"][:4]:
        L.append(f"    note {n[:80]}")
    if r["spend"]:
        L.append(f"  cost      ${r['spend']}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(
        description="Read a business's own site for the contact details it published.")
    ap.add_argument("url")
    ap.add_argument("--pages", type=int, default=MAX_PAGES)
    ap.add_argument("--no-paid", action="store_true",
                    help="never use the metered renderer, however empty the page")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = harvest(a.url, max_pages=a.pages, allow_paid=not a.no_paid)
    print(json.dumps(r, indent=2) if a.json else as_text(r))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
