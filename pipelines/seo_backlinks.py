"""
seo_backlinks.py — where a link to us could come from, and what to do about it.

    python -m pipelines.seo_backlinks              build/refresh the opportunity list
    python -m pipelines.seo_backlinks --list       what is on the list and its state
    python -m pipelines.seo_backlinks --pitch 4    draft a guest-post pitch for row 4

WHAT THIS SECTOR IS FOR, AND WHAT IT WILL NOT DO

The owner asked for backlink building and guest posting. Both are worth doing
properly and both are worth doing badly, so the line is drawn here in writing.

WILL NOT, EVER:
  - buy links, or use anything that sells them by the hundred
  - post in blog comments, forums or Q&A sites purely to leave a link
  - use a private blog network, an expired-domain scheme or a link exchange ring
  - spin one article into forty and scatter it

Not squeamishness. For a real trade business with a real address, those tactics
buy a short-lived ranking and a long-lived penalty, and the penalty lands on the
one domain that takes his enquiries. There is no second site to fall back on.

WILL:
  - LOCAL CITATIONS first. For a UAE glass and aluminium firm these are worth
    more than any blog link: they carry the name, address and phone that Google
    cross-checks before it will rank anybody in the map pack, and the map pack
    is where a Business Bay office manager actually finds a glazier.
  - SUPPLIER AND MANUFACTURER pages. Extruders and glass processors publish
    lists of fabricators and approved installers. Industry-relevant, high
    authority, and the owner already buys from several of them — this is a phone
    call he can make himself, not a cold pitch.
  - TRADE BODIES AND CHAMBERS. Membership he may already hold, with a directory
    entry he may never have claimed.
  - GUEST POSTS, but only where a real publication takes a real article. One
    piece on a UAE interior-design or construction title, written from ten years
    of actual jobs, beats fifty scattered paragraphs.
  - RIVAL GAPS, where somebody links to three competitors and not to us. That
    needs a backlink API and this says so plainly rather than pretending.

WHAT IT DOES NOT DO ITSELF

It does not contact anybody. It finds the opportunity, says why it is worth
having and how much work it is, and files it. A guest-post pitch is drafted by
Content and sent through Leads, under the cap and after Security clears the
recipient — the same road every other outgoing message takes. A sector that
could research a target AND mail it is a sector marking its own homework.
"""
import argparse
import json
import sqlite3
import time
from urllib.parse import urlparse

from coordination import board, cabinet, deskwork

try:
    from . import services
except ImportError:  # pragma: no cover
    import services

AGENT = "seo-backlinks"

# ---------------------------------------------------------------------------
# THE SEED LIST
#
# Real, public, UAE-relevant places a glass and aluminium firm belongs. Ordered
# by what actually moves the needle for a local trade, which is NOT the same as
# ordered by domain authority: a Dubai Chamber entry outranks a generic global
# directory for the searches that bring in work, every time.
#
# 'effort' is the honest cost to the owner, because that is what decides whether
# a thing gets done: free = fill it in, form = an application, pitch = you must
# persuade a person. Nothing here is 'paid'; paid is refused above.
# ---------------------------------------------------------------------------
SEEDS = [
    # --- the ones Google cross-checks before it will rank a local business ---
    ("google.com/business", "Google Business Profile", "citation", "free",
     "the single most valuable listing for a trade — it is what puts you in the "
     "map pack, and the map pack is how a Business Bay office manager finds a glazier"),
    ("bing.com/places", "Bing Places", "citation", "free",
     "second search engine, same name-address-phone check, ten minutes to claim"),
    ("apple.com/maps", "Apple Business Connect", "citation", "free",
     "every iPhone user asking Siri for a glazier near them"),
    ("yellowpages.ae", "Yellow Pages UAE", "citation", "free",
     "the oldest UAE business directory and still one Google trusts for verification"),
    ("uaecontact.com", "UAE Contact", "citation", "free",
     "long-standing local directory, free listing with a category for glass work"),
    ("yellowpages-uae.com", "Yellow Pages UAE (alt)", "citation", "free",
     "a second UAE directory — consistency across several is what the check rewards"),
    ("connect.ae", "Connect.ae", "citation", "form",
     "UAE business directory with a real services taxonomy"),
    ("dubaiyellowpagesonline.com", "Dubai Yellow Pages Online", "citation", "free",
     "Dubai-specific, indexes quickly"),
    ("hotfrog.ae", "Hotfrog UAE", "citation", "free",
     "free listing, accepts a service description and a link"),
    ("cybo.com", "Cybo", "citation", "free",
     "global directory with a strong UAE section"),

    # --- trade bodies: membership he may already hold and never have claimed ---
    ("dubaichamber.com", "Dubai Chamber of Commerce", "association", "form",
     "if the trade licence is Dubai, the chamber directory entry is already paid "
     "for by the membership — most owners never claim it"),
    ("adcci.gov.ae", "Abu Dhabi Chamber", "association", "form",
     "same again for Abu Dhabi work"),
    ("sharjahchamber.ae", "Sharjah Chamber of Commerce", "association", "form",
     "same again for Sharjah"),
    ("emiratesgbc.org", "Emirates Green Building Council", "association", "form",
     "relevant if any of the glazing work is thermal or energy-rated — worth a "
     "look before paying for membership"),

    # --- suppliers: industry-relevant, high authority, and he already buys ---
    ("gulfextrusions.com", "Gulf Extrusions", "supplier", "pitch",
     "aluminium extruder — ask whether they list fabricators who buy from them. "
     "This is a phone call to an existing supplier, not a cold pitch"),
    ("alupcogroup.com", "ALUPCO", "supplier", "pitch",
     "aluminium systems supplier with an installer network worth being on"),
    ("emiratesglass.com", "Emirates Glass", "supplier", "pitch",
     "processor — approved-installer pages carry real weight in this trade"),
    ("saint-gobain.ae", "Saint-Gobain Glass Gulf", "supplier", "pitch",
     "manufacturer partner listings; ask through whoever sells you their sheet"),
    ("technal.com", "Technal Middle East", "supplier", "pitch",
     "systems house with a fabricator directory"),

    # --- guest posts: real publications, real articles ---
    ("designmena.com", "Design MENA", "guest_post", "pitch",
     "regional design and build title. A piece on what actually fails in UAE "
     "glazing after five summers is publishable and nobody else can write it"),
    ("constructionweekonline.com", "Construction Week Middle East", "guest_post", "pitch",
     "trade title; takes contributed technical pieces from people who do the work"),
    ("identitydubai.com", "Identity", "guest_post", "pitch",
     "UAE interior design magazine — a finished villa with photographs is the pitch"),
    ("propertyfinder.ae/blog", "Property Finder blog", "guest_post", "pitch",
     "reaches villa owners and landlords directly, which is exactly the buyer"),
    ("gulfnews.com", "Gulf News property/home", "guest_post", "pitch",
     "hard to land and worth trying once you have two or three published pieces"),
]

# What we refuse, and the word that gives it away. Kept as data so the refusal
# can be shown to the owner rather than living only in a docstring.
REFUSED_TACTICS = {
    "paid links": "a link you paid for is a link Google can see you paid for",
    "pbn": "a private blog network is the fastest way to lose the only domain "
           "that takes your enquiries",
    "link exchange": "reciprocal rings are pattern-matched and discounted",
    "comment spam": "it does nothing for rankings and everything for your reputation",
    "article spinning": "forty bad copies of one article is forty bad signals",
}


def _now():
    return int(time.time())


def _host(u):
    u = (u or "").strip().lower()
    if "://" not in u:
        u = "https://" + u
    return (urlparse(u).netloc or "").replace("www.", "")


def _our_domain():
    try:
        from coordination import site
        return _host(site.status().get("url") or "")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# BUILD THE LIST
# ---------------------------------------------------------------------------

def build(con) -> dict:
    """
    Put every seed on the board once. UNIQUE(host, kind) means running this
    every day adds what is new and touches nothing that already has a decision
    against it — a row the owner marked 'live' must never quietly revert to
    'idea' because the pipeline ran again.
    """
    added, already = 0, 0
    for host, name, kind, effort, why in SEEDS:
        h = _host(host)
        cur = con.execute(
            "INSERT OR IGNORE INTO backlinks(host,url,name,kind,why,local,effort,"
            "status,found_at) VALUES(?,?,?,?,?,?,?, 'idea', ?)",
            (h, host if "://" in host else "https://" + host, name, kind, why,
             1 if h.endswith(".ae") or "dubai" in h or "uae" in h else 0,
             effort, _now()))
        if cur.rowcount:
            added += 1
        else:
            already += 1
    con.commit()
    return {"added": added, "already_known": already}


def rival_gaps(con) -> dict:
    """
    Who links to three of our rivals and not to us. The highest-value list there
    is, and we cannot build it honestly without a backlink index.

    Google does not publish one, and it cannot be scraped out of search results
    — anybody claiming otherwise is guessing. So this reports the gap in our own
    capability instead of inventing findings. Ahrefs, Majestic, Moz and
    DataForSEO all sell the index; any one of them on the seo_data shelf turns
    this on.
    """
    try:
        n = con.execute("SELECT count(*) FROM competitors").fetchone()[0]
    except sqlite3.Error:
        n = 0
    try:
        from providers import store
        has_index = any("backlink" in (t.get("name", "") + t.get("base_url", "")).lower()
                        for t in store.tools_on_shelf("seo_data"))
    except Exception:
        has_index = False
    return {
        "rivals_on_file": n,
        "possible": bool(has_index),
        "why_not": ("" if has_index else
                    "Rival-gap analysis needs a backlink index, and there is no honest "
                    "way to build one from search results. Put Ahrefs, Majestic, Moz or "
                    "DataForSEO's backlink endpoint on the seo_data shelf and this turns "
                    "on. Until then the citation and supplier work below is the higher-"
                    "value job anyway."),
    }


# How many of each kind make this week's list. A QUOTA, not a top-N.
#
# Ranking everything into one list and taking the top eight looked sensible and
# was wrong: citations sort first on every tiebreak, so the list came back as
# eight directories and a guest post never surfaced — the sector would have gone
# on filing guest-post opportunities for ever and never handing one to Content.
# A quota guarantees every stream moves each week, which is the difference
# between a plan and a backlog.
QUOTA = {"citation": 4, "association": 2, "supplier": 2, "guest_post": 2}


def shortlist(con, quota=None) -> list:
    """
    What to do next: a few from each stream, easiest-first within each.

    Free-and-local beats a prestigious pitch that will sit in a drafts folder,
    so effort breaks the tie inside a kind. Across kinds nothing competes —
    they each get their share.
    """
    quota = quota or QUOTA
    rank_effort = {"free": 0, "form": 1, "pitch": 2, "paid": 9}
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM backlinks WHERE status='idea' ORDER BY id")]

    out = []
    for kind in ("citation", "association", "supplier", "guest_post"):
        of_kind = [r for r in rows if r["kind"] == kind]
        of_kind.sort(key=lambda r: (rank_effort.get(r["effort"], 5),
                                    0 if r["local"] else 1, r["id"]))
        out.extend(of_kind[:quota.get(kind, 0)])
    return out


def critic_check(built, short, gaps, waiting=()):
    """
    This sector's failure modes: filing nothing, filing only the glamorous
    pitches nobody will send, claiming a rival analysis it cannot do, and
    starving one stream for ever because another sorts first.

    `waiting` is the set of kinds that still have unactioned rows on file.
    """
    reasons = []
    if not short and not built["already_known"]:
        reasons.append("nothing on the list at all — the seed list did not load")
    if short and not any(r["effort"] == "free" for r in short):
        reasons.append("every suggestion needs a pitch; at least one thing the owner "
                       "can finish today should be at the top")
    if short and not any(r["local"] for r in short):
        reasons.append("nothing local in the shortlist — for this trade the UAE "
                       "listings are the ones that pay")
    # The owner asked for guest posting specifically. A shortlist that never
    # contains one means the sector files them for ever and hands over none,
    # which is exactly how the first version of this ranking behaved: citations
    # won every tiebreak and took all eight slots.
    on_list = {r["kind"] for r in short}
    for kind in waiting:
        if kind not in on_list:
            reasons.append(f"nothing of kind '{kind}' reached the list even though "
                           "there are some waiting — that stream would never move")
    if gaps["possible"] is False and gaps.get("claimed"):
        reasons.append("claimed a rival-gap finding with no backlink index behind it")
    return (len(reasons) == 0, reasons)


def run(agent=AGENT) -> dict:
    with board.agent_run(agent) as run_row:
        with deskwork.at_the_desk("SEO", agent, run_row) as job:
            out = _work(agent)
            deskwork.settle(job, out, made=out.get("added"),
                            what="new link opportunity")
        run_row.note(out.get("summary") or out.get("error") or "backlink pass")
        return out


def _work(agent) -> dict:
    board.ensure_schema()

    # The list itself is ours to build, but reading anything about rivals is
    # borrowing the shelf like everybody else.
    b = cabinet.borrow("seo_data", agent, "SEO",
                       reason="backlink and guest-post opportunity pass",
                       data_class="external", wait=True)
    borrow_id = b.get("borrow_id")
    try:
        con = sqlite3.connect(board.DB, timeout=30)
        con.row_factory = sqlite3.Row
        built = build(con)
        gaps = rival_gaps(con)
        short = shortlist(con)
        waiting = {r[0] for r in con.execute(
            "SELECT DISTINCT kind FROM backlinks WHERE status='idea'")}
        ok, reasons = critic_check(built, short, gaps, waiting)

        result = {"ok": True, "agent": agent, **built,
                  "shortlist": [{"name": r["name"], "kind": r["kind"],
                                 "effort": r["effort"], "why": r["why"]}
                                for r in short],
                  "rival_gaps": gaps,
                  "refuses": list(REFUSED_TACTICS)}
        if not ok:
            con.close()
            result.update({"critic": "rejected", "critic_reasons": reasons,
                           "summary": "critic rejected: " + "; ".join(reasons)})
            return result
        result["critic"] = "passed"

        # Guest posts are Content's to write and Leads' to send. Ask, don't reach.
        pitches = [r for r in short if r["kind"] == "guest_post"]
        if pitches:
            body = ("These publications take contributed pieces from people who do the "
                    "work. Each needs ONE article built from a real finished job — "
                    "photographs, what went wrong, what it cost to put right:\n- "
                    + "\n- ".join(f"{r['name']} — {r['why']}" for r in pitches)
                    + "\n\nNothing is sent from here. Draft it, and Leads sends it "
                      "after Security has cleared the recipient.")
            result["asked_content"] = board.post_request(
                "SEO", "Content", "Guest-post pieces worth writing", body)

        free_now = [r for r in short if r["effort"] == "free"]
        if free_now:
            board.try_post_task(
                "SEO", f"Claim {len(free_now)} free listing(s) that need no pitch",
                "\n".join(f"{r['name']} ({r['url']}) — {r['why']}" for r in free_now),
                risk="green", created_by="SEO")

        did = board.log_decision(
            "seo-backlinks",
            reasoning=(f"{built['added']} new opportunities filed, {built['already_known']} "
                       f"already known. Shortlist leads with {len(free_now)} free local "
                       f"listing(s) because a citation Google can cross-check moves the "
                       f"map pack, and the map pack is where this trade gets found. "
                       f"Rival-gap analysis {'available' if gaps['possible'] else 'not possible'}."),
            expectation="The free local citations are claimed within the week and the "
                        "business starts appearing for 'near me' searches it misses today.",
            confidence=0.7,
            evidence={"backlink_ids": [r["id"] for r in short],
                      "free_now": [r["host"] for r in free_now]})
        result["decision_id"] = did
        con.close()

        result["summary"] = (
            f"{built['added']} new link opportunities, {len(free_now)} claimable today, "
            f"{len(pitches)} guest-post pieces handed to Content")
        return result
    finally:
        if borrow_id:
            cabinet.release("seo_data", agent, borrow_id=borrow_id, usd_cost=0.0)


def as_text() -> str:
    board.ensure_schema()
    con = sqlite3.connect(board.DB, timeout=30)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM backlinks ORDER BY status, kind, id")]
    con.close()
    if not rows:
        return "No link opportunities on file yet — run `python -m pipelines.seo_backlinks`."
    L = [f"LINK OPPORTUNITIES — {len(rows)} on file"]
    last = None
    for r in rows:
        if r["kind"] != last:
            L.append(f"\n  {r['kind'].replace('_', ' ').upper()}")
            last = r["kind"]
        L.append(f"    [{r['id']:>3}] {r['status']:<8} {r['effort']:<6} {r['name']}")
        L.append(f"          {r['why']}")
    L.append("\nWHAT THIS SECTOR REFUSES TO DO")
    for t, why in REFUSED_TACTICS.items():
        L.append(f"  x {t} — {why}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Backlinks and guest posts, honestly.")
    ap.add_argument("--list", action="store_true", help="show the list, run nothing")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.list:
        print(as_text())
        return 0
    out = run()
    print(json.dumps(out, indent=2) if a.json else json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
