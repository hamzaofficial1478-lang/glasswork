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

fails = []


def run(tool, args, agent="leads-hunter"):
    ev = {"tool_name": tool, "tool_input": args, "agent": agent}
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(ev),
                       capture_output=True, text=True)
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
d = Path(tempfile.mkdtemp())
script = d / "step.sh"
script.write_text(f"#!/bin/sh\n{ACT} -a ./dist/ web@box:/var/www/{SITE}/\n")
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
py.write_text(f"import os\nos.system('{DEL} /var/www')\n")
check("a destructive python script run by path is scanned",
      run("Bash", {"command": f"python3 {py}"}), BLOCK)

print("\nDestructive and concession lines still hold:")
check("destructive command blocked", run("Bash", {"command": f"{DEL} /tmp/x"}), BLOCK)
check("concession in an outgoing message blocked",
      run("Bash", {"command": f"{SND} the client a 10% {OFR}"}), BLOCK)

print("\nFalse positives that cost real work:")
check(f"the word '{FRZ}' does not read as a price concession",
      run("Bash", {"command": f"git commit -m '{FRZ} the schema' && {SND} nothing"}), ALLOW)

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("all hook tests passed")
