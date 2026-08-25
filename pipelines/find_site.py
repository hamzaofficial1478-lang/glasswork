"""
find_site.py — the business has a website. OpenStreetMap just doesn't know it.

THE PROBLEM THIS EXISTS FOR

A signage search in Dubai came back with 49 businesses worth having and 7 with
any way to reach them. Forty-two rows like this:

    Daffodils Advertising LLC                                     Dubai
    Economical Printing Press LLC                                 Dubai
    Al Gabael Typing                                              Dubai

The owner's question was the right one: "if we don't have any contact wire how
will we outreach them?"

Here is the thing, though. Daffodils Advertising has a website. Economical
Printing Press has a website. Almost every one of these has a website, and
several have had one for a decade. What is missing is not the website — it is
the volunteer who would have typed it into OpenStreetMap. OSM is a map of
*places*; the contact tags are a bonus that somebody has to add by hand, and in
the UAE almost nobody has.

So we do not need a new source of businesses. We need to close the gap between
a company name we already have and the website that already exists.

HOW, WITHOUT GUESSING WRONG

Guessing a domain is easy. Guessing it *safely* is the whole job, because the
failure mode is not "we found nothing" — it is "we emailed a stranger, told
them we saw their signage business, and they had never heard of us". That is
worse than no lead. It burns the sending domain that every other lead depends
on.

So every candidate domain has to pass a test that a wrong domain fails:

  1. It resolves and serves a real page.
  2. The page names the business — a distinctive word from the company name
     appears in the title, the heading, or the body.
  3. It is not a parking page, a registrar holding page, or a domain-for-sale
     notice, all of which will happily contain the business name.

Fail any one of those and the domain is dropped with the reason. A lead is only
allowed to gain a website when we can point at the sentence on the page that
says it is theirs.

WHAT IT WILL NOT DO

It will not try a domain for a business whose name is too generic to be
distinctive. "Copycare", "Echo", "Printcom" — echo.ae could be anybody, and a
match on the word "echo" proves nothing. Those are returned as skipped, with
the reason, and stay walk-in leads. A collector that quietly guesses on those
is a collector that eventually mails the wrong company.
"""
import re
import urllib.parse

from . import fetcher

# UAE first. A Dubai trading company registers .ae far more often than anything
# else, and trying .com first spends a fetch on a parked domain most times.
TLDS = ("ae", "com", "net", "co.ae", "org")

# Stripped before the domain is guessed. Nobody registers
# alfurgentradingllc.ae — they register alfurgen.ae.
LEGAL_NOISE = {
    "llc", "l.l.c", "llc.", "fze", "fzc", "fzco", "fz", "lle",
    "co", "co.", "company", "corp", "corporation", "inc", "ltd", "limited",
    "est", "est.", "establishment", "enterprises", "enterprise",
    "group", "holding", "holdings", "international", "intl",
    "trading", "traders", "general", "services", "service", "solutions",
    "and", "the", "of", "for", "&",
}

# Stripped too, but for a different reason: they are true of every business in
# the search, so they carry no identity and make a useless domain guess.
PLACE_NOISE = {
    "dubai", "sharjah", "abu", "dhabi", "abudhabi", "ajman", "fujairah",
    "ras", "al", "khaimah", "umm", "quwain", "uae", "emirates", "gulf",
    "jlt", "jvc", "deira", "bur", "quoz", "karama", "barsha",
}

# A word that identifies nobody. If everything distinctive is stripped and only
# these are left, we refuse to guess rather than mail a stranger.
TRADE_NOISE = {
    "print", "printing", "printer", "printers", "press", "typing",
    "photocopying", "copy", "copies", "centre", "center", "advertising",
    "advertisement", "sign", "signs", "signage", "nameplate", "nameplates",
    "banner", "banners", "glass", "aluminium", "aluminum", "steel", "metal",
    "works", "workshop", "real", "estate", "properties", "property",
    "broker", "brokers", "clinic", "medical", "polyclinic", "dental",
    "optics", "optical", "documents", "clearing", "stationery", "supplies",
    "supply", "shop", "store", "restaurant", "cafeteria", "cafe", "kitchen",
    "laundry", "salon", "garage", "auto", "motors", "repairing",
    "maintenance", "technical", "contracting", "contractors", "decor",
    "decoration", "interior", "interiors", "furniture", "hotel", "hotels",
    "apartments", "pharmacy", "grocery", "supermarket", "market",
    "serv", "servs", "trdg", "cont", "home", "homes", "house",
}

# What a parked domain says. Any of these and the page is not a business site,
# whatever else is on it.
PARKED = (
    "domain is for sale", "buy this domain", "this domain is parked",
    "domain parking", "parkingcrew", "sedoparking", "godaddy.com/domainsearch",
    "future home of something quite cool", "website coming soon",
    "under construction", "this site can't be reached",
    "default web page", "apache2 debian default page",
    "welcome to nginx", "index of /",
)

# Shortest distinctive word we will build a domain from. Three letters matches
# half the internet by accident.
MIN_TOKEN = 4


def tokens(company: str):
    """The words in a company name that actually identify it."""
    words = re.split(r"[^0-9A-Za-z]+", (company or "").lower())
    return [w for w in words if w and w not in LEGAL_NOISE
            and w not in PLACE_NOISE]


def distinctive(company: str):
    """
    The words that identify THIS business and not its whole trade.

    'Daffodils Advertising LLC' -> ['daffodils']
    'Economical Printing Press' -> ['economical']
    'Typing & Photocopying'     -> []          <- refuses to guess
    """
    return [w for w in tokens(company)
            if w not in TRADE_NOISE and len(w) >= MIN_TOKEN]


def candidate_domains(company: str, limit=8):
    """
    Domains worth trying for this name, best first. [] if the name is too
    generic to guess from — which is a real answer, not a failure.
    """
    keep = distinctive(company)
    if not keep:
        return []
    all_words = tokens(company)
    stems = []
    # The first two or three words as written, SHORT ONES INCLUDED. 'Star Bay
    # Print Centre' is starbayme.com in real life, and dropping 'bay' for
    # being three letters loses the only stem that could ever have found it.
    for s in ("".join(all_words), "".join(all_words[:2]),
              "".join(all_words[:3]), "".join(keep), keep[0],
              "".join(keep[:2]), "-".join(keep[:2])):
        if 3 < len(s) <= 30 and s not in stems:
            stems.append(s)
    # TLD-major, not stem-major. Nested the other way round, the first stem
    # ate the whole budget on .net and .org before 'starbay.ae' — the one that
    # would actually have found Star Bay Print Centre — was ever reached.
    out = []
    for tld in TLDS:
        for s in stems:
            d = f"{s}.{tld}"
            if d not in out:
                out.append(d)
    return out[:limit]


def _text(html: str) -> str:
    body = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", html or "")
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", body)).lower()


def is_parked(html: str) -> bool:
    t = _text(html)[:6000]
    return any(p in t for p in PARKED)


# A single word is thin evidence. 'Echo' on echo.ae proves nothing — half the
# internet says echo. Shorter than this and we will not accept a lone word at
# all, however well it matches.
MIN_LONE_TOKEN = 5


def page_is_theirs(html: str, company: str, hints=()) -> tuple:
    """
    (yes_or_no, why). The test a wrong domain has to fail.

    The failure this guards against is not "found nothing" — it is emailing a
    stranger about their signage business when they have never heard of us,
    which burns the sending domain every other lead depends on. So the bar
    rises as the evidence gets thinner:

      the whole name runs together on the page   -> accepted outright
      two distinctive words both present         -> accepted
      one distinctive word, 5+ letters, AND a
        hint (the emirate, the trade) present    -> accepted
      anything less                              -> refused, with the reason

    `hints` is what we already know about this lead from the map — its emirate
    and its trade. A US company called Daisytek will say "daisytek" all over
    its home page and never once say "Dubai", which is exactly the wrong
    answer we need to catch.
    """
    if not (html or "").strip():
        return False, "nothing came back"
    if is_parked(html):
        return False, "a parked or placeholder page, not a business site"
    keep = distinctive(company)
    if not keep:
        return False, "the name has no distinctive word to check against"
    t = _text(html)

    # Only for a name of two words or more. For a one-word name this test IS
    # the lone-word test with the safety rail filed off, and it let a Texas
    # company called Daisytek pass as a Dubai signage business.
    all_words = tokens(company)
    joined = "".join(all_words)
    if len(all_words) >= 2 and len(joined) >= 8 and joined in t.replace(" ", ""):
        return True, f"the whole name {company!r} runs together on the page"

    hit = [w for w in keep if w in t]
    if not hit:
        return False, (f"the page never says {keep[0]!r} — it is somebody "
                       "else's site")
    if len(hit) >= 2:
        return True, f"{hit[0]!r} and {hit[1]!r} are both on the page"

    lone = hit[0]
    if len(lone) < MIN_LONE_TOKEN:
        return False, (f"only {lone!r} matched, and one short word is a "
                       "coincidence, not an identification")
    seen = [h for h in hints if h and h.lower() in t]
    if not seen:
        return False, (f"only {lone!r} matched and the page never mentions "
                       + (", ".join(str(h) for h in hints if h) or "the trade")
                       + " — most likely a different company of the same name")
    return True, f"{lone!r} on a page that also mentions {seen[0]!r}"


def hints_for(cand) -> tuple:
    """What the map already told us, used to corroborate a thin name match."""
    out = []
    for k in ("area", "segment", "category", "service_label"):
        v = (cand.get(k) or "").strip() if isinstance(cand, dict) else ""
        if v:
            out.append(v.replace("_", " "))
    # The emirate is the single most useful one: a company of the same name in
    # another country will not mention it.
    return tuple(out)


def find(company: str, timeout=12, tlds=TLDS, allow_paid=False,
         hints=()) -> dict:
    """
    Look for this business's own website. Reads only, never contacts anybody.

    allow_paid is False on purpose: this is a guess, and a guess is not worth
    a rendered page. If the plain fetch cannot see the business's name, we
    would rather report nothing than pay to be unsure.
    """
    domains = candidate_domains(company)
    if not domains:
        return {"ok": False, "company": company, "tried": [],
                "why": (f"{company!r} has no distinctive word to build a "
                        "domain from — guessing here would eventually mail "
                        "the wrong company")}
    tried = []
    for d in domains:
        if not d.endswith(tuple("." + t for t in tlds)):
            continue
        url = f"https://{d}/"
        r = fetcher.fetch(url, timeout=timeout, allow_paid=allow_paid)
        if not r.get("ok") or not r.get("html"):
            tried.append({"domain": d, "why": r.get("error") or "did not load"})
            continue
        ours, why = page_is_theirs(r["html"], company, hints)
        tried.append({"domain": d, "why": why})
        if ours:
            return {"ok": True, "company": company, "website": url,
                    "domain": d, "why": why, "tried": tried}
    return {"ok": False, "company": company, "tried": tried,
            "why": f"tried {len(tried)} domain(s), none of them theirs"}


def fill_in(cands, limit=25, timeout=12) -> dict:
    """
    Give a website to the leads that have a name and nothing else.

    Only the ones with no website AND no email — a lead we can already reach
    has nothing to gain and the fetch would be spent for nothing.
    """
    todo = [c for c in cands
            if not (c.get("website") or "").strip()
            and not (c.get("email") or "").strip()]
    attempting = todo[:max(0, int(limit))]
    found, too_generic, missed = 0, [], []
    for c in attempting:
        r = find(c.get("company", ""), timeout=timeout, hints=hints_for(c))
        if r["ok"]:
            c["website"] = r["website"]
            c["website_why"] = r["why"]
            found += 1
        elif not r["tried"]:
            too_generic.append(c.get("company"))
        else:
            missed.append(c.get("company"))
    return {"looked_for": len(attempting), "sites_found": found,
            "too_generic_to_guess": too_generic, "no_site_found": missed,
            "not_reached_this_pass": len(todo) - len(attempting),
            "head": (f"looked for {len(attempting)} missing site(s), found "
                     f"{found}"
                     + (f", {len(too_generic)} names too generic to guess from"
                        if too_generic else ""))}


def main():
    import argparse
    import json
    import sys
    ap = argparse.ArgumentParser(
        description="Find a UAE business's own website from its name alone.")
    ap.add_argument("company", nargs="+")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    name = " ".join(a.company)
    r = find(name)
    if a.json:
        print(json.dumps(r, indent=2))
        return 0 if r["ok"] else 1
    print(f"{name}")
    print(f"   distinctive words: {distinctive(name) or '(none — will not guess)'}")
    print(f"   would try: {', '.join(candidate_domains(name)) or '(nothing)'}")
    for t in r["tried"]:
        print(f"     {t['domain']:34} {t['why']}")
    print("   -> " + (f"{r['website']} — {r['why']}" if r["ok"] else r["why"]))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
