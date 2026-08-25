"""
osm_tests.py — run: python3 -m pipelines.osm_tests

The keyless lead collector, and the day lock that locked the owner out.

WHAT IS AND IS NOT COVERED, SAID PLAINLY

The network hop is NOT tested here, and cannot be: the container this was
written in cannot reach any Overpass mirror. Everything either side of it is —
the query it builds, the way it reads an OSM element into a lead, what it does
when every mirror refuses, and the gate the results go through. `--probe` is
the part the owner runs, because his machine is the only one that can answer
the question.

Nothing here touches the network or the owner's cabinet.
"""
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from coordination import board
from . import leads_osm as O, leads_sources as ls

_fail = []
ROOT = Path(__file__).parents[1]


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


class _Cabinet:
    def __enter__(self):
        self.real = board.DB
        db = Path(tempfile.mkdtemp()) / "agency.db"
        con = sqlite3.connect(db)
        con.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
        con.commit()
        con.close()
        board.DB = str(db)
        board.ensure_schema()
        return self

    def __exit__(self, *a):
        board.DB = self.real

    def sql(self, q, args=()):
        c = sqlite3.connect(board.DB)
        c.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in c.execute(q, args)]
        finally:
            c.close()


# Real Overpass output shapes.
CAFE = {"type": "node", "id": 1, "tags": {
    "name": "Al Noor Cafeteria", "amenity": "cafe",
    "contact:phone": "+971 4 385 1122", "website": "alnoorcafeteria.ae",
    "addr:housenumber": "12", "addr:street": "23rd St",
    "addr:suburb": "Al Quoz", "addr:city": "Dubai"}}
MART = {"type": "way", "id": 2, "center": {"lat": 25.1, "lon": 55.2}, "tags": {
    "name:en": "Marina Supermarket", "name": "سوبرماركت",
    "shop": "supermarket", "phone": "04 555 9000",
    "contact:website": "https://marinasm.ae"}}
UNNAMED = {"type": "node", "id": 3, "tags": {"amenity": "cafe"}}


def test_the_query_asks_for_what_is_actually_mapped():
    print("\nThe query it builds:")
    q = O.build_query([("amenity", "cafe"), ("shop", "bakery")], O.BBOX["Dubai"], 20)
    check("both tags are asked for", q.count("nwr["), 2)
    check("bounded to the box", O.BBOX["Dubai"] in q, True)
    check("and capped", "out center 20;" in q, True)
    # nwr, not node. A supermarket is mapped as a building, and asking only for
    # pins quietly misses most of the businesses actually worth having.
    check("ways and relations too, not just pins", "node[" in q, False)
    check("json, with a timeout", q.startswith("[out:json][timeout:"), True)


def test_an_osm_point_becomes_a_lead():
    print("\nReading one point off the map:")
    c = O.to_candidate(CAFE, "food_stall", "Dubai")
    check("the name", c["company"], "Al Noor Cafeteria")
    check("the phone, in one shape", c["phone"], "+97143851122")
    check("a bare domain is made a URL", c["website"], "https://alnoorcafeteria.ae")
    check("the district, not the emirate we searched", c["area"], "Al Quoz")
    check("the address as posted", c["address"], "12, 23rd St, Al Quoz, Dubai")
    check("and where it came from", c["source"], "openstreetmap:node/1")
    check("with a service attached, so the mail has something to be about",
          bool(c["service"]), True)

    print("\n...and an English name is preferred where both exist:")
    m = O.to_candidate(MART, "supermarket", "Dubai")
    check("name:en wins", m["company"], "Marina Supermarket")
    check("a local landline is normalised too", m["phone"], "+97145559000")
    check("a way is as good as a node", m["source"], "openstreetmap:way/2")
    check("and the emirate stands in when there is no district", m["area"], "Dubai")

    print("\nAn unnamed pin is a dot on a map, not a business:")
    check("skipped", O.to_candidate(UNNAMED, "food_stall"), {})
    check("so is an empty element", O.to_candidate({}, "food_stall"), {})
    print("  ...but an operator counts as a name:")
    op = O.to_candidate({"type": "node", "id": 4,
                         "tags": {"operator": "Quick Wash", "shop": "laundry"}},
                        "laundry")
    check("taken", op["company"], "Quick Wash")


def test_phone_shapes_collapse_to_one_number():
    print("\nOSM carries the same line four ways; the cabinet holds one:")
    seen = set()
    for raw in ("+971 4 385 1122", "04-385 1122", "00971 4 3851122", "+97143851122"):
        e = {"type": "node", "id": 9, "tags": {"name": "X", "amenity": "cafe",
                                               "phone": raw}}
        seen.add(O.to_candidate(e, "food_stall")["phone"])
    check("all four are one number", sorted(seen), ["+97143851122"])
    print("  ...and something that is not a number is left alone, not mangled:")
    e = {"type": "node", "id": 9, "tags": {"name": "X", "amenity": "cafe",
                                           "phone": "call the shop"}}
    check("kept verbatim", O.to_candidate(e, "food_stall")["phone"], "call the shop")


def test_it_only_asks_for_segments_the_agency_has():
    print("\nEvery tag map points at a segment that exists:")
    unknown = [s for s in O.OSM_TAGS if s not in ls.SEGMENTS]
    check("no orphan segments", unknown, [])
    check("it covers a useful share of them",
          len(O.segments_covered()) >= 15, True)
    check("including the street-level ones the owner asked for",
          all(s in O.segments_covered() for s in
              ("food_stall", "salon", "supermarket", "laundry", "clinic_small")),
          True)
    print("\n...and it refuses what it cannot look up, rather than guessing:")
    r = O.find("private_individual", "Dubai")
    check("an unmapped segment is refused", r["ok"], False)
    check("and it lists what IS mapped", "food_stall" in r["error"], True)
    r = O.find("food_stall", "Atlantis")
    check("an unknown area is refused", r["ok"], False)
    check("and it names the ones it knows", "Dubai" in r["error"], True)


def test_every_mirror_refusing_is_a_reported_fact():
    """
    "OSM did not answer" and "OSM has nothing there" are completely different,
    and only one of them is something the owner can act on. Confusing them
    would have him hunting for a bug in a working collector.
    """
    print("\nWhen no mirror answers, it says so and names each one:")
    real = O._post

    def dead(url, query, timeout=0):
        raise OSError("connection reset by peer")
    O._post, real_pause, O.PAUSE = dead, O.PAUSE, 0
    try:
        r = O.ask("[out:json];out;")
        check("not ok", r["ok"], False)
        check("every mirror is named", len(r["tried"]), len(O.MIRRORS))
        check("with what each one did", "OSError" in r["tried"][0], True)
        f = O.find("food_stall", "Dubai")
        check("and find() passes that up rather than saying 'none found'",
              "no Overpass mirror answered" in f["error"], True)
    finally:
        O._post, O.PAUSE = real, real_pause


def test_the_collector_feeds_the_same_gate_as_every_other():
    print("\nWhat it finds goes through the Scanner, not straight into the cabinet:")
    with _Cabinet() as cab:
        board.post_task("Leads", "Collect today's leads across the mapped segments",
                        "work the source map")
        real = O.find
        O.find = lambda seg, area, limit=0: {
            "ok": True, "segment": seg, "area": area, "mirror": "test",
            "found": 1, "unnamed_skipped": 0, "with_phone": 1, "with_website": 1,
            "candidates": [O.to_candidate(CAFE, seg, area)]}
        real_pause, O.PAUSE = O.PAUSE, 0
        try:
            r = O._run(O.AGENT, "food_stall", "Dubai", 1, dry_run=False)
        finally:
            O.find, O.PAUSE = real, real_pause
        check("it ran", r["ok"], True)
        check("one lead stored", len(r["stored"]), 1)
        row = cab.sql("SELECT company, phone, website, segment, source FROM leads")[0]
        check("with the name", row["company"], "Al Noor Cafeteria")
        check("the normalised phone", row["phone"], "+97143851122")
        check("the website, for harvest to read next", row["website"],
              "https://alnoorcafeteria.ae")
        check("and where it came from, on the record",
              row["source"].startswith("openstreetmap:"), True)
        check("nobody was contacted", cab.sql("SELECT count(*) n FROM outreach_log")[0]["n"], 0)


def test_leads_is_no_longer_dark_without_a_places_key():
    """
    The lamp said BLOCKED because search_places was the only collector there
    was, and that was honest. There is a keyless one now, so a missing Places
    key is a coverage note rather than a dead room.
    """
    print("\nA missing Places key no longer blacks out the Leads room:")
    from coordination import readiness
    check("maps is not a precondition any more",
          readiness.SHELVES["leads"]["maps"], False)
    check("...and neither is the email shelf, which SMTP replaced",
          readiness.SHELVES["leads"]["email"], False)
    check("but SMTP still is, because there is no other way to send",
          [k for k, _ in readiness.ENV_NEEDS["leads"]], ["SMTP_HOST"])


# ---------------------------------------------------------------------------
# THE DAY LOCK THAT LOCKED HIM OUT
# ---------------------------------------------------------------------------

def test_a_dead_run_does_not_hold_the_door_for_three_hours():
    """
    The owner pressed Run and was told "a day is already running" by a day that
    had died hours before. The reaper would not reap while the lock was held,
    and the lock was held for its own three-hour lease — so each waited for the
    other and he had no way in.
    """
    from . import day_run as D
    print("\nA run that died does not hold the door until its lease expires:")
    with _Cabinet() as cab:
        long_ago = int(time.time()) - (D.STEP_TIMEOUT + 3600)
        cab_con = sqlite3.connect(board.DB)
        cab_con.execute("INSERT INTO day_runs(id,started_at,status,started_by,steps_total)"
                        " VALUES('dead',?,'running','owner',2)", (long_ago,))
        cab_con.execute("INSERT INTO day_steps(run_id,seq,label,module,status,started_at)"
                        " VALUES('dead',0,'The MD plans','pipelines.md_cycle','running',?)",
                        (long_ago,))
        cab_con.commit()
        cab_con.close()
        # the lock the dead run was holding, still well inside its lease
        board.acquire_lock(D.LOCK, D.LOCK_HOLDER, wait=False, ttl=3 * 3600)

        st = D.status()
        check("the reaper sees through the lock now", st["run"]["status"], "stalled")
        check("and lets go of it on the way out",
              cab.sql("SELECT count(*) n FROM locks WHERE resource=?",
                      (D.LOCK,))[0]["n"], 0)
        check("so the button works again",
              board.acquire_lock(D.LOCK, D.LOCK_HOLDER, wait=False, ttl=60), True)
        board.release_lock(D.LOCK, D.LOCK_HOLDER)

    print("\n...but a run that is genuinely moving is left alone:")
    with _Cabinet() as cab:
        now = int(time.time())
        con = sqlite3.connect(board.DB)
        con.execute("INSERT INTO day_runs(id,started_at,status,started_by,steps_total)"
                    " VALUES('live',?,'running','owner',2)", (now - 30,))
        con.execute("INSERT INTO day_steps(run_id,seq,label,module,status,started_at)"
                    " VALUES('live',0,'SEO watches','pipelines.competitors','running',?)",
                    (now - 20,))
        con.commit()
        con.close()
        board.acquire_lock(D.LOCK, D.LOCK_HOLDER, wait=False, ttl=3 * 3600)
        st = D.status()
        check("still running", st["run"]["status"], "running")
        check("and it still holds the lock",
              cab.sql("SELECT count(*) n FROM locks WHERE resource=?",
                      (D.LOCK,))[0]["n"], 1)
        board.release_lock(D.LOCK, D.LOCK_HOLDER)


def test_unlock_is_the_escape_hatch_and_says_what_it_did():
    from . import day_run as D
    print("\nAnd there is a way out by hand, for the case the reaper cannot judge:")
    with _Cabinet() as cab:
        now = int(time.time())
        con = sqlite3.connect(board.DB)
        con.execute("INSERT INTO day_runs(id,started_at,status,started_by,steps_total)"
                    " VALUES('live',?,'running','owner',1)", (now - 10,))
        con.commit()
        con.close()
        board.acquire_lock(D.LOCK, D.LOCK_HOLDER, wait=False, ttl=3 * 3600)
        r = D.unlock()
        check("the lock is gone",
              cab.sql("SELECT count(*) n FROM locks WHERE resource=?",
                      (D.LOCK,))[0]["n"], 0)
        check("the run it was holding is closed, not left saying 'running'",
              cab.sql("SELECT status FROM day_runs WHERE id='live'")[0]["status"],
              "stalled")
        check("it says how many it closed", r["runs_closed"], 1)
        check("and tells him what to do next", "Press Run again" in r["why"], True)


def main():
    for t in (test_the_query_asks_for_what_is_actually_mapped,
              test_an_osm_point_becomes_a_lead,
              test_phone_shapes_collapse_to_one_number,
              test_it_only_asks_for_segments_the_agency_has,
              test_every_mirror_refusing_is_a_reported_fact,
              test_the_collector_feeds_the_same_gate_as_every_other,
              test_leads_is_no_longer_dark_without_a_places_key,
              test_a_dead_run_does_not_hold_the_door_for_three_hours,
              test_unlock_is_the_escape_hatch_and_says_what_it_did):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        sys.exit(1)
    print("all osm tests passed")


if __name__ == "__main__":
    main()
