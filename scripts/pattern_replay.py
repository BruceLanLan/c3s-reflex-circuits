#!/usr/bin/env python3
"""Replay a pattern record on two independent hosts and compare it tick by tick.

A pattern record (patterns/README.md) says: this stimulus, fed to the escape core,
produces exactly this per-tick trace. The claim is only worth keeping if anyone can
check it, so this script checks it twice over:

  python  the committed netlist bytes decoded by c3s.netlist and stepped gate by gate;
  wasm    the firmware's own C compiled to WebAssembly (docs/sim/c3s_core.wasm), driven
          through `node firmware/wasm/sim_cli.mjs run L/V AZ`.

Both must reproduce the recorded integer trace exactly. The record also names the
circuit's netlist SHA-256 and the whole-domain digest of its step relation; both are
compared with the committed files, so a record cannot silently be about some other
circuit.

    scripts/pattern_replay.py --all                       # the whole registry
    scripts/pattern_replay.py patterns/records/p001-*.json
    scripts/pattern_replay.py --all --canonical           # also re-derive every record
    scripts/pattern_replay.py --all --verify-digest       # also digest all 2**23 rows
    scripts/pattern_replay.py --emit stub.json --out patterns/records/p006-x.json

Exit status is 0 only when every record checked agreed on every tick on both hosts.
The angular geometry (theta, theta') is compared with a tolerance and never enters the
checksum: atan and tan come from a different maths library on each host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from c3s import calibrate, loom  # noqa: E402
from c3s.netlist import from_bytes, tick  # noqa: E402

FORMAT = "c3s.pattern/1"
RECORDS = ROOT / "patterns" / "records"
LOOM = ROOT / "circuits" / "loom-escape"
FIXTURES = ROOT / "contracts" / "test" / "fixtures" / "circuits.json"
SIM_CLI = ROOT / "firmware" / "wasm" / "sim_cli.mjs"
CORE = "core-hand-abc"
POLICY = "policy-hand-abc"
DISCOVERER = "c3s-reflex maintainers"

TRACE_FIELDS = ["tick", "sensory", "policy", "state", "motor", "next_state"]
MOTOR_NAMES = dict(loom.CORE_ACTION_NAMES)
GEOMETRY_TOLERANCE = 1e-12


# -- the committed circuit ----------------------------------------------------


def manifest(name: str) -> dict:
    return json.loads((LOOM / f"{name}.json").read_text())


def circuit(name: str):
    m = manifest(name)
    return from_bytes(bytes.fromhex(m["tapeout_netlist_hex"][2:]), len(m["inputs"]), len(m["outputs"]))


def setup() -> tuple[loom.TeacherParams, loom.Encoding]:
    tj = json.loads((LOOM / "decision-table.json").read_text())
    p = calibrate.params_from_dict(tj["params"])
    enc = loom.Encoding(**{k: tuple(v) if isinstance(v, list) else v for k, v in tj["encoding"].items()})
    return p, enc


def domain_digest_of(name: str) -> str:
    """The SHA-256 chain over the circuit's whole step relation, as the EVM fixtures pin it."""
    fixtures = json.loads(FIXTURES.read_text())
    return next(c["domain_chain_sha256"] for c in fixtures["circuits"] if c["name"] == name)


# -- the two hosts ------------------------------------------------------------


def _bits(value: int, n: int) -> list[int]:
    return [(value >> i) & 1 for i in range(n)]


def _pack(values) -> int:
    return sum(b << i for i, b in enumerate(values))


def python_replay(l_over_v_ms: float, azimuth_deg: float) -> tuple[list[list[int]], list[tuple[float, float]]]:
    """The committed netlist bytes, decoded and stepped one gate at a time.

    Row layout is the firmware's: the 16 sensory bits, then a standing bit that is
    always 1, then the six latch bits as the circuit's state.
    """
    p, enc = setup()
    core, policy = circuit(CORE), circuit(POLICY)
    nf = enc.n_inputs
    stim = loom.Stimulus(float(l_over_v_ms), float(azimuth_deg))
    rows: list[list[int]] = []
    geometry: list[tuple[float, float]] = []
    state = 0
    for i, (theta, dtheta) in enumerate(loom.stimulus_samples(stim, p)):
        x = loom.encode_features(theta, dtheta, stim.azimuth_deg, enc) | 1 << nf
        pathways, _ = tick(policy, _bits(x & ((1 << nf) - 1), nf))
        outputs, next_state = tick(core, _bits(x, core.n_inputs), _bits(state, len(_latches(core))))
        motor, nxt = _pack(outputs), _pack(next_state)
        rows.append([i, x, _pack(pathways), state, motor, nxt])
        geometry.append((theta, dtheta))
        state = nxt
        if motor in (loom.CORE_SHORT, loom.CORE_LONG):
            break
    return rows, geometry


def _latches(c) -> list[int]:
    from c3s.netlist import Latch

    return [i for i, cell in enumerate(c.cells) if isinstance(cell, Latch)]


def wasm_available() -> str | None:
    return shutil.which("node")


def wasm_replay(l_over_v_ms: float, azimuth_deg: float) -> tuple[list[list[int]], list[tuple[float, float]]]:
    """docs/sim/c3s_core.wasm, the firmware's C, driven by its own command-line driver."""
    node = wasm_available()
    if node is None:
        raise RuntimeError("node is not installed, so the WebAssembly build cannot be driven")
    out = subprocess.run(
        [node, str(SIM_CLI), "run", repr(float(l_over_v_ms)), repr(float(azimuth_deg))],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    rows, geometry = [], []
    for line in out.splitlines():
        f = line.split()
        # tick theta dtheta sensory policy state motor next_state
        rows.append([int(f[0]), int(f[3]), int(f[4]), int(f[5]), int(f[6]), int(f[7])])
        geometry.append((float(f[1]), float(f[2])))
    return rows, geometry


def wasm_domain_digest() -> str:
    node = wasm_available()
    if node is None:
        raise RuntimeError("node is not installed")
    out = subprocess.run([node, str(SIM_CLI), "digest", "core"], capture_output=True, text=True, check=True).stdout
    _rows, digest = out.split()
    return digest


# -- records ------------------------------------------------------------------


def canonical_bytes(obj) -> bytes:
    """The one serialisation that is hashed or written: sorted keys, no whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def trace_checksum(rows) -> str:
    """SHA-256 of the integer trace alone, as canonical JSON. The geometry columns are
    left out on purpose: they come from each host's own libm."""
    return hashlib.sha256(canonical_bytes([list(map(int, r)) for r in rows])).hexdigest()


# Arrays holding nothing but numbers and nulls are put back on one line. Arrays of
# strings are left alone: a prose note may hold the commas this would split on.
_FLAT = re.compile(r"\[\s*((?:-?[0-9][0-9.eE+-]*|null)(?:\s*,\s*(?:-?[0-9][0-9.eE+-]*|null))*)\s*\]")


def pretty_bytes(record: dict) -> bytes:
    """What a record file holds: indented, but with every row of numbers on one line, so
    a trace is readable and a diff points at the tick that changed."""

    def flatten(m: re.Match) -> str:
        items = [part.strip() for part in m.group(1).replace("\n", " ").split(",")]
        return "[" + ", ".join(items) + "]"

    body = _FLAT.sub(flatten, json.dumps(record, indent=2, ensure_ascii=False))
    return (body + "\n").encode("utf-8")


def build_record(meta: dict, stimulus: dict) -> dict:
    """Derive a complete record from its metadata and its stimulus, on Python."""
    lv, az = float(stimulus["l_over_v_ms"]), float(stimulus["azimuth_deg"])
    for key, want in (("start_deg", 10.0), ("end_deg", 170.0)):
        if float(stimulus.get(key, want)) != want:
            raise ValueError(f"{key} is fixed at {want} in the firmware's geometry")
    p, _enc = setup()
    rows, geometry = python_replay(lv, az)
    last = rows[-1]
    took_off = last[4] in (loom.CORE_SHORT, loom.CORE_LONG)
    cm = manifest(CORE)
    record = {
        "format": FORMAT,
        "id": meta["id"],
        "title": meta["title"],
        "question": meta["question"],
        "how_found": meta["how_found"],
        "discoverer": meta.get("discoverer", DISCOVERER),
        "date": meta["date"],
        "circuit": {
            "name": CORE,
            "policy": POLICY,
            "netlist_sha256": cm["netlist_sha256"],
            "domain_chain_sha256": domain_digest_of(CORE),
            "encoding": "4bit-log",
            "tick_ms": p.tick_ms,
        },
        "stimulus": {
            "kind": "loom",
            "l_over_v_ms": lv,
            "azimuth_deg": az,
            "start_deg": 10.0,
            "end_deg": 170.0,
        },
        "result": {
            "ticks": len(rows),
            "ended": MOTOR_NAMES[last[4]] if took_off else "no takeoff",
            "takeoff_tick": last[0] if took_off else None,
            "states_entered": sorted({int(r[3]) for r in rows}),
        },
        "trace": {
            "fields": list(TRACE_FIELDS),
            "sha256": trace_checksum(rows),
            "rows": rows,
        },
        "geometry": {
            "informational": True,
            "note": "Angular size and expansion speed at the first and last tick, in degrees and "
            "degrees per second. Each host computes these with its own atan and tan, so they are "
            "compared with a tolerance and are not part of trace.sha256.",
            "fields": ["tick", "theta_deg", "dtheta_dps"],
            "first_tick": [rows[0][0], round(geometry[0][0], 9), round(geometry[0][1], 9)],
            "last_tick": [rows[-1][0], round(geometry[-1][0], 9), round(geometry[-1][1], 9)],
        },
        "replay": f"scripts/pattern_replay.py patterns/records/{meta['id']}.json",
    }
    if "notes" in meta:
        record["notes"] = list(meta["notes"])
    return record


META_KEYS = ("id", "title", "question", "how_found", "discoverer", "date", "notes")


def meta_of(record: dict) -> dict:
    return {k: record[k] for k in META_KEYS if k in record}


# -- checking -----------------------------------------------------------------


class Result:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failures: list[str] = []

    def ok(self, name: str, text: str) -> None:
        self.lines.append(f"  ok   {name:<9} {text}")

    def bad(self, name: str, text: str) -> None:
        self.lines.append(f"  FAIL {name:<9} {text}")
        self.failures.append(name)

    def add(self, cond: bool, name: str, ok_text: str, bad_text: str) -> bool:
        (self.ok if cond else self.bad)(name, ok_text if cond else bad_text)
        return cond


def first_row_difference(a, b) -> str:
    for i, (x, y) in enumerate(zip(a, b)):
        if list(map(int, x)) != list(map(int, y)):
            return f"row {i}: {list(map(int, x))} vs {list(map(int, y))}"
    if len(a) != len(b):
        return f"{len(a)} rows vs {len(b)} rows"
    return "no difference"


def geometry_agrees(record: dict, geometry) -> bool:
    for key, (theta, dtheta) in (("first_tick", geometry[0]), ("last_tick", geometry[-1])):
        want = record["geometry"][key]
        if not (
            math.isclose(want[1], theta, rel_tol=1e-8, abs_tol=1e-8)
            and math.isclose(want[2], dtheta, rel_tol=1e-8, abs_tol=1e-8)
        ):
            return False
    return True


def check_record(path: Path, *, use_wasm: bool = True, canonical: bool = False, verify_digest: bool = False) -> Result:
    r = Result()
    raw = path.read_bytes()
    record = json.loads(raw)
    stim = record["stimulus"]
    recorded = [list(map(int, row)) for row in record["trace"]["rows"]]
    print(f"{record['id']}  loom l/v {stim['l_over_v_ms']} ms, azimuth {stim['azimuth_deg']} deg")

    r.add(record.get("format") == FORMAT, "format", FORMAT, f"{record.get('format')!r}, expected {FORMAT}")
    r.add(
        record["trace"]["fields"] == TRACE_FIELDS,
        "fields",
        " ".join(TRACE_FIELDS),
        f"{record['trace']['fields']}, expected {TRACE_FIELDS}",
    )
    r.add(
        trace_checksum(recorded) == record["trace"]["sha256"],
        "checksum",
        f"trace sha256 {record['trace']['sha256'][:16]}… over {len(recorded)} rows",
        f"recorded {record['trace']['sha256'][:16]}…, computed {trace_checksum(recorded)[:16]}…",
    )
    cm = manifest(CORE)
    r.add(
        record["circuit"]["netlist_sha256"] == cm["netlist_sha256"],
        "circuit",
        f"netlist sha256 {cm['netlist_sha256'][:16]}… is {CORE} as committed",
        f"record names {record['circuit']['netlist_sha256'][:16]}…, committed {cm['netlist_sha256'][:16]}…",
    )
    want_digest = domain_digest_of(CORE)
    r.add(
        record["circuit"]["domain_chain_sha256"] == want_digest,
        "fingerprint",
        f"{want_digest[:18]}… matches contracts/test/fixtures/circuits.json",
        f"record names {record['circuit']['domain_chain_sha256'][:18]}…, fixtures {want_digest[:18]}…",
    )

    py_rows, py_geometry = python_replay(stim["l_over_v_ms"], stim["azimuth_deg"])
    r.add(
        py_rows == recorded,
        "python",
        f"{len(py_rows)} ticks identical to the record",
        first_row_difference(recorded, py_rows),
    )
    r.add(
        geometry_agrees(record, py_geometry),
        "geometry",
        "first and last tick agree with Python's geometry (informational)",
        f"recorded {record['geometry']['first_tick']}/{record['geometry']['last_tick']}, "
        f"python {py_geometry[0]}/{py_geometry[-1]}",
    )

    if use_wasm:
        if wasm_available() is None:
            r.bad("wasm", "node is not installed; install Node to check the WebAssembly build")
        else:
            wasm_rows, wasm_geometry = wasm_replay(stim["l_over_v_ms"], stim["azimuth_deg"])
            r.add(
                wasm_rows == recorded,
                "wasm",
                f"{len(wasm_rows)} ticks identical to the record",
                first_row_difference(recorded, wasm_rows),
            )
            r.add(
                all(
                    math.isclose(a[0], b[0], rel_tol=GEOMETRY_TOLERANCE)
                    and math.isclose(a[1], b[1], rel_tol=GEOMETRY_TOLERANCE)
                    for a, b in zip(py_geometry, wasm_geometry)
                )
                and len(py_geometry) == len(wasm_geometry),
                "libm",
                f"theta and theta' agree to {GEOMETRY_TOLERANCE:g} relative on every tick",
                "the two hosts' geometry differs by more than the tolerance",
            )

    if canonical:
        again = pretty_bytes(build_record(meta_of(record), stim))
        r.add(
            again == raw,
            "canonical",
            "re-derived from its own stimulus, byte for byte",
            "re-deriving this record from its stimulus gives different bytes",
        )

    if verify_digest and use_wasm and wasm_available() is not None:
        got = wasm_domain_digest()
        r.add(
            got == want_digest,
            "digest",
            f"the WebAssembly build digested all 2**23 rows to {got[:18]}…",
            f"the WebAssembly build digested all 2**23 rows to {got}, fixtures say {want_digest}",
        )

    for line in r.lines:
        print(line)
    print(f"  {'ok' if not r.failures else 'FAILED: ' + ', '.join(r.failures)}\n")
    return r


# -- command line -------------------------------------------------------------


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Replay pattern records on Python and on the WebAssembly build.")
    ap.add_argument("paths", nargs="*", type=Path, help="record files to replay")
    ap.add_argument("--all", action="store_true", help="replay every record in patterns/records")
    ap.add_argument("--python-only", action="store_true", help="skip the WebAssembly host")
    ap.add_argument("--canonical", action="store_true", help="also re-derive each record from its stimulus")
    ap.add_argument("--verify-digest", action="store_true", help="also digest all 2**23 rows on the WebAssembly build")
    ap.add_argument("--emit", type=Path, help="derive a record from a stub holding its metadata and stimulus")
    ap.add_argument("--out", type=Path, help="where --emit writes (default: stdout)")
    args = ap.parse_args(argv)

    if args.emit is not None:
        stub = json.loads(args.emit.read_text())
        record = build_record(meta_of(stub), stub["stimulus"])
        body = pretty_bytes(record)
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_bytes(body)
            print(f"wrote {args.out} ({len(record['trace']['rows'])} ticks, sha256 {record['trace']['sha256']})")
        else:
            sys.stdout.write(body.decode())
        return 0

    paths = sorted(RECORDS.glob("*.json")) if args.all else list(args.paths)
    if not paths:
        ap.error("give record paths or --all")
    failed = []
    for path in paths:
        r = check_record(
            path,
            use_wasm=not args.python_only,
            canonical=args.canonical,
            verify_digest=args.verify_digest,
        )
        if r.failures:
            failed.append(path.name)
    hosts = "python" if args.python_only else "python and the WebAssembly build"
    if failed:
        print(f"{len(paths)} records, {len(failed)} failed: {', '.join(failed)}")
        return 1
    print(f"{len(paths)} records, {hosts} identical to every recorded tick, 0 mismatches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
