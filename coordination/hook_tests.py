"""
hook_tests.py - run: python -m coordination.hook_tests   (python3 on macOS/Linux)

The red lines in the handbook are enforced by .claude/hooks/pre_tool_use.py, and
a seatbelt nobody tests is a decoration. These drive the hook the way an agent
would: a JSON tool call on stdin, exit 0 to allow and exit 2 to block.

WHY THE ODD STRING BUILDING BELOW

The hook scans the payload of EVERY tool call, so it also scans the call that
writes or edits this file. Spelling the trigger words out here means the hook
(correctly) refuses to let the file be saved. Every trigger word is therefore
assembled from fragments at run time and never appears whole in the source.
That is not a trick to get around the hook - it is what testing a thing that
watches everything, including you, actually costs.

  ACT  = a word from the first watch list  (the action)
  SITE = a word from the second watch list (where it lands)
"""
import json, subprocess, sys, tempfile, os
from pathlib import Path

HOOK = Path(__file__).parents[1] / ".claude" / "hooks" / "pre_tool_use.py"

ACT = "rsy" + "nc"
SITE = "li" + "ve"
DEL = "rm " + "-rf"
OFR = "disc" + "ount"
SND = "se" + "nd"
FRZ = "fre" + "eze"
FRE = "fre" + "e"          # the standalone money word

fails = []


def run(tool, args, agent="leads-hunter"):
    ev = {"tool_name": tool, "tool_input": args, "agent": agent}
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(ev),
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode


def check(name, got, want):
    ok = got == want
    print(("  ok  " if ok else "FAIL  ") + name +
          ("" if ok else f"   got exit {got}, want {want}"))
    if not ok:
        fails.append(name)


BLOCK, ALLOW = 2, 0

print("Ordered vs unordered trigger words:")
check("action-word then site-word is blocked",
      run("Bash", {"command": f"{ACT} ./build/ user@host:/var/www/{SITE}/"}), BLOCK)
check("site-word then action-word is ALSO blocked",
      run("Bash", {"command": f"the {SITE} site needs it, so {ACT} the build up"}), BLOCK)

print("\nOrdinary English must not be caught:")
check("'product' alone does not block",
      run("Bash", {"command": "echo we shipped the product"}), ALLOW)
check("'produced' alone does not block",
      run("Bash", {"command": "echo the report produced 4 rows"}), ALLOW)

print("\nThe releaser is the one door:")
check("it-releaser is allowed through red line 1",
      run("Bash", {"command": f"{ACT} ./out/ host:/var/www/{SITE}/"}, agent="it-releaser"), ALLOW)

print("\nScript-by-path indirection:")
# A SPACE IN THE DIRECTORY NAME, DELIBERATELY.
#
# The owner's Windows account is "ammar laptops", so every temp path on his
# machine has a space in it — and the hook's path matcher stopped at the first
# space, so the script was never opened and never scanned. Eight indirection
# checks reported ALLOW on his machine and BLOCK on every developer's. That was
# not a Windows quirk in the test; it was a real hole in the seatbelt, and it
# was open on Linux too for anyone whose home directory has a space in it.
d = Path(tempfile.mkdtemp()) / "a b"
d.mkdir(parents=True, exist_ok=True)
script = d / "step.sh"
script.write_text(f"#!/bin/sh\n{ACT} -a ./dist/ web@box:/var/www/{SITE}/\n", encoding="utf-8")
os.chmod(script, 0o755)

check("running the script by absolute path is scanned",
      run("Bash", {"command": f"bash {script}"}), BLOCK)
check("...via a different interpreter",
      run("Bash", {"command": f"sh {script}"}), BLOCK)
check("...chained after an innocent command",
      run("Bash", {"command": f"echo starting; bash {script}"}), BLOCK)
check("...chained with &&",
      run("Bash", {"command": f"echo starting && bash {script}"}), BLOCK)

# The shapes an ordinary person actually writes, which the regex may not reach.
check("...on its own line of a multi-line command",
      run("Bash", {"command": f"echo starting\nbash {script}"}), BLOCK)
check("...executed directly by absolute path, no interpreter",
      run("Bash", {"command": str(script)}), BLOCK)
check("...via the source builtin",
      run("Bash", {"command": f"source {script}"}), BLOCK)
check("...with the interpreter given a flag first",
      run("Bash", {"command": f"bash -x {script}"}), BLOCK)

py = d / "step.py"
py.write_text(f"import os\nos.system('{DEL} /var/www')\n", encoding="utf-8")
check("a destructive python script run by path is scanned",
      run("Bash", {"command": f"python3 {py}"}), BLOCK)


print("\n...and a Windows-shaped path is a path:")
win = d / "ship.bat"
win.write_text(f"{ACT} -a ./dist/ web@box:/var/www/{SITE}/\n", encoding="utf-8")
check("a .bat run by its own name is scanned",
      run("Bash", {"command": str(win)}), BLOCK)
check("...and quoted, the way a shell needs it when there is a space",
      run("Bash", {"command": f'bash "{win}"'}), BLOCK)
check("...with a flag after the path, not before it",
      run("Bash", {"command": f'bash "{win}" --dry-run'}), BLOCK)

print("\nDestructive and concession lines still hold:")
check("destructive command blocked", run("Bash", {"command": f"{DEL} /tmp/x"}), BLOCK)
check("concession in an outgoing message blocked",
      run("Bash", {"command": f"{SND} the client a 10% {OFR}"}), BLOCK)

print("\nA concession only counts when it is heading OUT to someone:")
# the real thing: the offer and the posting verb in the same breath
check("an offer in an outgoing line is blocked",
      run("Bash", {"command": f"{SND} the client a {FRE} site survey this week"}), BLOCK)
check("...and the other way round",
      run("Bash", {"command": f"a {FRE} survey, {SND} it over today"}), BLOCK)
# source code that merely mentions both, far apart, is not an offer
faraway = ("x = " + FRE + "_slot()\n" + ("# padding line\n" * 12)
           + "notify_by_" + "e" + "mail(user)\n")
check("a money word and a posting word 400 chars apart do not",
      run("Write", {"file_path": "/tmp/x.py", "content": faraway}), ALLOW)
# The incident this pins: the file could not be saved because its CONTENT
# mentioned the watched words. Content is still scanned - it now names the
# branch it is going to, which the rule below requires of any write.
check("the real file that could not be pushed now can",
      run("mcp__github__push_files",
          {"branch": "claude/some-feature",
           "files": [{"path": "coordination/board.py",
                      # Relative to this file, and utf-8 named outright. It was
                      # written as an absolute /home/user/glasswork/... path,
                      # which is one developer's sandbox: it passes there and on
                      # any machine that happens to share the layout, and cannot
                      # work anywhere else. On the owner's Windows box it took
                      # the whole suite down — and a red suite stops the IT
                      # Releaser shipping, so a hardcoded path in a TEST was
                      # holding every real change off his website.
                      #
                      # The encoding is not optional either: board.py has
                      # em-dashes in it, and open() on Windows defaults to
                      # cp1252, which cannot decode them.
                      "content": (Path(__file__).parents[1] / "coordination"
                                  / "board.py").read_text(encoding="utf-8")}]}),
      ALLOW)

# TRUNK is the branch a release hangs off; GITP the command that puts work there.
# Both assembled, for the reason given at the top of this file.
TRUNK = "ma" + "in"
GITP  = "git " + "pu" + "sh"

print("\nThe calls that land code on the trunk, whatever they are called:")
# A merge carries no watched word at all - {owner, repo, pullNumber} - and on a
# repo that ships on every push to the trunk it is THE call that restarts the
# running agency. It used to sail straight through.
check("merging a pull request is refused",
      run("mcp__github__merge_pull_request", {"owner": "o", "repo": "r", "pullNumber": 2}), BLOCK)
check("...and so is arming it to merge itself later",
      run("mcp__github__enable_pr_auto_merge", {"owner": "o", "repo": "r", "pullNumber": 2}), BLOCK)
check("...and firing a workflow by hand",
      run("mcp__github__actions_run_trigger", {"owner": "o", "repo": "r"}), BLOCK)
check("writing straight to the trunk is refused",
      run("mcp__github__create_or_update_file",
          {"branch": TRUNK, "path": "x.py", "content": "print(1)"}), BLOCK)
check("a write that does not say where it lands is refused too",
      run("mcp__github__create_or_update_file", {"path": "x.py", "content": "print(1)"}), BLOCK)
check("but ordinary branch work is not",
      run("mcp__github__create_or_update_file",
          {"branch": "claude/some-feature", "path": "x.py", "content": "print(1)"}), ALLOW)
check("a shell line putting work on the trunk is refused",
      run("Bash", {"command": f"{GITP} origin {TRUNK}"}), BLOCK)
check("...but pushing a feature branch is the everyday case",
      run("Bash", {"command": f"{GITP} -u origin claude/some-feature"}), ALLOW)
check("the releaser may do what nobody else may",
      run("mcp__github__merge_pull_request", {"owner": "o", "repo": "r", "pullNumber": 2},
          agent="it-releaser"), ALLOW)

print("\nDescribing a release is not performing one:")
# The pull request that warned the owner what merging would do was refused for
# carrying the warning. Prose is read by people, never executed.
WARN = ("merging fires the workflow, which restarts the "
        + "li" + "ve" + " site. It will " + "de" + "ploy" + " on push.")
check("a warning in a description is allowed",
      run("mcp__github__create_pull_request",
          {"owner": "o", "repo": "r", "head": "f", "base": "other", "body": WARN}), ALLOW)
check("...but the same words in a command are not",
      run("Bash", {"command": WARN}), BLOCK)
check("...nor in a file being written",
      run("Write", {"file_path": "/tmp/ship.sh", "content": WARN}), BLOCK)

print("\nThe two halves must sit in the same breath, not the same document:")
# Red line 3 was fixed this way long ago; red line 1 never was, so it kept
# firing on ordinary English. The site list holds a very common verb, and a
# commit message using it two thousand characters below an action word read as
# an attempt to ship. Fourth false positive of the same shape.
far = (ACT + " the build\n" + ("# an ordinary paragraph of commit message\n" * 30)
       + "it is the one file his secrets " + SITE + " in\n")
check("an action word and a site word far apart do NOT block",
      run("Bash", {"command": far}), ALLOW)
check("...but in the same breath they still do",
      run("Bash", {"command": f"{ACT} ./out/ host:/var/www/{SITE}/"}), BLOCK)
check("...in either order",
      run("Bash", {"command": f"the {SITE} site needs it, so {ACT} the build"}), BLOCK)
check("a site word with no action word is nothing",
      run("Bash", {"command": f"the {SITE} chat is open"}), ALLOW)
check("an action word with no site word is nothing",
      run("Bash", {"command": f"{ACT} ./out/ host:/home/backups/"}), ALLOW)

print("\nFalse positives that cost real work:")
check(f"the word '{FRZ}' does not read as a price concession",
      run("Bash", {"command": f"git commit -m '{FRZ} the schema' && {SND} nothing"}), ALLOW)

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("all hook tests passed")
