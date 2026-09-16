#!/bin/sh
# Install the `c3s` command from this checkout and start the boundary console.
#
#   sh install.sh               # install, then `c3s up`
#   sh install.sh --lan         # …and bind 0.0.0.0 so a phone on the Wi-Fi can pair
#   sh install.sh --service     # …and install the launchd agent (survives a reboot)
#   sh install.sh --no-browser  # …and do not open the page (for a script or a remote shell)
#   sh install.sh --no-start    # install only
#
# It installs into an isolated tool environment (uv, or pipx, or a plain venv under
# ~/.c3s-circuit-agent/venv) — never into the system Python, and never with sudo. Nothing
# here touches a key, a wallet or the network beyond fetching the two Python packages.
set -eu

lan=""; service=""; browser=""; start="yes"
for arg in "$@"; do
  case "$arg" in
    --lan) lan="--lan" ;;
    --service) service="--service" ;;
    --no-browser) browser="--no-browser" ;;
    --no-start) start="" ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

here=$(cd "$(dirname "$0")" && pwd)
config=${REFLEX_CONFIG_DIR:-$HOME/.c3s-circuit-agent}

# The circuits are in this repository, one level up from console/. C3S_REPO still wins, and
# the older two-checkout layout still resolves, because people have it.
if [ -n "${C3S_REPO:-}" ]; then
  repo=$C3S_REPO
elif [ -f "$here/../c3s/policy.py" ]; then
  repo=$(cd "$here/.." && pwd)
else
  repo=$HOME/work/c3s-reflex
fi

echo "console checkout: $here"

# --- the circuits: the compiler and the verified netlist -------------------------------
if [ ! -f "$repo/c3s/policy.py" ]; then
  echo "cannot find the circuits (c3s/policy.py) at $repo." >&2
  echo "the console compiles and checks every rule with them. They live in this same" >&2
  echo "repository, one level above console/ — so a full clone already has them:" >&2
  echo >&2
  echo "  git clone https://github.com/BruceLanLan/c3s-reflex-circuits" >&2
  echo "  cd c3s-reflex-circuits/console && sh install.sh" >&2
  echo >&2
  echo "If you keep them somewhere else, say where: C3S_REPO=/path/to/it sh install.sh" >&2
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
  # --reinstall as well as --force: without it uv keeps a cached build of this same
  # version number, so re-running after a `git pull` would leave the old `c3s` in place
  # and say nothing about it.
  uv tool install --force --reinstall --python "$python" "$here"
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

# We can start the console by its full path whatever the PATH says, but the next command
# the reader types is `c3s status` in some other shell — and on a fresh macOS account
# ~/.local/bin is not on PATH, so that would be "command not found" with the console
# happily running. Say it here, once, with the line to paste.
case ":$PATH:" in
  *":$bindir:"*) ;;
  *)
    echo
    echo "note: $bindir is not on your PATH, so plain \`c3s\` will not be found."
    echo "      add this to ~/.zshrc (or ~/.bashrc) and open a new shell:"
    echo
    echo "        export PATH=\"$bindir:\$PATH\""
    ;;
esac

if [ -n "$start" ]; then
  echo
  # shellcheck disable=SC2086
  C3S_REPO="$repo" "$c3s" up $lan $service $browser
else
  echo
  echo "installed. Next: c3s up            (add --lan to pair a phone, --service for launchd)"
fi
