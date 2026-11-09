"""
wp_fix_tests.py — run: python -m pipelines.wp_fix_tests

The Fix button and the Botix helper on WordPress. A stand-in WordPress
(pipelines/testsites/mini-wordpress.php) is served by PHP on this machine with
the REAL helper plugin (paste-into-your-site/botix-helper.php) inside it, and
the agency talks to it the way it talks to a real one: the REST API, with an
Application Password. Nothing reaches the internet.

"make wordpress sites fixes too with fix button everytime when you will make
 any change or will update that should also be updated and build for the
 wordpress too"

What is protected:
  - with no helper installed, Fix says exactly that and how to install it, and
    shows the words it would use — it never pretends;
  - with the helper: repeated titles and missing descriptions are set per page
    and read off the live pages; a page hidden by its own setting comes back;
    WordPress's "Discourage search engines" box is unticked when it hides the
    whole site; robots.txt gains the sitemap; llms.txt is published;
  - a picture's description goes into the page's own content (a revision kept),
    with the block editor's markup intact; the theme's footer year is "your
    theme", never "fixed";
  - every write is the Releaser's; a change that breaks the home page is put
    back at once;
  - the helper updates itself from Botix — whole, newer, really itself, runnable
    — keeping the old copy, and refuses anything else;
  - every fix the button can do has a WordPress route.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from coordination import board
from . import connect, it_release, seo_fixer as F, site_api as S, site_blueprint as bp, wp_bridge

ROOT = Path(__file__).parents[1]
STUB = Path(__file__).parent / "testsites" / "mini-wordpress.php"
USER, PASSWORD = "owner", "abcd efgh ijkl mnop qrst uvwx"
_fail = []

WORDS = ("<!-- wp:paragraph --><p>We measure, make and fit frameless shower screens, glass partitions and aluminium "
         "doors. Every job is measured on site by the person who will fit it. Toughened glass is cut to the "
         "millimetre and checked before it leaves the workshop. Fitting usually takes one morning, and we leave the "
         "room clean. Hinges come in chrome, brushed nickel or matt black, and every quote lists the glass "
         "thickness.</p><!-- /wp:paragraph -->")


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _h1(text):
    return f'<!-- wp:heading {{"level":1}} --><h1 class="wp-block-heading">{text}</h1><!-- /wp:heading -->'


def _data(blog_public=1):
    return {
        "user": USER, "password": PASSWORD.replace(" ", ""), "front": 10,
        "options": {"blog_public": blog_public},
        "pages": {
            "10": {"slug": "home", "title": "Home", "content": _h1("Glass and aluminium work in Dubai") + WORDS
                   + '<!-- wp:image {"id":5} --><figure class="wp-block-image"><img src="/wp-content/uploads/'
                     'frameless-shower-screen-marina.jpg" class="wp-image-5"/></figure><!-- /wp:image -->'},
            "11": {"slug": "about", "title": "Glass work", "content": _h1("About Glass Co, fitters since 2012") + WORDS},
            "12": {"slug": "services", "title": "Glass work", "content": _h1("Shower screens, partitions and doors")
                   + WORDS},
        },
        "posts": {"50": {"slug": "doors", "title": "Aluminium doors made to measure in Dubai", "noindex": True,
                         "content": _h1("Aluminium doors") + WORDS}},
    }


class WordPress:
    """The stand-in WordPress, served by PHP, with or without the helper plugin."""

    def __init__(self, helper=True, blog_public=1):
        self.helper, self.blog_public = helper, blog_public

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp())
        self.web = self.dir / "public_html"
        self.web.mkdir()
        shutil.copy(STUB, self.web / "index.php")
        (self.web / "data.json").write_text(json.dumps(_data(self.blog_public)), encoding="utf-8")
        self.plugin_dir = self.web / "wp-content" / "plugins" / "botix-helper"
        self.plugin_dir.mkdir(parents=True)
        if self.helper:
            self.install()
        self.port = _port()
        self.proc = subprocess.Popen(["php", "-S", f"127.0.0.1:{self.port}", "-t", str(self.web),
                                      str(self.web / "index.php")],
                                     env=dict(os.environ, PHP_CLI_SERVER_WORKERS="4"),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.2)
        self.home = f"http://127.0.0.1:{self.port}"
        self.env = {k: os.environ.get(k) for k in ("SITE_MODE", "SITE_API_BASE", "SITE_API_TOKEN", "SITE_URL",
                                                   "SITE_CONNECT", "WP_USER", "WP_APP_PASSWORD")}
        os.environ.update({"SITE_MODE": "full", "SITE_URL": self.home, "SITE_CONNECT": "wordpress",
                           "WP_USER": USER, "WP_APP_PASSWORD": wp_bridge.normalise_password(PASSWORD),
                           "SITE_API_BASE": "", "SITE_API_TOKEN": ""})
        self.real_db = board.DB
        board.DB = str(self.dir / "agency.db")
        board.ensure_schema()
        S._SEEN.clear()
        return self

    def install(self, source=None):
        shutil.copy(source or S.WP_HELPER, self.plugin_dir / "botix-helper.php")

    def __exit__(self, *a):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        board.DB = self.real_db
        shutil.rmtree(self.dir, ignore_errors=True)

    def data(self):
        return json.loads((self.web / "data.json").read_text(encoding="utf-8"))

    def set_data(self, d):
        (self.web / "data.json").write_text(json.dumps(d), encoding="utf-8")

    def live(self, path="/"):
        import requests
        return requests.get(self.home + path, timeout=10).text


def _release(limit=10):
    return it_release.run(limit=limit, skip_tests=True)


def _think(sector, messages, **kw):
    prompt, out = messages[0]["content"], {}
    for line in prompt.splitlines():
        if not line.startswith("{"):
            continue
        it = json.loads(line)
        h = it.get("heading") or it.get("page_heading") or "Glass work"
        out[it["key"]] = ((h + " in Dubai | Glass Co")[:58] if "page title" in prompt else
                          "A frameless shower screen fitted in a Dubai flat" if "picture description" in prompt else
                          (h + ". Measured, made and fitted by our own team across Dubai and Sharjah, with clean "
                               "seals and a tidy finish.")[:155])
    return {"ok": True, "output": json.dumps(out)}


def _checked(w):
    r = bp.check(home=w.home, force=True)
    return r, {g["code"]: g for g in r["gaps"]}


def _fix(code, **kw):
    st = F.start(code)
    assert st.get("ok"), st
    kw.setdefault("release", _release)
    return F.run(st["id"], **kw)


def _title(html):
    import re
    m = re.search(r"<title>(.*?)</title>", html)
    return m.group(1) if m else ""


def test_every_fix_has_a_wordpress_route():
    print("\nEvery fix the button can do has a WordPress route:")
    check("no fix without one", sorted(set(F.AUTO) - set(F.WP_ROUTE)), [])
    check("  and none left over", sorted(set(F.WP_ROUTE) - set(F.AUTO)), [])
    check("the helper plugin is valid PHP", subprocess.run(["php", "-l", str(S.WP_HELPER)], capture_output=True,
                                                           text=True, encoding="utf-8").returncode, 0)
    z = connect.helper_zip()
    import io
    import zipfile
    names = zipfile.ZipFile(io.BytesIO(z["zip"])).namelist()
    check("it downloads as a zip WordPress installs from Plugins → Add New → Upload",
          (z["ok"], "botix-helper/botix-helper.php" in names), (True, True))


def test_without_the_helper():
    print("\nWithout the helper, Fix says so, how to install it, and the words it would use:")
    with WordPress(helper=False) as w:
        r, g = _checked(w)
        check("the WordPress site was read", (r["ok"], "core-title-unique" in g), (True, True))
        rep = _fix("core-title-unique", think=_think)
        check("not fixed, and it says why", (rep["state"], "helper plugin is not on your WordPress" in rep["headline"],
                                             "Plugins" in rep["headline"]), ("cannot", True, True))
        check("  with the words it would have used", all(p["values"].get("title") for p in rep["pages"]
                                                         if p["state"] == "skipped"), True)
        check("  and nothing changed on the site", w.data()["options"].get("botix_meta"), None)


def test_titles_descriptions_and_hidden_pages():
    print("\nWith the helper: titles, descriptions and a hidden page, read off the live pages:")
    with WordPress() as w:
        r, g = _checked(w)
        h = S.wp_helper()
        check("the helper answers", (h["ok"], h["helper"], h["seo_plugin"]), (True, S.WP_HELPER_NEWEST, ""))
        named = [p.rstrip("/").rsplit("/", 1)[-1] for p in g["core-title-unique"]["pages"]]
        rep = _fix("core-title-unique", think=_think)
        check("repeated titles: fixed on every page that had it", (rep["state"], rep["fixed"]), ("fixed", len(named)))
        about, services = _title(w.live("/about/")), _title(w.live("/services/"))
        check("  the page that repeated a title now has its own, live", (named, about != services,
                                                                         "Shower screens" in services),
              (["services"], True, True))
        check("  kept by the helper, by address", "/services" in (w.data()["options"].get("botix_meta") or {}), True)
        check("  written by the Releaser", {(c[0], c[1]) for c in _queue()}, {("it-fixer", "applied")})

        rep = _fix("core-description-present", think=_think)
        check("missing descriptions: fixed", rep["state"], "fixed")
        check("  printed by the helper (no SEO plugin here)", 'name="description" content="Shower screens' in
              w.live("/services/"), True)

        rep = _fix("core-noindex")
        check("a post hidden by its own setting is listed again", (rep["state"], "noindex" in w.live("/doors/")),
              ("fixed", False))


def test_whole_site_hidden():
    print("\nWordPress's 'Discourage search engines' box hiding the whole site:")
    with WordPress(blog_public=0) as w:
        d = w.data()
        d["posts"]["50"]["noindex"] = False        # hidden only by the box, this time
        w.set_data(d)
        r, g = _checked(w)
        check("every page hidden is reported, not 'could not be read'", (r["ok"], r.get("all_hidden"),
                                                                         "core-noindex" in g), (True, True, True))
        check("  and the score is held down", r["seo"]["score"] <= 20, True)
        rep = _fix("core-noindex")
        check("Fix unticks the box: fixed on the live site", (rep["state"], w.data()["options"]["blog_public"],
                                                              "noindex" in w.live("/")), ("fixed", 1, False))
        check("  and says what it did", any("Discourage search engines" in d for p in rep["pages"]
                                            for d in p["did"]), True)
    with WordPress(blog_public=0) as w:
        r, g = _checked(w)
        rep = _fix("core-noindex")
        rows = {x["path"]: x for x in rep["pages"]}
        check("a post also hidden by its own setting is not counted as fixed by unticking the box",
              (rep["state"], rows.get("/doors/", {}).get("state")), ("partly", "still"))


def test_content_robots_and_llms():
    print("\nA picture in the page's content, the theme's footer, robots.txt and llms.txt:")
    with WordPress() as w:
        r, g = _checked(w)
        rep = _fix("images-alt", think=_think)
        home = w.data()["pages"]["10"]
        check("the picture is described in the page's own content", (rep["state"], 'alt="A frameless shower screen'
                                                                     in home["content"]), ("fixed", True))
        check("  the version before kept as a revision", len(home.get("revs") or []), 1)
        check("  and the block editor's markup left whole", ('<!-- wp:image {"id":5} -->' in home["content"],
                                                             '<!-- wp:heading {"level":1} -->' in home["content"]),
              (True, True))
        if "content-old-copyright" in g:
            rep = _fix("content-old-copyright")
            check("the footer year is in the theme: never called fixed", (rep["state"], "theme" in json.dumps(
                rep["pages"])), ("cannot", True))
        check("robots.txt has no sitemap line yet", "robots_no_sitemap" in g, True)
        rep = _fix("robots_no_sitemap")
        check("Fix adds it through the helper, read back live", (rep["state"], "Sitemap: " + w.home + "/wp-sitemap.xml"
                                                                 in w.live("/robots.txt")), ("fixed", True))
        rep = _fix("llms_missing")
        check("llms.txt is published through the helper", (rep["state"], w.live("/llms.txt").startswith("# ")),
              ("fixed", True))


def test_put_back_and_self_update():
    print("\nA change that breaks the home page is put back; the helper updates itself:")
    with WordPress() as w:
        r, g = _checked(w)
        real = F._fetcher()
        broke = {"on": False}

        def release_then_break(limit=10):
            out = _release(limit)
            broke["on"] = True
            return out

        def fetch(url):
            if broke["on"] and url.rstrip("/") == w.home:
                return 500, "", {}, url
            return real(url)
        rep = _fix("core-title-unique", think=_think, fetch=fetch, release=release_then_break)
        check("put back at once, and said", (rep["state"], "put back" in rep["headline"],
                                             w.data()["options"].get("botix_meta") or {}), ("not_fixed", True, {}))

        v2 = w.dir / "botix-helper-v2.php"
        v2.write_text(S.WP_HELPER.read_text(encoding="utf-8").replace(
            f"const BOTIX_HELPER = {S.WP_HELPER_NEWEST};", f"const BOTIX_HELPER = {S.WP_HELPER_NEWEST + 1};"),
            encoding="utf-8")
        real_file, real_newest = S.WP_HELPER, S.WP_HELPER_NEWEST
        S.WP_HELPER, S.WP_HELPER_NEWEST = v2, real_newest + 1
        try:
            up = S.update_wp_helper(agent=S.AGENT)
            check("a newer helper is taken and answers as the new version", (up["ok"], up.get("helper")),
                  (True, real_newest + 1))
            check("  the old copy kept beside it", (w.plugin_dir / f"botix-helper-v{real_newest}.php.bak").exists(),
                  True)
            check("  only the Releaser may send it", S.update_wp_helper(agent="it-fixer")["ok"], False)
            bad = w.dir / "botix-helper-v3.php"
            bad.write_text(v2.read_text(encoding="utf-8").replace(
                f"const BOTIX_HELPER = {real_newest + 1};", f"const BOTIX_HELPER = {real_newest + 2};")
                + "\nthis is not php (", encoding="utf-8")
            S.WP_HELPER, S.WP_HELPER_NEWEST = bad, real_newest + 2
            before = (w.plugin_dir / "botix-helper.php").read_text(encoding="utf-8")
            up = S.update_wp_helper(agent=S.AGENT)
            check("a copy that would not run is refused, and nothing changes",
                  (up["ok"], "would not run" in (up.get("error") or ""),
                   (w.plugin_dir / "botix-helper.php").read_text(encoding="utf-8") == before), (False, True, True))
        finally:
            S.WP_HELPER, S.WP_HELPER_NEWEST = real_file, real_newest


def _queue():
    with board._conn() as c:
        return c.execute("SELECT agent, status FROM site_queue").fetchall()


def main():
    if not shutil.which("php"):
        test_every_fix_has_a_wordpress_route.__doc__  # the route check needs no website
        print("SKIPPED the WordPress website tests: PHP is not installed here.")
        print("\nwordpress fixes: the website tests need PHP.")
        return 0
    for t in (test_every_fix_has_a_wordpress_route, test_without_the_helper, test_titles_descriptions_and_hidden_pages,
              test_whole_site_hidden, test_content_robots_and_llms, test_put_back_and_self_update):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("wordpress fixes: through the helper and the REST API, checked on the live pages, reported as they are.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
