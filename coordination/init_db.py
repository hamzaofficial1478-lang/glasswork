"""Create the filing cabinet from schema.sql."""
import sqlite3
from pathlib import Path

HERE = Path(__file__).parent
DB = HERE / "agency.db"
SCHEMA = HERE / "schema.sql"

def init():
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA.read_text())
    con.commit()
    con.close()
    print(f"filing cabinet ready at {DB}")

if __name__ == "__main__":
    init()
