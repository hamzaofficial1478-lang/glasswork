"""
launch.pyw — what the desktop and Start-menu shortcuts open, on a computer the
program was installed on with Setup.exe.

    "i want to build the program as a software as the softwares have their
     files and user install the whole program with just 1 click"

So it behaves like one: no black console window. It starts the office the way
run.py always has, and opens it in the browser; if the office is already
running it just opens the browser again. What would have been printed goes to
logs/office.log in the data folder, and if it cannot start at all it says why
in an ordinary Windows message box.

The program closes from inside the app (Settings → This program → Close the
program), which is when this ends.
"""
import os
import runpy
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _log_path() -> Path:
    try:
        import json
        data = json.loads((ROOT / "install.json").read_text(encoding="utf-8")).get("data") or ""
    except Exception:
        data = ""
    base = Path(data) if data else ROOT / "data"
    logs = base / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return logs / "office.log"


def _tell(text: str) -> None:
    """A plain Windows message box — the one way to be seen with no console."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "Glasswork", 0x10)
    except Exception:
        pass


def main() -> int:
    log_file = _log_path()
    log = open(log_file, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log
    print(f"\n==== started {time.strftime('%Y-%m-%d %H:%M:%S')} ====")
    os.chdir(ROOT)
    sys.argv = [str(ROOT / "run.py"), "--no-update"]
    started = time.time()
    try:
        runpy.run_path(str(ROOT / "run.py"), run_name="__main__")
    except SystemExit as e:
        if e.code not in (0, None):
            _tell(f"Glasswork stopped (code {e.code}). What happened is written in:\n{log_file}")
            return int(e.code) if isinstance(e.code, int) else 1
    except Exception:
        traceback.print_exc()
        _tell("Glasswork could not start:\n\n" + traceback.format_exc(limit=2)[-600:]
              + f"\n\nThe whole story is in:\n{log_file}")
        return 1
    if time.time() - started < 5:
        # It came back at once: either it was already open (and the browser
        # was pointed at it), or it could not start. The log says which.
        tail = log_file.read_text(encoding="utf-8", errors="replace")[-1200:]
        if "already running" not in tail and "stopped before it could start" in tail:
            _tell("Glasswork could not start. What happened is written in:\n" + str(log_file))
    return 0


if __name__ == "__main__":
    sys.exit(main())
