"""
speed_tests.py — run: python -m coordination.speed_tests   (python3 on macOS/Linux)

    "the program lags while changing sections or buttons it should be fast at
     it ... that should not put load on program and ui works smoothly"

The office keeps the answer a screen was given and gives it again at once —
but only while it is provably still the answer. These tests boot the real
server.js on a spare port against a throwaway cabinet and prove the three ways
a kept answer must be dropped: something was saved, an agent wrote to the
cabinet, or the answer was an error in the first place.

And one rule about the Run button, through the same door the screen uses:

    "with out any order the program will not start the day"

Nothing here reaches the network, and nothing is written outside a temporary
folder.
"""
import http.client
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).parents[1]
_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Office:
    """server.js on a spare port, with its own data folder and cabinet."""

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp())
        self.db = self.dir / "agency.db"
        self.port = _free_port()
        env = dict(os.environ, PORT=str(self.port), AGENCY_DATA=str(self.dir / "data"),
                   AGENCY_DB=str(self.db), AGENCY_IGNORE_DOTENV="1", CLAUDE_TEAM_OFF="1")
        self.proc = subprocess.Popen(["node", str(ROOT / "server.js")], cwd=str(ROOT), env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(120):
            try:
                self.ask("GET", "/api/ping")
                break
            except Exception:
                time.sleep(0.25)
        return self

    def __exit__(self, *a):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        shutil.rmtree(self.dir, ignore_errors=True)

    def ask(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        headers = {"Content-Type": "application/json"} if body is not None else {}
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        r = c.getresponse()
        data = r.read().decode("utf-8", "replace")
        kept = (r.getheader("X-Kept") or "") == "1"
        c.close()
        try:
            j = json.loads(data)
        except Exception:
            j = {"raw": data[:200]}
        return r.status, j, kept


def test_kept_answers():
    print("\nA screen asked twice gets the same answer at once:")
    with Office() as o:
        st, first, kept = o.ask("GET", "/api/orders")
        if st == 401:
            print("  SKIPPED: this computer's office has a password, so it cannot be asked here.")
            return
        check("the first time it is worked out", (st, kept), (200, False))
        # A brand-new cabinet finishes setting itself up on its first read —
        # a write — so the next read is rightly worked out again. After that
        # nothing has changed, and the answer is kept.
        st, first, _ = o.ask("GET", "/api/orders")
        st, again, kept = o.ask("GET", "/api/orders")
        check("asked again with nothing changed, it is the kept answer", kept, True)
        check("  and the very same one", again, first)

        print("\nSaving anything means nothing kept is trusted:")
        st, j, _ = o.ask("POST", "/api/orders", {"orders": {"SEO": "pergola pages"}})
        check("the order was saved", j.get("ok"), True)
        st, after, kept = o.ask("GET", "/api/orders")
        check("the next read is worked out again", kept, False)
        check("  and shows what was just saved",
              any(r.get("text") == "pergola pages" for r in after.get("orders") or []), True)

        print("\nAn agent writing to the cabinet does the same, with no click at all:")
        o.ask("GET", "/api/orders")
        check("kept again before the write", o.ask("GET", "/api/orders")[2], True)
        time.sleep(0.05)
        con = sqlite3.connect(o.db)
        con.execute("INSERT INTO sector_orders(day, sector, orders, updated_at)"
                    " VALUES('2000-01-01', 'IT', 'an agent wrote this', 0)")
        con.commit()
        con.close()
        check("after the write it is worked out again", o.ask("GET", "/api/orders")[2], False)

        print("\nAn error is never kept, and some answers are never kept at all:")
        o.ask("GET", "/api/mail/sent/999999")
        st, j, kept = o.ask("GET", "/api/mail/sent/999999")
        check("asked twice, an error is asked again", (j.get("ok"), kept), (False, False))
        o.ask("GET", "/api/product")
        check("the product card is not on the list", o.ask("GET", "/api/product")[2], False)

        print("\nNo orders, no day — through the Run button's own door:")
        con = sqlite3.connect(o.db)
        con.execute("DELETE FROM sector_orders")
        con.commit()
        con.close()
        st, j, _ = o.ask("POST", "/api/run/start", {})
        check("it does not start", (j.get("ok"), j.get("started")), (False, False))
        check("  and says the orders were missing", j.get("no_orders"), True)
        secs = (j.get("check") or {}).get("sectors") or []
        check("  having checked every sector", len(secs), 7)
        check("  and every desk in them", all(s.get("desks") for s in secs), True)
        check("  in words he can act on", "Your orders" in (j.get("why") or ""), True)
        con = sqlite3.connect(o.db)
        runs = con.execute("SELECT count(*) FROM day_runs").fetchone()[0]
        con.close()
        check("  and no day was written down", runs, 0)
        st, j, _ = o.ask("POST", "/api/run/start", {"orders": {"Marketing": "sell more"}})
        check("an order for a sector that does not exist is still no order", j.get("no_orders"), True)


def test_the_screen_side():
    print("\nThe screen keeps what it read, and throws it away on any change:")
    app = (ROOT / "ui" / "app.html").read_text(encoding="utf-8")
    check("reads are kept and shown at once", "const KEPT = new Map()" in app and "return k.j;" in app, True)
    check("an older one is asked for again quietly", "recheck(url)" in app, True)
    check("  and redrawn only if the answer changed", "now.s !== before" in app, True)
    check("any change throws the kept answers away", "KEPT.clear();" in app, True)
    check("a screen left behind cannot paint over the next one", "if (gen === VIEW.gen) t[k] = v;" in app, True)
    check("typing is never drawn over", "typing(main) || VIEW.dirty" in app, True)
    check("Activity filters what is already on the screen",
          "const redraw = () => activity(main, true);" in app, True)
    check("  Problems only is its own switch, off each time the screen opens",
          'id="actProb"' in app and "ACT.problems = false; }" in app, True)
    check("  and a filter that hides lines says so", "Show everything" in app, True)
    check("Run with no orders shows the check, not a started day",
          "if (j && j.no_orders)" in app and "function openDeskCheck()" in app, True)


def main():
    if not shutil.which("node"):
        print("SKIPPED: Node is not installed, so the office cannot be started.")
        test_the_screen_side()
    else:
        test_kept_answers()
        test_the_screen_side()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("speed: all good — what is kept is still true, and no order means no day.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
