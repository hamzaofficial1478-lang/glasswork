"""
workspace_tests.py — run: python -m coordination.workspace_tests

"User can use 2 websites ... 1 website at 1 run ... the user will switch the
 website and will start using the second website" / "there will be different
 setup for each user"

What is protected here:
  - before anyone makes an account, nothing moves: every path is the one the
    program has always used, so the owner's own agency carries on untouched;
  - two websites, and a third is refused;
  - each website keeps its own cabinet, settings and files — a task posted on
    one is not on the other;
  - what belongs to the person (notifications, map keys) is shared by both;
  - switching is refused while a day's run is going;
  - bringing his current website in COPIES, and leaves the originals alone;
  - server.js and the Python side agree on which settings are the person's.
"""
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

from . import board, workspace

_fail = []
ROOT = Path(__file__).parents[1]


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


class _Place:
    """A data folder of its own, and the real cabinet and settings switched back on."""

    KEEP = ("AGENCY_DATA", "AGENCY_DB", "AGENCY_IGNORE_DOTENV")

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in self.KEEP}
        self.dir = Path(tempfile.mkdtemp(prefix="workspace-"))
        os.environ["AGENCY_DATA"] = str(self.dir / "data")
        # db_path() and read_env() are what is being tested, so neither the
        # test run's own cabinet nor its "ignore the settings files" may stand in.
        os.environ.pop("AGENCY_DB", None)
        os.environ.pop("AGENCY_IGNORE_DOTENV", None)
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_nothing_moves_before_an_account():
    print("Before anyone makes an account, every path is the program's original one:")
    with _Place():
        check("no account is open", workspace.legacy(), True)
        check("the cabinet", workspace.db_path(), ROOT / "coordination" / "agency.db")
        check("the settings", workspace.env_paths(), (ROOT / ".env", ROOT / ".env"))
        check("a website's file", workspace.config_path("site.json"), ROOT / "config" / "site.json")
        check("the person's file", workspace.config_path("providers.local.json"),
              ROOT / "config" / "providers.local.json")
        check("the logs", workspace.logs_dir(), ROOT / "logs")
        os.environ["AGENCY_DB"] = "/tmp/test-cabinet.db"
        check("the test run's own cabinet always wins", workspace.db_path(),
              Path("/tmp/test-cabinet.db"))


def test_two_websites_and_no_more():
    print("\nTwo websites to an account, and a third is refused:")
    with _Place():
        acct = workspace.create_account("Ammar")["account"]
        a = workspace.add_site(acct["id"], "World Deal", "https://www.worlddealtechnicalservices.com")
        b = workspace.add_site(acct["id"], "Second shop", "https://secondshop.ae")
        check("the first is added", a["ok"], True)
        check("the second is added", b["ok"], True)
        third = workspace.add_site(acct["id"], "Third", "https://third.ae")
        check("the third is refused", third["ok"], False)
        check("  and says why, naming the two", ("2 websites" in third["error"],
                                                 "World Deal" in third["error"]), (True, True))
        other = workspace.create_account("Someone else")["account"]
        again = workspace.add_site(other["id"], "Mine too", "worlddealtechnicalservices.com")
        check("another account may have a website of the same address", again["ok"], True)
        dup = workspace.add_site(acct["id"], "x", "http://worlddealtechnicalservices.com/")
        check("the same address twice on one account is refused", dup["ok"], False)


def test_each_website_keeps_its_own_things():
    print("\nEach website keeps its own cabinet, settings and files:")
    from providers import integrations
    with _Place():
        acct = workspace.create_account("Ammar")["account"]
        a = workspace.add_site(acct["id"], "World Deal", "https://www.worlddealtechnicalservices.com")["site"]
        b = workspace.add_site(acct["id"], "Second shop", "https://secondshop.ae")["site"]
        real_db = board.DB
        try:
            check("the first website opens", workspace.open_site(acct["id"], a["id"])["ok"], True)
            check("an account is open now", workspace.legacy(), False)
            check("its cabinet is its own",
                  workspace.db_path(), workspace.site_dir(acct["id"], a["id"]) / "agency.db")
            board.DB = workspace.db_path()
            board.post_task("IT", "Change the dummy email", "on World Deal only")
            integrations.write_env({"SITE_URL": "https://www.worlddealtechnicalservices.com",
                                    "TELEGRAM_CHAT_ID": "12345"})
            mine, site = workspace.env_paths()
            check("the website's address went in the website's settings",
                  "SITE_URL=" in site.read_text(encoding="utf-8"), True)
            check("how to reach him went in the person's settings",
                  ("TELEGRAM_CHAT_ID=12345" in mine.read_text(encoding="utf-8"),
                   "TELEGRAM" in site.read_text(encoding="utf-8")), (True, False))
            check("its Search Console key is its own",
                  workspace.config_path("gsc-service-account.local.json").parent,
                  workspace.site_dir(acct["id"], a["id"]) / "config")

            check("the second website opens", workspace.open_site(acct["id"], b["id"])["ok"], True)
            board.DB = workspace.db_path()
            con = sqlite3.connect(board.DB)
            board.ensure_schema(con)
            n = con.execute("SELECT count(*) FROM tasks").fetchone()[0]
            con.close()
            check("the first website's task is not on the second's board", n, 0)
            env = integrations.read_env()
            check("the first website's address is not the second's", env.get("SITE_URL"), None)
            check("but how to reach him is shared by both", env.get("TELEGRAM_CHAT_ID"), "12345")
            check("the person's tool keys are one file for both",
                  workspace.config_path("providers.local.json"),
                  workspace.account_dir(acct["id"]) / "config" / "providers.local.json")
        finally:
            board.DB = real_db
        check("closing goes back to the original layout",
              (workspace.close()["ok"], workspace.legacy()), (True, True))


def test_no_switching_mid_run():
    print("\nOne website per run — switching is refused while a run is going:")
    with _Place():
        acct = workspace.create_account("Ammar")["account"]
        a = workspace.add_site(acct["id"], "World Deal", "https://www.worlddealtechnicalservices.com")["site"]
        b = workspace.add_site(acct["id"], "Second shop", "https://secondshop.ae")["site"]
        workspace.open_site(acct["id"], a["id"])
        real = workspace._run_going
        workspace._run_going = lambda: "run-1"
        try:
            r = workspace.open_site(acct["id"], b["id"])
            check("refused", r["ok"], False)
            check("  saying which website's run and what to do",
                  ("World Deal" in r["error"], "Stop" in r["error"]), (True, True))
            check("the first website is still the open one",
                  workspace.active(), {"account": acct["id"], "site": a["id"]})
            check("signing out is refused too", workspace.close()["ok"], False)
        finally:
            workspace._run_going = real
        check("once the run is over, it switches",
              workspace.open_site(acct["id"], b["id"])["ok"], True)


def test_his_website_is_copied_in_not_moved():
    print("\nBringing his current website in copies everything, and moves nothing:")
    with _Place() as place:
        old = place.dir / "program"
        (old / "config").mkdir(parents=True)
        (old / "coordination").mkdir()
        env = ("SITE_URL=https://www.worlddealtechnicalservices.com\n"
               "TELEGRAM_CHAT_ID=999\nSMTP_HOST=mail.x\n")
        (old / ".env").write_text(env, encoding="utf-8")
        (old / "config" / "site.json").write_text('{"url": "x"}', encoding="utf-8")
        (old / "config" / "providers.local.json").write_text("{}", encoding="utf-8")
        (old / "config" / "claude_code.local.json").write_text("{}", encoding="utf-8")
        (old / "config" / "site.example.json").write_text("{}", encoding="utf-8")
        con = sqlite3.connect(old / "coordination" / "agency.db")
        con.execute("CREATE TABLE leads (company TEXT)")
        con.execute("INSERT INTO leads VALUES ('Marina Hotel')")
        con.commit()
        con.close()
        real = workspace.ROOT
        workspace.ROOT = old
        try:
            acct = workspace.create_account("Ammar")["account"]
            site = workspace.add_site(acct["id"], "World Deal",
                                      "https://www.worlddealtechnicalservices.com")["site"]
            r = workspace.bring_in_current(acct["id"], site["id"])
        finally:
            workspace.ROOT = real
        sdir, adir = workspace.site_dir(acct["id"], site["id"]), workspace.account_dir(acct["id"])
        check("it worked", r["ok"], True)
        senv = (sdir / "site.env").read_text(encoding="utf-8")
        aenv = (adir / "account.env").read_text(encoding="utf-8")
        check("the website's settings went with the website",
              ("SITE_URL=" in senv, "SMTP_HOST=" in senv), (True, True))
        check("his own went on his account",
              ("TELEGRAM_CHAT_ID=999" in aenv, "TELEGRAM" in senv), (True, False))
        check("the website's files went with it", (sdir / "config" / "site.json").exists(), True)
        check("his tool keys went on his account",
              (adir / "config" / "providers.local.json").exists(), True)
        check("the computer's own file stayed the computer's",
              (sdir / "config" / "claude_code.local.json").exists()
              or (adir / "config" / "claude_code.local.json").exists(), False)
        con = sqlite3.connect(sdir / "agency.db")
        check("the cabinet came across, leads and all",
              con.execute("SELECT company FROM leads").fetchall(), [("Marina Hotel",)])
        con.close()
        check("and every original is still where it was",
              all(p.exists() for p in (old / ".env", old / "config" / "site.json",
                                       old / "coordination" / "agency.db")), True)


def test_server_and_python_agree():
    print("\nserver.js and the Python side agree on which settings are the person's:")
    js = (ROOT / "server.js").read_text(encoding="utf-8")
    m = re.search(r"const ACCOUNT_KEYS = new Set\(\[(.*?)\]\);", js, re.S)
    check("server.js lists them", bool(m), True)
    got = set(re.findall(r'"([A-Z0-9_]+)"', m.group(1))) if m else set()
    check("the same list", sorted(got), sorted(workspace.ACCOUNT_KEYS))
    for name in ("ACCOUNT_CONFIG", "MACHINE_CONFIG"):
        m = re.search(rf"const {name} = new Set\(\[(.*?)\]\);", js, re.S)
        got = set(re.findall(r'"([^"]+)"', m.group(1))) if m else set()
        check(f"the same {name.lower().replace('_', ' ')} files", sorted(got),
              sorted(getattr(workspace, name)))


def main():
    for t in (test_nothing_moves_before_an_account, test_two_websites_and_no_more,
              test_each_website_keeps_its_own_things, test_no_switching_mid_run,
              test_his_website_is_copied_in_not_moved, test_server_and_python_agree):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        sys.exit(1)
    print("workspace: all good — two websites, each its own, and his left untouched.")


if __name__ == "__main__":
    main()
