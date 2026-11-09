"""
stock_photos.py — a light photograph for a service, from the stock shelf.

    python -m pipelines.stock_photos "glass partition"
    python -m pipelines.stock_photos --check

"There is no need to directly extract images from website on every mail
 that's why i added the stock's apis pexals and pixabay you can pick images
 from there to make mail lighter."

He is right about the symptom. Four of the seven photographs on his own site
are over a megabyte and folding.png is 2.3MB, so an email built from them pulls
down six megabytes and sits there loading on mobile data.

WHERE I WOULD NOT GO ALL THE WAY WITH HIM, AND WHY IT IS A SETTING

A glazier's brochure showing somebody else's glass is a weaker brochure. His own
photographs are the one thing a competitor cannot copy, and a customer who
recognises a stock image has learned something about the business that is not
true. So this is a source the caller can choose, not a replacement:

    site    his own photographs, whatever they weigh
    stock   these, always
    auto    his own where they are light enough, stock where they are not

'auto' is the default. It fixes exactly the thing he complained about — the
2.3MB ones become 60KB ones — and leaves his real work in place where it costs
nothing, which on his site today is the wooden door and the sign board.

WHY THESE URLS ARE SMALL

Both services resize on their own CDN from a query parameter, so this never
downloads or re-hosts anything. It asks for a 600px-wide version and links
straight to it: about 40-70KB against his 1-2MB, from a host built to serve it.

Attribution: Pexels and Pixabay both allow commercial use without credit. The
photographer is recorded anyway, in the row this writes, so the answer to
'where did that picture come from' exists.
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

SHELF = "stock_images"
TIMEOUT = 12
UA = "glasswork-agency/1.0 (stock photo lookup)"

# What a 600px-wide photograph costs, roughly, from either service. Used to say
# what was saved rather than to decide anything.
TYPICAL_BYTES = 60_000


def _keys() -> list:
    """
    Every stock API on the shelf, best first, then the environment.

    Same shape as pipelines/leads_hunt._places_key: the shelf is where he puts
    keys through /setup, and the environment is the way in for anyone running
    a pipeline by hand.
    """
    out = []
    try:
        from providers import store
        for t in store.tools_on_shelf(SHELF):
            k = (t.get("api_key") or "").strip()
            if not k:
                continue
            name = (t.get("name") or "").lower()
            which = "pexels" if "pexel" in name else "pixabay" if "pixabay" in name else ""
            if which:
                out.append((which, k, t.get("name") or which))
    except Exception:
        pass
    for env, which in (("PEXELS_API_KEY", "pexels"), ("PIXABAY_API_KEY", "pixabay")):
        k = (os.environ.get(env) or "").strip()
        if k and not any(w == which for w, _, _ in out):
            out.append((which, k, env))
    return out


def _get(url, headers=None, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _pexels(query, key, width):
    d = _get("https://api.pexels.com/v1/search?"
             + urllib.parse.urlencode({"query": query, "per_page": 5,
                                       "orientation": "landscape"}),
             headers={"Authorization": key})
    for p in d.get("photos") or []:
        src = (p.get("src") or {}).get("original") or ""
        if not src:
            continue
        # Their own resizer. 'tinysrgb' plus a width is the light one.
        return {"url": f"{src}?auto=compress&cs=tinysrgb&w={width}",
                "by": p.get("photographer") or "", "where": "Pexels",
                "page": p.get("url") or ""}
    return None


def _pixabay(query, key, width):
    d = _get("https://pixabay.com/api/?"
             + urllib.parse.urlencode({"key": key, "q": query, "per_page": 5,
                                       "image_type": "photo",
                                       "orientation": "horizontal",
                                       "safesearch": "true"}))
    for p in d.get("hits") or []:
        # webformatURL is already 640px wide and is the light one they publish.
        src = p.get("webformatURL") or ""
        if not src:
            continue
        return {"url": src, "by": p.get("user") or "", "where": "Pixabay",
                "page": p.get("pageURL") or ""}
    return None


def find(query, width=600) -> dict:
    """
    One landscape photograph for this phrase, or a plain reason there is none.

    Tries each configured service in shelf order and stops at the first answer.
    Never raises: a picture is decoration, and a stock service being down must
    not take an email with it.
    """
    keys = _keys()
    if not keys:
        return {"ok": False, "why": "no stock photo API is set up — add Pexels "
                                    "or Pixabay on /setup, Stock images"}
    tried = []
    for which, key, label in keys:
        try:
            got = (_pexels if which == "pexels" else _pixabay)(query, key, width)
        except Exception as e:
            tried.append(f"{label}: {type(e).__name__}")
            continue
        if got:
            return {"ok": True, **got, "query": query}
        tried.append(f"{label}: nothing for '{query}'")
    return {"ok": False, "why": "; ".join(tried) or "no photograph found"}


# A service name is not a good search. "Lamination Glass" returns laminated
# paper; "Wood Door" returns a forest. These are the words that actually find
# the right picture for this trade, and anything not listed falls back to the
# service name with the trade appended.
BETTER_WORDS = {
    "glass installation": "glass facade building installation",
    "aluminum installation": "aluminium window frame installation",
    "aluminium installation": "aluminium window frame installation",
    "lamination glass": "frosted laminated glass panel",
    "laminated glass": "frosted laminated glass panel",
    "folding door": "bifold glass patio door",
    "folding doors": "bifold glass patio door",
    "glass partition": "office glass partition wall",
    "glass partitions": "office glass partition wall",
    "wood door": "modern wooden door interior",
    "wooden door": "modern wooden door interior",
    "wooden doors": "modern wooden door interior",
    "sign board": "shop signage storefront sign",
    "sign boards": "shop signage storefront sign",
    "pergola": "aluminium pergola terrace",
    "pergolas": "aluminium pergola terrace",
}


def for_service(label, width=600) -> dict:
    """A photograph for one of his services, searched with words that work."""
    key = " ".join((label or "").lower().split())
    try:
        from coordination import profile
        trade = "glass aluminium" if profile.is_starter() else profile.trade_line()
    except Exception:
        trade = "glass aluminium"
    return find(BETTER_WORDS.get(key, f"{label} {trade}".strip()), width=width)


def _cli():
    ap = argparse.ArgumentParser(description="A light photograph from the stock shelf.")
    ap.add_argument("query", nargs="?", default="", help="a service or a phrase")
    ap.add_argument("--check", action="store_true", help="is anything configured")
    ap.add_argument("--width", type=int, default=600)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.check or not a.query:
        keys = _keys()
        r = {"ok": bool(keys), "configured": [label for _w, _k, label in keys]}
        print(json.dumps(r, indent=2) if a.json else
              ("Stock shelf: " + (", ".join(r["configured"]) if keys else
                                  "nothing set up. Add Pexels or Pixabay on "
                                  "/setup, Stock images.")))
        return 0 if keys else 1

    r = for_service(a.query, width=a.width)
    print(json.dumps(r, indent=2) if a.json else
          (f"{r['where']} — {r['url']}\n  by {r['by']}" if r.get("ok")
           else "None: " + r["why"]))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(_cli())
