"""
app_tests.py — run: python -m coordination.app_tests

"have you not update the ui yet? i opened the program it opened at 3001 and
 the ui was still same not as you showed here"

The new design (ui/app.html) is the front door now, and the previous office
stays at /classic. What is protected here:
  - / serves the new app and /classic the previous office; run.py opens /;
  - every address the new app asks the server for is one the server answers
    (a renamed route would otherwise show an empty screen, not an error);
  - it works in light and dark, follows the computer, and paints its own
    background in both;
  - it loads nothing from anywhere but Google Fonts;
  - data from the agency is written onto the screen escaped, never as markup;
  - the connection summary it reads says read-only or full access, never a key.

"make sure the dashboard should have a full control ui like it was before the
 sectors and agents ... which agent is working properly and which agent is
 failed at work and also as full control of agents like to chat with the agents"

And for the agency floor (#agency):
  - every desk the classic office drew is on the new floor, and every desk on
    it is one the agent chat knows — so no desk answers on somebody else's
    brain;
  - every sector the server reports has a room, on the cards and on the plan;
  - the three kinds of red are named, not guessed: failed, stalled, held;
  - talking to a desk sends its id, never its label;
  - the side panel is a dialog that Escape closes, and the floor refreshes
    itself but never while something is being typed on it;
  - Activity and Results are reachable from the new app, not only /classic.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


APP = (ROOT / "ui" / "app.html").read_text(encoding="utf-8")
# The publisher's users room, on his own copy only (it is left out of every
# copy he gives out). Where it is, it is held to the same rules as the app.
PUB_FILE = ROOT / "ui" / "publisher.js"
PUBJS = PUB_FILE.read_text(encoding="utf-8") if PUB_FILE.exists() else ""
SERVER = (ROOT / "server.js").read_text(encoding="utf-8")
RUN = (ROOT / "run.py").read_text(encoding="utf-8")


def test_the_front_door():
    print("\nThe new design is the front door; the previous office is kept:")
    check("/ serves the new app",
          'app.get("/", async (_q, r) => r.sendFile(path.join(__dirname, "ui", "app.html")))' in SERVER, True)
    check("/classic serves the previous office",
          'app.get("/classic", async (_q, r) => r.sendFile(path.join(__dirname, "ui", "office.html")))' in SERVER,
          True)
    check("run.py opens the new app", 'url = f"http://localhost:{port}/"' in RUN and "/office.html" not in RUN, True)


def _routes():
    out = set()
    for m in re.finditer(r'app\.(get|post)\("([^"]+)"', SERVER):
        out.add((m.group(1).upper(), m.group(2)))
    return out


def test_every_address_it_asks_for_exists():
    print("\nEvery address the new app asks for is one the server answers:")
    routes = _routes()
    asked = set()
    for m in re.finditer(r"api\('(/api/[^'?]+)(?:\?[^']*)?'(\s*,\s*([^)]+))?\)", APP + PUBJS):
        method = "GET" if not m.group(2) else "POST"
        asked.add((method, m.group(1)))
    # A route with a parameter (/api/review/start/:job) answers every value of it.
    patterns = [(m, re.compile("^" + re.sub(r":[a-zA-Z]+", "[^/]+", p) + "$")) for m, p in routes if ":" in p]
    missing = sorted(f"{m} {p}" for m, p in asked if (m, p) not in routes
                     and not any(m == pm and rx.match(p) for pm, rx in patterns))
    check("it asks for something", len(asked) >= 12, True)
    check("and the server answers all of it", missing, [])


def test_light_and_dark():
    print("\nLight and dark, following the computer, background painted in both:")
    check("the light palette is the default", ":root{" in APP and "--bg:#F3F5F5" in APP, True)
    check("dark follows the computer unless light was chosen",
          '@media (prefers-color-scheme:dark){\n  :root:not([data-theme="light"])' in APP, True)
    check("and dark can be chosen outright", ':root[data-theme="dark"]{' in APP, True)
    check("the page paints its own background", "html,body{margin:0;background:var(--bg)" in APP, True)


def test_nothing_from_elsewhere():
    print("\nIt loads nothing from anywhere but Google Fonts:")
    srcs = re.findall(r'<script[^>]+src="([^"]+)"', APP)
    links = re.findall(r'<link[^>]+href="(https?://[^"]+)"', APP)
    check("no outside scripts", srcs, [])
    check("only Google Fonts", all(u.startswith("https://fonts.g") for u in links), True)


def test_data_is_escaped():
    print("\nWhat the agency says is put on screen as text, never as markup:")
    risky = []
    for m in re.finditer(r"\$\{([^{}]*?(?:\.(?:text|why|what|title|company|summary|url|email|area|head|name|segment|source|page)\b)[^{}]*)\}", APP + PUBJS):
        expr = m.group(1)
        # row(), pill() and panelHead() escape what they are given themselves.
        if not any(h in expr for h in ("esc(", "deskName(", "row(", "pill(", "panelHead(")):
            risky.append(expr.strip()[:60])
    check("every agency field inside the markup goes through esc()", risky, [])


def test_the_users_room_is_his_alone():
    """
    "will the users will see that section YOUR USERS Section? ... the users
     should not have the program back end"
    """
    print("\nThe users room is the publisher's alone:")
    check("the app itself has no users room in it", any(w in APP for w in ("Your users", "/api/publisher/users",
                                                                            "/api/publisher/collect")), False)
    check("  it loads one only when the office says this copy is the publisher's",
          "p && p.ok && p.can_publish" in APP and "s.src = '/publisher.js'" in APP, True)
    check("the office says so only where his side of the program is",
          'const PUBLISHER_HERE = fs.existsSync(path.join(__dirname, "coordination", "publisher.py"));' in SERVER
          and 'if (!PUBLISHER_HERE) return res.json({ ok: true, publisher: false, can_publish: false });' in SERVER, True)
    check("  and everything under it answers 404 where it is not",
          'app.use("/api/publisher", (_q, res, next) => PUBLISHER_HERE ? next()' in SERVER, True)
    if not PUBJS:
        print("  SKIP  this copy has no users room, as a copy given out should not")
        return
    check("the room says where the records are: on this computer", "Kept on this computer — not on your website." in PUBJS, True)
    check("  and collects from the postbox when it is opened", "api('/api/publisher/collect', {})" in PUBJS, True)


def test_the_connection_summary():
    print("\nThe connection summary says how, never with what:")
    m = re.search(r'name: "Website", sub:[^\n]*\n(.*?)\n.*?kind:', SERVER, re.S)
    block = SERVER[SERVER.find('name: "Website"'):SERVER.find('name: "Website"') + 400]
    check("it says read-only or full access", "mode:" in block and "SITE_MODE" in block, True)
    check("and never the token or password", "SITE_API_TOKEN" in block or "WP_APP_PASSWORD" in block, False)
    _ = m


def test_the_agency_floor():
    print("\nThe agency floor: every desk, every sector, every kind of red:")
    sys.path.insert(0, str(ROOT))
    from coordination import agent_chat, state
    block = APP[APP.index("const FLOOR = ["):APP.index("const SLOT_ALIAS")]
    desks = re.findall(r"\['([a-z-]+)', '([a-z]+)', '([A-Z][a-z]+)', '([^']+)'\]", block)
    rooms = re.findall(r"\{key: '([A-Za-z]+)'", block)
    check("the floor has its desks", len(desks) >= 20, True)
    classic = (ROOT / "ui" / "office.html").read_text(encoding="utf-8")
    old = re.findall(r'\{id:"([a-z-]+)"', classic[classic.index("function sectors()"):classic.index("const BLANK_METRICS")])
    check("every desk the classic office drew is on the new floor", sorted(set(old) - {d[0] for d in desks}), [])
    check("every desk is one the agent chat knows",
          [d[1] for d in desks if d[1] not in agent_chat.DESK_SECTOR], [])
    check("every desk's slot is what the server calls it",
          [d[0] for d in desks if state._desk_of(d[0]) != d[1]], [])
    check("every sector the server reports has a room", sorted(k for k, *_ in state.SECTORS), sorted(rooms))
    plan = APP[APP.index("const PLAN = {"):APP.index("function planEdge")]
    check("  and a place on the floor plan", [k for k in rooms if f"{k}: [" not in plan], [])
    look = APP[APP.index("const ST_LOOK"):APP.index("const RDY_LOOK")]
    check("the three kinds of red are named", all(f"{k}: ['stuck'" in look for k in ("failed", "stalled", "held")), True)
    check("talking to a desk sends its id, never its label", "api('/api/chat', {agent: id, message: v})" in APP, True)
    check("the side panel is a dialog", 'id="drawer" role="dialog" aria-modal="true"' in APP, True)
    check("  that Escape closes", "e.key === 'Escape' && FL.open" in APP, True)
    check("the floor refreshes itself, but not while something is typed on it",
          "agency: 10000" in APP and "polling = setInterval(poll, every)" in APP
          and "/^(INPUT|TEXTAREA|SELECT)$/" in APP, True)
    routes = APP[APP.index("const ROUTES = {"):].split("}", 1)[0]
    for r in ("agency", "activity", "results"):
        check(f"#{r} is a screen, and in the menu", r in routes and f'href="#{r}" data-r="{r}"' in APP, True)


def test_mail_content_and_the_shelf():
    print("\nLeads has its mail room, Content its topics and editor, the floor its agents and shelf:")
    for tab in ("leads", "mail", "send", "sent"):
        check(f"Leads has the {tab} tab", f"['{tab}', " in APP[APP.index("const tabs = [['leads'"):][:400], True)
    check("a sent mail can be read in full", "api(`/api/mail/sent/${Number(lead)}`)" in APP, True)
    check("sent mail can be downloaded as CSV", 'href="/api/mail/sent.csv" download' in APP, True)
    check("  and deleted, ticked or all", "reviewAct('sent_delete'," in APP and "reviewAct('sent_delete_all')" in APP, True)
    check("today's topic is chosen from SEO's list or typed",
          "api('/api/content/choose', own ? {topic: own} : {key: CT.pick})" in APP, True)
    check("every post has Delete, a live one Delete from website", "/delete`, {})" in APP and "reviewAct('blog_unpublish'" in APP, True)
    tools = APP[APP.index('role="toolbar" aria-label="Formatting"'):APP.index('id="edBody"')]
    for cmd in ("bold", "italic", "underline", "link", "image", "h2", "insertUnorderedList"):
        check(f"the editor can {cmd}", f'"{cmd}"' in tools or f"'{cmd}'" in tools, True)
    for ctl in ("edSize", "edFont", "edColour"):
        check(f"  and change text with {ctl}", f'id="{ctl}"' in tools, True)
    check("each post shows its SEO score, originality and voice",
          all(w in APP for w in ("SEO score", "Original", "Reads like a person")), True)
    check("the floor draws agents, not dots", "agentFigure(cx, cy, k)" in APP and 'class="lamp-c' not in APP, True)
    sb = APP[APP.index("function shelfButton"):APP.index("function shelfBody")]
    check("the shelf is a button that glows while a tool is in use, with the count",
          "busy ? ' glow' : ''" in sb and '<span class="n mono">${busy}</span>' in sb, True)
    check("  and the glow rests when someone prefers less motion", "@media (prefers-reduced-motion:reduce){.shelf-btn.glow" in APP, True)
    check("the shelf is no longer a card on the floor", 'id="floor-shelf"' not in APP, True)


def main():
    test_the_front_door()
    test_every_address_it_asks_for_exists()
    test_light_and_dark()
    test_nothing_from_elsewhere()
    test_data_is_escaped()
    test_the_users_room_is_his_alone()
    test_the_connection_summary()
    test_the_agency_floor()
    test_mail_content_and_the_shelf()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("All app tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
