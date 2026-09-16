"""The firmware's evaluator, in both of its builds, against the Python engine.

firmware/cardputer runs the committed netlist bytes through lib/c3s_core, and the
online simulator (docs/sim) runs the same C files compiled to WebAssembly. These tests
drive both builds through the same commands: the host C compiler's build via
firmware/host/c3s_host.c, and the committed docs/sim/c3s_core.wasm in Node via
firmware/wasm/sim_cli.mjs. What they establish holds for that code:

- the generated data files are what scripts/build_firmware.py writes from the demo, and
  docs/sim/build.json matches the committed module and its sources;
- the core's step relation over all 2**23 (input, state) rows, and the policy's over
  all 2**16 inputs, equals exhaust.step_table on the manifests' bytes;
- the on-boot self-test passes, with hashes equal to the manifests';
- the sensory encoding equals loom.encode_features, including one ulp either side of
  every bin edge and of the binocular overlap;
- a looming episode run tick by tick, as the firmware runs it, matches the Python
  geometry, encoding, policy and core for every train and holdout stimulus.
"""

from __future__ import annotations

import hashlib
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
SIM = ROOT / "docs" / "sim"
CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
NODE = shutil.which("node")


def manifest(name: str) -> dict:
    return json.loads((LOOM / f"{name}.json").read_text())


def circuit(name: str):
    m = manifest(name)
    return from_bytes(bytes.fromhex(m["tapeout_netlist_hex"][2:]), len(m["inputs"]), len(m["outputs"]))


@pytest.fixture(scope="module", params=["native", "wasm"])
def evaluator(request, tmp_path_factory) -> list[str]:
    if request.param == "wasm":
        if NODE is None:
            pytest.skip("node not installed")
        return [NODE, str(ROOT / "firmware" / "wasm" / "sim_cli.mjs")]
    if CC is None:
        pytest.skip("no C compiler")
    exe = tmp_path_factory.mktemp("firmware") / "c3s_host"
    sources = [LIB / "c3s_core.c", LIB / "c3s_data.c", ROOT / "firmware" / "host" / "c3s_host.c"]
    cmd = [CC, "-std=c99", "-O2", "-Wall", "-Wextra", "-ffp-contract=off", f"-I{LIB}", *map(str, sources), "-o", str(exe), "-lm"]
    subprocess.run(cmd, check=True)
    return [str(exe)]


def run(evaluator: list[str], *args, stdin: str | None = None, check: bool = True) -> str:
    return subprocess.run([*evaluator, *map(str, args)], input=stdin, capture_output=True, text=True, check=check).stdout


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


def test_simulator_module_was_built_from_the_committed_sources():
    build = json.loads((SIM / "build.json").read_text())
    module = SIM / build["module"]
    assert hashlib.sha256(module.read_bytes()).hexdigest() == build["sha256"]
    assert module.stat().st_size == build["bytes"]
    assert {"firmware/cardputer/lib/c3s_core/c3s_core.c", "firmware/cardputer/lib/c3s_core/c3s_data.c", "firmware/wasm/sim.c"} <= set(build["sources"])
    for path, digest in build["sources"].items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest, f"{path} changed after docs/sim was built: run scripts/build_sim.py"


def test_on_boot_self_test_passes(evaluator):
    report = dict(line.split(" ", 1) for line in run(evaluator, "selftest", check=False).splitlines())
    assert report["core_sha256"] == manifest("core-hand-abc")["netlist_sha256"]
    assert report["policy_sha256"] == manifest("policy-hand-abc")["netlist_sha256"]
    assert report["decode"] == "0 0"
    assert report["episodes"] == "3/3"
    assert report["rest"] == f"{2 * 2**16}/{2 * 2**16}"
    assert report["geometry"] == "3/3"
    assert report["pass"] == "1"


def test_core_step_relation_equals_the_python_engine_on_all_rows(evaluator, tmp_path, core_tables):
    outs, nxt = core_tables
    run(evaluator, "table", "core", tmp_path / "core.bin")
    got = np.fromfile(tmp_path / "core.bin", dtype=np.uint8).reshape(-1, 2).astype(np.uint64)
    assert len(got) == len(outs) == 2**23
    assert exhaust.first_mismatch(got[:, 0], outs) is None
    assert exhaust.first_mismatch(got[:, 1], nxt) is None


def test_policy_truth_table_equals_the_python_engine_on_all_rows(evaluator, tmp_path):
    want = exhaust.truth_table(circuit("policy-hand-abc"))
    run(evaluator, "table", "policy", tmp_path / "policy.bin")
    got = np.fromfile(tmp_path / "policy.bin", dtype=np.uint8).reshape(-1, 2).astype(np.uint64)
    assert len(got) == 2**16
    assert exhaust.first_mismatch(got[:, 0], want) is None
    assert not got[:, 1].any()


def test_simulator_skeleton_data_matches_the_connectome_subgraph():
    """docs/sim/skeletons.{json,bin} are the released shapes of cells the model names.
    Every cell in them must be one the subgraph lists, with the subgraph's own synapse
    count, and the binary's size must follow the layout it declares."""
    doc = json.loads((SIM / "skeletons.json").read_text())
    raw = (ROOT / "data" / "malecns-v1.0-gf-escape-subgraph.json").read_bytes()
    assert doc["source"]["subgraph_sha256"] == hashlib.sha256(raw).hexdigest()
    sub = json.loads(raw)
    known: dict[tuple[int, str], tuple[str, int]] = {}
    for gf in sub["giant_fibers"]:
        side = gf["instance"].rstrip(")").split("_")[-1]
        known[(int(gf["body_id"]), side)] = ("DNp01", gf["visual_projection_input_synapses"])
        for t in ("LC4", "LPLC2"):
            for body, syn in gf["inputs"][t]["per_cell_synapses"]:
                known[(int(body), side)] = (t, int(syn))
    for cell in doc["cells"]:
        key = (cell["body_id"], cell["side"])
        assert key in known, key
        assert (cell["type"], cell["synapses"]) == known[key]
    layout = doc["layout"]
    assert layout["segments_byte_offset"] == layout["points"] * 12
    assert (SIM / "skeletons.bin").stat().st_size == layout["points"] * 12 + layout["segments"] * 8
    assert len(doc["source"]["files"]) == len(doc["cells"])


def test_simulator_wiring_data_matches_the_connectome_subgraph():
    """docs/sim/wiring.json feeds the page's wiring view. Every number in it must come
    from the committed subgraph, and it must name that file's real digest."""
    doc = json.loads((SIM / "wiring.json").read_text())
    raw = (ROOT / "data" / doc["source"]["file"]).read_bytes()
    assert doc["source"]["sha256"] == hashlib.sha256(raw).hexdigest()
    sub = json.loads(raw)
    assert len(doc["giant_fibers"]) == len(sub["giant_fibers"])
    for got, want in zip(doc["giant_fibers"], sub["giant_fibers"]):
        assert got["instance"] == want["instance"]
        assert got["visual_projection_input_synapses"] == want["visual_projection_input_synapses"]
        for t in ("LC4", "LPLC2"):
            counts = sorted((int(n) for _, n in want["inputs"][t]["per_cell_synapses"]), reverse=True)
            assert got[t]["synapses_per_cell"] == counts
            assert got[t]["cells"] == want["inputs"][t]["cells"] == len(counts)
            assert got[t]["synapses"] == sum(counts)


def test_simulator_onchain_data_matches_the_artifact_and_the_manifest():
    """docs/sim/onchain.json lets the page have a public node evaluate the core through an
    eth_call state override. Its bytecode, selector and netlist must be the committed ones."""
    doc = json.loads((SIM / "onchain.json").read_text())
    m = manifest("core-hand-abc")
    assert doc["core"]["netlist"] == m["tapeout_netlist_hex"]
    assert doc["core"]["netlist_sha256"] == m["netlist_sha256"]
    assert (doc["core"]["n_inputs"], doc["core"]["n_outputs"]) == (len(m["inputs"]), len(m["outputs"]))
    assert doc["core"]["n_state"] == m["metrics"]["latch"]
    ev = doc["evaluator"]
    assert ev["runtime_sha256"] == hashlib.sha256(bytes.fromhex(ev["runtime_bytecode"][2:])).hexdigest()
    assert ev["runtime_bytes"] == len(ev["runtime_bytecode"]) // 2 - 1

    artifact = ROOT / "contracts" / "out" / "NandMachine.sol" / "NandMachine.json"
    if not artifact.exists():
        pytest.skip("Foundry artifact not built")
    art = json.loads(artifact.read_text())
    assert ev["runtime_bytecode"].removeprefix("0x") == art["deployedBytecode"]["object"].removeprefix("0x")
    assert ev["selector"].removeprefix("0x") == art["methodIdentifiers"][ev["signature"]]


@pytest.mark.parametrize("name,rows", [("core", 2**23), ("policy", 2**16)])
def test_whole_domain_digest_equals_the_evm_fixture_chain(evaluator, name, rows):
    """The firmware's own bit-sliced digest of the complete step relation must equal the
    SHA-256 chain in the EVM fixtures, which the Solidity evaluator is checked against."""
    fixtures = json.loads((ROOT / "contracts" / "test" / "fixtures" / "circuits.json").read_text())
    want = next(c["domain_chain_sha256"] for c in fixtures["circuits"] if c["name"] == f"{name}-hand-abc")
    got_rows, got = run(evaluator, "digest", name).split()
    assert int(got_rows) == rows
    assert got == want


def test_encoding_equals_python_at_every_edge(evaluator, setup):
    _, enc = setup

    def around(edges, extra):
        return sorted({v for e in edges for v in (math.nextafter(e, -math.inf), e, math.nextafter(e, math.inf))} | set(extra))

    sizes = around(enc.size_edges_deg, [0.0, 1.0, 95.0, 179.99, 200.0])
    speeds = around(enc.speed_edges_dps, [0.0, 5.0, 999.0, 20000.0])
    azimuths = around([-30.0, 30.0], [-90.0, -10.0, 0.0, 10.0, 90.0])
    rows = [(s, v, a) for s in sizes for v in speeds for a in azimuths]
    got = [int(x) for x in run(evaluator, "encode", stdin="".join(f"{s!r} {v!r} {a!r}\n" for s, v, a in rows)).split()]
    assert got == [loom.encode_features(s, v, a, enc) for s, v, a in rows]


def test_episodes_run_as_on_the_device_match_python(evaluator, setup, core_tables):
    p, enc = setup
    outs, nxt = core_tables
    policy = exhaust.truth_table(circuit("policy-hand-abc"))
    nf = enc.n_inputs
    for stim in loom.family("train") + loom.family("holdout"):
        lines = run(evaluator, "run", repr(stim.l_over_v_ms), repr(stim.azimuth_deg)).splitlines()
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
