"""The console as a launchd user agent, so it survives a reboot and a kill.

macOS only, and deliberately a *user* agent (`~/Library/LaunchAgents`), never a daemon:
nothing here needs root, and the console must run as the person whose token and rules it
holds. `KeepAlive` restarts it when it dies; `RunAtLoad` starts it when they log in.

The plist holds no secret. The operator token stays in `~/.c3s-circuit-agent/operator-token`
at mode 600 and the console reads it itself — a plist is world-readable.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from . import paths


def gui_target() -> str:
    return f"gui/{os.getuid()}"


def plist_body(console: Path, repo: Path, host: str, port: int,
               python: str | None = None) -> dict:
    env = {
        "C3S_REPO": str(repo),
        "CONSOLE_HOST": host,
        "CONSOLE_PORT": str(port),
        # launchd starts with almost no PATH and does not read a shell profile. `cast`
        # (Foundry) is what the manifest's keccak256 needs; without it the console still
        # runs and says the hash is unavailable.
        "PATH": f"{Path.home()}/.foundry/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    }
    for name in ("REFLEX_CONFIG_DIR", "REFLEX_CARDPUTER", "REFLEX_STATE_FILE", "REFLEX_TOKEN_FILE",
                 "BSC_RPC", "VERIFY_ON_CHAIN"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    return {
        "Label": paths.LABEL,
        "ProgramArguments": [python or sys.executable, "-u", "console.py"],
        "WorkingDirectory": str(console),
        "EnvironmentVariables": env,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 2,
        "ProcessType": "Interactive",
        "StandardOutPath": str(paths.LOG_FILE),
        "StandardErrorPath": str(paths.LOG_FILE),
    }


def write_plist(console: Path, repo: Path, host: str, port: int, python: str | None = None) -> Path:
    paths.PLIST.parent.mkdir(parents=True, exist_ok=True)
    paths.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with paths.PLIST.open("wb") as fh:
        plistlib.dump(plist_body(console, repo, host, port, python), fh)
    return paths.PLIST


def _launchctl(*args: str) -> tuple[int, str]:
    done = subprocess.run(["launchctl", *args], capture_output=True, text=True)
    return done.returncode, (done.stdout + done.stderr).strip()


def loaded() -> bool:
    code, _ = _launchctl("print", f"{gui_target()}/{paths.LABEL}")
    return code == 0


def pid() -> int | None:
    """The pid launchd currently has for the job, if it is running."""
    code, out = _launchctl("print", f"{gui_target()}/{paths.LABEL}")
    if code != 0:
        return None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("pid = "):
            try:
                return int(line.split("=", 1)[1])
            except ValueError:
                return None
    return None


def bootstrap() -> tuple[bool, str]:
    """Load the job. Already loaded is not an error: it is reloaded instead."""
    if loaded():
        _launchctl("bootout", f"{gui_target()}/{paths.LABEL}")
    code, out = _launchctl("bootstrap", gui_target(), str(paths.PLIST))
    if code != 0:
        return False, out or f"launchctl bootstrap exited {code}"
    return True, out


def bootout() -> tuple[bool, str]:
    if not loaded():
        return False, "no launchd job to unload"
    code, out = _launchctl("bootout", f"{gui_target()}/{paths.LABEL}")
    return code == 0, out or ("unloaded" if code == 0 else f"launchctl bootout exited {code}")


def kickstart() -> tuple[bool, str]:
    """Restart the job in place (`-k` kills what is running first)."""
    code, out = _launchctl("kickstart", "-k", f"{gui_target()}/{paths.LABEL}")
    return code == 0, out or ("restarted" if code == 0 else f"launchctl kickstart exited {code}")
