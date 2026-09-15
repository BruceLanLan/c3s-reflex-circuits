"""Compile the firmware's C code to WebAssembly for the online simulator.

    python scripts/build_sim.py [--clang PATH]

Writes docs/sim/c3s_core.wasm from firmware/cardputer/lib/c3s_core and
firmware/wasm/sim.c, and docs/sim/build.json with the compiler, the flags, the
module's SHA-256 and the SHA-256 of every source file. Needs a clang with the wasm32
target and wasm-ld (Homebrew's llvm has both).

Another clang version may emit different bytes for the same sources, so the module is
not rebuilt by scripts/verify.sh. tests/test_firmware.py instead checks that
build.json matches the committed module and sources, and runs the module in Node
against the Python engine on every (input, state) row.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = "firmware/cardputer/lib/c3s_core"
SOURCES = [f"{LIB}/c3s_core.c", f"{LIB}/c3s_data.c", "firmware/wasm/sim.c"]
HEADERS = [f"{LIB}/c3s_core.h", f"{LIB}/c3s_data.h", "firmware/wasm/include/math.h", "firmware/wasm/include/string.h"]
OUT = "docs/sim/c3s_core.wasm"
FLAGS = [
    "--target=wasm32", "-std=c99", "-O2", "-ffreestanding", "-nostdlib", "-mbulk-memory", "-ffp-contract=off",
    "-Wall", "-Wextra", "-Ifirmware/wasm/include", f"-I{LIB}",
    "-Wl,--no-entry", "-Wl,--strip-all", "-Wl,-z,stack-size=65536",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def default_clang() -> str:
    brew = Path("/opt/homebrew/opt/llvm/bin/clang")
    return str(brew) if brew.exists() else (shutil.which("clang") or "clang")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clang", default=default_clang())
    args = ap.parse_args()
    (ROOT / OUT).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([args.clang, *FLAGS, *SOURCES, "-o", OUT], cwd=ROOT, check=True)
    version = subprocess.run([args.clang, "--version"], capture_output=True, text=True, check=True).stdout.splitlines()[0]
    build = {
        "module": Path(OUT).name,
        "sha256": sha256(ROOT / OUT),
        "bytes": (ROOT / OUT).stat().st_size,
        "compiler": version,
        "flags": FLAGS,
        "sources": {p: sha256(ROOT / p) for p in SOURCES + HEADERS},
    }
    (ROOT / "docs" / "sim" / "build.json").write_text(json.dumps(build, indent=2) + "\n")
    print(f"wrote {OUT} ({build['bytes']} bytes, sha256 {build['sha256'][:16]}...) with {version}")


if __name__ == "__main__":
    main()
