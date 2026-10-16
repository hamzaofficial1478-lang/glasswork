"""
licence_tests.py — run: python -m coordination.licence_tests   (python3 on macOS/Linux)

    "user buy a plan in which the program cannot fetch the leads more then 10
     or cannot write posts more then 1 a day ... even user will use his own llm
     or will use our llm the user will be limitated with the plan he selected"

Botix Home's licences.php on a real PHP server; the publisher's program
switching plans on, making keys and changing plans; and a customer's copy
keeping to them. What has to hold:

  a plan is a plan: leads, emails and posts a day for each website, the words
    in a post, the websites, Website Check reports a month — counted by the
    Home, refused with a plain sentence when used up, back at midnight or when
    the plan renews
  the Home knows who bought what and when: Stripe's messages (signed, or
    nothing happens) make, upgrade, renew and cancel a plan; one trial per
    customer; the buyer sees their key once
  cancelled or ended means stopped, at once — the work kept
  one key, one computer at a time: a second computer is asked to move it, a
    key moves a few times a month and no more, two copies at once are told apart
  a copy carries on for a few days offline, within the plan, and the Home
    counts that work when it is back
  the plan cannot be edited on the customer's computer (signed), and the
    publisher's own copy never asks for a key

Nothing reaches the internet. The publisher file, keys and data folders are
temporary; the program's own config folder is never written.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
import time
import urllib.request
from pathlib import Path

from . import licence, product, publisher, workspace
from .publish_tests import Home

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


ENV_KEYS = ("GLASSWORK_PUBLISHER_FILE", "AGENCY_DATA", "AGENCY_ACCOUNT", "AGENCY_SITE", "BOTIX_RUN",
            "GLASSWORK_LICENCE_HOME", "GLASSWORK_LICENCE_KEY", "AGENCY_KEY", "SMTP_HOST", "SMTP_USER")


class World:
    """His Home and his program (the owner), and a customer's copy on another computer."""

    def __init__(self, h):
        self.h = h
        self.owner = {k: os.environ.get(k) for k in ENV_KEYS}
        made = publisher.licence_setup()
        assert made.get("ok"), made
        self.setup = made
        self.public = dict(product.publisher()["public"])
        self.root = Path(h.dir)
        self.copies = {}

    def _apply(self, env):
        for k in ENV_KEYS:
            if env.get(k) is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = env[k]
        licence._DOWN_UNTIL = 0

    def as_owner(self):
        self._apply(self.owner)

    def copy(self, name, run="aaaa1111", sites=(("My shop", "https://shop.example"),)):
        """A customer's copy: its own data folder, an account, a website — and the release's public keys only."""
        if name not in self.copies:
            d = self.root / f"copy-{name}"
            pub = d / "product.json"
            d.mkdir(parents=True, exist_ok=True)
            pub.write_text(json.dumps({"public": self.public}), encoding="utf-8")
            env = {"GLASSWORK_PUBLISHER_FILE": str(pub), "AGENCY_DATA": str(d / "data"), "BOTIX_RUN": run}
            self._apply(env)
            acct = workspace.create_account(f"Customer {name}")["account"]["id"]
            ids = []
            for sname, url in sites:
                os.environ["AGENCY_ACCOUNT"] = acct
                r = workspace.add_site(acct, sname, url)
                ids.append(r["site"]["id"])
            env.update(AGENCY_ACCOUNT=acct, AGENCY_SITE=ids[0])
            self.copies[name] = env
        env = dict(self.copies[name])
        if run:
            env["BOTIX_RUN"] = run
        self._apply(env)
        return env

    def lic_file(self, key) -> Path:
        k = re.sub(r"[^A-Z0-9]", "", key.upper())
        k = k[3:] if k.startswith("BTX") else k
        return self.h.web / "glasswork-home" / "private" / "licences" / (hashlib.sha256(("botix-licence:" + k).encode()).hexdigest() + ".php")

    def edit(self, key, fn):
        """Change a licence as kept on the Home (dates in the past, for example)."""
        f = self.lic_file(key)
        head, body = f.read_text(encoding="utf-8").split("\n", 1)
        d = json.loads(body)
        fn(d)
        f.write_text(head + "\n" + json.dumps(d), encoding="utf-8")

    def stripe(self, event, secret="whsec_test_secret", t=None, sign=True):
        raw = json.dumps(event).encode("utf-8")
        t = int(t or time.time())
        req = urllib.request.Request(self.h.url + "/glasswork-home/index.php?do=stripe", data=raw, method="POST",
                                     headers={"Content-Type": "application/json"})
        if sign:
            sig = hmac.new(secret.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
            req.add_header("Stripe-Signature", f"t={t},v1={sig}")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def page(self, path) -> str:
        with urllib.request.urlopen(self.h.url + path, timeout=10) as r:
            return r.read().decode("utf-8", "replace")


def _sub(sid, cus, price, status="active", start=None, end=None, cancel_end=False, trial_end=None):
    now = int(time.time())
    return {"id": sid, "object": "subscription", "customer": cus, "status": status,
            "cancel_at_period_end": cancel_end, "trial_end": trial_end,
            "items": {"data": [{"price": {"id": price}, "current_period_start": start or now,
                                "current_period_end": end or now + 30 * 86400}]}}


# ---------------------------------------------------------------------------

def test_switching_plans_on(w):
    print("\nThe publisher switches plans on:")
    check("his licence key is made and his Home holds it", w.setup.get("ok"), True)
    check("  the public half goes in what a release carries",
          json.loads(publisher.release_product_json()).get("licence_key"), w.setup["public_key"])
    plans = w.setup["settings"]["plans"]
    check("the plans on his website are there: Starter, Growth, Pro", list(plans), ["starter", "growth", "pro"])
    check("  Starter: 1 website, 7 leads and 5 emails a day, 1 post of up to 400 words",
          (plans["starter"]["websites"], plans["starter"]["limits"]["leads"]["n"], plans["starter"]["limits"]["mails"]["n"],
           plans["starter"]["limits"]["posts"]["n"], plans["starter"]["post_words"]), (1, 7, 5, 1, 400))
    check("  Pro: 2 websites, 50 leads, 30 emails, 2 posts of up to 1,200 words, his own logo",
          (plans["pro"]["websites"], plans["pro"]["limits"]["leads"]["n"], plans["pro"]["limits"]["mails"]["n"],
           plans["pro"]["limits"]["posts"]["n"], plans["pro"]["post_words"], plans["pro"]["features"]),
          (2, 50, 30, 2, 1200, ["own_logo"]))
    again = publisher.licence_setup()
    check("pressing it again keeps the same key (customers' copies keep working)",
          again.get("public_key"), w.setup["public_key"])
    check("his own copy never asks for a key", (licence.owner_copy(), licence.enabled(), licence.take("leads").get("ok")),
          (True, False, True))
    fake = Path(w.h.dir) / "fake-owner.json"
    other = Path(w.h.dir) / "other.key"
    other.write_bytes(os.urandom(32))
    fake.write_text(json.dumps({"home_key": "x" * 40, "signing_key": str(other), "public": w.public}), encoding="utf-8")
    os.environ["GLASSWORK_PUBLISHER_FILE"] = str(fake)
    check("  a settings file claiming to be his, without his signing key, is not his copy",
          (licence.owner_copy(), licence.enabled()), (False, True))
    w.as_owner()


def test_a_key_on_a_computer(w):
    print("\nA customer enters the key they bought:")
    made = publisher.licence_new("starter", "aisha@example.com", "Aisha Trading", days=30, note="bank transfer")
    check("he makes a Starter plan sold by hand, and sees its key once", bool(re.fullmatch(r"BTX(-[0-9A-Z]{5}){4}", made.get("key", ""))), True)
    w.key = made["key"]
    w.lic_id = made["licence"]["id"]
    w.copy("one")
    check("a copy given out with plans asks for a key", licence.enabled(), True)
    nk = licence.working()
    check("  without one, nothing runs — and it says where the key is",
          (nk.get("ok"), "Enter your Botix key" in nk.get("error", "")), (False, True))
    check("  a job is refused too", licence.take("leads").get("ok"), False)
    check("a key the wrong shape is refused before anything is sent", licence.activate("hello").get("ok"), False)
    check("a key nobody made is not known", "not known" in licence.activate("BTX-AAAAA-BBBBB-CCCCC-DDDDD").get("error", ""), True)
    v = licence.activate(w.key.lower().replace("-", " "))
    check("the real key works however it is typed", (v.get("ok"), v.get("working"), v.get("plan")), (True, True, "starter"))
    check("  and the program knows the plan, when it was bought and until when",
          (v.get("plan_name"), v.get("bought_at", 0) > 0, v.get("period_end", 0) > time.time() + 29 * 86400), ("Starter", True, True))
    check("  the words and websites the plan allows", (v.get("post_words"), v.get("websites")), (400, 1))
    st = json.loads(workspace.read_secret(workspace.config_path(licence.STATE)))
    check("the key is kept in the account's own folder, not the program's", str(workspace.config_path(licence.STATE)).startswith(os.environ["AGENCY_DATA"]), True)
    check("  with the Home's signed answer", bool(licence.verify(st.get("token"))), True)


def test_a_plan_is_a_plan(w):
    print("\nThe limits, whichever AI does the work:")
    w.copy("one")
    got = [licence.take("leads").get("ok") for _ in range(7)]
    check("7 new leads today on Starter", got, [True] * 7)
    eighth = licence.take("leads")
    check("  the 8th is refused, in plain words, until midnight",
          (eighth.get("ok"), "Today's new leads are used up: 7 of 7 on the Starter plan" in eighth.get("error", ""),
           "midnight" in eighth.get("error", "")), (False, True, True))
    looks = [licence.take("mails", dry=True).get("used") for _ in range(3)]
    check("  a look does not count", (looks, {m["kind"]: m["used"] for m in licence.view()["meters"]}["mails"]), ([1, 1, 1], 0))
    check("Website Check is not part of Starter", "not part of the Starter plan" in licence.take("checks").get("error", ""), True)
    two = licence.take("leads", site="another-website")
    check("a second website is beyond a 1-website plan", (two.get("ok"), "covers 1 website" in two.get("error", "")), (False, True))
    w.as_owner()
    ch = publisher.licence_change(w.lic_id, plan="growth", note="upgraded on the phone")
    check("he upgrades them to Growth mid-month", (ch.get("ok"), ch["licence"]["plan"]), (True, "growth"))
    w.copy("one")
    licence.check(force=True)
    v = licence.view()
    check("  the copy knows at its next check — Growth, plan since today", (v.get("plan"), v.get("plan_since", 0) >= time.time() - 120), ("growth", True))
    more = [licence.take("leads").get("ok") for _ in range(13)]
    check("  today's 7 still count: 13 more make Growth's 20", (more, licence.take("leads").get("ok")), ([True] * 13, False))
    check("  a second website now has its own 20", licence.take("leads", site="another-website").get("ok"), True)
    check("  and a third website is beyond Growth's two", licence.take("leads", site="third-website").get("ok"), False)
    first = licence.take("checks")
    month = licence.take("checks")
    check("one Website Check report a month on Growth", (first.get("ok"), month.get("ok")), (True, False))
    check("  the second says when the plan renews", "This month's Website Check reports are used up" in month.get("error", ""), True)
    w.as_owner()
    publisher.licence_change(w.lic_id, restart=True)
    w.copy("one")
    check("  a new month (a renewal) brings it back", licence.take("checks").get("ok"), True)
    meters = {m["kind"]: m for m in licence.view()["meters"]}
    check("the plan card shows what is used today", (meters["leads"]["used"], meters["leads"]["limit"], meters["leads"]["per"]), (20, 20, "day"))


def test_stopping(w):
    print("\nCancelled or ended means stopped — at once, with the work kept:")
    w.as_owner()
    publisher.licence_change(w.lic_id, status="cancelled", note="asked to stop")
    w.copy("one")
    c = licence.check(force=True)
    check("cancelled: the copy stops at its next check", (c.get("ok"), "cancelled" in c.get("error", "")), (False, True))
    check("  every job is refused, even with the customer's own AI", licence.take("posts").get("ok"), False)
    from pipelines import day_run
    d = day_run.start()
    check("  the day does not start, and says why", (d.get("ok"), d.get("plan"), "renew" in d.get("why", "")), (False, True, True))
    from pipelines import seo_fixer
    check("  nor does the Fix button", seo_fixer.start("title_missing").get("plan"), True)
    w.as_owner()
    publisher.licence_change(w.lic_id, status="active")
    w.copy("one")
    check("started again: it works again", licence.check(force=True).get("ok"), True)
    w.edit(w.key, lambda d: d.update(period_end=int(time.time()) - 86400))
    check("a payment a day late: still working for the grace days", licence.check(force=True).get("ok"), True)
    w.edit(w.key, lambda d: d.update(period_end=int(time.time()) - 4 * 86400))
    e = licence.check(force=True)
    check("past the grace days: ended", (e.get("ok"), "ended" in e.get("error", "")), (False, True))
    w.as_owner()
    publisher.licence_change(w.lic_id, extend_days=30, note="renewed by transfer")
    w.copy("one")
    check("he adds 30 days: working again", licence.check(force=True).get("ok"), True)
    w.as_owner()
    hist = [x["what"] for x in publisher.licences()["licences"][0]["history"]]
    check("his records say what happened, in order",
          [x for x in hist if x in ("bought", "upgraded", "new_period", "cancelled", "reactivated", "renewed")],
          ["bought", "upgraded", "new_period", "cancelled", "reactivated", "renewed"])


def test_one_computer(w):
    print("\nOne key, one computer at a time:")
    w.copy("two", run="bbbb2222")
    r = licence.activate(w.key)
    check("the key entered on a second computer asks to move it", (r.get("ok"), r.get("in_use")), (False, True))
    check("  naming the computer it is on now", [c["name"] for c in r.get("computers", [])], [licence.device_name()])
    m = licence.activate(w.key, move=True, name="New laptop")
    check("moving it: it works here", (m.get("ok"), m.get("working"), m.get("moves_left")), (True, True, 2))
    w.copy("one")
    o = licence.check(force=True)
    check("  and the first computer stops, told the key moved", (o.get("ok"), "another computer" in o.get("error", "")), (False, True))
    check("  its jobs are refused", licence.take("leads").get("ok"), False)
    for i in range(2):
        w.copy("one" if i % 2 == 0 else "two")
        licence.activate(w.key, move=True)
    w.copy("one")
    last = licence.activate(w.key, move=True)
    check("a key moves 3 times a month and no more", (last.get("ok"), "3 times in the last month" in last.get("error", "")), (False, True))
    w.as_owner()
    publisher.licence_unbind(w.lic_id)
    w.copy("one")
    check("he can clear its computers (a new laptop, a reinstall)", licence.activate(w.key).get("working"), True)
    # Two copies at once with the same key: a copied data folder.
    src = Path(w.copies["one"]["AGENCY_DATA"])
    dst = Path(w.h.dir) / "copy-clone" / "data"
    shutil.copytree(src, dst)
    w.copies["clone"] = {**w.copies["one"], "AGENCY_DATA": str(dst)}
    w.copy("one", run="aaaa1111")
    check("the first copy is running", licence.check(force=True).get("ok"), True)
    w.copy("clone", run="cccc3333")
    licence.check(force=True)
    w.copy("one", run="aaaa1111")
    busy = licence.check(force=True)
    check("a copied folder running at the same time: only one of them works",
          (busy.get("ok"), "in use on another computer right now" in (busy.get("error") or "")), (False, True))
    w.copy("one", run="dddd4444")
    st = json.loads(workspace.read_secret(workspace.config_path(licence.STATE)))
    st["run"] = "cccc3333"
    workspace.write_secret(workspace.config_path(licence.STATE), json.dumps(st))
    check("  the same office opened again carries on (it names the run before)", licence.check(force=True).get("ok"), True)


def test_offline(w):
    print("\nWithout the internet for a while:")
    w.as_owner()
    publisher.licence_unbind(w.lic_id)
    w.copy("one", run="eeee5555")
    licence.activate(w.key)
    licence.check(force=True)
    used = licence.view()["meters"]
    leads_now = {m["kind"]: m["used"] for m in used}["leads"]
    os.environ["GLASSWORK_LICENCE_HOME"] = "http://127.0.0.1:9"
    licence._DOWN_UNTIL = 0
    check("the copy keeps working from the Home's signed answer", licence.working().get("ok"), True)
    t = licence.take("mails")
    check("  within the plan, counted here", (t.get("ok"), t.get("offline")), (True, True))
    left = 15 - 1 - {m["kind"]: m["used"] for m in used}["mails"]
    got = [licence.take("mails").get("ok") for _ in range(left)]
    over = licence.take("mails")
    check("  and the plan still holds offline", (all(got), over.get("ok"), "used up" in over.get("error", "")), (True, False, True))
    os.environ.pop("GLASSWORK_LICENCE_HOME", None)
    licence._DOWN_UNTIL = 0
    licence.check(force=True)
    check("back online, the Home counts the work done offline",
          {m["kind"]: m["used"] for m in licence.view()["meters"]}["mails"], 15)
    check("  (leads untouched)", {m["kind"]: m["used"] for m in licence.view()["meters"]}["leads"], leads_now)
    w.as_owner()
    publisher.licence_settings({"offline_days": 0})
    w.copy("one", run="eeee5555")
    licence.check(force=True)
    os.environ["GLASSWORK_LICENCE_HOME"] = "http://127.0.0.1:9"
    licence._DOWN_UNTIL = 0
    off = licence.working()
    check("past the days a copy may work alone, it pauses and says why",
          (off.get("ok"), "reach its plan server" in off.get("error", "")), (False, True))
    os.environ.pop("GLASSWORK_LICENCE_HOME", None)
    w.as_owner()
    publisher.licence_settings({"offline_days": 3})


def test_cannot_be_edited_here(w):
    print("\nThe plan cannot be edited on the customer's computer:")
    w.copy("one", run="eeee5555")
    licence.check(force=True)
    path = workspace.config_path(licence.STATE)
    st = json.loads(workspace.read_secret(path))
    real = st["token"]
    body, sig = st["token"].split(".")
    plan = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    plan["limits"]["leads"]["n"] = 100000
    plan["status"] = "active"
    forged = base64.urlsafe_b64encode(json.dumps(plan).encode()).decode().rstrip("=") + "." + sig
    check("a plan changed by hand fails the signature", licence.verify(forged), {})
    st["token"] = forged
    workspace.write_secret(path, json.dumps(st))
    os.environ["GLASSWORK_LICENCE_HOME"] = "http://127.0.0.1:9"
    licence._DOWN_UNTIL = 0
    check("  so offline it counts as no answer at all", licence.working().get("ok"), False)
    os.environ.pop("GLASSWORK_LICENCE_HOME", None)
    licence._DOWN_UNTIL = 0
    check("  and online the Home's real answer replaces it", licence.check(force=True).get("ok"), True)
    w.copy("two", run="ffff6666")
    check("one computer's signed answer does not work on another", licence.verify(real), {})


def test_gates(w):
    print("\nEvery job asks first:")
    from coordination import board
    from pipelines import content_write, leads_sources, mail, website_check
    real_db = board.DB
    d = Path(tempfile.mkdtemp(prefix="lic-db-"))
    import sqlite3
    con = sqlite3.connect(d / "agency.db")
    con.executescript((Path(__file__).parent / "schema.sql").read_text(encoding="utf-8"))
    con.close()
    board.DB = d / "agency.db"
    try:
        w.as_owner()
        fresh = publisher.licence_new("starter", "omar@example.com", days=30)
        w.copy("gates", run="abab1212", sites=(("Omar Glass", "https://omar-glass.example"),))
        licence.activate(fresh["key"])
        biz = [{"company": f"Marina Fitout {i} LLC", "segment": "fitout_contractor", "area": "Dubai Marina",
                "email": f"info@marinafitout{i}.ae", "source": "Dubai Chamber"} for i in range(10)]
        got = leads_sources.intake(biz)
        check("the Hunter stores 7 new leads on Starter and stops", len(got["stored"]), 7)
        check("  saying why, and how many waited", (bool(got.get("plan_stop")), got.get("not_stored")), (True, 3))
        again = leads_sources.intake(biz[:3])
        check("  a lead already in the cabinet is never counted against the plan", len(again["duplicates"]), 3)
        check("the day's emails never above the plan's", mail.daily_cap(), 5)
        for _ in range(5):
            licence.take("mails")
        os.environ.update(SMTP_HOST="smtp.example.invalid", SMTP_USER="me@omar-glass.example")
        sent = mail._smtp_send({"id": 1, "email": "info@marinafitout1.ae", "area": "Dubai Marina", "country": "AE"}, "Subject: Hello\n\nHi")
        check("  the 6th email is not sent, and says it is the plan", (sent.get("ok"), sent.get("plan"), "used up" in sent.get("error", "")),
              (False, True, True))
        os.environ.pop("SMTP_HOST", None)
        os.environ.pop("SMTP_USER", None)
        check("posts on Starter: 300 to 400 words", content_write.length_words("blog"), "300 to 400 words")
        long = {"html": "".join(f"<h2>Part {i}</h2><p>" + " ".join(["glass"] * 60) + "</p>" for i in range(15))}
        cut = content_write.trim_to_plan(long)
        check("  a longer draft ends at the last whole paragraph that fits",
              (content_write.words_in(cut) <= 400, cut["html"].endswith("</p>")), (True, True))
        check("  the writer is told when it runs long",
              any("no more than 400" in p for p in content_write.voice_problems({"html": "<p>" + " ".join(["word"] * 600) + "</p>"}, "blog")), True)
        wc = website_check.new("https://example.org")
        check("Website Check refused on Starter", (wc.get("ok"), wc.get("plan")), (False, True))
        acct = os.environ["AGENCY_ACCOUNT"]
        more = workspace.add_site(acct, "Second shop", "https://second.example")
        check("a second website refused on a 1-website plan", (more.get("ok"), more.get("plan")), (False, True))
        png = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
        website_check.save_brand(name="Omar Glass", logo=png)
        check("his logo is kept, but not on the reports on Starter", (website_check.brand().get("name"), website_check.brand().get("logo")), ("Omar Glass", ""))
        w.as_owner()
        publisher.licence_change(fresh["licence"]["id"], plan="pro")
        w.copy("gates", run="abab1212")
        licence.check(force=True)
        check("on Pro his logo is on them", website_check.brand().get("logo"), png)
        check("  posts of up to 1,200 words", content_write.length_words("blog"), "900 to 1,200 words")
        check("  and a second website is allowed", workspace.add_site(acct, "Second shop", "https://second.example").get("ok"), True)
    finally:
        board.DB = real_db
        shutil.rmtree(d, ignore_errors=True)


def test_stripe(w):
    print("\nStripe tells the Home who bought, upgraded, renewed and cancelled:")
    w.as_owner()
    s = publisher.licence_settings({"stripe_secret": "whsec_test_secret", "cancel_effect": "now",
                                    "prices": {"price_starter": {"plan": "starter"}, "price_growth": {"plan": "growth"},
                                               "price_pro_ai": {"plan": "pro", "variant": "ai"}}})
    check("his Stripe settings are saved; the secret is never read back", (s.get("ok"), s.get("has_stripe_secret"), "stripe_secret" in s), (True, True, False))
    check("  he is given the two addresses to put in Stripe", ("do=stripe" in s.get("webhook_url", ""), "{CHECKOUT_SESSION_ID}" in s.get("welcome_url", "")), (True, True))
    before = len(publisher.licences()["licences"])
    trial_end = int(time.time()) + 14 * 86400
    check("a message not signed by Stripe changes nothing",
          (w.stripe({"type": "customer.subscription.created", "data": {"object": _sub("sub_x", "cus_x", "price_pro_ai")}}, sign=False),
           len(publisher.licences()["licences"])), (400, before))
    check("  nor one signed with the wrong secret, or an old one",
          (w.stripe({"type": "checkout.session.completed", "data": {"object": {}}}, secret="whsec_wrong"),
           w.stripe({"type": "checkout.session.completed", "data": {"object": {}}}, t=time.time() - 3600)), (400, 400))
    w.stripe({"type": "checkout.session.completed", "data": {"object": {"id": "cs_test_1", "customer": "cus_1", "subscription": "sub_1",
                                                                        "customer_details": {"email": "Buyer@Example.com"}}}})
    w.stripe({"type": "customer.subscription.created", "data": {"object": _sub("sub_1", "cus_1", "price_starter", "trialing", trial_end=trial_end)}})
    lic = next(x for x in publisher.licences()["licences"] if x["email"] == "buyer@example.com")
    check("a new customer on a 14-day trial of Starter", (lic["plan"], lic["status"], lic["source"], lic["period_end"]), ("starter", "trial", "stripe", trial_end))
    page = w.page("/glasswork-home/index.php?do=welcome&session=cs_test_1")
    key = (re.search(r"BTX(-[0-9A-Z]{5}){4}", page) or [""])[0]
    check("  the page they land on after paying shows their key", bool(key), True)
    check("  a stranger's session shows nothing", "BTX-" in w.page("/glasswork-home/index.php?do=welcome&session=cs_nobody"), False)
    w.copy("buyer", run="b0b0b0b0")
    check("  and the key works in their copy", (licence.activate(key).get("working"), licence.view().get("status")), (True, "trial"))
    now = int(time.time())
    w.stripe({"type": "customer.subscription.updated", "data": {"object": _sub("sub_1", "cus_1", "price_growth", "active", now, now + 30 * 86400)}})
    w.as_owner()
    lic = next(x for x in publisher.licences()["licences"] if x["email"] == "buyer@example.com")
    check("they pay and upgrade to Growth: the Home knows, and since when",
          (lic["plan"], lic["status"], lic["plan_since"] >= now - 5, "upgraded" in [h["what"] for h in lic["history"]]), ("growth", "active", True, True))
    w.copy("buyer", run="b0b0b0b0")
    check("  their copy has Growth's limits at its next check", (licence.check(force=True).get("ok"), licence.cap("leads")), (True, 20))
    w.stripe({"type": "customer.subscription.updated", "data": {"object": _sub("sub_1", "cus_1", "price_growth", "active", now, now + 30 * 86400, cancel_end=True)}})
    c = licence.check(force=True)
    check("they cancel: stopped at once (his choice)", (c.get("ok"), "cancelled" in c.get("error", "")), (False, True))
    w.stripe({"type": "customer.subscription.updated", "data": {"object": _sub("sub_1", "cus_1", "price_growth", "active", now, now + 30 * 86400)}})
    check("  they change their mind: working again", licence.check(force=True).get("ok"), True)
    w.as_owner()
    publisher.licence_settings({"cancel_effect": "period_end"})
    w.stripe({"type": "customer.subscription.updated", "data": {"object": _sub("sub_1", "cus_1", "price_growth", "active", now, now + 30 * 86400, cancel_end=True)}})
    w.copy("buyer", run="b0b0b0b0")
    licence.check(force=True)
    v = licence.view()
    check("set to stop at the end of the paid month: working until then, the date shown",
          (v.get("working"), v.get("cancel_at")), (True, now + 30 * 86400))
    w.stripe({"type": "invoice.payment_failed", "data": {"object": {"subscription": "sub_1", "customer": "cus_1"}}})
    w.stripe({"type": "customer.subscription.updated", "data": {"object": _sub("sub_1", "cus_1", "price_growth", "past_due", now, now + 30 * 86400)}})
    check("a failed payment: overdue, still working for the grace days", (licence.check(force=True).get("ok"), licence.view().get("status")), (True, "past_due"))
    w.stripe({"type": "customer.subscription.deleted", "data": {"object": _sub("sub_1", "cus_1", "price_growth", "canceled", now, now + 30 * 86400)}})
    check("Stripe ends the subscription: stopped", licence.check(force=True).get("ok"), False)
    w.as_owner()
    publisher.licence_settings({"cancel_effect": "now"})
    # The same person again, for a second trial — the messages in the other order this time.
    w.stripe({"type": "customer.subscription.created", "data": {"object": _sub("sub_2", "cus_2", "price_starter", "trialing", trial_end=trial_end)}})
    w.stripe({"type": "checkout.session.completed", "data": {"object": {"id": "cs_test_2", "customer": "cus_2", "subscription": "sub_2",
                                                                        "customer_details": {"email": "buyer@example.com"}}}})
    second = [x for x in publisher.licences()["licences"] if x["email"] == "buyer@example.com" and x["id"] != lic["id"]]
    check("one trial per customer: a second trial for the same address is refused",
          [x["status"] for x in second], ["trial_used"])
    k2 = (re.search(r"BTX(-[0-9A-Z]{5}){4}", w.page("/glasswork-home/index.php?do=welcome&session=cs_test_2")) or [""])[0]
    w.copy("buyer2", run="b1b1b1b1")
    r = licence.activate(k2)
    check("  their copy says so plainly", (r.get("working"), "already had a trial" in r.get("error", "")), (False, True))


def test_nothing_of_the_work_reaches_the_home(w):
    print("\nWhat the Home keeps:")
    folder = w.h.web / "glasswork-home" / "private" / "licences"
    text = "".join(p.read_text(encoding="utf-8", errors="replace") for p in folder.glob("*.php") if not p.name.startswith("welcome-"))
    check("no lead, website address or email the program sent is kept there",
          any(x in text for x in ("marinafitout", "omar-glass.example", "shop.example", "Hello")), False)
    check("  no key is kept there either — only fingerprints", "BTX-" in text, False)
    held = [json.loads(p.read_text(encoding="utf-8").split("\n", 1)[1]) for p in folder.glob("welcome-*.php")]
    check("  except a bought key for its thank-you page, for two days at most",
          (bool(held), all(x["until"] <= time.time() + 2 * 86400 + 5 for x in held)), (True, True))


def main() -> int:
    from . import publish_tests
    if not shutil.which("php"):
        print("SKIPPED: PHP is not installed here, so the Home cannot be run.")
        return 0
    stray = Path(__file__).parents[1] / "config" / licence.STATE
    was_there = stray.exists()
    with Home() as h:
        w = World(h)
        try:
            for t in (test_switching_plans_on, test_a_key_on_a_computer, test_a_plan_is_a_plan, test_stopping,
                      test_one_computer, test_offline, test_cannot_be_edited_here, test_gates, test_stripe,
                      test_nothing_of_the_work_reaches_the_home):
                try:
                    t(w)
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    check(f"{t.__name__} ran to the end", repr(e), "no error")
                finally:
                    w.as_owner()
        finally:
            w.as_owner()
    publish_tests._fail.clear()
    check("\nthe program's own config folder was never written", stray.exists(), was_there)
    print("\nAll green." if not _fail else f"\n{len(_fail)} FAILED: " + "; ".join(_fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
