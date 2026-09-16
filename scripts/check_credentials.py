"""Refuse to build a firmware image that carries a Wi-Fi network, a password or a token.

    python scripts/check_credentials.py [--root DIR]     # 0 = clean, 1 = something found

The device reaches the boundary console over the person's own Wi-Fi (W11), and the way in
is the device's own keyboard: the network's name, its password, the console's address and
the device token are typed on the device and kept in its NVS. None of them may be in a
source file, because a source file is committed, published and built into every image —
one hardcoded password would put it in everybody's flash and in the release binary.

So this runs before every `pio run` (`extra_scripts` in firmware/cardputer/platformio.ini)
and in `tests/test_firmware_credentials.py`. It looks for the shapes a credential takes in
C++ and in a PlatformIO build:

    const char *WIFI_SSID = "TheRouter";      an assignment to a credential-ish name
    #define WIFI_PASSWORD "hunter2"           a define
    WiFi.begin("TheRouter", "hunter2");       a literal handed to the radio
    store.putString("sec", "hunter2");        a literal written to the credential store
    build_flags = -DWIFI_PASS=hunter2         a credential compiled in from the build file

An empty literal is what the firmware's own buffers are initialised to (`char g_secret[65]
= "";`) and is fine: it is the absence of a credential. A key *name* ("ssid", "sec") is a
literal too, and is fine where the code uses it as a key rather than as a value — which is
why only assignments and the calls above are looked at, not every string in the file.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Where the image comes from: the firmware sources, the libraries built into it, and the
# build file that can define anything into them.
SOURCES = ("firmware/cardputer/src", "firmware/cardputer/lib", "firmware/cardputer/include")
BUILD_FILES = ("firmware/cardputer/platformio.ini",)
SUFFIXES = (".c", ".cc", ".cpp", ".h", ".hpp", ".ino")

# A name that would be holding a credential rather than talking about one.
SECRETISH = re.compile(r"ssid|pass|psk|secret|token|credential", re.I)
LITERAL = r'"(?:[^"\\\n]|\\.)*"'
ASSIGNED = re.compile(rf"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[[^\]\n]*\])?\s*=\s*({LITERAL})")
DEFINED = re.compile(rf"#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s+({LITERAL})")
RADIO = re.compile(rf"\bWiFi\s*\.\s*(begin|softAP|setHostname)\s*\(\s*({LITERAL})")
STORED = re.compile(rf"\bput(?:String|Bytes)\s*\(\s*{LITERAL}\s*,\s*({LITERAL})")
FLAG = re.compile(r"-D\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\S+)")


def nonempty(literal: str) -> bool:
    """A literal that actually carries something. `""` is the absence of a credential."""
    return len(literal.strip('"').strip()) > 0


def findings_in(text: str, name: str) -> list[str]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        code = line.split("//", 1)[0] if not name.endswith(".ini") else line.split(";", 1)[0]
        if name.endswith(".ini"):
            for flag, value in FLAG.findall(code):
                if SECRETISH.search(flag) and value.strip('"').strip():
                    out.append(f"{name}:{i}: -D{flag}= carries a credential; type it on the device instead")
            continue
        for who, literal in ASSIGNED.findall(code):
            if SECRETISH.search(who) and nonempty(literal):
                out.append(f"{name}:{i}: {who} is assigned {literal}; type it on the device instead")
        for who, literal in DEFINED.findall(code):
            if SECRETISH.search(who) and nonempty(literal):
                out.append(f"{name}:{i}: #define {who} {literal}; type it on the device instead")
        for call, literal in RADIO.findall(code):
            if nonempty(literal):
                out.append(f"{name}:{i}: WiFi.{call}({literal}, …) hands the radio a literal; "
                           f"pass what the person typed on the device")
        for literal in STORED.findall(code):
            if nonempty(literal):
                out.append(f"{name}:{i}: {literal} is written to the credential store as a default; "
                           f"leave it empty until a person types one")
    return out


def check(root: Path = ROOT) -> list[str]:
    """Every credential-looking literal in what becomes the image. Empty list = clean."""
    findings: list[str] = []
    for where in SOURCES:
        base = root / where
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix.lower() in SUFFIXES and path.is_file():
                findings += findings_in(path.read_text(errors="replace"), str(path.relative_to(root)))
    for where in BUILD_FILES:
        path = root / where
        if path.exists():
            findings += findings_in(path.read_text(errors="replace"), str(path.relative_to(root)))
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=ROOT, help="the repository to check")
    args = ap.parse_args(argv)
    findings = check(args.root)
    for line in findings:
        print(f"credential in the build: {line}", file=sys.stderr)
    if findings:
        print(f"\n{len(findings)} credential(s) would be built into the image. A Wi-Fi network, its password\n"
              f"and the device token are typed on the device and kept in its NVS: see the Network page and\n"
              f"docs/FIRMWARE.md. Nothing that reaches a release image may carry them.", file=sys.stderr)
        return 1
    print("no Wi-Fi network, password or token in the firmware sources or the build file")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
