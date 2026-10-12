"""
briefing.py — what this agency can actually do, right now.

    python -m coordination.briefing            (read it)
    python -m coordination.briefing --json     (feed it to an agent)

WHY THIS EXISTS

An agent that does not know what tools the agency owns does one of two things,
and both are bad. It either refuses work it could easily have done, or it
promises work it has no way of doing — a Hunter that says "collecting leads
now" with no maps key, a Writer that offers images with no image shelf. Neither
is fixable with a better prompt, because the answer changes every time the owner
adds a key on /setup.

So the briefing is generated, not written. It reads the shelf, the board and the
cabinet and says plainly: these are the sectors and what each one does, these are
the capabilities that are switched on, these are the ones that are missing and
what turns them on, and this is what the agency is currently holding. Paste it
into an agent's context and it knows the state of its own employer.

Everything here is read-only. It changes nothing and sends nothing.
"""
import argparse
import json
import sqlite3
import time

# the open website's cabinet; the test run's own when AGENCY_DB is set
DB = __import__("coordination.workspace", fromlist=["db_path"]).db_path()

# What each sector is for, and the command that makes it do a real cycle. The
# commands are the honest bit: an agent can read this and know the sector is not
# a description, it is something that runs.
SECTORS = {
    "Director": {
        "does": "Reads the state of the business, reasons out the day's plan, has "
                "the Critic challenge it, then posts tasks to the board. Executes nothing.",
        "runs": [],
    },
    "SEO": {
        "does": "Finds service+area keyword gaps from real demand data, and hands the "
                "buying-intent ones to Content. Watches what the named rivals "
                "publish, by diffing their own sitemaps run to run.",
        "runs": ["python -m pipelines.seo_gap",
                 "python -m pipelines.competitors        (rival sitemap diff)"],
    },
    "Content": {
        "does": "Clusters those keywords into one page per service+area and briefs them. "
                "One brief goes ready-to-draft; the rest wait on SEO's volume call.",
        "runs": ["python -m pipelines.content_briefs"],
    },
    "Leads": {
        "does": "Finds real UAE businesses by segment and area, works out which service "
                "each one needs, reads the email they published on their own site, and "
                "stores what is genuinely new. Contacts nobody without clearance.",
        "runs": ["python -m pipelines.leads_sources",
                 "python -m pipelines.leads_hunt --dry-run",
                 "python -m pipelines.mail            (preview an email)"],
    },
    "IT": {
        "does": "Read-only health check of the site, enquiry path first. Files tickets "
                "and queues fixes for the owner. Only the Releaser may reach production.",
        "runs": ["python -m pipelines.it_health"],
    },
    "Security": {
        "does": "Screens everything arriving from outside — prompt injection in scraped "
                "text, scam signals, unsafe sources — before any sector acts on it.",
        "runs": ["python -m pipelines.security_screen"],
    },
    "Report": {
        "does": "Scores each sector, marks the MD's past decisions worked, failed or partial "
                "against real cabinet evidence, and writes the daily digest to Telegram.",
        "runs": ["python -m pipelines.report_daily"],
    },
}

# What a shelf lets the agency do, in the owner's terms rather than the vendor's,
# and what to do about it when it is empty.
SHELF_MEANING = {
    "maps": ("collect real UAE businesses by area — the Hunter's main source",
             "add a TomTom key on Setup → Integrations (free, no Google account), or a "
             "Google Places key on the 'maps' shelf"),
    "seo_data": ("monthly search volumes and automated rank tracking — the two "
                 "things the free sources genuinely cannot give you",
                 "entirely optional. SEO works without it: seo_intel.py ranks "
                 "keywords on Search Console impressions, autocomplete breadth, "
                 "rival pages and your own enquiries. Add DataForSEO only if you "
                 "want volume numbers and daily rank checks on top"),
    "trends": ("see which services are rising or falling in search",
               "add a Google Trends source on the 'trends' shelf"),
    "email": ("send outreach and replies", "add SMTP or an email API on the 'email' shelf"),
    "stock_images": ("every picture that is not one of your own job photos — "
                     "banners, backgrounds, stock shots (never a project shot)",
                     "add your Pexels key here, and a second stock provider "
                     "under it as the fallback"),
    "crawler": ("read competitor and prospect pages at scale", "add a crawler key"),
    "llm": ("the text brain every sector reasons and writes with",
            "add a model on the 'llm' shelf — without it agents can plan but not write"),
    "vision": ("read and judge images", "optional"),
    "embedding": ("similarity and de-duplication", "optional"),
    "speech": ("transcription", "optional"),
}



def _trade() -> str:
    """What the business does and where, for an agent's briefing."""
    try:
        from . import profile
        if profile.is_starter():
            return "glass & aluminium fabrication and installation, UAE"
        return f"{profile.business().get('trade') or profile.trade_line()}, {profile.where()}"
    except Exception:
        return "glass & aluminium fabrication and installation, UAE"

def _one(c, q, args=(), default=0):
    try:
        r = c.execute(q, args).fetchone()
        return r[0] if r and r[0] is not None else default
    except Exception:
        return default


def _shelves():
    """Which capabilities are switched on, and which are not."""
    on, off = [], []
    try:
        from providers import store
        cat = store.shelf_catalogue()
        names = sorted(set(store.known_shelves()) | set(cat))
    except Exception:
        return [], [], "the provider store could not be read"
    for n in names:
        items = [i.get("name", "") for i in cat.get(n, [])]
        what, howto = SHELF_MEANING.get(n, (f"the '{n}' capability", "add a key on /setup"))
        if items:
            on.append({"shelf": n, "lets_us": what, "using": items[0],
                       "fallbacks": items[1:]})
        else:
            off.append({"shelf": n, "would_let_us": what, "to_switch_on": howto})
    return on, off, None


def build():
    c = None
    try:
        c = sqlite3.connect(DB)
    except Exception:
        pass

    holding = {}
    if c is not None:
        holding = {
            "keywords tracked": _one(c, "SELECT count(*) FROM keywords"),
            "pages briefed": _one(c, "SELECT count(*) FROM content_queue"),
            "leads in the cabinet": _one(c, "SELECT count(*) FROM leads"),
            "site tickets open": _one(c, "SELECT count(*) FROM site_issues WHERE status='open'"),
            "tasks on the board": _one(c, "SELECT count(*) FROM tasks WHERE status IN ('open','in_progress')"),
            "decisions logged": _one(c, "SELECT count(*) FROM decisions"),
            "decisions still unscored": _one(c, "SELECT count(*) FROM decisions WHERE outcome IS NULL"),
            "people contacted so far": _one(c, "SELECT count(*) FROM outreach_log WHERE direction='sent'"),
        }
        c.close()

    on, off, shelf_err = _shelves()

    try:
        from pipelines import services as _svc
        sells = list(_svc.ALL_SERVICE_LABELS)
    except Exception:
        sells = []
    try:
        from pipelines import leads_sources as _ls
        sells_to = list(_ls.SEGMENTS)
        areas = list(_ls.DISTRICTS)
    except Exception:
        sells_to, areas = [], []

    # What the owner has actually asked for today. This goes near the top of the
    # briefing on purpose: an agent that reads the capability list but not the
    # orders will do what it CAN rather than what it was ASKED, which is the
    # difference between a useful agency and a busy one.
    try:
        from . import targets as _t
        orders = _t.status()
    except Exception:
        orders = {"ok": False, "jobs": [], "orders": ""}

    return {
        "ok": True,
        "generated_at": int(time.time()),
        "todays_orders": orders,
        "business": {
            "trade": _trade(),
            "services": sells,
            "sells_to": sells_to,
            "areas": areas,
        },
        "sectors": SECTORS,
        "can_do_now": on,
        "cannot_do_yet": off,
        "shelf_error": shelf_err,
        "currently_holding": holding,
        "rules_that_bind_every_agent": [
            "Claim a task before working it. Never grab one someone else holds.",
            "Ask the sector that owns a judgement; never overrule it on its own turf.",
            "Append to a request thread. Never overwrite one.",
            "Only the IT Releaser may reach the live site.",
            "No price or concession goes to a client without the owner.",
            "Nothing is published outside without passing the Auditor.",
            "Never collect or mail the same lead twice.",
            "Every action is logged. Nothing happens in the dark.",
        ],
    }


def as_text(b=None) -> str:
    """The same briefing, as something a person or an agent can just read."""
    b = b or build()
    L = []
    biz = b["business"]
    L.append("WHAT THIS AGENCY IS")
    L.append(f"  {biz['trade']}.")
    if biz["services"]:
        L.append(f"  {len(biz['services'])} services: " + ", ".join(biz["services"]) + ".")
    if biz["sells_to"]:
        L.append(f"  Sells to {len(biz['sells_to'])} kinds of business across "
                 f"{len(biz['areas'])} areas.")

    o = b.get("todays_orders") or {}
    if o.get("ok"):
        L.append("")
        L.append("WHAT THE OWNER HAS ASKED FOR TODAY")
        if not o.get("any_target_set"):
            L.append("  Nothing set. Do not invent a workload — ask, or work the board.")
        for j in o.get("jobs", []):
            if not j["per_day"]:
                continue
            state = "done" if j["hit"] else f"{j['remaining']} still to do"
            L.append(f"  {j['done_today']}/{j['per_day']}  {j['label'].lower()} — {state}")
            if j["warning"]:
                L.append(f"        WARNING: {j['warning']}")
        if o.get("orders"):
            L.append("  In his own words: " + o["orders"].replace("\n", " / "))

    L.append("")
    L.append("THE SECTORS, AND WHAT EACH ONE RUNS")
    for name, s in b["sectors"].items():
        L.append(f"  {name} — {s['does']}")
        for cmd in s["runs"]:
            L.append(f"      $ {cmd}")

    L.append("")
    L.append("WHAT IT CAN DO RIGHT NOW")
    if b["can_do_now"]:
        for s in b["can_do_now"]:
            fb = f"  (falls back to {', '.join(s['fallbacks'])})" if s["fallbacks"] else ""
            L.append(f"  [on ] {s['lets_us']} — via {s['using']}{fb}")
    else:
        L.append("  [   ] nothing yet — no shelf has a key on it")

    L.append("")
    L.append("WHAT IT CANNOT DO YET")
    for s in b["cannot_do_yet"]:
        L.append(f"  [off] {s['would_let_us']}")
        L.append(f"        -> {s['to_switch_on']}")

    L.append("")
    L.append("WHAT IT IS HOLDING")
    for k, v in b["currently_holding"].items():
        L.append(f"  {str(v).rjust(5)}  {k}")

    L.append("")
    L.append("RULES THAT BIND EVERY AGENT")
    for r in b["rules_that_bind_every_agent"]:
        L.append(f"  - {r}")
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="What this agency can do, right now.")
    ap.add_argument("--json", action="store_true", help="machine-readable, for an agent's context")
    a = ap.parse_args()
    print(json.dumps(build(), indent=2) if a.json else as_text())
