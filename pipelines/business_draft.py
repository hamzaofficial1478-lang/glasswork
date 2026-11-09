"""
business_draft.py — "Draft it from my website": a first go at the business
profile, read off the business's own pages, for the owner to correct.

    python -m pipelines.business_draft --url https://example.co.uk

Filling in what a business sells and who buys it, from nothing, is the part a
new owner would skip. Their own website already says most of it. This reads
the home page and the pages it links to as its services, asks the AI to put
that into the profile's shape, and hands it back UNSAVED: the "Your business"
page fills its boxes with it and nothing changes until they press Save.

Nothing is invented. A service has to be named on the site; a customer type
has to be one the program knows how to find on a map (the catalogue in
pipelines/leads_sources.py); a country that cannot be read is left empty.
"""
import argparse
import html as _html
import json
import re
import sys
import urllib.parse

from . import leads_sources, site_services

_TAG = re.compile(r"<[^>]+>")
_JUNK = re.compile(r"<(script|style|svg|noscript)[^>]*>.*?</\1>", re.S | re.I)
MOST_TEXT = 9000

PROMPT = """You are reading a small business's own website to fill in its profile.
Use ONLY what the text says. Do not invent services, places or facts.

Reply with JSON and nothing else:
{{"business_name": "<as the site names it, or empty>",
 "trade": "<what the business does, in plain words, under 12 words>",
 "trade_short": "<the same in two or three words>",
 "description": "<one sentence a customer would understand>",
 "country": "<two-letter ISO country code if the site makes it clear, else empty>",
 "home": "<the town or city it is based in, if stated, else empty>",
 "areas": ["<towns, cities or districts it says it serves>"],
 "services": [{{"label": "<a service it sells>", "also_called": ["<other names the site uses>"],
                "line": "<one plain sentence about it>",
                "buyers": ["<keys from the customer list below that buy this>"]}}],
 "customers": ["<keys from the customer list below that this business sells to>"]}}

Customer list (key: what it is) — use these keys only:
{catalogue}

THE WEBSITE ({url}):
{text}
"""


def _text(html) -> str:
    t = _JUNK.sub(" ", html or "")
    t = _TAG.sub(" ", t)
    return re.sub(r"\s+", " ", _html.unescape(t)).strip()


def read_site(url, timeout=15) -> dict:
    """The home page and a few of the pages it links to, as plain text."""
    if not url:
        return {"ok": False, "error": "no website address — save it first"}
    if "://" not in url:
        url = "https://" + url
    base = url.rstrip("/")
    home = site_services._get(base, timeout)
    if not home:
        return {"ok": False, "error": f"{base} did not answer, so there was nothing to read"}
    texts, read = [_text(home)], [base]
    for link in site_services._internal_links(home, base)[:12]:
        low = link.lower()
        if not any(w in low for w in ("service", "about", "what-we", "product", "solution")):
            continue
        page = site_services._get(link, timeout)
        if page:
            texts.append(_text(page))
            read.append(link)
        if len(read) >= 4:
            break
    return {"ok": True, "url": base, "pages": read, "text": " \n".join(texts)[:MOST_TEXT]}


def _slug(text) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:40] or "service"


def shape(d: dict) -> dict:
    """The AI's reply, made safe and put into the profile's own shape."""
    cat = leads_sources.CATALOGUE
    customers = [c for c in (d.get("customers") or []) if c in cat]
    services, order = {}, []
    for s in d.get("services") or []:
        label = str(s.get("label") or "").strip()[:80]
        if not label:
            continue
        key = _slug(label)
        while key in services:
            key += "_2"
        buyers = [b for b in (s.get("buyers") or []) if b in cat]
        services[key] = {"label": label,
                         "names": list(dict.fromkeys([label.lower()] + [
                             str(n).strip().lower() for n in (s.get("also_called") or [])
                             if str(n).strip()]))[:8],
                         "buyers": buyers, "related": [],
                         "line": str(s.get("line") or "").strip()[:200]}
        order.append(key)
        for b in buyers:
            if b not in customers:
                customers.append(b)
    cc = str(d.get("country") or "").strip().upper()
    from coordination import countries
    if cc and not countries.name(cc):
        cc = ""
    areas = [str(a).strip() for a in (d.get("areas") or []) if str(a).strip()][:20]
    home = str(d.get("home") or "").strip()
    if home and home not in areas:
        areas.insert(0, home)
    return {
        "business": {"name": str(d.get("business_name") or "").strip()[:80],
                     "trade": str(d.get("trade") or "").strip()[:120],
                     "trade_short": str(d.get("trade_short") or "").strip()[:40],
                     "description": str(d.get("description") or "").strip()[:240]},
        "country": cc,
        "place": {"home": home, "served": areas, "cities": areas},
        "services": services, "service_order": order,
        "customers": {c: {"why": cat[c]["label"], "queries": list(cat[c]["queries"])}
                      for c in customers},
        "local_customers": [c for c in customers if cat[c].get("local")],
    }


def draft(url, think=None, read=None) -> dict:
    """A proposed profile from the business's website, or why not. Saves nothing."""
    got = (read or read_site)(url)
    if not got.get("ok"):
        return got
    catalogue = "\n".join(f"  {k}: {v['label']}" for k, v in leads_sources.CATALOGUE.items())
    prompt = PROMPT.format(catalogue=catalogue, url=got["url"], text=got["text"])
    if think is None:
        from providers import think as _think
        think = _think
    r = think("content", [{"role": "user", "content": prompt}], agent="business-draft",
              max_tokens=2000)
    if not r.get("ok"):
        return {"ok": False, "error": "the AI did not answer, so nothing was drafted — "
                                      "fill it in by hand: " + str(r.get("error") or r.get("why") or "")}
    raw = r.get("output") or r.get("text") or ""
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except Exception:
        d = {}
    if not d:
        return {"ok": False, "error": "the AI's answer was not in the shape asked for — "
                                      "fill it in by hand, or try again"}
    out = shape(d)
    return {"ok": True, "draft": out, "pages_read": got["pages"],
            "head": (f"read {len(got['pages'])} page(s); found {len(out['services'])} service(s) "
                     f"and {len(out['customers'])} kind(s) of customer — check them, then Save")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Draft a business profile from its website.")
    ap.add_argument("--url", default="")
    a = ap.parse_args(argv)
    url = a.url
    if not url:
        try:
            from coordination import site
            url = site.site_url()
        except Exception:
            url = ""
    print(json.dumps(draft(urllib.parse.unquote(url))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
