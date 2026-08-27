"""
leads_hunt.py — the Hunter. Goes and gets real UAE businesses off the map.

    python -m pipelines.leads_hunt --segment hotel --area "Dubai Marina"
    python -m pipelines.leads_hunt --dry-run          (see what it would collect)

HOW IT ACTUALLY WORKS, AND WHY THIS WAY

Two hops, because no single source has everything.

  1. THE MAP. Google Maps is where every UAE business is listed with its
     category, area, phone and website — the four things we need. We read it
     through the Places API rather than driving a browser at maps.google.com.
     That is not squeamishness: scraping the Maps front end breaks Google's
     terms and gets the IP blocked within days, usually mid-run, and you find
     out when the pipeline has been returning empty for a week. The API is the
     same data through the front door, it costs a few dollars per thousand
     places, and Google gives every account a monthly credit that covers far
     more than this business will use.

  2. THE COMPANY'S OWN WEBSITE. Maps has no email addresses — not hidden, not
     paywalled, simply not in there. The email comes off the company's own
     contact page, where they printed it precisely so people would write to
     them. That hop is where the working addresses come from, and it is why a
     listing without a website is worth much less than one with.

Everything a business publishes about itself is fair game. What the Hunter will
not do is invent an address it never saw: no pattern-guessing info@<domain>
because it looks likely. A guessed address bounces, and bounces are what get an
outreach domain blacklisted, which would cost every real lead behind it.

WHAT COMES OUT

One row per business, with the service it most likely needs already worked out
from its own listing (see services.py). A hotel comes back tagged for shower
glass and mirrors; a warehouse for aluminium doors. That tag is what lets the
mail talk about the right thing instead of reciting a catalogue.

Nothing here contacts anybody. Collection and outreach are separate jobs on
purpose, with Security's clearance in between.
"""
import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from coordination import board
from coordination import deskwork
from . import harvest
from . import leads_sources as ls
from . import services

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
FIELDS = ("places.displayName,places.formattedAddress,places.nationalPhoneNumber,"
          "places.internationalPhoneNumber,places.websiteUri,places.primaryTypeDisplayName,"
          "places.types,places.businessStatus,places.userRatingCount")

UA = "Mozilla/5.0 (compatible; glasswork-leads/1.0; +contact-page reader)"

# Pages a UAE business puts its address on, in the order worth trying.
CONTACT_PATHS = ("", "/contact", "/contact-us", "/contactus", "/about",
                 "/about-us", "/get-in-touch", "/enquiry")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Addresses that belong to the website's builder, not the business.
JUNK_EMAIL = ("example.com", "sentry.io", "wordpress", "wixpress", "godaddy",
              "squarespace", "shopify", "yourdomain", "domain.com", "email.com",
              "sample", "test@", "noreply", "no-reply", "donotreply")
JUNK_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")


# ---------------------------------------------------------------------------
# 1. the map
# ---------------------------------------------------------------------------

def _places_key():
    """The Places key, from the shelf if it is on there, else the environment."""
    try:
        from providers import store
        for t in store.tools_on_shelf("maps"):
            k = (t.get("api_key") or "").strip()
            if k:
                return k, t.get("name", "maps shelf")
    except Exception:
        pass
    k = (os.environ.get("GOOGLE_PLACES_API_KEY")
         or os.environ.get("GOOGLE_MAPS_API_KEY") or "").strip()
    return (k, "GOOGLE_PLACES_API_KEY") if k else (None, None)


def search_places(query, area, key, limit=20, timeout=25):
    """
    One text search against the Places API. Returns (places, error).

    regionCode=AE keeps the results in the country even when a query is generic,
    which matters: 'hotel Marina' without it returns Marina del Rey.
    """
    body = json.dumps({
        "textQuery": f"{query} in {area}, United Arab Emirates",
        "regionCode": "AE",
        "maxResultCount": min(int(limit), 20),
    }).encode()
    req = urllib.request.Request(PLACES_URL, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": FIELDS,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace")).get("places", []), None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        return [], f"HTTP {e.code} from the Places API — {detail}"
    except Exception as e:
        return [], str(e)


def _place_to_candidate(p, segment, area):
    """Flatten one Places result into the shape the cabinet stores."""
    name = ((p.get("displayName") or {}).get("text") or "").strip()
    site = (p.get("websiteUri") or "").strip()
    cat = (p.get("primaryTypeDisplayName") or {}).get("text") or ""
    types = " ".join(p.get("types") or [])
    svc, why = services.match_service(name, cat, types, segment=segment)
    return {
        "company": name,
        "name": "",                      # a business, not a person, until proven otherwise
        "email": "",                     # comes from their own site, below
        "phone": (p.get("internationalPhoneNumber")
                  or p.get("nationalPhoneNumber") or "").strip(),
        "website": site,
        "address": (p.get("formattedAddress") or "").strip(),
        "area": area,
        "segment": segment,
        "category": cat,
        "source": "google maps (places api)",
        "service": svc,
        "service_label": services.label(svc),
        "service_reason": why,
        "reviews": p.get("userRatingCount") or 0,
        "status": p.get("businessStatus") or "",
    }


# ---------------------------------------------------------------------------
# 2. the company's own contact page
# ---------------------------------------------------------------------------

def _get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "html" not in ctype and "text" not in ctype:
                return ""
            return r.read(600_000).decode("utf-8", "replace")
    except Exception:
        return ""


def usable_email(addr: str) -> bool:
    a = (addr or "").strip().lower()
    if not a or len(a) > 120:
        return False
    if any(j in a for j in JUNK_EMAIL):
        return False
    if any(a.endswith(x) for x in JUNK_EXT):
        return False
    return "@" in a and "." in a.split("@")[-1]


FREE_MAILBOXES = ("gmail.", "hotmail.", "yahoo.", "outlook.", "live.", "icloud.")


def rank_emails(found, site_host=""):
    """
    Best address first. A role inbox on the company's own domain beats a named
    person on the same domain, which beats a free mailbox — and info@ still
    works after the person who owns the named address leaves.

    It also DROPS addresses belonging to someone else. Half the small-business
    sites in the UAE carry a "built by <web agency>" line in the footer with the
    agency's address next to it, and that address ranks perfectly well on every
    other test. Mailing it wastes the slot and tells the agency we scraped their
    client. Their own domain or a free mailbox they published: nothing else.
    """
    host = (site_host or "").lower().lstrip("www.")

    def theirs(a):
        dom = a.partition("@")[2].lower()
        if not host:
            return True                  # no site to compare against — keep it
        return host in dom or dom in host or any(f in dom for f in FREE_MAILBOXES)

    def score(a):
        a = a.lower()
        local, _, dom = a.partition("@")
        s = 0
        if host and host in dom:
            s -= 4                       # their own domain
        if any(local == r or local.startswith(r) for r in ls.ROLE_INBOXES):
            s -= 3                       # a role inbox
        if any(g in dom for g in FREE_MAILBOXES):
            s += 2                       # free mailbox: works, but weaker
        if "." in local or "_" in local:
            s += 1                       # firstname.lastname: a person
        return (s, len(a))

    keep = set(a.lower() for a in found if usable_email(a))
    return sorted((a for a in keep if theirs(a)), key=score)


def emails_from_site(url, max_pages=3, timeout=12):
    """
    Read the company's own pages and take the addresses they published there.
    Returns (emails_best_first, pages_read).

    Nothing is guessed. If a company has not published an address, it comes back
    with none and stays a phone-only lead, which is an honest outcome.
    """
    if not url:
        return [], []
    try:
        parsed = urllib.parse.urlparse(url if "://" in url else "https://" + url)
    except Exception:
        return [], []
    base = f"{parsed.scheme}://{parsed.netloc}"
    host = parsed.netloc.lower().lstrip("www.")

    found, read = [], []
    for path in CONTACT_PATHS[:max_pages + 1]:
        html = _get(base + path, timeout)
        if not html:
            continue
        read.append(base + path)
        # mailto: links first — those are unambiguous, an address they chose to publish
        found += [m.split("?")[0] for m in
                  re.findall(r'mailto:([^"\'>\s]+)', html, re.I)]
        found += EMAIL_RE.findall(html)
        if rank_emails(found, host):
            break                        # got one worth having; stop reading
        time.sleep(0.4)                  # be polite to someone else's server
    return rank_emails(found, host), read


def enrich(candidate, timeout=12):
    """
    Read the business's own site for everything Maps does not hold, and read
    their own words for a better service match.

    THIS USED TO BE ITS OWN LITTLE CRAWLER AND IT WAS THE WEAK LINK.

    Raw urllib against eight guessed paths: no rendering, so a Wix or
    Squarespace site — which is most UAE small businesses — came back an empty
    shell and the lead was filed phone-only; no cache; no spend guard; and a
    guessed /contact was a 404 on any site that calls the page something else.
    The owner's own site calls it /contact.php, so the guesser would have missed
    the address on the business this agency belongs to.

    pipelines/harvest.py replaces it: the site's OWN links are followed, pages
    are read through the fetcher (address checked, plain HTTP first because it
    is nothing, renderer behind it, cached, metered), and every detail comes
    back with a note saying where it was found and how.
    """
    site = candidate.get("website")
    if not site:
        candidate["pages_read"] = []
        return candidate

    h = harvest.harvest(site, timeout=max(timeout, 20))
    pages = [p["url"] for p in h.get("pages") or []]
    candidate["pages_read"] = pages
    candidate["harvest"] = h.get("head", "")

    if h.get("emails"):
        candidate["email"] = h["emails"][0]["value"]
        candidate["other_emails"] = [e["value"] for e in h["emails"][1:4]]
    # Maps gives A number. The site often gives the one somebody answers.
    if h.get("phones") and not candidate.get("phone"):
        candidate["phone"] = h["phones"][0]["value"]
    # ONLY from a click-to-chat button. See the note in harvest.py: a business
    # that put a WhatsApp button on its own website has invited people to
    # message it there, and that is not the same as a mobile number we happened
    # to find. The maybes are carried separately and never used to contact.
    if h.get("whatsapp"):
        candidate["whatsapp"] = h["whatsapp"][0]["value"]
    if h.get("whatsapp_maybe"):
        candidate["whatsapp_maybe"] = [w["value"] for w in h["whatsapp_maybe"][:2]]
    if h.get("address"):
        candidate["address"] = h["address"]
    if h.get("people"):
        candidate["name"] = h["people"][0]["name"]
        candidate["name_role"] = h["people"][0].get("role", "")
    if h.get("socials"):
        candidate["socials"] = ", ".join(s["value"] for s in h["socials"][:4])
    # Where every detail came from, kept with the lead. A contact with no
    # provenance is a rumour, and in six months nobody can tell a real one from
    # a parsing accident.
    candidate["evidence"] = {
        k: [{kk: vv for kk, vv in row.items() if kk in ("value", "name", "how", "where")}
            for row in (h.get(k) or [])[:4]]
        for k in ("emails", "phones", "whatsapp", "people")}

    if pages:
        # their own site describes what they do better than a Maps category does
        blurb = _get(pages[0], timeout)[:20000]
        svc, why = services.match_service(candidate["company"], candidate.get("category"),
                                          blurb, segment=candidate.get("segment", ""))
        candidate["service"], candidate["service_label"] = svc, services.label(svc)
        candidate["service_reason"] = why
    return candidate


# ---------------------------------------------------------------------------
# 3. the run
# ---------------------------------------------------------------------------

def local_first(areas):
    """
    Nearest out. The owner's own base first, then the rest of that emirate, then
    the others.

    "Main target is locals" is not a preference, it is how this trade makes
    money: a Business Bay enquiry gets surveyed tomorrow and quoted the same
    week, while a Ras Al Khaimah one is two hours each way before anybody has
    measured anything. The search budget always runs out — it should run out at
    the far end.

    The base comes from the address the owner saved on /setup. With none saved
    the list is left exactly as written, which already starts in Dubai; guessing
    a home city from nothing would be inventing a fact.
    """
    try:
        from coordination import site
        base = (site.status().get("url") or "")
    except Exception:
        base = ""
    home = ""
    for city in ("dubai", "abu dhabi", "sharjah", "ajman", "ras al khaimah", "al ain"):
        if city.replace(" ", "") in base.lower().replace(" ", "").replace("-", ""):
            home = city
            break
    if not home:
        return list(areas)

    def rank(a):
        al = a.lower()
        if al == home:
            return 0                      # the emirate itself
        if home == "dubai" and al not in ("abu dhabi", "al ain", "sharjah",
                                          "ajman", "ras al khaimah"):
            return 1                      # a Dubai district
        return 2
    return sorted(areas, key=rank)


def plan_targets(segment=None, area=None, per_segment=2, service=None):
    """
    Which searches to run.

    Three ways to aim it, and the third is the one the owner asked for:

      nothing named   the whole market. The Hunter is not restricted to a chosen
                      few, which is the point of the segment table.
      a segment       just that kind of business.
      A SERVICE       every kind of business that BUYS that service, and nobody
                      else. "Get me shower-glass work this week" now targets
                      hotels, gyms, villas and fit-out contractors — not a
                      warehouse that will never want one. The buyer list comes
                      off services.py, so it can never drift from what the mail
                      will end up writing about.

    Aiming by service also STAMPS the service onto every target, so a lead
    collected for shower glass is stored knowing that. Before, the service was
    guessed back out of the business's own listing afterwards — fine when you
    are sweeping the market, wrong when you went looking for one specific job.

    LOCALS FIRST. Areas are searched nearest-out: the owner's own districts
    before the far emirates. A glass firm in Dubai wins a Business Bay job it
    can survey tomorrow; a Ras Al Khaimah enquiry is two hours each way before
    anyone has quoted anything. When the search budget runs out — and it always
    does — it should run out on the far end, not the near one.

    The order is round-robin across segments, not segment by segment. It matters
    because callers cap the number of searches: nested loops would spend the
    whole budget on fit-out contractors in six districts and never reach a hotel,
    which is exactly the narrowness this sector was rebuilt to get away from.
    """
    svc = services.resolve_service(service) if service else ""
    if service and not svc:
        return []                      # an unknown service must find nobody

    if segment:
        segs = [segment]
    elif svc:
        # only the segments that actually buy this job
        segs = [s for s in services.buyers_of(svc) if s in ls.SEGMENTS]
    else:
        segs = list(ls.SEGMENTS)

    areas = [area] if area else local_first(ls.DISTRICTS)

    per_seg = []
    for s in segs:
        meta = ls.SEGMENTS.get(s)
        if not meta:
            continue
        per_seg.append([{"segment": s, "query": q, "area": a,
                         **({"for_service": svc} if svc else {})}
                        for q in list(meta["queries"])[:per_segment] for a in areas])

    out, i = [], 0
    while any(len(q) > i for q in per_seg):
        for q in per_seg:
            if len(q) > i:
                out.append(q[i])
        i += 1
    return out


def critic_check(candidates, searched, errors):
    """
    The Hunter's failure modes: collecting nothing and calling it a day,
    collecting the same firm twice, or collecting rows nobody can contact.
    """
    reasons = []
    if not searched:
        reasons.append("no searches ran — there is nothing to report")
    if searched and not candidates and not errors:
        reasons.append("every search came back empty and nothing errored — that is "
                       "not a real result, check the query and the key")
    names = [c["company"].strip().lower() for c in candidates if c.get("company")]
    if len(names) != len(set(names)):
        reasons.append("the same company appears twice in one run")
    blank = [c for c in candidates if not (c.get("email") or c.get("phone"))]
    if blank:
        reasons.append(f"{len(blank)} rows have no way to reach them at all")
    noservice = [c for c in candidates if not c.get("service")]
    if noservice:
        reasons.append(f"{len(noservice)} rows have no service attached — we would not "
                       "know what to write to them about")
    return (not reasons, reasons)


def run(agent="leads-hunter", segment=None, area=None, limit=20, per_segment=1,
        max_searches=6, dry_run=False, enrich_sites=True, service=None):
    """
    Collect leads — and, first, read the board to find out which leads.

    THE HUNTER USED TO IGNORE THE BOARD ENTIRELY. It ran the same sweep every
    day whatever the MD had posted, so "find 20 leads in Dubai" sat open while
    the desk that could do it ran past it. Now the job steers the pass: how
    many, which emirate, which service. Anything the job does not say keeps the
    caller's default, so a plain scheduled run behaves exactly as before.
    """
    with board.agent_run(agent) as run_row:
        with deskwork.at_the_desk("Leads", agent, run_row) as job:
            if job:
                limit, max_searches = _sized(job.how_many(None),
                                             limit, max_searches)
                area = job.first_mention(ls.DISTRICTS, area)
                # resolve_service already forgives our internal spelling —
                # "frameless shower" and "shower_glass" land on the same job
                service = services.resolve_service(job.text) or service
                # a job naming an emirate but no district still means that
                # emirate — search it wide rather than not at all
                if area is None:
                    area = job.first_mention(EMIRATES, None)
            out = _hunt(agent, segment, area, limit, per_segment, max_searches,
                        dry_run, enrich_sites, run_row, service)
            _answer(job, out, area, service)
        run_row.note(out.get("summary") or out.get("error") or "hunted")
        return out


# Google Places returns at most 20 results for one search, so a job asking for
# more than that is asking for more searches, not a bigger page.
PLACES_PAGE = 20
MOST_SEARCHES = 12


def _sized(wanted, limit, max_searches):
    """
    Turn "find 20 leads" into a page size and a number of searches.

    Twenty is one page. Sixty is three. Not every result becomes a stored lead
    — most are duplicates or get screened out — so the searches are rounded up
    rather than down, and capped, because a job typed as "500" is a typo and
    not a mandate to spend the month's Places budget in one pass.
    """
    if not wanted:
        return limit, max_searches
    wanted = max(1, int(wanted))
    page = min(PLACES_PAGE, wanted)
    searches = min(MOST_SEARCHES, -(-wanted // PLACES_PAGE))
    return page, max(max_searches if wanted <= PLACES_PAGE else 1, searches)


EMIRATES = ("Dubai", "Abu Dhabi", "Sharjah", "Ajman", "Ras Al Khaimah",
            "Fujairah", "Umm Al Quwain")


def _answer(job, out, area, service):
    """
    Close the job the way the hunt actually went — and only the way it went.

    The distinction that matters to him: a pass that could not run (no key on
    the maps shelf) is BLOCKED with that sentence, because there is something
    to fix. A pass that ran and found only firms already in the cabinet is
    DONE, because the desk did its job and the answer was 'nobody new'. A pass
    that found nothing and cannot say why hands the job back rather than
    claiming either.
    """
    if not job:
        return
    where = f" in {area}" if area else ""
    what = f" for {services.label(service)}" if service else ""
    if not out.get("ok"):
        job.cannot(out.get("error") or "the hunt did not complete and gave no reason.")
        return
    if out.get("dry_run"):
        job.cannot("This was a planning pass only — nothing was collected, so "
                   "the job is not done.")
        return
    if out.get("critic") == "rejected":
        job.cannot("The Critic rejected the batch: "
                   + "; ".join(out.get("critic_reasons") or ["no reason given"]))
        return
    stored, dupes = out.get("stored", 0), out.get("duplicates", 0)
    if stored:
        job.did(f"Collected {stored} new lead(s){what}{where} from "
                f"{len(out.get('searches') or [])} searches"
                + (f"; {dupes} were already in the cabinet." if dupes else "."),
                stored=stored, duplicates=dupes)
    elif dupes:
        job.did(f"Searched{where}{what} and found {dupes} firm(s) — every one of "
                "them already in the cabinet, so nothing new was collected. "
                "This source is worked out; a different area or segment would "
                "be a better ask.", stored=0, duplicates=dupes)
    else:
        errs = [s.get("error") for s in (out.get("searches") or []) if s.get("error")]
        if errs:
            job.cannot("Every search failed: " + "; ".join(sorted(set(errs))[:3]))
        else:
            job.did(f"Searched{where}{what} across "
                    f"{len(out.get('searches') or [])} queries and found no "
                    "businesses at all matching them.", stored=0)


def _hunt(agent, segment, area, limit, per_segment, max_searches, dry_run,
          enrich_sites, run_row, service=None):
    key, key_from = _places_key()
    targets = plan_targets(segment, area, per_segment, service)[:max_searches]
    if service and not targets:
        return {"ok": False, "error":
                f"'{service}' is not a service this business sells, so there is "
                f"nobody to go and look for. One of: "
                + ", ".join(sorted(services.SERVICES))}

    if dry_run or not key:
        plan = {"ok": bool(targets), "dry_run": True, "searches_planned": len(targets),
                "targets": targets[:20],
                "segments_in_scope": len(ls.SEGMENTS),
                "areas_in_scope": len(ls.DISTRICTS),
                "summary": f"planned {len(targets)} searches across "
                           f"{len(set(t['segment'] for t in targets))} segments"}
        if not key:
            plan["ok"] = False
            plan["error"] = (
                "No Google Places key. Put one on the 'maps' shelf (Setup -> Shelf -> maps) "
                "or set GOOGLE_PLACES_API_KEY. Get it at console.cloud.google.com: enable "
                "'Places API (New)', create an API key, restrict it to that API. Google's "
                "monthly credit covers far more searching than this business will do. "
                "Without it nothing real can be collected — the plan above is what it "
                "WOULD run.")
        return plan

    # only one Hunter goes shopping at a time: two of them running the same
    # segment would spend twice and collect the same firms.
    with board.lock("leads_collection", agent, ttl=900):
        candidates, searched, errors = [], [], []
        seen_here = set()
        for t in targets:
            places, err = search_places(t["query"], t["area"], key, limit)
            searched.append({**t, "found": len(places), "error": err})
            if err:
                errors.append(err)
                continue
            for p in places:
                c = _place_to_candidate(p, t["segment"], t["area"])
                # Hunting FOR a service beats guessing one back out of the
                # listing: we already know what job we went looking for, and the
                # listing only ever hinted at it.
                if t.get("for_service"):
                    c["service"] = t["for_service"]
                    c["service_label"] = services.label(t["for_service"])
                    c["service_reason"] = ("collected specifically for "
                                           + services.label(t["for_service"]))
                nm = c["company"].strip().lower()
                if not nm or nm in seen_here:
                    continue
                if (c.get("status") or "OPERATIONAL") != "OPERATIONAL":
                    continue             # closed businesses are not leads
                seen_here.add(nm)
                candidates.append(c)
            time.sleep(0.5)

        if enrich_sites:
            for c in candidates:
                if c.get("website"):
                    enrich(c)

        ok, reasons = critic_check(candidates, searched, errors)
        if not ok:
            return {"ok": True, "critic": "rejected", "critic_reasons": reasons,
                    "searches": searched, "found": len(candidates),
                    "stored": 0,
                    "summary": "critic rejected: " + "; ".join(reasons)}

        # screen + dedup + store. intake() is the only door into the leads table.
        result = ls.intake(candidates, agent=agent)

        did = board.log_decision(
            "leads-hunt",
            reasoning=(f"Searched {len(searched)} segment/area combinations via {key_from}; "
                       f"{len(candidates)} live businesses found, "
                       f"{len(result['stored'])} new, {len(result['duplicates'])} already "
                       f"in the cabinet, {len(result['refused'])} screened out."),
            expectation="Each new lead carries the service it needs, so outreach writes "
                        "about that job rather than reciting the whole service list.",
            confidence=0.7,
            evidence={"lead_ids": [s["id"] for s in result["stored"]]})

        by_service = {}
        for c in candidates:
            by_service[c["service_label"]] = by_service.get(c["service_label"], 0) + 1

        return {"ok": True, "agent": agent, "critic": "passed",
                "searches": searched, "found": len(candidates),
                "stored": len(result["stored"]), "duplicates": len(result["duplicates"]),
                "refused": result["refused"],
                "with_email": sum(1 for c in candidates if c.get("email")),
                "with_phone": sum(1 for c in candidates if c.get("phone")),
                "by_service": by_service,
                "personal_data_held": result["personal_data_held"],
                "contacted": 0, "decision_id": did,
                "summary": (f"{len(result['stored'])} new leads from {len(searched)} "
                            f"searches ({sum(1 for c in candidates if c.get('email'))} "
                            f"with a published email)")}


def main():
    ap = argparse.ArgumentParser(description="Collect real UAE business leads.")
    ap.add_argument("--segment", help="one segment (default: every segment)")
    ap.add_argument("--service", help="hunt for the businesses that BUY this "
                    "service, e.g. --service 'shower glass'")
    ap.add_argument("--area", help="one area (default: nearest first)")
    ap.add_argument("--limit", type=int, default=20, help="places per search (max 20)")
    ap.add_argument("--max-searches", type=int, default=6)
    ap.add_argument("--per-segment", type=int, default=1, help="search terms per segment")
    ap.add_argument("--dry-run", action="store_true", help="plan only, collect nothing")
    ap.add_argument("--no-enrich", action="store_true", help="skip reading their websites")
    a = ap.parse_args()
    print(json.dumps(run(segment=a.segment, area=a.area, limit=a.limit,
                         per_segment=a.per_segment, max_searches=a.max_searches,
                         dry_run=a.dry_run, enrich_sites=not a.no_enrich,
                         service=a.service), indent=2))


if __name__ == "__main__":
    main()
