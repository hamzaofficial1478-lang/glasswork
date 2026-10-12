"""
sitepace_tests.py — run: python -m coordination.sitepace_tests

"i checked the website and it says site cannot be reached with
 worlddealtechnicalservices.com unexpectedly closed the connection"

His site was up; his own address had been shut out by his host's firewall,
because the agency read the site from three desks at once. What has to hold:

  every desk's requests to HIS site go one at a time, in turn
  one dropped line is a blip and is tried again; the second time in a row the
  site shuts the door, the agency stops asking
  other websites are not held up by any of it
  he can see it, and tell the agency the door is open again

A throwaway cabinet, and a fake website on this machine that behaves like a
firewall. No real site is touched.
"""
import os
import socket
import sqlite3
import tempfile
import threading
import http.client
import urllib.error
import urllib.request
from pathlib import Path

from . import board, sitepace

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
        self.real = (board.DB, sitepace._host_cache, os.environ.get("SITEPACE_TEST"))
        d = Path(tempfile.mkdtemp())
        db = d / "agency.db"
        con = sqlite3.connect(db)
        con.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
        con.commit()
        con.close()
        board.DB = str(db)
        sitepace._host_cache = "127.0.0.1"
        os.environ["SITEPACE_TEST"] = "1"
        return self

    def __exit__(self, *a):
        board.DB, sitepace._host_cache, was = self.real
        if was is None:
            os.environ.pop("SITEPACE_TEST", None)
        else:
            os.environ["SITEPACE_TEST"] = was


def _slamming_server():
    """A 'website' that accepts and hangs up at once — what the firewall did."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(5)
    port = s.getsockname()[1]

    def serve():
        for _ in range(5):
            try:
                conn, _addr = s.accept()
                conn.close()
            except OSError:
                return
    threading.Thread(target=serve, daemon=True).start()
    return s, port


def test_one_request_at_a_time():
    print("\nEvery desk's requests to his site wait their turn:")
    with _Cabinet():
        waits = []
        sitepace.before("https://127.0.0.1/", sleep=waits.append)
        sitepace.before("https://127.0.0.1/about", sleep=waits.append)
        sitepace.before("https://127.0.0.1/contact", sleep=waits.append)
        check("the first goes at once, the next two wait in turn",
              len(waits), 2)
        check("each about a second behind the one before",
              all(0.5 <= w <= 2.5 for w in waits), True)
        other = []
        sitepace.before("https://someone-else.ae/", sleep=other.append)
        check("another website is not held up", other, [])


def test_the_first_closed_door_stops_every_desk():
    print("\nThe first time his site shuts the door, the agency stops asking:")
    with _Cabinet():
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
        srv, port = _slamming_server()
        try:
            def knock():
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)
                    return False
                except Exception:
                    return True
            check("the fake firewall hung up", knock(), True)
            check("once is a blip: noted, and nobody is stopped",
                  (bool(sitepace.resting()), sitepace.warned()), (False, True))
            check("it hung up again", knock(), True)
            rest = sitepace.resting()
            check("the agency noticed and is leaving the site alone", bool(rest), True)
            check("and says why", "closed the connection" in rest.get("why", ""), True)
            try:
                sitepace.before(f"http://127.0.0.1:{port}/about")
                refused = ""
            except sitepace.SiteResting as e:
                refused = str(e)
            check("the next desk is refused at once instead of retrying",
                  "leaving it alone until" in refused, True)
        finally:
            srv.close()

    print("\nHowever Windows words the hang-up, it counts:")
    for name, err in (
            ("10053, wrapped by urllib", urllib.error.URLError(ConnectionAbortedError(
                10053, "An established connection was aborted by the software in your "
                       "host machine"))),
            ("10054, wrapped by urllib", urllib.error.URLError(ConnectionResetError(
                10054, "An existing connection was forcibly closed by the remote host"))),
            ("10053, bare", ConnectionAbortedError(10053, "[WinError 10053] An established "
                                                          "connection was aborted")),
            ("no answer at all", http.client.RemoteDisconnected(
                "Remote end closed connection without response"))):
        with _Cabinet():
            check(f"  {name}", sitepace.told_to_stop("http://127.0.0.1/", error=err), True)

    print("\nOrdinary answers do not count as a closed door:")
    with _Cabinet():
        for status in (200, 301, 401, 404, 500):
            sitepace.told_to_stop("https://127.0.0.1/x", status=status)
        check("401, 404 and the rest leave it open", sitepace.resting(), {})
        sitepace.told_to_stop("https://127.0.0.1/x", status=429)
        check("429 — too many requests — closes it", bool(sitepace.resting()), True)


def test_he_sees_it_and_can_open_it_again():
    print("\nHe sees it in 'Waiting on you', and his yes lets the agency back in:")
    with _Cabinet():
        for _ in range(2):
            sitepace.told_to_stop("https://127.0.0.1/", error=ConnectionResetError(
                "An existing connection was forcibly closed by the remote host"))
        from . import state
        items = [a for a in state.build().get("approvals") or []
                 if a.get("kind") == "site_resting"]
        check("it is on his list", len(items), 1)
        check("with how to check it on his phone", "mobile data" in items[0]["why"], True)
        r = board.decide_approval("site_resting", "site", "approved")
        check("his yes is recorded", r["ok"], True)
        check("and the agency reads the site again", sitepace.resting(), {})


def test_one_drop_is_a_short_pause_and_repeats_grow():
    """
    "approved a blog and passed it to publish to wesbite but this issue happenes"
    — one closed connection stopped every desk for thirty minutes, and his blog
    was refused inside the pause.
    """
    print("\nOne dropped connection is a blip, tried again — nothing waits for it:")
    import time as _t
    with _Cabinet():
        sitepace.told_to_stop("https://127.0.0.1/a.html", error=ConnectionResetError("reset"))
        check("no desk is stopped", sitepace.resting(), {})
        check("but it is noted", sitepace.warned(), True)
        sitepace.got_through("https://127.0.0.1/a.html")
        check("and the next request getting through wipes the note", sitepace.warned(), False)
        sitepace.told_to_stop("https://127.0.0.1/a.html", error=ConnectionResetError("reset"))
        check("so a drop after a good answer is a blip again", sitepace.resting(), {})

    print("\nTwo in a row is a short pause; only repeats grow it:")
    with _Cabinet():
        rests = []
        for n in range(3):
            for _ in range(2):
                sitepace.told_to_stop("https://127.0.0.1/blog-x.html",
                                      error=ConnectionResetError("forcibly closed"))
            rests.append(round((sitepace.resting()["until"] - _t.time()) / 60))
        check("two minutes, then ten, then thirty", rests, [2, 10, 30])
        why = sitepace.resting()["why"]
        check("it says which page and what came back",
              "/blog-x.html" in why and "ConnectionResetError" in why, True)
        sitepace.clear()
        for _ in range(2):
            sitepace.told_to_stop("https://127.0.0.1/", error=ConnectionResetError("reset"))
        check("his 'the site is fine' starts the count again",
              round((sitepace.resting()["until"] - _t.time()) / 60), 2)
    with _Cabinet():
        sitepace.told_to_stop("https://127.0.0.1/", status=429)
        check("'too many requests' in words is fifteen minutes at once",
              round((sitepace.resting()["until"] - _t.time()) / 60), 15)


def main():
    for t in (test_one_request_at_a_time,
              test_one_drop_is_a_short_pause_and_repeats_grow,
              test_the_first_closed_door_stops_every_desk,
              test_he_sees_it_and_can_open_it_again):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        raise SystemExit(1)
    print("sitepace: all good — his site is read gently, and left alone when it says stop.")


if __name__ == "__main__":
    main()
