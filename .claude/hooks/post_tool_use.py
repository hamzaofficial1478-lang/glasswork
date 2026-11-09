#!/usr/bin/env python3
"""POST-TOOL-USE HOOK — records the result so nothing happens in the dark."""
import sys, json, time, sqlite3
from pathlib import Path
DB = Path(__file__).parents[2] / "coordination" / "agency.db"
try:
    # The open website's cabinet, once accounts exist (coordination/workspace.py).
    sys.path.insert(0, str(Path(__file__).parents[2]))
    from coordination import workspace as _ws
    DB = _ws.db_path()
except Exception:
    pass  # the program's original cabinet; the log is never skipped for this
def main():
    ev = json.load(sys.stdin)
    try:
        con = sqlite3.connect(DB)
        con.execute("INSERT INTO audit_log(agent,tool,args,decision,reason,at) VALUES(?,?,?,?,?,?)",
                    (ev.get("agent","unknown"), ev.get("tool_name",""),
                     "result", "completed", str(ev.get("tool_response",""))[:500], int(time.time())))
        con.commit(); con.close()
    except Exception:
        pass
    sys.exit(0)
if __name__ == "__main__":
    main()
