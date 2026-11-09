#!/usr/bin/env bash
# install.sh - installs the program on a Mac or on Linux.
#
#   Mac:    double-click install/Install-Mac.command
#   Linux:  bash install/install.sh
#
# What it does, in order, saying each step as it goes:
#   1. Finds Python 3.10 or newer and Node.js 18 or newer. On a Mac with
#      Homebrew it offers to install what is missing; otherwise it says exactly
#      what to install and stops.
#   2. Makes a private Python folder for the program (.venv inside this folder)
#      and installs its libraries there, so nothing else on the computer changes.
#   3. Installs the office's Node libraries from the lockfile (npm ci).
#   4. Makes "Start <name>.command" in this folder (and a copy on the Mac desktop),
#      then offers to open the program.
#
# Run it again at any time: it only does what is still missing.
#   --check-only   say what is missing and change nothing
#   --yes          answer yes to every question (for unattended installs)

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"
CHECK_ONLY=0
YES=0
for a in "$@"; do
  case "$a" in
    --check-only) CHECK_ONLY=1 ;;
    --yes) YES=1 ;;
  esac
done
NAME="$(sed -n 's/.*"name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' config/product.json 2>/dev/null | head -n1)"
NAME="${NAME:-Glasswork}"
MAC=0; [ "$(uname -s)" = "Darwin" ] && MAC=1

say()  { printf '  %s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
fail() { printf '\n  %s\n' "$*" >&2; exit 1; }
ask()  { [ "$YES" = 1 ] && return 0; read -r -p "  $1 (Y/n) " r; case "${r:-y}" in [Nn]*) return 1 ;; *) return 0 ;; esac; }

find_python() {
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        command -v "$c"; return 0
      fi
    fi
  done
  return 1
}
node_ok() {
  command -v node >/dev/null 2>&1 || return 1
  local major; major="$(node --version | sed 's/^v//; s/\..*//')"
  [ "${major:-0}" -ge 18 ]
}

printf '\nInstalling %s in %s\n' "$NAME" "$ROOT"

step "Python and Node.js"
PY="$(find_python || true)"
NODE_READY=0; node_ok && NODE_READY=1
[ -n "$PY" ] && say "Python $("$PY" -c 'import platform; print(platform.python_version())') found." || say "Python 3.10 or newer: MISSING"
[ "$NODE_READY" = 1 ] && say "Node.js $(node --version) found." || say "Node.js 18 or newer: MISSING"
if [ "$CHECK_ONLY" = 1 ]; then printf '\n'; say "Check only: nothing was changed."; exit 0; fi

if [ -z "$PY" ] || [ "$NODE_READY" = 0 ]; then
  if [ "$MAC" = 1 ] && command -v brew >/dev/null 2>&1; then
    WANT=""; [ -z "$PY" ] && WANT="python@3.12"; [ "$NODE_READY" = 0 ] && WANT="$WANT node"
    ask "Install$WANT with Homebrew?" || fail "Stopped. Install$WANT, then run this again."
    # shellcheck disable=SC2086
    brew install $WANT
    PY="$(find_python || true)"; node_ok && NODE_READY=1
  elif [ "$MAC" = 1 ]; then
    fail "Install Python from https://www.python.org/downloads/ and Node.js (LTS) from https://nodejs.org/, then run this again."
  else
    fail "Install Python 3.10+ (with venv) and Node.js 18+ — on Ubuntu or Debian: sudo apt install python3 python3-venv nodejs npm — then run this again."
  fi
  [ -n "$PY" ] && [ "$NODE_READY" = 1 ] || fail "Python or Node.js still cannot be found. Open a new terminal and run this again."
fi

step "The program's own Python folder"
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv || fail "Could not make the .venv folder. On Linux, install python3-venv and try again."
  say "Made .venv."
else
  say ".venv is already there."
fi
.venv/bin/python -m pip install --upgrade pip --disable-pip-version-check -q
.venv/bin/python -m pip install -r requirements.txt --disable-pip-version-check || fail "Installing the Python libraries failed. The lines above say why."
say "Python libraries installed."

step "The office's Node libraries"
if ! npm ci --no-audit --no-fund; then
  say "npm ci could not use the lockfile; trying npm install."
  npm install --no-audit --no-fund || fail "Installing the Node libraries failed. The lines above say why."
fi
say "Node libraries installed."

step "Shortcuts"
START="$ROOT/Start $NAME.command"
cat > "$START" <<LAUNCH
#!/usr/bin/env bash
cd "\$(dirname "\$0")"
exec ./.venv/bin/python run.py "\$@"
LAUNCH
chmod +x "$START"
say "Made \"Start $NAME.command\" in this folder."
if [ "$MAC" = 1 ] && [ -d "$HOME/Desktop" ]; then
  cat > "$HOME/Desktop/$NAME.command" <<LAUNCH
#!/usr/bin/env bash
exec "$START" "\$@"
LAUNCH
  chmod +x "$HOME/Desktop/$NAME.command"
  say "Added $NAME to the desktop."
fi

printf '\n  %s is installed.\n' "$NAME"
if [ "$YES" = 0 ] && ask "Open it now?"; then exec "$START"; fi
