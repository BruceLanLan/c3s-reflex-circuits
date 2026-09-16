"""Where everything lives, and how the command line finds it.

Two directories matter and neither is inside this package:

  * the **console checkout** — `console.py`, `static/index.html`, the adapters. The command
    line starts the console from the checkout instead of from a copy inside the wheel, so
    the page you see is the page in the repository and an upgrade is a `git pull`.
  * the **circuits repository** (`c3s-reflex`) — the verified netlist, the encoder and the
    on-chain evaluator. `console.py` puts it on `sys.path`; nothing works without it.

Both are found by the same ladder: an environment variable, then a path remembered from
the last time a checkout was seen, then the current directory, then the usual place under
`~/work`. When neither exists the error says which variable to set, in one sentence.
"""

from __future__ import annotations

import os
import socket
import struct
from pathlib import Path

LABEL = "work.c3s.console"

CONFIG_DIR = Path(os.environ.get("REFLEX_CONFIG_DIR", Path.home() / ".c3s-circuit-agent")).expanduser()
LOG_FILE = CONFIG_DIR / "console.log"
PID_FILE = CONFIG_DIR / "console.pid"
TOKEN_FILE = Path(os.environ.get("REFLEX_TOKEN_FILE", CONFIG_DIR / "operator-token")).expanduser()
CONSOLE_DIR_MEMO = CONFIG_DIR / "console-dir"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

DEFAULT_CONSOLE_DIR = Path.home() / "work" / "reflex-console"
DEFAULT_REPO = Path.home() / "work" / "c3s-reflex"


class Missing(Exception):
    """Something the console cannot run without, with one sentence a person can act on."""


def default_port() -> int:
    return int(os.environ.get("CONSOLE_PORT", "8765"))


def default_host() -> str:
    return os.environ.get("CONSOLE_HOST", "127.0.0.1")


def _looks_like_console(path: Path) -> bool:
    return (path / "console.py").is_file() and (path / "static" / "index.html").is_file()


def console_dir(remember: bool = False) -> Path:
    """The reflex-console checkout the service is started from."""
    tried = []
    candidates = [os.environ.get("REFLEX_CONSOLE_DIR"),
                  CONSOLE_DIR_MEMO.read_text().strip() if CONSOLE_DIR_MEMO.is_file() else None,
                  Path.cwd(), DEFAULT_CONSOLE_DIR]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        tried.append(str(path))
        if _looks_like_console(path):
            if remember:
                remember_console_dir(path)
            return path.resolve()
    raise Missing(
        "cannot find the reflex-console checkout (console.py and static/index.html): set "
        f"REFLEX_CONSOLE_DIR to it, or run c3s from inside it. Tried: {', '.join(tried)}.")


def remember_console_dir(path: Path) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONSOLE_DIR_MEMO.write_text(str(Path(path).resolve()) + "\n")


def circuits_repo() -> Path:
    """The c3s-reflex checkout: the compiler, the netlist and the on-chain evaluator."""
    path = Path(os.environ.get("C3S_REPO", DEFAULT_REPO)).expanduser()
    if not (path / "c3s" / "policy.py").is_file():
        raise Missing(
            f"the circuits repository is not at {path}: clone "
            "https://github.com/BruceLanLan/c3s-reflex-circuits and set C3S_REPO to it "
            "(the console compiles its rules with that repository's checker).")
    return path.resolve()


# --------------------------------------------------------------- this machine's addresses

def _home_network(ip: str) -> bool:
    """A private address of the kind a phone on the same Wi-Fi would have (RFC 1918)."""
    if ip.startswith(("192.168.", "10.")):
        return True
    parts = ip.split(".")
    return len(parts) == 4 and parts[0] == "172" and parts[1].isdigit() and 16 <= int(parts[1]) <= 31


def lan_ipv4() -> list[str]:
    """Every IPv4 address this machine answers to on a network, the likeliest first.

    The same addresses `console.py` adds to its allowed Host names when it is bound to
    0.0.0.0 — the two are deliberately computed the same way (there,
    `_own_ipv4_addresses`), so the address in the pairing QR is one the console accepts.
    Loopback and link-local (169.254.x) are left out: a phone reaches neither.

    The order matters, because the first one goes in the QR. A Wi-Fi or Ethernet address in
    a private range comes first; a VPN tunnel's address (a `utun` in 198.18.x, say) comes
    last, because the phone in the room cannot reach it even though this machine answers to
    it. `c3s pair --ip` overrides the choice, and `c3s pair` lists the rest.
    """
    ranked: list[tuple[int, int, str]] = []
    seen: set[str] = set()

    def add(ip: str, interface: str = "") -> None:
        if (not ip or ip in seen or ip.startswith(("127.", "169.254.")) or ip == "0.0.0.0"):
            return
        seen.add(ip)
        physical = interface.startswith(("en", "eth", "wl"))
        home = _home_network(ip)
        rank = 0 if (home and physical) else 1 if home else 2 if physical else 3
        ranked.append((rank, len(ranked), ip))

    try:  # the address the default route would use; it has no interface name here
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 9))          # a documentation address; nothing is sent
            add(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass
    try:
        import fcntl                                  # every interface that has an IPv4 address
        for _index, name in socket.if_nameindex():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                packed = fcntl.ioctl(sock.fileno(), 0xC0206921,  # SIOCGIFADDR
                                     struct.pack("16s16x", name.encode()[:15]))
                add(socket.inet_ntoa(packed[20:24]), name)
            except OSError:
                pass
            finally:
                sock.close()
    except (ImportError, OSError):
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass
    return [ip for _rank, _order, ip in sorted(ranked)]
