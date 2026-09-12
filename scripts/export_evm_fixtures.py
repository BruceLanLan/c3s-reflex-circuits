"""Export Foundry fixtures: full-domain SHA-256 chains and core episodes.

    python scripts/export_evm_fixtures.py

For each circuit the chain is h_0 = 0x00..00 and, for each block of 256 rows
(or fewer when the domain is smaller), h = sha256(h ‖ output planes ‖ next-state
planes), every plane a 32-byte big-endian word whose bit j is row base + j. Row
bits are inputs first, then latch state, as in `c3s.exhaust.step_table`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from c3s import calibrate, exhaust, loom
from c3s.components import CATALOG
from c3s.netlist import Circuit, Latch, from_bytes, to_bytes

ROOT = Path(__file__).resolve().parents[1]
LOOM = ROOT / "circuits" / "loom-escape"
OUT = ROOT / "contracts" / "test" / "fixtures" / "circuits.json"
LOOM_CIRCUITS = ("policy-hand-abc", "policy-table-abc", "core-hand-abc", "core-table-abc")


def _planes(values: np.ndarray, width: int, total: int) -> np.ndarray:
    """(blocks, width) array of 32-byte big-endian words."""
    blocks = (total + 255) // 256
    out = np.zeros((blocks, width, 32), dtype=np.uint8)
    for k in range(width):
        bits = ((values >> np.uint64(k)) & np.uint64(1)).astype(np.uint8)
        padded = np.zeros(blocks * 256, np.uint8)
        padded[:total] = bits
        packed = np.packbits(padded.reshape(blocks, 256), axis=1, bitorder="little")  # little-endian bytes
        out[:, k, :] = packed[:, ::-1]  # as a big-endian uint256
    return out


def domain_chain(circuit: Circuit) -> str:
    outs, nxt = exhaust.step_table(circuit)
    n_state = sum(isinstance(c, Latch) for c in circuit.cells)
    total = len(outs)
    op = _planes(outs, circuit.n_outputs, total)
    sp = _planes(nxt, n_state, total)
    h = bytes(32)
    for i in range(op.shape[0]):
        h = hashlib.sha256(h + op[i].tobytes() + sp[i].tobytes()).digest()
    return "0x" + h.hex()


def entry(name: str, circuit: Circuit) -> dict:
    return {
        "name": name,
        "n_inputs": circuit.n_inputs,
        "n_outputs": circuit.n_outputs,
        "n_state": sum(isinstance(c, Latch) for c in circuit.cells),
        "netlist": to_bytes(circuit).hex(),
        "domain_chain_sha256": domain_chain(circuit),
    }


def load_manifest(path: Path) -> Circuit:
    m = json.loads(path.read_text())
    return from_bytes(bytes.fromhex(m["tapeout_netlist_hex"][2:]), len(m["inputs"]), len(m["outputs"]))


def main() -> None:
    circuits = [entry(n, c.build()) for n, c in sorted(CATALOG.items())]
    for name in LOOM_CIRCUITS:
        circuits.append(entry(name, load_manifest(LOOM / f"{name}.json")))
        print("chained", name)

    tj = json.loads((LOOM / "decision-table.json").read_text())
    p = calibrate.params_from_dict(tj["params"])
    enc = loom.Encoding(**{k: tuple(v) if isinstance(v, list) else v for k, v in tj["encoding"].items()})
    core = load_manifest(LOOM / "core-hand-abc.json")
    outs, nxt = exhaust.step_table(core)
    nf = enc.n_inputs
    runs = []
    for stim in (loom.Stimulus(10.0, 0.0), loom.Stimulus(40.0, -60.0), loom.Stimulus(120.0, 60.0)):
        state, ins, motor = 0, [], []
        for th, dth in loom.stimulus_samples(stim, p):
            x = loom.encode_features(th, dth, stim.azimuth_deg, enc) | (1 << nf)
            row = x | (state << (nf + 1))
            ins.append(x)
            motor.append(int(outs[row]))
            state = int(nxt[row])
            if motor[-1] in (loom.CORE_SHORT, loom.CORE_LONG):
                break
        runs.append({"l_over_v_ms": stim.l_over_v_ms, "azimuth_deg": stim.azimuth_deg, "inputs": ins, "motor": motor})
    raw = to_bytes(core)
    fixtures = {
        "count": len(circuits),
        "circuits": circuits,
        "episodes": {
            "circuit": "core-hand-abc",
            "netlist": raw.hex(),
            "netlist_sha256": "0x" + hashlib.sha256(raw).hexdigest(),
            "n_inputs": core.n_inputs,
            "n_outputs": core.n_outputs,
            "n_state": sum(isinstance(c, Latch) for c in core.cells),
            "count": len(runs),
            "runs": runs,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(fixtures, indent=1) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(circuits)} circuits, {len(runs)} episodes")


if __name__ == "__main__":
    main()
