"""The Cardputer firmware's evaluator, compiled for the host, against the Python engine.

firmware/cardputer runs the committed netlist bytes through lib/c3s_core. These tests
build that same C code with the host compiler (firmware/host/c3s_host.c is a thin
driver), so what they establish holds for the code on the device:

- the generated data files are what scripts/build_firmware.py writes from the demo;
- the core's step relation over all 2**23 (input, state) rows, and the policy's over
  all 2**16 inputs, equals exhaust.step_table on the manifests' bytes;
- the on-boot self-test passes, with hashes equal to the manifests';
- the sensory encoding equals loom.encode_features, including one ulp either side of
  every bin edge and of the binocular overlap;
- a looming episode run tick by tick, as the firmware runs it, matches the Python
  geometry, encoding, policy and core for every train and holdout stimulus.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from c3s import calibrate, exhaust, loom
from c3s.netlist import from_bytes

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "firmware" / "cardputer" / "lib" / "c3s_core"
LOOM = ROOT / "circuits" / "loom-escape"
CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")

pytestmark = pytest.mark.skipif(CC is None, reason="no C compiler")


def manifest(name: str) -> dict:
    return json.loads((LOOM / f"{name}.json").read_text())


def circuit(name: str):
    m = manifest(name)
    return from_bytes(bytes.fromhex(m["tapeout_netlist_hex"][2:]), len(m["inputs"]), len(m["outputs"]))


@pytest.fixture(scope="module")
def host(tmp_path_factory) -> Path:
    exe = tmp_path_factory.mktemp("firmware") / "c3s_host"
    sources = [LIB / "c3s_core.c", LIB / "c3s_data.c", ROOT / "firmware" / "host" / "c3s_host.c"]
    cmd = [CC, "-std=c99", "-O2", "-Wall", "-Wextra", "-ffp-contract=off", f"-I{LIB}", *map(str, sources), "-o", str(exe), "-lm"]
    subprocess.run(cmd, check=True)
    return exe


def run(host: Path, *args, stdin: str | None = None, check: bool = True) -> str:
    return subprocess.run([str(host), *map(str, args)], input=stdin, capture_output=True, text=True, check=check).stdout


@pytest.fixture(scope="module")
def setup():
    tj = json.loads((LOOM / "decision-table.json").read_text())
    p = calibrate.params_from_dict(tj["params"])
    enc = loom.Encoding(**{k: tuple(v) if isinstance(v, list) else v for k, v in tj["encoding"].items()})
    return p, enc


@pytest.fixture(scope="module")
def core_tables():
    return exhaust.step_table(circuit("core-hand-abc"))


def test_generated_data_files_are_current(tmp_path):
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_firmware.py"), "--out", str(tmp_path)], check=True, capture_output=True)
    for name in ("c3s_data.h", "c3s_data.c"):
        assert (tmp_path / name).read_bytes() == (LIB / name).read_bytes(), f"{name} is stale: run scripts/build_firmware.py"


def test_on_boot_self_test_passes(host):
    report = dict(line.split(" ", 1) for line in run(host, "selftest", check=False).splitlines())
    assert report["core_sha256"] == manifest("core-hand-abc")["netlist_sha256"]
    assert report["policy_sha256"] == manifest("policy-hand-abc")["netlist_sha256"]
    assert report["decode"] == "0 0"
    assert report["episodes"] == "3/3"
    assert report["rest"] == f"{2 * 2**16}/{2 * 2**16}"
    assert report["geometry"] == "3/3"
    assert report["pass"] == "1"


def test_core_step_relation_equals_the_python_engine_on_all_rows(host, tmp_path, core_tables):
    outs, nxt = core_tables
    run(host, "table", "core", tmp_path / "core.bin")
    got = np.fromfile(tmp_path / "core.bin", dtype=np.uint8).reshape(-1, 2).astype(np.uint64)
    assert len(got) == len(outs) == 2**23
    assert exhaust.first_mismatch(got[:, 0], outs) is None
    assert exhaust.first_mismatch(got[:, 1], nxt) is None


def test_policy_truth_table_equals_the_python_engine_on_all_rows(host, tmp_path):
    want = exhaust.truth_table(circuit("policy-hand-abc"))
    run(host, "table", "policy", tmp_path / "policy.bin")
    got = np.fromfile(tmp_path / "policy.bin", dtype=np.uint8).reshape(-1, 2).astype(np.uint64)
    assert len(got) == 2**16
    assert exhaust.first_mismatch(got[:, 0], want) is None
    assert not got[:, 1].any()


def test_encoding_equals_python_at_every_edge(host, setup):
    _, enc = setup

    def around(edges, extra):
        return sorted({v for e in edges for v in (math.nextafter(e, -math.inf), e, math.nextafter(e, math.inf))} | set(extra))

    sizes = around(enc.size_edges_deg, [0.0, 1.0, 95.0, 179.99, 200.0])
    speeds = around(enc.speed_edges_dps, [0.0, 5.0, 999.0, 20000.0])
    azimuths = around([-30.0, 30.0], [-90.0, -10.0, 0.0, 10.0, 90.0])
    rows = [(s, v, a) for s in sizes for v in speeds for a in azimuths]
    got = [int(x) for x in run(host, "encode", stdin="".join(f"{s!r} {v!r} {a!r}\n" for s, v, a in rows)).split()]
    assert got == [loom.encode_features(s, v, a, enc) for s, v, a in rows]


def test_episodes_run_as_on_the_device_match_python(host, setup, core_tables):
    p, enc = setup
    outs, nxt = core_tables
    policy = exhaust.truth_table(circuit("policy-hand-abc"))
    nf = enc.n_inputs
    stimuli = loom.family("train") + loom.family("holdout")
    for stim in stimuli:
        lines = run(host, "run", repr(stim.l_over_v_ms), repr(stim.azimuth_deg)).splitlines()
        state, want = 0, []
        for i, (th, dth) in enumerate(loom.stimulus_samples(stim, p)):
            x = loom.encode_features(th, dth, stim.azimuth_deg, enc) | 1 << nf
            row = x | state << (nf + 1)
            motor, nxt_state = int(outs[row]), int(nxt[row])
            want.append((i, th, dth, x, int(policy[x & (1 << nf) - 1]), state, motor, nxt_state))
            state = nxt_state
            if motor in (loom.CORE_SHORT, loom.CORE_LONG):
                break
        assert len(lines) == len(want), stim
        for line, (i, th, dth, *bits) in zip(lines, want):
            f = line.split()
            assert int(f[0]) == i
            assert math.isclose(float(f[1]), th, rel_tol=1e-12) and math.isclose(float(f[2]), dth, rel_tol=1e-12), (stim, i)
            assert [int(v) for v in f[3:]] == bits, (stim, i)
