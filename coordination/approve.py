"""
approve.py — the owner's yes or no, from the Control Room to the cabinet.

A thin front door so the server has one command to shell into, the same way it
does for everything else. The judgement lives in board.decide_approval; this
only reads the request and reports what happened.

    echo '{"kind":"task","id":"abc","decision":"approved"}' | python3 -m coordination.approve
"""
import json
import sys

try:
    from . import board
except ImportError:  # pragma: no cover
    import board


def main():
    try:
        req = json.loads(sys.stdin.read() or "{}")
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"bad input: {e}"}))
        return 2
    try:
        out = board.decide_approval(str(req.get("kind", "")),
                                    req.get("id"),
                                    str(req.get("decision", "")))
    except (ValueError, TypeError) as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    except Exception as e:                       # pragma: no cover
        print(json.dumps({"ok": False, "error": f"could not record it: {e}"}))
        return 1
    print(json.dumps(out))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
