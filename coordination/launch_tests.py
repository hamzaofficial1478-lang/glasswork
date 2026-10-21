"""
launch_tests.py — run: python -m coordination.launch_tests

"git pull the program into folder and run the program and shows this error
 {"error":"Page not found."}"

Those were another program's words: something else on his computer had the
office's port, and the browser — opened two seconds after launch whatever had
happened — showed that program instead of the office.

What is protected here:
  - a port another program holds is seen as taken, and the office moves to the
    next free one and says so;
  - the browser opens only once THIS folder's office answers (the HOME id in
    /api/ping, which Python and the server work out the same way);
  - an office already running from this folder is opened, not started twice;
  - the server, started by hand on a taken port, says so in words and exits
    with PORT_TAKEN instead of a stack trace.

Everything here stays on this computer.
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import run  # noqa: E402

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Squatter:
    """Somebody else's program on a port, answering everything with its own 404."""

    def __enter__(self):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                b = json.dumps({"error": "Page not found."}).encode()
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()


def _env(tmp, port):
    env = {k: v for k, v in os.environ.items() if k not in ("AGENCY_KEY",)}
    env.update(AGENCY_DATA=str(tmp / "data"), AGENCY_DB=str(tmp / "agency.db"),
               AGENCY_IGNORE_DOTENV="1", CLAUDE_TEAM_OFF="1", PORT=str(port),
               NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1")
    return env


def _start_office(tmp, port):
    proc = subprocess.Popen(["node", str(ROOT / "server.js")], cwd=str(ROOT), env=_env(tmp, port),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        if run.who_answers(port, timeout=1).get("office"):
            break
        time.sleep(0.25)
    return proc


def _stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


def _stop_tree(proc):
    """Stop a process AND the office it started — only those, never his own office."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


def test_a_port_someone_else_holds():
    print("\nA port somebody else's program holds is seen, and passed over:")
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost,::1"
    with Squatter() as sq:
        check("it is not free", run.port_free(sq.port), False)
        check("and what answers there is not an office", run.who_answers(sq.port),
              {"office": False, "mine": False})
        port, note = run.choose_port(sq.port)
        check("the office moves to another port", port != sq.port and port > 0, True)
        check("  and says why, in words", "another program on this computer" in note, True)
    free = _free_port()
    check("a free port is free", run.port_free(free), True)
    check("and nothing answers there", run.who_answers(free, timeout=0.5), {})


def test_the_office_knows_itself():
    print("\nThe office that answers is this folder's — or the browser waits:")
    if not shutil.which("node"):
        print("  SKIP  Node is not installed here")
        return
    tmp = Path(tempfile.mkdtemp(prefix="launch-"))
    port = _free_port()
    proc = _start_office(tmp, port)
    try:
        r = run.who_answers(port)
        check("the office answers /api/ping as this folder's", r, {"office": True, "mine": True})
        check("so a second start opens it instead of starting it again", run.choose_port(port),
              (port, "running"))
    finally:
        _stop(proc)


def test_started_by_hand_on_a_taken_port():
    print("\nStarted by hand on a taken port, the server says so and stops:")
    if not shutil.which("node"):
        print("  SKIP  Node is not installed here")
        return
    tmp = Path(tempfile.mkdtemp(prefix="launch-"))
    with Squatter() as sq:
        r = subprocess.run(["node", str(ROOT / "server.js")], cwd=str(ROOT), env=_env(tmp, sq.port),
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        check("it stops with PORT_TAKEN, not a crash", r.returncode, run.PORT_TAKEN)
        check("  and the reason is in words", "already in use on this computer" in (r.stderr or ""), True)
        check("  with no stack trace", "at Server.setupListenHandle" in (r.stderr or ""), False)


def test_run_moves_and_waits():
    print("\nrun.py, end to end: moves off the taken port, and opens the office itself:")
    if not shutil.which("node"):
        print("  SKIP  Node is not installed here")
        return
    tmp = Path(tempfile.mkdtemp(prefix="launch-"))
    script = tmp / "go.py"
    code = ("import sys, webbrowser\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "import run\n"
            "webbrowser.open = lambda url, *a, **k: print('OPENED ' + url, flush=True)\n"
            "run.PORT = sys.argv[1]\n"
            "run.office()\n")
    script.write_text(code, encoding="utf-8")
    with Squatter() as sq:
        group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                 else {"start_new_session": True})
        proc = subprocess.Popen([sys.executable, str(script), str(sq.port)], cwd=str(ROOT),
                                env=_env(tmp, sq.port), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", **group)
        lines, opened = [], ""
        end = time.time() + 60
        try:
            while time.time() < end:
                line = proc.stdout.readline()
                if not line:
                    break
                lines.append(line.rstrip())
                if line.startswith("OPENED "):
                    opened = line.split(" ", 1)[1].strip()
                    break
        finally:
            _stop_tree(proc)
        said = "\n".join(lines)
        check("it says the port was taken and where the office is instead",
              "is in use by another program" in said, True)
        check("the browser is opened on the new port, not the taken one",
              bool(opened) and f":{sq.port}/" not in opened, True)


def main():
    test_a_port_someone_else_holds()
    test_the_office_knows_itself()
    test_started_by_hand_on_a_taken_port()
    test_run_moves_and_waits()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("All launch tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
