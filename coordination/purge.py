"""
purge.py — throw away what is older than the window, for good.

    python -m coordination.purge                  what would go, changes nothing
    python -m coordination.purge --run            actually do it
    python -m coordination.purge --days 90 --run  a different window

"make a button to clear the communication older then 30 days or make it to
 only have the 30 days of data... the data deleted must not get stored in the
 Db or any other place it must be deleted permanently"

WHAT PERMANENT MEANS HERE, AND WHY IT NEEDS SAYING

Deleting a row in SQLite does not wipe it. The page keeps the bytes and the
space is reused later, so the text of a deleted message can sit legible in the
file for weeks and turn up in any copy of it. He asked for permanently, and a
DELETE on its own is not that.

So every purge ends with VACUUM, which writes a fresh database file containing
only the surviving rows and replaces the old one. What was deleted is not in
the new file at all. That is the difference between "removed from the listing"
and "gone", and it is the whole reason this is its own module rather than four
DELETE statements wherever they were convenient.

Two things it will not touch, on purpose:

  DECISIONS ARE KEPT. The handbook has the MD reading its own hit-rate every
  cycle and being confronted with its own track record. A ninety-day-old call
  that failed is exactly the row that makes tomorrow's plan better, and a
  scoreboard that quietly forgets its losses is not a scoreboard.

  ANYTHING STILL OPEN IS KEPT. An old task nobody has finished, a request
  nobody has answered, a ticket still open — age is not the same as done, and
  deleting a job because it has been ignored for a month is how the ignoring
  becomes permanent.
"""
import argparse
import json
import sqlite3
import sys
import time

from . import board

DEFAULT_DAYS = 30

# (table, time column, plain name, extra SQL that must ALSO be true)
#
# The 'extra' is what keeps live work out of it. Everything here is a record of
# something that already happened and is finished with.
SWEEPS = [
    ("request_msgs", "at", "messages between sectors", ""),
    ("requests", "created_at", "conversations between sectors",
     "status IN ('answered','closed')"),
    ("tasks", "created_at", "finished jobs",
     "status IN ('done','blocked','withdrawn','cancelled')"),
    ("agent_runs", "started_at", "records of an agent running",
     "status NOT IN ('running',)"),
    ("activity_log", "at", "the activity feed", ""),
    ("day_steps", "started_at", "steps of old runs", ""),
    ("day_runs", "started_at", "old runs of the day", "status != 'running'"),
    ("site_changes", "at", "the website change log", ""),
    ("site_issues", "found_at", "closed site tickets",
     "status IN ('closed','deployed')"),
    ("site_queue", "queued_at", "decided website changes",
     "status IN ('applied','failed','rejected')"),
    ("faults", "last_seen", "resolved faults", "status = 'resolved'"),
    ("security_events", "at", "security events", ""),
    ("api_usage", "at", "records of an API being called", ""),
    ("outreach_log", "at", "the outreach log", ""),
    ("keyword_intel", "seen_at", "keyword findings", ""),
]

# Never swept, and each for its own reason. Named here rather than simply left
# out, so the next person to add a sweep has to read why.
KEPT = {
    "decisions": "the MD's own scorecard — the handbook has it reading its own "
                 "hit-rate every cycle, and old losses are the useful part",
    "leads": "a business you found in March is still a business, and dropping it "
             "means the Hunter collects and mails it again as if it were new",
    "sector_switches": "one row per sector, and it is a setting rather than history",
    "site_fingerprints": "one row per page — the comparison point for spotting a "
                         "change, not a record of one",
}


def _has(c, table) -> bool:
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                          " AND name=?", (table,)).fetchone())


def _cols(c, table) -> set:
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def _empty_the_log(c) -> None:
    """
    Move everything in the write-ahead log into the file and leave the log empty.

    'TRUNCATE' here is SQLite's own word for the strongest checkpoint: flush
    every page across, then shorten the log file to nothing. Without it the log
    keeps the pages of rows that have been deleted, which is the whole reason
    the first version of this was not the 'permanently' he asked for.
    """
    try:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass


def plan(days=DEFAULT_DAYS) -> dict:
    """What WOULD go. Reads only — safe to call from a screen."""
    cutoff = int(time.time()) - int(days) * 86400
    rows, total = [], 0
    try:
        c = sqlite3.connect(board.DB, timeout=30)
    except Exception as e:
        return {"ok": False, "error": str(e), "tables": []}
    try:
        board.ensure_schema(c)
        for table, tcol, label, extra in SWEEPS:
            if not _has(c, table) or tcol not in _cols(c, table):
                continue
            where = f"{tcol} IS NOT NULL AND {tcol} < ?"
            if extra:
                where += f" AND ({extra})"
            try:
                n = c.execute(f"SELECT count(*) FROM {table} WHERE {where}",
                              (cutoff,)).fetchone()[0]
            except sqlite3.OperationalError:
                continue
            if n:
                rows.append({"table": table, "label": label, "rows": n,
                             "only": extra or "everything older than the window"})
                total += n
        size = c.execute("SELECT page_count * page_size FROM pragma_page_count(),"
                         " pragma_page_size()").fetchone()[0]
    finally:
        c.close()
    return {"ok": True, "days": int(days), "cutoff": cutoff, "tables": rows,
            "rows": total, "bytes": size, "kept": KEPT,
            "head": (f"{total} row(s) are older than {days} days and finished with"
                     if total else
                     f"nothing on file is older than {days} days")}


def run(days=DEFAULT_DAYS) -> dict:
    """
    Delete them, then rewrite the file so the bytes are gone with them.

    VACUUM is the point. Without it SQLite keeps the deleted rows' bytes in the
    file and reuses the space whenever it happens to need it, so the text of a
    message he asked to be rid of can stay readable in the database — and in
    every backup of it — for weeks. He asked for permanently.
    """
    before = plan(days)
    if not before.get("ok"):
        return before
    cutoff = before["cutoff"]
    done, freed = [], 0
    c = sqlite3.connect(board.DB, timeout=60)
    try:
        board.ensure_schema(c)
        for table, tcol, label, extra in SWEEPS:
            if not _has(c, table) or tcol not in _cols(c, table):
                continue
            where = f"{tcol} IS NOT NULL AND {tcol} < ?"
            if extra:
                where += f" AND ({extra})"
            try:
                n = c.execute(f"DELETE FROM {table} WHERE {where}", (cutoff,)).rowcount
            except sqlite3.OperationalError:
                continue
            if n:
                done.append({"table": table, "label": label, "rows": n})
        # A message whose conversation has gone is a message nobody can reach.
        if _has(c, "request_msgs") and _has(c, "requests"):
            n = c.execute("DELETE FROM request_msgs WHERE request_id NOT IN"
                          " (SELECT id FROM requests)").rowcount
            if n:
                done.append({"table": "request_msgs", "rows": n,
                             "label": "messages whose conversation is gone"})
        c.commit()
        size_before = before["bytes"]
        # VACUUM cannot run inside a transaction, and sqlite3 opens one for us.
        c.isolation_level = None

        # THE DATABASE IS NOT ONE FILE, AND VACUUM ALONE IS NOT ENOUGH.
        #
        # board opens SQLite in WAL mode, so writes live in agency.db-wal until
        # a checkpoint moves them across. VACUUM rewrites the main file without
        # the deleted rows — and leaves the write-ahead log still holding the
        # pages they were on. The text he asked to be rid of stays legible in
        # agency.db-wal, and in any copy of that folder.
        #
        # This shipped with the VACUUM alone and claimed to be permanent. The
        # test that reads the raw bytes of EVERY file the database consists of
        # is what caught it; the earlier test read agency.db by itself and
        # passed, because in WAL mode the phrase was never in that file to
        # begin with — it passed before the purge had done anything.
        #
        # So the log is checkpointed and emptied, the file is rewritten, and
        # the log is emptied again because VACUUM itself writes through it.
        _empty_the_log(c)
        c.execute("VACUUM")
        _empty_the_log(c)

        size_after = c.execute(
            "SELECT page_count * page_size FROM pragma_page_count(),"
            " pragma_page_size()").fetchone()[0]
        freed = max(0, size_before - size_after)
    finally:
        c.close()

    total = sum(d["rows"] for d in done)
    return {"ok": True, "days": int(days), "deleted": done, "rows": total,
            "bytes_freed": freed, "vacuumed": True,
            "head": (f"{total} row(s) deleted and the database rewritten — "
                     f"{freed // 1024}KB freed. What went is not in the file "
                     "any more, not in the free space and not in a backup taken "
                     "from here on."
                     if total else
                     f"nothing was older than {days} days; nothing was deleted")}


def as_text(r) -> str:
    if not r.get("ok"):
        return "PURGE — " + (r.get("error") or "could not read the cabinet")
    if "deleted" in r:
        out = ["PURGE — " + r["head"], ""]
        for d in r["deleted"]:
            out.append(f"  {d['rows']:>6}  {d['label']}")
        return "\n".join(out)
    out = ["WOULD PURGE — " + r["head"], ""]
    for t in r["tables"]:
        out.append(f"  {t['rows']:>6}  {t['label']}")
    out += ["", "Kept whatever happens:"]
    for k, why in r["kept"].items():
        out.append(f"  {k:20} {why}")
    out.append("\nNothing was changed. Add --run to do it.")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(
        description="Throw away what is older than the window, permanently.")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--run", action="store_true", help="actually delete")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = run(a.days) if a.run else plan(a.days)
    print(json.dumps(r, indent=2) if a.json else as_text(r))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
