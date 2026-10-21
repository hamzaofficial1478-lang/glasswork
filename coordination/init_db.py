"""Create the filing cabinet from schema.sql."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The server runs this BY PATH (python coordination/init_db.py), where the
# package is not on the import path — and the package has to load before
# sqlite3 does, because an encrypted website's cabinet only opens through it.
sys.path.insert(0, str(HERE.parent))
from coordination import workspace  # noqa: E402
import sqlite3                      # noqa: E402

DB = workspace.db_path()            # the open website's cabinet
SCHEMA = HERE / "schema.sql"

def init():
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    con.commit()
    con.close()
    print(f"filing cabinet ready at {DB}")

if __name__ == "__main__":
    init()
