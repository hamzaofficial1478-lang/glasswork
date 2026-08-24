"""
console.py — make the terminal accept the words this program actually writes.

THE BUG THIS EXISTS FOR

The owner ran the test suite on Windows and five suites failed that pass on
every Linux box. Nothing was wrong with the code they were testing. Every one
died the same way:

    UnicodeEncodeError: 'charmap' codec can't encode character '\\u25cf'

Python on Windows writes text files and stdout in the machine's ANSI codepage —
cp1252 in western Europe, cp1256 in the UAE — not UTF-8. This program is written
in plain English prose and prints the readiness lamps (● ○ ◐ ✕), ticks, and
em-dashes all over its output. cp1252 has no ● and no ✕, so `print()` itself
raises before a single assertion has been checked, and the suite reports FAIL
for a reason that has nothing to do with what it was testing.

The same default breaks reading: a bare read_text on ui/office.html decodes
with the ANSI codepage, and office.html contains ✍ — a byte sequence with no
cp1252 meaning at all — so the read throws too.

THE FIX, AND WHY IT LIVES IN A PACKAGE __init__

`setup()` is called from coordination/__init__.py, pipelines/__init__.py and
providers/__init__.py. Every entry point in this program is either `python -m
<package>.<module>` or imports one of those packages, so three lines cover all
twenty-six test suites, every pipeline, and run.py — with no per-file ritual
anybody can forget to copy into the next module.

errors="replace" rather than "strict": a console that cannot show a tick should
print a question mark, not take the run down. Losing one glyph is a cosmetic
problem; losing the run is not.

It also sets PYTHONUTF8 and PYTHONIOENCODING in the environment. Those do
nothing for THIS process — the interpreter read them at start-up — but every
child process this program spawns inherits them, which is how a pipeline run by
day_run.py or run.py gets the same treatment before its first print.
"""
import os
import sys

_DONE = False


def setup():
    """Idempotent. Safe to call from every package import."""
    global _DONE
    if _DONE:
        return
    _DONE = True

    for stream in (sys.stdout, sys.stderr):
        try:
            enc = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
            if enc not in ("utf8", "utf8mb4"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            # A stream with no reconfigure (a pipe wrapper, a captured buffer in
            # somebody's test harness) is not a reason to fail an import.
            pass

    # For the children. Set, never overwritten — if the owner has deliberately
    # chosen an encoding for a run, that is his call and it stands.
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def read(path, default=""):
    """
    Read a project file as UTF-8, whatever the machine's codepage is.

    Every file in this repository is UTF-8 — the source, the HTML, the schema,
    CLAUDE.md. Reading them with the locale codepage is never right and on
    Windows it is an exception waiting for the first em-dash.
    """
    from pathlib import Path
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    except UnicodeDecodeError:
        # Somebody's editor saved a file as cp1252. Do not lose the run over it.
        return Path(path).read_text(encoding="utf-8", errors="replace")
