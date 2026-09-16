#!/bin/sh
# Install the `c3s` command from this checkout and start the boundary console.
#
#   sh install.sh              # install, then `c3s up`
#   sh install.sh --lan        # …and bind 0.0.0.0 so a phone on the Wi-Fi can pair
#   sh install.sh --service    # …and install the launchd agent (survives a reboot)
#   sh install.sh --no-start   # install only
#
# It installs into an isolated tool environment (uv, or pipx, or a plain venv under
# ~/.c3s-circuit-agent/venv) — never into the system Python, and never with sudo. Nothing
# here touches a key, a wallet or the network beyond fetching the two Python packages.
set -eu

lan=""; service=""; start="yes"
for arg in "$@"; do
  case "$arg" in
    --lan) lan="--lan" ;;
    --service) service="--service" ;;
    --no-start) start="" ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

here=$(cd "$(dirname "$0")" && pwd)
repo=${C3S_REPO:-$HOME/work/c3s-reflex}
config=${REFLEX_CONFIG_DIR:-$HOME/.c3s-circuit-agent}

echo "reflex-console checkout: $here"

# --- the circuits repository: the compiler and the verified netlist ---------------------
if [ ! -f "$repo/c3s/policy.py" ]; then
  echo "the circuits repository is not at $repo." >&2
  echo "clone https://github.com/BruceLanLan/c3s-reflex-circuits and set C3S_REPO to it," >&2
  echo "then run this again — the console compiles and checks its rules with that repo." >&2
  exit 3
fi
echo "circuits repository:     $repo"

# --- Python 3.10 or newer --------------------------------------------------------------
python=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
    python=$(command -v "$candidate"); break
  fi
done
if [ -z "$python" ]; then
  echo "no Python 3.10 or newer on PATH (this machine's \`python\` is Python 2)." >&2
  echo "install one — \`brew install python@3.12\` or https://www.python.org/downloads/ — and run this again." >&2
  exit 4
fi
echo "python:                  $python ($("$python" -c 'import sys; print(sys.version.split()[0])'))"

# --- install the command ---------------------------------------------------------------
mkdir -p "$config"
if command -v uv >/dev/null 2>&1; then
  echo "installing with uv …"
  uv tool install --force --python "$python" "$here"
  bindir=${UV_TOOL_BIN_DIR:-$HOME/.local/bin}
elif command -v pipx >/dev/null 2>&1; then
  echo "installing with pipx …"
  pipx install --force --python "$python" "$here"
  bindir="$HOME/.local/bin"
else
  echo "no uv and no pipx: installing into $config/venv …"
  "$python" -m venv "$config/venv"
  "$config/venv/bin/python" -m pip install --quiet --upgrade pip
  "$config/venv/bin/python" -m pip install --quiet "$here"
  bindir="$config/venv/bin"
fi

c3s="$bindir/c3s"
if [ ! -x "$c3s" ]; then
  c3s=$(command -v c3s || true)
fi
if [ -z "$c3s" ]; then
  echo "installed, but no \`c3s\` on PATH: add $bindir to PATH (uv and pipx both use ~/.local/bin)." >&2
  exit 5
fi
echo "c3s:                     $c3s"

# Remember this checkout, so `c3s up` from any directory starts this console.
printf '%s\n' "$here" > "$config/console-dir"

if [ -n "$start" ]; then
  echo
  # shellcheck disable=SC2086
  C3S_REPO="$repo" "$c3s" up $lan $service
else
  echo
  echo "installed. Next: c3s up            (add --lan to pair a phone, --service for launchd)"
fi
