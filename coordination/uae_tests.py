"""
uae_tests.py — run: python -m coordination.uae_tests

Two things the owner said in one message, and they are the same bug twice.

"there you mentioned only 3 cities of UAE while we are covering the whole UAE
 and you can collect the leads from any where in the UAE"

Fourteen files each held their own ["Dubai", "Abu Dhabi", "Sharjah"]. Four
emirates he works in did not exist as far as the agency was concerned.

"the main thing from leads is the one lead that had already scrapped that will
 not get scrapped again."

The dedup compared the raw columns, which catches a directory that spells
everything exactly as the last one did and nothing else. So the tests here are
written from the real shapes: the same firm as three directories actually print
it, and a UAE phone as four formats actually write it.

Throwaway cabinet, no network.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

from . import board, uae

_fail = []

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def _fresh_db():
    d = Path(tempfile.mkdtemp())
    db = d / "agency.db"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    board.DB = db
    board.ensure_schema()
    return db


# ---------------------------------------------------------------------------
# THE WHOLE COUNTRY
# ---------------------------------------------------------------------------

def test_all_seven_emirates_not_three():
    print("\nThe country he actually works in:")
    check("seven emirates", len(uae.EMIRATES), 7)
    for e in ("Ajman", "Umm Al Quwain", "Ras Al Khaimah", "Fujairah"):
        check(f"{e} exists", e in uae.EMIRATES, True)
    check("and unset means all of them", uae.served(), list(uae.EMIRATES))
    check("which reads as the country", uae.served_phrase(), "the UAE")


def test_a_district_carries_its_emirate():
    """
    "aluminium work ajman" used to score as general interest because Ajman was
    not in the area list. "glass partition mussafah" still would even with the
    emirates fixed, because Mussafah is a district. Both are local buying
    searches in places he works.
    """
    print("\nA place is a place, however it is named:")
    for text, want in (("aluminium work ajman", "Ajman"),
                       ("glass partition mussafah", "Abu Dhabi"),
                       ("pergola in al ain", "Abu Dhabi"),
                       ("shower glass khor fakkan", "Sharjah"),
                       ("balustrade jumeirah lake towers", "Dubai"),
                       ("glass repair fujairah", "Fujairah")):
        check(f"{text!r} -> {want}", uae.emirate_in(text), want)
    check("and somewhere abroad is not the UAE",
          uae.emirate_in("aluminium work islamabad"), "")
    check("nor is it claimed as one", uae.is_uae("shower glass karachi"), False)
    check("but 'across the uae' is", uae.is_uae("glass work across the uae"), True)


def test_searching_starts_where_firms_are_found():
    print("\nWhere to actually go looking:")
    areas = uae.search_areas()
    check("districts come before emirates",
          areas.index("Al Quoz") < areas.index("Dubai"), True)
    check("every emirate is in there somewhere",
          all(e in areas for e in uae.EMIRATES), True)
    check("nothing is listed twice", len(areas), len(set(areas)))
    check("and it can be cut short", len(uae.search_areas(5)), 5)


def test_he_can_still_narrow_it():
    print("\nBUSINESS_AREAS is his to set:")
    import os
    was = os.environ.get("BUSINESS_AREAS")
    os.environ["BUSINESS_AREAS"] = "Dubai, Sharjah"
    try:
        check("only what he named", uae.served(), ["Dubai", "Sharjah"])
        check("said as a person would", uae.served_phrase(), "Dubai and Sharjah")
    finally:
        if was is None:
            os.environ.pop("BUSINESS_AREAS", None)
        else:
            os.environ["BUSINESS_AREAS"] = was


# ---------------------------------------------------------------------------
# COLLECTED ONCE, NEVER TWICE
# ---------------------------------------------------------------------------

def test_one_uae_number_however_it_is_written():
    print("\nOne phone number, four formats:")
    for written in ("+971 4 123 4567", "04-123 4567", "00971 4 1234567",
                    "(971) 4 123-4567"):
        check(f"{written!r}", board.phone_key(written), "41234567")
    print("\n...and a mobile is its own number, not the landline:")
    for written in ("+971 50 123 4567", "050 123 4567", "0501234567"):
        check(f"{written!r}", board.phone_key(written), "501234567")
    check("nothing in, nothing out", board.phone_key(""), "")


def test_one_firm_however_it_is_spelt():
    print("\nOne company, as three directories print it:")
    for written in ("Al Quoz Fitout L.L.C.", "AL QUOZ FIT OUT General Trading",
                    "al quoz fitout", "Al Quoz Fit-Out LLC"):
        check(f"{written!r}", board.company_key(written), "alquozfitout")
    check("a different firm stays different",
          board.company_key("Marina Glass Works LLC"), "marinaglassworks")


def test_one_website_however_it_is_linked():
    print("\nOne website, as four listings link it:")
    for written in ("https://www.alquozfitout.ae/contact",
                    "http://alquozfitout.ae",
                    "alquozfitout.ae/",
                    "WWW.AlQuozFitout.AE"):
        check(f"{written!r}", board.domain_key(written), "alquozfitout.ae")
    check("not a domain, not a key", board.domain_key("call us"), "")


def test_the_same_firm_is_never_collected_twice():
    """
    The whole point. One firm goes in; then the same firm comes back from a
    second directory six different ways, and none of them may become a row.
    A second row is a second email to somebody who already had one.
    """
    print("\nThe second directory has the same firm on it:")
    _fresh_db()
    first = board.add_lead_if_new({
        "company": "Al Quoz Fitout L.L.C.", "phone": "+971 4 123 4567",
        "website": "https://www.alquozfitout.ae/contact",
        "email": "info@alquozfitout.ae", "area": "Al Quoz", "source": "google maps"})
    check("the first one is stored", bool(first), True)

    again = [
        ("the phone, written locally", {"company": "Al Quoz Fit Out",
                                        "phone": "04-123 4567"}),
        ("the phone, with 00971", {"company": "AL QUOZ FITOUT LLC",
                                   "phone": "00971 4 1234567"}),
        ("no phone at all, same website", {"company": "Quoz Interiors",
                                           "website": "alquozfitout.ae"}),
        ("the name with the LLC dropped", {"company": "al quoz fitout"}),
        ("the name spaced differently", {"company": "AL QUOZ FIT OUT General Trading"}),
        ("the email in capitals", {"company": "Something Else",
                                   "email": "INFO@AlQuozFitout.ae"}),
    ]
    for label, v in again:
        check(f"skipped — {label}",
              board.add_lead_if_new({"area": "Dubai", "source": "directory", **v}),
              None)

    check("a genuinely different firm still gets in",
          bool(board.add_lead_if_new({
              "company": "Marina Glass Works", "phone": "+971 4 999 8888",
              "website": "marinaglass.ae", "email": "hi@marinaglass.ae",
              "area": "Dubai Marina", "source": "directory"})), True)

    with sqlite3.connect(board.DB) as c:
        n = c.execute("SELECT count(*) FROM leads").fetchone()[0]
    check("two firms, two rows", n, 2)


def test_an_older_cabinet_is_brought_up_to_date():
    """
    A cabinet written before the key columns existed holds rows with no keys.
    Those rows still have to block a duplicate, or the day the columns arrive
    is the day every existing lead gets collected a second time.
    """
    print("\nRows written before the keys existed:")
    db = _fresh_db()
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO leads(company,email,phone,website,found_at)"
                  " VALUES('Old Firm LLC','old@oldfirm.ae','+971 4 555 1234',"
                  "'https://www.oldfirm.ae',1)")
    board._MIGRATED.discard(str(db))
    board.ensure_schema()

    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        r = c.execute("SELECT * FROM leads").fetchone()
    check("the old row was backfilled", r["company_key"], "oldfirm")
    check("with its phone", r["phone_key"], "45551234")
    check("and its domain", r["domain_key"], "oldfirm.ae")

    check("so the same firm is still refused",
          board.add_lead_if_new({"company": "OLD FIRM", "phone": "04 555 1234",
                                 "area": "Dubai", "source": "directory"}), None)


def main():
    real = board.DB
    _fresh_db()
    try:
        test_all_seven_emirates_not_three()
        test_a_district_carries_its_emirate()
        test_searching_starts_where_firms_are_found()
        test_he_can_still_narrow_it()
        test_one_uae_number_however_it_is_written()
        test_one_firm_however_it_is_spelt()
        test_one_website_however_it_is_linked()
        test_the_same_firm_is_never_collected_twice()
        test_an_older_cabinet_is_brought_up_to_date()
    finally:
        board.DB = real

    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all uae and dedup tests passed")


if __name__ == "__main__":
    main()
