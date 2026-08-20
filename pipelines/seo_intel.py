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
"""
import argparse
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

from coordination import board, cabinet

try:
    from . import seo_gap, services
except ImportError:  # pragma: no cover
    import seo_gap, services

AGENT = "seo-strategist"
UA = "Mozilla/5.0 (compatible; glasswork-seo/1.0)"

AREAS = ["Dubai", "Abu Dhabi", "Sharjah", "Al Quoz", "Business Bay",
         "Jumeirah", "Deira", "Ajman", "Dubai Marina"]

# How a person finishes a search when they are close to paying somebody.
BUY_WORDS = ["price", "cost", "near me", "company", "supplier", "installation",
             "repair", "contractor", "fixing", "quotation"]

# How a person searches when they are still working out what they want. Worth
# less as a lead, worth more for AEO — these are the ones an answer engine
# intercepts, so they feed straight into seo_answers.
ASK_WORDS = ["how much", "what is", "how to", "can you", "best", "which"]


# ---------------------------------------------------------------------------
# 1. GOOGLE SEARCH CONSOLE — free, and it is his own data
# ---------------------------------------------------------------------------

GSC_SA = "config/gsc-service-account.local.json"
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"


def gsc_ready():
    """(ok, why_not) — is Search Console actually usable right now?"""
    from pathlib import Path
    root = Path(__file__).parents[1]
    try:
        from providers import integrations
        prop = (integrations.read_env().get("GSC_PROPERTY") or "").strip()
    except Exception:
        prop = ""
    if not prop:
        return False, ("no GSC_PROPERTY set. Search Console is free and it is the "
                       "single best keyword source you own — add it on /setup → "
                       "Integrations.")
    if not (root / GSC_SA).exists():
        return False, (f"GSC_PROPERTY is set but {GSC_SA} is missing. Upload the "
                       f"service-account JSON on /setup → Integrations, and share "
                       f"the property with that account's email in Search Console.")
    try:
        import google.oauth2.service_account  # noqa: F401
    except Exception:
        return False, ("the Google auth library is not installed. One command, "
                       "once: pip install google-auth requests")
    return True, ""


def gsc_queries(days=90, limit=500):
    """
    Real queries that brought impressions to this site, with position.

    Returns [] and a reason rather than raising, because every other source here
    works without it and a missing optional login must not stop the pass.
    """
    ok, why = gsc_ready()
    if not ok:
        return [], why
    from pathlib import Path
    import google.auth.transport.requests
    from google.oauth2 import service_account
    import requests

    root = Path(__file__).parents[1]
    from providers import integrations
    prop = (integrations.read_env().get("GSC_PROPERTY") or "").strip()

    creds = service_account.Credentials.from_service_account_file(
        str(root / GSC_SA), scopes=[GSC_SCOPE])
    creds.refresh(google.auth.transport.requests.Request())

    end = time.strftime("%Y-%m-%d", time.localtime())
    start = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    url = ("https://searchconsole.googleapis.com/webmasters/v3/sites/"
           + urllib.parse.quote(prop, safe="") + "/searchAnalytics/query")
    try:
        r = requests.post(url, timeout=45,
                          headers={"Authorization": "Bearer " + creds.token},
                          json={"startDate": start, "endDate": end,
                                "dimensions": ["query"], "rowLimit": limit})
        if r.status_code != 200:
            return [], f"Search Console said {r.status_code}: {r.text[:160]}"
        rows = r.json().get("rows") or []
    except Exception as e:
        return [], f"Search Console call failed: {e}"

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
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        return [s.strip().lower() for s in (d[1] if len(d) > 1 else []) if s.strip()]
    except Exception:
        return []


def _suggest(q):
    """Both engines, deduped. seo_gap owns the Google one; do not duplicate it."""
    out = []
    try:
        out += seo_gap._suggest(q)
    except Exception:
        pass
    out += _bing_suggest(q)
    seen, uniq = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


# The daily runner kills a step at 420 seconds. Twelve seeds times twenty probes
# times two engines is comfortably inside that on a good line and comfortably
# outside it on a bad one, and a step that dies at 419 seconds throws away every
# phrase it gathered. So the pass works to a clock: when the budget is spent it
# stops, keeps what it has, and says how far it got. Partial and honest beats
# complete and killed.
TIME_BUDGET = 300


def expand(seed, areas=None, deep=False, pause=0.2, deadline=None):
    """
    One seed, worked from every angle a customer would type it.

    Autocomplete only answers the prefix you give it, so asking once for
    "glass partition" and stopping is how you get eight results and conclude
    there is no demand. Asking it the way people actually type — with the area,
    with the price question, with the letter they are part-way through — is what
    turns one seed into forty real phrases for nothing.
    """
    areas = list(areas or AREAS[:3])
    prefixes = [seed] + [f"{w} {seed}" for w in ASK_WORDS]
    suffixes = [f"{seed} {a}" for a in areas] + [f"{seed} {w}" for w in BUY_WORDS]
    probes = prefixes + suffixes
    if deep:
        probes += [f"{seed} {c}" for c in "abcdefghijklmnopqrstuvwxyz"]

    found, stopped = {}, False
    for p in probes:
        if deadline and time.time() > deadline:
            stopped = True
            break
        for s in _suggest(p):
            found.setdefault(s, 0)
            found[s] += 1
        time.sleep(pause)                 # free endpoints, so be a good guest
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

STRIKING_LOW, STRIKING_HIGH = 4.5, 20.5


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
    s = 0.0
    s += min(row.get("impressions", 0), 500) * 0.20
    if row.get("striking"):
        s += 40
    s += min(row.get("suggest_breadth", 0), 30) * 1.5
    s += min(row.get("rival_pages", 0), 5) * 6
    if row.get("intent") == "buy":
        s += 18
    if row.get("area"):
        s += 12
    if row.get("in_enquiries"):
        s += 25
    if row.get("we_have_page"):
        s -= 20                # already covered; the gap is elsewhere
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


def build(con, seeds=None, deep=False, our_paths=(), budget=None) -> dict:
    seeds = list(seeds or seo_gap.SERVICES)
    gsc, gsc_why = gsc_queries()
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
                  "striking": int(STRIKING_LOW <= g["position"] <= STRIKING_HIGH
                                  and g["impressions"] >= 10)})
        r["sources"].add("search-console")

    deadline = time.time() + (budget or TIME_BUDGET)
    seeds_done, ran_out = 0, False
    for seed in seeds:
        if time.time() > deadline:
            ran_out = True
            break
        got, stopped = expand(seed, deep=deep, deadline=deadline)
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
    return {"phrases": len(rows), "gsc_rows": len(gsc), "gsc_why": gsc_why,
            "striking": sum(1 for r in rows.values() if r.get("striking")),
            "from_enquiries": sum(1 for r in rows.values() if r.get("in_enquiries")),
            "seeds_done": seeds_done, "seeds_total": len(seeds),
            "ran_out_of_time": ran_out}


def striking_distance(con, limit=15):
    """Ranked 5th to 20th with real impressions. Free money, no tool required."""
    _table(con)
    return [dict(r) for r in con.execute(
        "SELECT * FROM keyword_intel WHERE striking=1"
        " ORDER BY impressions DESC LIMIT ?", (limit,))]


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


def run(agent=AGENT, deep=False) -> dict:
    with board.agent_run(agent) as run_row:
        out = _work(agent, deep)
        run_row.note(out.get("summary") or out.get("error") or "keyword intel pass")
        return out


def _work(agent, deep) -> dict:
    board.ensure_schema()
    b = cabinet.borrow("seo_data", agent, "SEO",
                       reason="free keyword intelligence pass (no paid API)",
                       data_class="external", wait=True)
    borrow_id = b.get("borrow_id")
    try:
        con = sqlite3.connect(board.DB, timeout=30)
        con.row_factory = sqlite3.Row

        our_paths = []
        try:
            our_paths = [r[0].lower() for r in con.execute(
                "SELECT DISTINCT url FROM aeo_findings")]
        except sqlite3.Error:
            pass

        built = build(con, deep=deep, our_paths=our_paths)
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
            board.post_task(
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
                + "\n".join(f"- {t['phrase']}\n    {t['evidence']}" for t in writeable))

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
        result["summary"] = (
            f"{built['phrases']} phrases from free sources, {built['striking']} in "
            f"striking distance, {len(writeable)} pages handed to Content" + partial)
        return result
    finally:
        if borrow_id:
            cabinet.release("seo_data", agent, borrow_id=borrow_id, usd_cost=0.0)


def as_text(wins_only=False) -> str:
    board.ensure_schema()
    con = sqlite3.connect(board.DB, timeout=30)
    con.row_factory = sqlite3.Row
    _table(con)
    if wins_only:
        rows = striking_distance(con, 25)
        con.close()
        if not rows:
            return ("No striking-distance queries on file. Either the intel pass has "
                    "not run, or Search Console is not connected — that is the source "
                    "for this one, and it is free.\n"
                    "  python -m pipelines.seo_intel")
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
    a = ap.parse_args()
    if a.wins:
        print(as_text(wins_only=True))
        return
    if a.report:
        print(as_text())
        return
    print(json.dumps(run(deep=a.deep), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
