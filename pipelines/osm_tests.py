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
    r = O.find("submarine_dealership", "Dubai")
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
        O.find = lambda seg, area, limit=0, chains=False: {
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



# ---------------------------------------------------------------------------
# CHAINS ARE NOT LOCALS
# ---------------------------------------------------------------------------

CHAIN = {"type": "node", "id": 10, "tags": {
    "name": "McDonald's", "amenity": "fast_food", "brand": "McDonald's",
    "brand:wikidata": "Q38076", "phone": "+971600588885",
    "website": "https://www.mcdelivery.ae/"}}
LOCAL = {"type": "node", "id": 11, "tags": {
    "name": "Al Hunoof Public Kitchen", "amenity": "fast_food",
    "addr:street": "Al Quoz 3"}}


def test_the_franchises_are_dropped_and_the_locals_kept():
    """
    The first real Dubai run came back Krispy Kreme, McDonald's, Burger King,
    Subway, Costa Coffee, KFC, Starbucks, Five Guys, Papa John's — every one a
    franchise buying centrally, usually abroad. The owner had already said,
    about Google, that he wanted the locals and not the majors. Swapping the
    data source and keeping the chains would have handed him his own complaint
    back with a different logo on it.
    """
    print("\nA franchise is not a local business:")
    hit, why = O.chain_verdict(CHAIN["tags"])
    check("a brand:wikidata tag settles it", hit, True)
    check("and it names the brand rather than a rule number",
          "McDonald's" in why, True)
    check("a one-room kitchen has no such tag",
          O.chain_verdict(LOCAL["tags"])[0], False)

    print("\n...and four branches of one name is a chain whatever the tags say:")
    costas = [O.to_candidate({"type": "node", "id": 20 + i,
                              "tags": {"name": "Costa Coffee", "amenity": "cafe"}},
                             "food_stall", "Dubai") for i in range(4)]
    keep, dropped = O.drop_chains(costas)
    check("all four dropped", len(dropped), 4)
    check("on the evidence in the data, not a list of names",
          "branches in one search" in dropped[0]["why"], True)

    print("\n...but two branches is a local success story, not a franchise:")
    two = [O.to_candidate({"type": "node", "id": 30 + i,
                           "tags": {"name": "Al Noor Cafeteria", "amenity": "cafe",
                                    "addr:street": "23rd St"}},
                          "food_stall", "Dubai") for i in range(2)]
    keep, dropped = O.drop_chains(two)
    check("both kept", len(keep), 2)
    check("nothing dropped", dropped, [])

    print("\n...and the owner can ask for the chains if he ever wants them:")
    both = [O.to_candidate(CHAIN, "food_stall", "Dubai"),
            O.to_candidate(LOCAL, "food_stall", "Dubai")]
    for c in both:
        c.pop("_chain_why", None)
    check("nothing is dropped when they are not filtered",
          len(O.drop_chains(both)[1]), 0)


def test_the_regional_groups_are_caught_by_the_mappers_own_words():
    """
    A real Dubai clinic run came back Medcare, Magrabi and Prime Medical —
    multi-branch groups with a head office and a procurement department, which
    is precisely what the owner said he did not want. None carries
    brand:wikidata, and no two branches are spelled the same, so both existing
    rules waved them through. What gives them away is operator=Medcare on each
    pin: the mapper writing down who runs them.
    """
    print("\nTwo pins the mapper says one company runs is a group:")
    med = [O.to_candidate({"type": "node", "id": 60 + i, "tags": {
        "name": n, "amenity": "clinic", "operator": "Medcare",
        "phone": "+9714300000" + str(i)}}, "clinic_small", "Dubai")
        for i, n in enumerate(["Medcare Hospital Al Safa",
                               "Medcare Medical Centre Jumeirah"])]
    keep, dropped = O.drop_chains(med)
    check("both dropped", len(dropped), 2)
    check("naming the parent, not a rule number",
          "Medcare" in dropped[0]["why"], True)
    check("and saying what it counted", "2 branches" in dropped[0]["why"], True)

    print("\n...one branch of a group is still just one shop, and is kept:")
    one = [O.to_candidate({"type": "node", "id": 70, "tags": {
        "name": "Magrabi Eye Centre", "amenity": "clinic",
        "operator": "Magrabi", "phone": "+97143001111"}}, "clinic_small", "Dubai")]
    keep, dropped = O.drop_chains(one)
    check("kept", len(keep), 1)
    check("nothing dropped", dropped, [])

    print("\n...and an independent tagged with its own name twice is not a group:")
    same = [O.to_candidate({"type": "node", "id": 80 + i, "tags": {
        "name": n, "amenity": "cafe", "operator": n,
        "addr:street": "23rd St"}}, "food_stall", "Dubai")
        for i, n in enumerate(["Al Noor Cafeteria", "Hunoof Kitchen"])]
    check("operator that only repeats the name is ignored",
          O.group_of({"name": "Al Noor Cafeteria", "operator": "Al Noor Cafeteria"}),
          "")
    keep, dropped = O.drop_chains(same)
    check("both kept", len(keep), 2)


def test_the_headline_says_what_is_actually_worth_having():
    """
    57 found meant 7 contactable and the rest chains or dots on a map. A count
    that hides that is a count that sets him up to think the Hunter is broken.
    """
    print("\nThe count separates 'on the map' from 'worth having':")
    real = O.ask
    O.ask = lambda q, timeout=0: {
        "ok": True, "mirror": "test", "tried": [],
        "elements": [CHAIN, LOCAL,
                     {"type": "node", "id": 12, "tags": {"amenity": "cafe"}},
                     {"type": "node", "id": 13,
                      "tags": {"name": "Nowhere Snacks", "amenity": "cafe"}}]}
    try:
        r = O.find("food_stall", "Dubai")
    finally:
        O.ask = real
    check("three had names", r["on_the_map"], 3)
    check("the unnamed pin was skipped", r["unnamed_skipped"], 1)
    check("the franchise was dropped", len(r["chains_dropped"]), 1)
    check("the one with no way in was counted, not filed",
          r["no_contact_route"], 1)
    check("leaving the one worth having", r["found"], 1)
    check("and it is the local", r["candidates"][0]["company"],
          "Al Hunoof Public Kitchen")
    check("the headline says all of it", "worth having" in r["head"], True)


def test_a_street_address_is_a_way_in_for_this_trade():
    """
    Most small UAE businesses on OSM carry a name and an address and no phone.
    The intake gate demanded an email or a phone, which would have thrown away
    the majority of exactly the local leads he asked for — and for a glass
    workshop, an address IS the contact route: you walk in.
    """
    print("\nA name and a street is a lead you can walk into:")
    ok, why = ls.screen_lead({"company": "Al Hunoof Public Kitchen",
                              "area": "Al Quoz", "address": "Al Quoz 3",
                              "segment": "food_stall"})
    check("accepted", ok, True)
    print("  ...but nothing at all is still nothing:")
    ok, why = ls.screen_lead({"company": "Ghost Cafeteria", "area": "Dubai",
                              "segment": "food_stall"})
    check("refused", ok, False)
    check("and says what is missing", "not even a street address" in why, True)


# ---------------------------------------------------------------------------
# THE SEGMENT HAS TO BE THE TRUTH, NOT NEARLY
# ---------------------------------------------------------------------------

def test_a_car_workshop_is_not_a_laundry():
    """
    A real Sharjah laundry search came back twenty-six "worth having", and
    fourteen of them were car workshops — Wheelocity Garage, Alrehan Garage,
    Top Auto Repairing. Good leads, wrong segment, and the segment is what the
    mail is written from: the garage would have been offered a mirror wall and
    the laundry numbers would have been a lie.
    """
    print("\nA car workshop is not a laundry:")
    check("car_repair is off the laundry list",
          ("shop", "car_repair") in O.OSM_TAGS["laundry"], False)
    check("so is the car wash it used to drag in",
          ("amenity", "car_wash") in O.OSM_TAGS["laundry"], False)
    check("laundry asks for laundries",
          sorted(v for _, v in O.OSM_TAGS["laundry"]),
          ["dry_cleaning", "laundry", "tailor"])

    print("\n...it is a segment of its own, because it is a real customer:")
    check("garage is mapped", "garage" in O.OSM_TAGS, True)
    check("and the agency knows the segment", "garage" in ls.SEGMENTS, True)
    check("it is a local, not a major", "garage" in ls.LOCAL_SEGMENTS, True)
    check("the collector can reach it", "garage" in O.segments_covered(), True)

    print("\n...and we have something to sell it — roller-door frontage, "
          "an office partition:")
    from . import services
    buys = [s for s, d in services.SERVICES.items() if "garage" in d["buyers"]]
    check("shopfront", "shopfront" in buys, True)
    check("glass partition for the front office", "glass_partition" in buys, True)
    check("a sign board", "sign_board" in buys, True)
    print("  ...so a garage lead gets a service attached, not a blank:")
    g = O.to_candidate({"type": "node", "id": 40,
                        "tags": {"name": "Alrehan Garage", "shop": "car_repair",
                                 "phone": "065551234"}}, "garage", "Sharjah")
    check("matched", bool(g["service"]), True)

    print("\nEvery segment the collector can find is one we sell to:")
    orphans = [s for s in O.segments_covered()
               if not any(s in d["buyers"] for d in services.SERVICES.values())]
    check("none without a service", orphans, [])


# ---------------------------------------------------------------------------
# HALF THE UAE IS MAPPED IN ARABIC
# ---------------------------------------------------------------------------

def test_the_map_speaks_arabic_and_the_cabinet_does_not():
    """
    A real Dubai clinic run filed "Joseph Qusais Polyclinic ... دبي" and
    "French Bakery ... دبي". Both are Dubai. The cabinet groups by string, so
    they were about to sit in a pile of their own and the emirate counts would
    have been quietly wrong.
    """
    print("\nThe emirates come back in English however they were mapped:")
    check("دبي", O._english_area("دبي"), "Dubai")
    check("الشارقة", O._english_area("الشارقة"), "Sharjah")
    check("أبو ظبي, with the hamza", O._english_area("أبو ظبي"), "Abu Dhabi")
    check("ابوظبي, without it, and run together",
          O._english_area("ابوظبي"), "Abu Dhabi")
    check("رأس الخيمة", O._english_area("رأس الخيمة"), "Ras Al Khaimah")
    check("العين", O._english_area("العين"), "Al Ain")

    print("\n...but a district nobody translated is left as the mapper wrote it:")
    check("no half-guessed transliteration",
          O._english_area("القصيص الصناعية الثانية"),
          "القصيص الصناعية الثانية")
    check("and English is untouched", O._english_area("Al Quoz"), "Al Quoz")
    check("empty stays empty", O._english_area(""), "")

    print("\nA point mapped only in Arabic files as Dubai, not as دبي:")
    c = O.to_candidate({"type": "node", "id": 41, "tags": {
        "name": "Joseph Qusais Polyclinic", "amenity": "clinic",
        "phone": "+97142636767", "addr:city": "دبي"}}, "clinic_small", "Dubai")
    check("the area", c["area"], "Dubai")
    check("and the address reads in English too", c["address"], "Dubai")

    print("\n...and an English tag beats an Arabic one where both are present:")
    c = O.to_candidate({"type": "node", "id": 42, "tags": {
        "name": "X", "amenity": "clinic", "addr:suburb": "القصيص",
        "addr:suburb:en": "Al Qusais", "addr:city": "دبي"}},
        "clinic_small", "Dubai")
    check("the English suburb wins", c["area"], "Al Qusais")


def test_the_box_overshoots_the_border_and_says_so():
    """
    A bounding box is a rectangle; an emirate border is not. A Dubai clinic
    search returned Solanki Dental Center with addr:city Sharjah. Those are
    still real UAE leads and throwing them away would be waste — but "20 in
    Dubai" has to stop meaning "and a few in Sharjah".
    """
    print("\nWhat the box caught over the border is counted, not hidden:")
    real = O.ask
    O.ask = lambda q, timeout=0: {
        "ok": True, "mirror": "test", "tried": [],
        "elements": [
            {"type": "node", "id": 50, "tags": {
                "name": "Al Quoz Dental", "amenity": "dentist",
                "phone": "+97143001234", "addr:city": "Dubai"}},
            {"type": "node", "id": 51, "tags": {
                "name": "Solanki Dental Center", "amenity": "dentist",
                "phone": "+97165001234", "addr:city": "Sharjah"}},
            {"type": "node", "id": 52, "tags": {
                "name": "Nahda Polyclinic", "amenity": "clinic",
                "phone": "+97165009999", "addr:city": "الشارقة"}}]}
    try:
        r = O.find("clinic_small", "Dubai")
    finally:
        O.ask = real
    check("all three are kept — they are real leads", r["found"], 3)
    check("two of them are not in Dubai", r["over_the_border"], 2)
    check("and it names where they actually are", r["spilled_into"], ["Sharjah"])
    check("the Arabic one counted as the same emirate as the English one",
          len(r["spilled_into"]), 1)
    check("the headline admits it", "actually in Sharjah" in r["head"], True)
    check("nothing internal leaks into the stored row",
          [k for k in r["candidates"][0] if k.startswith("_")], [])

    print("\n...and a clean search says nothing about borders at all:")
    O.ask = lambda q, timeout=0: {
        "ok": True, "mirror": "test", "tried": [],
        "elements": [{"type": "node", "id": 53, "tags": {
            "name": "Al Quoz Dental", "amenity": "dentist",
            "phone": "+97143001234", "addr:city": "Dubai"}}]}
    try:
        r = O.find("clinic_small", "Dubai")
    finally:
        O.ask = real
    check("no spill", r["over_the_border"], 0)
    check("and no noise in the headline", "actually in" in r["head"], False)


# ---------------------------------------------------------------------------
# A PIN ON A MAP IS NOT AN ADDRESS YOU CAN WRITE TO
# ---------------------------------------------------------------------------

class _FakeHarvest:
    """Stands in for pipelines/harvest.py. No network, no fetcher, no spend."""

    def __init__(self, by_site):
        self.by_site, self.asked = by_site, []

    def __call__(self, url, timeout=20, allow_paid=True):
        self.asked.append(url)
        return self.by_site.get(url, {"ok": False, "error": "did not load"})


def _with_harvest(fake, fn):
    real = O.harvest.harvest
    O.harvest.harvest = fake
    try:
        return fn()
    finally:
        O.harvest.harvest = real


def test_the_leads_actually_get_an_email_address():
    """
    The owner read a clinic run and asked "as we are getting the leads are we
    also getting the mails?". We were not. This file carried a comment saying
    email came from harvest, and nothing in the OSM path ever called harvest —
    so every lead it filed had an empty email column and the mail runner had
    nobody to write to. The comment was the only thing doing the work.
    """
    print("\nThe website OSM gave us is read for the email it publishes:")
    fake = _FakeHarvest({
        "https://femiclinic.com/": {
            "ok": True, "spend": 0.0,
            "emails": [{"value": "info@femiclinic.com", "where": "the contact page",
                        "how": "a mailto link"}],
            "phones": [{"value": "+97144542640"}], "whatsapp": [],
            "address": "Jumeirah 1, Dubai", "socials": []}})
    cands = [{"company": "Femiclinic", "website": "https://femiclinic.com/",
              "email": "", "phone": "", "address": ""}]
    e = _with_harvest(fake, lambda: O.enrich(cands))
    check("the site was read", e["sites_read"], 1)
    check("an email came back", e["emails_found"], 1)
    check("and it is on the lead", cands[0]["email"], "info@femiclinic.com")
    check("with where it was found, so it is arguable later",
          cands[0]["email_where"], "the contact page")
    check("the phone the site gave, since the map had none",
          cands[0]["phone"], "+97144542640")
    check("and the address", cands[0]["address"], "Jumeirah 1, Dubai")

    print("\n...and it does not spend a fetch on a lead it cannot help:")
    already = {"company": "A", "website": "https://a.ae", "email": "a@a.ae"}
    no_site = {"company": "B", "website": "", "email": ""}
    fake2 = _FakeHarvest({})
    e = _with_harvest(fake2, lambda: O.enrich([already, no_site]))
    check("nothing was read", fake2.asked, [])
    check("because one is already mailable and the other has no site",
          e["sites_read"], 0)
    check("the mailable one is untouched", already["email"], "a@a.ae")

    print("\n...a site with no email on it is reported, not silently dropped:")
    quiet = [{"company": "Al Amal Medical Center", "website": "https://alamal.ae",
              "email": "", "phone": "+97141112222"}]
    fake3 = _FakeHarvest({"https://alamal.ae": {
        "ok": True, "spend": 0.0, "emails": [], "phones": [], "whatsapp": [],
        "address": "", "socials": []}})
    e = _with_harvest(fake3, lambda: O.enrich(quiet))
    check("read", e["sites_read"], 1)
    check("no email", e["emails_found"], 0)
    check("and it says why", "no email published" in e["no_email_on_site"][0]["why"],
          True)

    print("\n...a site that throws is a failed lead, not a failed run:")
    def boom(url, timeout=20, allow_paid=True):
        raise OSError("connection reset by peer")
    bad = [{"company": "C", "website": "https://c.ae", "email": ""}]
    e = _with_harvest(boom, lambda: O.enrich(bad))
    check("the pass survived", e["emails_found"], 0)
    check("and named the casualty", e["no_email_on_site"][0]["company"], "C")

    print("\n...and one pass will not read sixty websites:")
    many = [{"company": f"C{i}", "website": f"https://c{i}.ae", "email": ""}
            for i in range(30)]
    fake4 = _FakeHarvest({})
    e = _with_harvest(fake4, lambda: O.enrich(many, limit=5))
    check("capped", len(fake4.asked), 5)
    check("and the rest are counted, not forgotten",
          e["not_reached_this_pass"], 25)


def test_the_run_says_how_each_lead_can_be_reached():
    """
    Of twenty clinics in his Dubai run, four had a website and six a phone.
    Calling all twenty "leads" would have him waiting on emails that were never
    going to exist. The count has to separate what the mail runner can do
    tonight from what he has to do by hand.
    """
    print("\nFour buckets, because they are four different mornings:")
    r = O.reach_of([
        {"email": "a@a.ae"},                       # write to it tonight
        {"email": "", "website": "https://b.ae"},  # read the site first
        {"email": "", "website": "", "phone": "+97141112222"},   # his WhatsApp
        {"email": "", "website": "", "phone": "", "address": "Al Quoz 3"},
    ])
    check("mailable", r["mailable"], 1)
    check("a site to read", r["harvestable"], 1)
    check("phone only — his job, not the agency's", r["message"], 1)
    check("and a door to knock on", r["walk_in"], 1)
    check("the line reads plainly",
          "1 mailable now, 1 with a site to read, 1 phone-only (WhatsApp by "
          "hand), 1 walk-in only", O.reach_line(r))

    print("\nAnd the pass itself enriches before it files, not after:")
    with _Cabinet() as cab:
        board.post_task("Leads", "Collect today's leads across the mapped segments",
                        "work the source map")
        real_find, real_pause = O.find, O.PAUSE
        O.PAUSE = 0
        O.find = lambda seg, area, limit=0, chains=False: {
            "ok": True, "segment": seg, "area": area, "mirror": "test",
            "found": 1, "unnamed_skipped": 0, "with_phone": 0, "with_website": 1,
            "candidates": [{"company": "Femiclinic", "email": "",
                            "website": "https://femiclinic.com/", "phone": "",
                            "address": "Jumeirah 1", "area": "Dubai",
                            "segment": seg, "source": "openstreetmap:node/9"}]}
        fake = _FakeHarvest({"https://femiclinic.com/": {
            "ok": True, "spend": 0.0, "whatsapp": [], "phones": [], "socials": [],
            "address": "", "emails": [{"value": "info@femiclinic.com",
                                       "where": "the contact page"}]}})
        try:
            r = _with_harvest(fake, lambda: O._run(O.AGENT, "clinic_small",
                                                   "Dubai", 1, dry_run=False))
        finally:
            O.find, O.PAUSE = real_find, real_pause
        check("it ran", r["ok"], True)
        check("the site was read during the pass", r["enriched"]["sites_read"], 1)
        check("the lead is mailable", r["reach"]["mailable"], 1)
        check("the email reached the cabinet, not just the return value",
              cab.sql("SELECT email FROM leads")[0]["email"], "info@femiclinic.com")
        check("and the headline says so, so he is not guessing",
              "1 mailable now" in r["head"], True)
        check("still nobody contacted",
              cab.sql("SELECT count(*) n FROM outreach_log")[0]["n"], 0)


# ---------------------------------------------------------------------------
# WHAT A MAP CANNOT DO, SAID OUT LOUD
# ---------------------------------------------------------------------------

def test_villa_owners_are_not_on_a_map_and_it_says_where_they_are_instead():
    """
    He asked to test villa owners, house owners and rental owners. A villa on
    OSM is a building outline: no owner, no name, no number, because the person
    living in it never published one. The only lists that claim otherwise were
    compiled without asking anybody, which is how a sending domain dies.

    Saying "no" and stopping would waste his week. Every villa has an agency or
    a management office that holds the keys and authorises the work, and those
    ARE businesses on the map.
    """
    print("\nA private home is not a business, and the refusal says why:")
    for seg in ("villa_compound", "landlord", "private_individual"):
        r = O.find(seg, "Dubai")
        check(f"{seg} is refused", r["ok"], False)
        check(f"{seg} — flagged as a data limit, not a bug",
              r.get("unmappable"), True)
        check(f"{seg} — and it names the way in",
              bool(r.get("reach_instead")), True)

    print("\n...and the way in is a segment that really is findable:")
    for seg in ("villa_compound", "landlord"):
        instead = O.find(seg, "Dubai")["reach_instead"]
        named = [s for s in O.segments_covered() if s in instead]
        check(f"{seg} points at something on the map", bool(named), True)


def test_the_segments_he_asked_to_test_are_all_findable():
    print("\nThe segments he named for this test:")
    for seg in ("hotel", "signage", "real_estate_agency",
                "property_management", "holiday_let"):
        check(seg, seg in O.OSM_TAGS, True)

    print("\n...with the tags the UAE actually uses:")
    check("hotel apartments, which are half the Dubai market",
          ("tourism", "apartment") in O.OSM_TAGS["holiday_let"], True)
    check("advertising agencies, for the billboard work",
          ("office", "advertising_agency") in O.OSM_TAGS["signage"], True)
    check("property managers, who hold other people's villas",
          ("office", "property_management") in O.OSM_TAGS["property_management"],
          True)
    check("every mapped segment is one the agency knows",
          [s for s in O.OSM_TAGS if s not in ls.SEGMENTS], [])


def test_sharjah_in_lower_case_is_still_sharjah():
    """
    His run printed `Solanki Dental Center ... sharjah`. Lower case, so it
    missed the emirate list, was filed as a district, and was not counted as
    the border spill it actually was.
    """
    print("\nHowever the mapper typed it:")
    check("sharjah", O._english_area("sharjah"), "Sharjah")
    check("DUBAI", O._english_area("DUBAI"), "Dubai")
    check("rak", O._english_area("rak"), "Ras Al Khaimah")
    check("and it is recognised as an emirate, not a district",
          O._emirate_of({"addr:city": "sharjah"}), "Sharjah")
    check("a genuine district is still left alone",
          O._english_area("al quoz"), "al quoz")


def main():
    for t in (test_the_query_asks_for_what_is_actually_mapped,
              test_an_osm_point_becomes_a_lead,
              test_phone_shapes_collapse_to_one_number,
              test_it_only_asks_for_segments_the_agency_has,
              test_every_mirror_refusing_is_a_reported_fact,
              test_the_collector_feeds_the_same_gate_as_every_other,
              test_leads_is_no_longer_dark_without_a_places_key,
              test_the_franchises_are_dropped_and_the_locals_kept,
              test_the_regional_groups_are_caught_by_the_mappers_own_words,
              test_the_headline_says_what_is_actually_worth_having,
              test_a_street_address_is_a_way_in_for_this_trade,
              test_a_car_workshop_is_not_a_laundry,
              test_the_map_speaks_arabic_and_the_cabinet_does_not,
              test_the_box_overshoots_the_border_and_says_so,
              test_sharjah_in_lower_case_is_still_sharjah,
              test_the_leads_actually_get_an_email_address,
              test_the_run_says_how_each_lead_can_be_reached,
              test_villa_owners_are_not_on_a_map_and_it_says_where_they_are_instead,
              test_the_segments_he_asked_to_test_are_all_findable,
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
