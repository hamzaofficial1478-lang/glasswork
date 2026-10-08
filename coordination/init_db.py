"""Create the filing cabinet from schema.sql."""
import sqlite3
from pathlib import Path

from coordination import workspace

HERE = Path(__file__).parent
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
