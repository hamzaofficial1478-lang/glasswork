"""
seed.py — one-time baseline data for a UAE glass & aluminium business.

Fills the `keywords` and `competitors` tables with the obvious service+area terms
and a handful of real UAE competitors, so the Managing Director has real facts to
plan from on day one. Safe to run more than once: it never inserts a duplicate.

    python3 coordination/seed.py
"""
import sqlite3

from . import uae

DB = __import__("coordination.workspace", fromlist=["db_path"]).db_path()   # the open website's

# ---- the services this business actually sells -----------------------------
SERVICES = [
    "glass work", "aluminium work", "aluminium windows", "aluminium doors",
    "glass doors", "glass partition", "frameless shower glass", "glass railing",
    "aluminium pergola", "curtain wall glazing", "shopfront glass",
    "glass replacement", "wooden doors", "skylight glass", "office glass partition",
]

# service + main emirate  -> local-intent terms
CITIES = uae.served()

# high buying-intent services in the Dubai districts worth chasing
HOT_SERVICES = ["aluminium pergola", "frameless shower glass",
                "glass partition", "aluminium windows"]
DISTRICTS = ["Business Bay", "Al Quoz", "Al Barsha",
             "Jumeirah Village Circle", "Dubai Marina", "Downtown Dubai"]

# a few generic high-intent terms with no area
GENERIC_BUY = [
    "glass and aluminium company", "aluminium fabrication UAE",
    "glass and aluminium contractor",
]

# ---- real UAE competitors (from public directories) ------------------------
COMPETITORS = [
    ("Glass Room Metal", "glassroom.ae"),
    ("John Aluminium & Glass Factory (JAGF)", "johnaluminium-me.com"),
    ("Glassman", "glassman.ae"),
    ("ALUTAL Aluminium & Glass", "alutaluae.com"),
    ("Aman Aluminum", "amanaluminum.com"),
    ("Riaz Glass", "riazglassdubai.com"),
]


# THE LISTS ABOVE ARE THE OWNER'S. Any other website is seeded from its own
# profile (coordination/profile.py): its services, its places, no rivals until
# the watch finds some.
from . import profile as _profile  # noqa: E402

_WIDE = "UAE"
if not _profile.is_starter():
    from . import countries as _countries
    _labels = [sv.get("label") or k.replace("_", " ")
               for k, sv in (_profile.services() or {}).items()]
    SERVICES = _labels
    HOT_SERVICES = _labels[:4]
    DISTRICTS = list(uae.DISTRICTS[:6]) or list(uae.served()[:6])
    _trade = _profile.trade_line()
    _WIDE = _countries.name(_profile.country()) or _profile.where()
    GENERIC_BUY = [f"{_trade} company", f"{_trade} {_WIDE}"] if _trade else []
    COMPETITORS = []


def _keywords():
    rows = []
    for svc in SERVICES:
        for city in CITIES:
            rows.append((f"{svc} {city}", city, "local"))
    for svc in HOT_SERVICES:
        for d in DISTRICTS:
            rows.append((f"{svc} {d}", d, "buy"))
    for term in GENERIC_BUY:
        rows.append((term, _WIDE, "buy"))
    return rows


def seed():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    kw_added = comp_added = 0

    for phrase, area, intent in _keywords():
        exists = con.execute(
            "SELECT 1 FROM keywords WHERE phrase=? AND area=?", (phrase, area)
        ).fetchone()
        if not exists:
            con.execute(
                "INSERT INTO keywords(phrase, area, intent) VALUES(?,?,?)",
                (phrase, area, intent),
            )
            kw_added += 1

    for name, domain in COMPETITORS:
        exists = con.execute(
            "SELECT 1 FROM competitors WHERE domain=?", (domain,)
        ).fetchone()
        if not exists:
            con.execute(
                "INSERT INTO competitors(name, domain, gbp_reviews) VALUES(?,?,NULL)",
                (name, domain),
            )
            comp_added += 1

    con.commit()
    kw_total = con.execute("SELECT COUNT(*) FROM keywords").fetchone()[0]
    comp_total = con.execute("SELECT COUNT(*) FROM competitors").fetchone()[0]
    con.close()
    print(f"keywords: +{kw_added} added ({kw_total} total)")
    print(f"competitors: +{comp_added} added ({comp_total} total)")


if __name__ == "__main__":
    seed()
