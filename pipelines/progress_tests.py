"""
progress_tests.py — run: python -m pipelines.progress_tests

"a live progress of website like how website is performing ... when user will
 see his website live progress on google"

What is protected (a throwaway cabinet; nothing reaches Google):
  - today's numbers are written down from what the desks found, and a count
    can never stop the work;
  - Google's own daily numbers are kept by day and replaced, never doubled;
  - the headline numbers are honest: 28 days against the 28 before, position
    weighted by how often each day was shown, lower position counted as better;
  - the agency's own line still grows with no Search Console;
  - the office reads it all without asking Google anything;
  - the charts never put two scales on one chart, and say what they show.
"""
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from coordination import board
from . import site_progress as sp

ROOT = Path(__file__).resolve().parents[1]
_fail = []


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


def _day(n, now):
    return time.strftime("%Y-%m-%d", time.localtime(now - n * 86400))


def test_numbers():
    print("\nGoogle's numbers, and the agency's own, by day:")
    now = time.time()
    with _Cabinet():
        rows = []
        for n in range(56, 0, -1):
            new = n <= 28
            rows.append({"day": _day(n, now), "clicks": 3 if new else 2, "impressions": 100 if new else 80,
                         "position": 12.0 if new else 18.0})
        sp.keep_google(rows)
        sp.keep_google(rows[-5:])             # fetched again: replaced, not doubled
        sp.note(day=_day(30, now), on_google=2, pages=10, have=10, total=20)
        sp.note(day=_day(1, now), on_google=6, pages=10, have=15, total=20, leads=40)
        sp.note(speed_ms=812)
        s = sp.summary(now=now)
        t = s["tiles"]
        check("56 days kept once each", len(s["google"]), 56)
        check("clicks: the last 28 days against the 28 before", (t["clicks"]["value"], t["clicks"]["change"]), (84, 50))
        check("shown in Google, the same way", (t["impressions"]["value"], t["impressions"]["change"]), (2800, 25))
        check("position weighted by impressions, lower is better",
              (t["position"]["value"], t["position"]["then"], t["position"]["better"]), (12.0, 18.0, True))
        check("pages on Google, against the first day recorded", (t["on_google"]["value"], t["on_google"]["of"],
                                                                  t["on_google"]["first"]), (6, 10, 2))
        check("how complete the site is, against the first check", (t["complete"]["value"], t["complete"]["first"]), (75, 50))
        check("today's speed is on today's row", s["site"][-1]["speed_ms"], 812)
        check("and it says Google's numbers are late", "two days late" in s["note"], True)


def test_without_search_console():
    print("\nWith no Search Console, the agency's own line still grows:")
    with _Cabinet():
        sp.note(on_google=1, pages=8)
        s = sp.summary()
        check("no Google rows, no Google tiles", ("clicks" in s["tiles"], s["google"]), (False, []))
        check("but pages on Google is there", s["tiles"]["on_google"]["value"], 1)
        check("and it says Search Console is not connected", s["google_connected"], False)
        check("a count never raises, even with nonsense", sp.note(nothing=1) is None, True)


def test_the_screen():
    print("\nThe screen:")
    app = (ROOT / "ui" / "app.html").read_text(encoding="utf-8")
    server = (ROOT / "server.js").read_text(encoding="utf-8")
    check("the office reads it without asking Google", '"pipelines.site_progress", ["--json"]' in server, True)
    check("On Google and Today both show it", app.count("api('/api/site/progress')"), 2)
    chart = app[app.index("function lineChart"):app.index("function wireCharts")]
    check("one series and one scale per chart", chart.count("<path d=\"${line}\""), 1)
    check("position is drawn with the top as first place", "{invert: true" in app and "1 is the top" in app, True)
    check("every chart has its numbers as a table", "See the numbers" in chart, True)
    wire = app[app.index("function wireCharts"):app.index("function progressSection")]
    check("the readout follows the pointer and the arrow keys",
          "pointermove" in wire and "ArrowLeft" in wire and "textContent" in wire, True)
    check("the chart colour is a token, defined for light and dark",
          (ROOT / "ui" / "botix.css").read_text(encoding="utf-8").count("--chart:"), 3)


def main():
    for t in (test_numbers, test_without_search_console, test_the_screen):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("progress: every number counted, by day.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
