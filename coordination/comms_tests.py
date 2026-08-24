"""
comms_tests.py — run: python -m coordination.comms_tests

One promise, tested from several directions: the office floor shows what the
sectors actually said, and nothing else.

The beams used to be eight hardcoded strings on a timer. They fired at the same
rate on a cabinet with nothing in it as on a busy afternoon, so the floor told
the owner his agency was working on a morning when it had not started. The first
test below is therefore the important one — an empty cabinet must produce an
empty floor — and the rest check that what IS drawn matches the rows it came
from, including the awkward cases: a sector talking to itself, a sender who has
no room on the map, and a question that has been sitting unanswered for days.
"""
import sqlite3
import tempfile
import time
from pathlib import Path

from . import board
from . import comms as C

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
        d = Path(tempfile.mkdtemp())
        db = d / "agency.db"
        con = sqlite3.connect(db)
        con.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
        con.commit()
        con.close()
        board.DB = str(db)
        return self

    def __exit__(self, *a):
        board.DB = self.real


def test_an_empty_cabinet_draws_nothing():
    print("\nNothing said means nothing drawn:")
    with _Cabinet():
        d = C.feed()
        check("no beams", d["beams"], [])
        check("no threads", d["threads"], [])
        check("and the floor says so rather than inventing traffic",
              d["head"], "the sectors have not spoken yet")


def test_a_real_request_becomes_a_real_beam():
    print("\nOne sector asking another is one beam, labelled with what it asked:")
    with _Cabinet():
        board.post_request("Content", "SEO", "Is 'glass pergola Dubai' worth writing for?",
                           "Three of our pages could target it.")
        d = C.feed()
        check("one beam", len(d["beams"]), 1)
        b = d["beams"][0]
        check("from the room that asked", b["from_room"], "Content")
        check("to the room that was asked", b["to_room"], "SEO")
        check("labelled with the real subject", b["label"].startswith("Is 'glass pergola"), True)
        check("and the question itself is readable",
              b["messages"][0]["body"], "Three of our pages could target it.")


def test_an_answer_changes_the_state():
    print("\nAn answer is visible as an answer:")
    with _Cabinet():
        rid = board.post_request("Leads", "Security", "Clear this lead?", "acme-glass.ae")
        check("waiting before the reply", C.feed()["threads"][0]["state"], "open")
        board.reply_request(rid, "security-screener", "Cleared — real company, real site.")
        t = C.feed()["threads"][0]
        check("answered after it", t["state"], "answered")
        check("and both sides of the exchange are there", t["n_msgs"], 2)
        check("with the answer readable",
              t["messages"][-1]["body"], "Cleared — real company, real site.")
        check("an answered exchange is not drawn as a problem", t["tone"], "glass")


def test_a_question_left_hanging_goes_amber():
    print("\nA question nobody answered is drawn as a problem, not as traffic:")
    with _Cabinet():
        rid = board.post_request("SEO", "IT", "Change the pergola page", "Add the FAQ block.")
        c = sqlite3.connect(board.DB)
        c.execute("UPDATE requests SET created_at=? WHERE id=?",
                  (int(time.time()) - C.WAITING_LINE - 600, rid))
        c.execute("UPDATE request_msgs SET at=? WHERE request_id=?",
                  (int(time.time()) - C.WAITING_LINE - 600, rid))
        c.commit(); c.close()
        t = C.feed()["threads"][0]
        check("it is amber", t["tone"], "amber")
        check("it is counted as stuck", C.feed()["stuck"], 1)
        check("and it is still waiting", t["waiting"], True)


def test_the_overseer_is_drawn_from_the_directors_office():
    print("\nA sender with no room of its own is drawn from the room it works in:")
    with _Cabinet():
        board.post_request("Overseer", "SEO", "Your desk is down", "seo-strategist stalled.")
        t = C.feed()["threads"][0]
        check("drawn from Director", t["from_room"], "Director")
        check("but credited to the Overseer, who actually sent it", t["from"], "Overseer")
        check("and it is drawable", t["drawable"], True)


def test_a_room_talking_to_itself_is_listed_but_not_drawn():
    print("\nThe Director's office writing to itself has no beam to draw:")
    with _Cabinet():
        board.post_request("Overseer", "Director", "Escalating", "SEO has three faults.")
        d = C.feed()
        check("it is in the list, because it happened", len(d["threads"]), 1)
        check("but there is no beam between a room and itself", d["beams"], [])


def test_labels_are_shortened_not_invented():
    print("\nA long subject is shortened, never replaced:")
    with _Cabinet():
        subj = ("Clear these page targets for volume and difficulty before Content "
                "commits to writing them")
        board.post_request("Content", "SEO", subj, "The list is in keyword_intel.")
        t = C.feed()["threads"][0]
        check("the label fits a beam", len(t["label"]) <= 22, True)
        check("and is the real subject's opening words",
              subj.startswith(t["label"].rstrip("…")), True)
        check("while the full subject is kept for reading", t["subject"], subj)


def test_reading_one_thread():
    print("\nOne exchange can be opened in full:")
    with _Cabinet():
        rid = board.post_request("Content", "Security", "Scraped this — check it",
                                 "Found on a rival blog.")
        board.reply_request(rid, "security-screener", "Prompt injection in paragraph 3. Binned.")
        got = C.thread(rid)
        check("it reads", got["ok"], True)
        check("with both messages in order",
              [m["author"] for m in got["thread"]["messages"]],
              ["Content", "security-screener"])
        check("an id that does not exist says so", C.thread("nope")["ok"], False)


def main():
    for t in (test_an_empty_cabinet_draws_nothing,
              test_a_real_request_becomes_a_real_beam,
              test_an_answer_changes_the_state,
              test_a_question_left_hanging_goes_amber,
              test_the_overseer_is_drawn_from_the_directors_office,
              test_a_room_talking_to_itself_is_listed_but_not_drawn,
              test_labels_are_shortened_not_invented,
              test_reading_one_thread):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
    else:
        print("all passed")
    return not _fail


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
