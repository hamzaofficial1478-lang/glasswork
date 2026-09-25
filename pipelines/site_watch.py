"""
site_watch.py — look at every page and notice what moved.

    python -m pipelines.site_watch          take a look now
    python -m pipelines.site_watch --json

WHY THIS IS SEPARATE FROM THE CHANGE LOG

coordination/changelog.py records changes and touches no website. This fetches
and touches no database directly. Splitting them that way is deliberate: the
recording of a change must not be able to fail because a site was down, and
the log has to be testable with no network at all — the one thing a change log
must never do is lose a change.

WHAT IT IS FOR

The agency records its own writes at the moment it makes them, so those are
exact. This is the other half, and the half he actually asked for: the pages
change when HE edits them in the file manager, when a web designer touches
something, when a plugin rewrites a template overnight, when a hosting company
restores last Tuesday by mistake. None of that goes through the agency and all
of it is his website changing under him.

Fingerprinting every page and reporting what moved catches all of it. It also
catches the case nobody thinks about until it happens: the agency's own careful
change being quietly overwritten by something else three days later.

THE COST, HONESTLY

One GET per page — nine on his site — because the comparison is on content.
Comparing the size and the timestamp the server reports would be free and
wrong in both directions: touching a file moves the timestamp without changing
a thing, and a rewrite that happens to be the same length moves neither.
"""
import argparse
import json
import sys

from coordination import board, changelog, deskwork
from . import site_api

AGENT = "it-watcher"


def look(s=None) -> dict:
    """Read every page, hand the lot to the log, and report what it said."""
    why = site_api.why_not_ready(s)
    if why:
        # Not set up is idle, not failed — the same rule Algolia with no keys
        # and Places with no key already follow. Read-only is a mode he chose;
        # a missing address or token is a box not yet filled in. Neither is the
        # watcher breaking, and scoring it red put a failed step in every run
        # on a machine without full access. The branch below — configured, and
        # the site will not answer — IS a failure and stays one.
        return {"ok": False, "idle": True, "error": why,
                "why": ("The agency cannot see your files, so it cannot tell "
                        "you what changed in them. " + why)}
    listing = site_api.pages(s=s)
    if not listing.get("ok"):
        return {"ok": False, "error": listing.get("error") or "could not list the pages",
                "why": "Could not read the list of pages from your site: "
                       + str(listing.get("error") or "no detail")}

    # THE LISTING IS NOT THE WHOLE SITE, and the part it misses is the part
    # that matters most. /pages returns the top level only, so includes/header
    # — the one file every page pulls in, and the one file the agency writes
    # to — was not being watched at all. Somebody could have overwritten the
    # header on every page of his site and this would have reported nothing.
    #
    # site_api keeps the list of usual include locations for exactly this
    # reason; find_placeholders already learned the same lesson the hard way.
    names, rows = [], {}
    for row in listing.get("pages") or []:
        if row.get("page"):
            names.append(row["page"])
            rows[row["page"]] = row
    guessed = set()
    for extra in site_api.LIKELY_INCLUDES:
        if extra not in rows:
            names.append(extra)
            rows[extra] = {"page": extra}
            guessed.add(extra)

    seen, unreadable, confirmed_gone = [], [], []
    # A guess that has already been fingerprinted is no longer a guess:
    # it exists, we have seen it, and it must be treated like any other
    # watched page rather than silently dropped when a read fails.
    already_known = set(changelog.fingerprints())
    for name in names:
        row = rows[name]
        got = site_api.read_page(name, s=s)
        if got.get("ok"):
            seen.append({"page": name, "bytes": row.get("bytes"),
                         "changed": row.get("changed"), "html": got.get("html") or ""})
        elif name in guessed and name not in already_known:
            # A guess that is not there. Most sites have one of these locations
            # and not the other eleven, so a missing one is the normal case and
            # says nothing — recording it would fill his log with a dozen files
            # that never existed.
            continue
        else:
            # Listed by the site itself, and then not readable. That IS worth
            # knowing. Recorded WITHOUT html so the comparison falls back to
            # size and timestamp rather than being dropped — dropping it would
            # read as the page having vanished.
            # WHY IT COULD NOT BE READ DECIDES WHAT IT MEANS.
            #
            # A 404 is the site saying the file is not there — that is news. A
            # timeout, a refused connection or a 500 is us failing to ask, and
            # calling that "the page was deleted" is how he got told his header
            # had vanished from a site he had not touched. Only the first kind
            # is passed to the sweep as confirmed gone.
            why = str(got.get("error") or "").lower()
            if "404" in why or "not found" in why or "no such file" in why:
                confirmed_gone.append(name)
            else:
                unreadable.append(name)
                seen.append({"page": name, "bytes": row.get("bytes"),
                             "changed": row.get("changed")})

    out = changelog.sweep(seen, confirmed_gone=confirmed_gone)
    out["unreadable"] = unreadable
    out["confirmed_gone"] = confirmed_gone
    out["ok"] = True
    return out


def run(agent=AGENT) -> dict:
    with board.agent_run(agent) as run_row:
        # it-watcher is two pipelines: it_health checks the site is up and
        # working, this one watches for files being changed underneath us.
        # Only take the jobs this pass can actually answer.
        with deskwork.at_the_desk(
                "IT", agent, run_row,
                takes=lambda j: j.wants("change", "changed", "changes", "watch",
                                        "overwritten", "overwrote", "edited",
                                        "modified", "change log", "changelog",
                                        "diff", "tamper", "tampered")) as job:
            r = look()
            moved = len((r.get("added") or []) + (r.get("edited") or [])
                        + (r.get("gone") or []))
            deskwork.settle(job, {"ok": r.get("ok", True),
                                  "error": r.get("why"),
                                  "summary": r.get("head") or ""},
                            made=moved, what="change to the site|changes to the site")
        run_row.note(r.get("head") or r.get("why") or "could not look")
        return r


def _as_text(r):
    if not r.get("ok"):
        return "SITE WATCH — " + (r.get("why") or r.get("error") or "could not look")
    lines = ["SITE WATCH — " + r["head"]]
    for p in r.get("added") or []:
        lines.append(f"   new      {p}")
    for p in r.get("edited") or []:
        lines.append(f"   changed  {p}   (not by the agency)")
    for p in r.get("gone") or []:
        lines.append(f"   gone     {p}")
    for p in r.get("was_us") or []:
        lines.append(f"   ours     {p}   (already in the log)")
    for p in r.get("unreadable") or []:
        lines.append(f"   ?        {p}   (listed but could not be read)")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Notice what changed on the website.")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = run()
    print(json.dumps(r, indent=2) if a.json else _as_text(r))
    return deskwork.exit_code(r)


if __name__ == "__main__":
    sys.exit(main())
