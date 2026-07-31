"""coordination — the shared filing cabinet and the turn-taking layer.

Making this a package lets other modules import the board and the cabinet cleanly
(`from coordination import board, cabinet`) while each file still runs as a script
(`python3 coordination/init_db.py`) via the defensive imports inside them.
"""
