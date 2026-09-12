"""Evaluate the frozen circuits on the pre-registered sealed stimulus family.

    python scripts/evaluate_sealed.py --spec path/to/sealed-family.json

The spec's SHA-256 must equal circuits/loom-escape/sealed-family.sha256, which
was committed before the controls and before this evaluation. The result records
the hashes of every upstream artifact it depends on; if any of them changes
later, the sealed result no longer applies to the changed artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from c3s import calibrate, exhaust, loom, reflex
from c3s.netlist import from_bytes

ROOT = Path(__file__).resolve().parents[1]
LOOM = ROOT / "circuits" / "loom-escape"
SUBGRAPH = ROOT / "data" / "malecns-v1.0-gf-escape-subgraph.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", type=Path, required=True)
    args = ap.parse_args()
    raw = args.spec.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    committed = (LOOM / "sealed-family.sha256").read_text().split()[0]
    if digest != committed:
        raise SystemExit(f"spec digest {digest} does not match the committed digest {committed}")
    spec = json.loads(raw)
    stims = [loom.Stimulus(float(s["l_over_v_ms"]), float(s["azimuth_deg"])) for s in spec["stimuli"]]

    tj = json.loads((LOOM / "decision-table.json").read_text())
    p = calibrate.params_from_dict(tj["params"])
    enc = loom.Encoding(**{k: tuple(v) if isinstance(v, list) else v for k, v in tj["encoding"].items()})
    table = loom.build_table(SUBGRAPH, p, enc)
    if hashlib.sha256(table.table.astype(np.uint8).tobytes()).hexdigest() != tj["table_sha256"]:
        raise SystemExit("rebuilt decision table does not match the committed one")
    weights = loom.load_weights(SUBGRAPH, p)

    cores = {}
    m = json.loads((LOOM / "core-hand-abc.json").read_text())
    cores["core-hand-abc"] = from_bytes(bytes.fromhex(m["tapeout_netlist_hex"][2:]), len(m["inputs"]), 2)
    for f in sorted(LOOM.glob("dlgn-*.json")):
        d = json.loads(f.read_text())
        policy = from_bytes(bytes.fromhex(d["tapeout_netlist_hex"][2:]), enc.n_inputs, 2)
        cores[f"core-{d['tag']}"] = reflex.escape_core(policy, p)
    tables = {name: exhaust.step_table(c) for name, c in cores.items()}

    rows = []
    for s in stims:
        t = loom.run_teacher_episode(s, weights, p)
        q = loom.run_quantized_teacher_episode(s, table)
        row = {"l_over_v_ms": s.l_over_v_ms, "azimuth_deg": s.azimuth_deg, "teacher": [t.action, t.tick], "quantised_teacher": [q.action, q.tick]}
        for name, (outs, nxt) in tables.items():
            c = reflex.run_core_episode(outs, nxt, s, p, enc)
            row[name] = [c.action, c.tick]
        rows.append(row)

    def summarise(key: str) -> dict:
        mode = [r[key][0] == r["teacher"][0] for r in rows]
        escape = [(r[key][0] == loom.CORE_HOLD) == (r["teacher"][0] == loom.CORE_HOLD) for r in rows]
        dt = [abs(r[key][1] - r["teacher"][1]) for r in rows if r[key][1] is not None and r["teacher"][1] is not None]
        return {
            "mode_agreement": round(float(np.mean(mode)), 4),
            "escape_agreement": round(float(np.mean(escape)), 4),
            "mean_abs_takeoff_tick_difference": round(float(np.mean(dt)), 3) if dt else None,
        }

    keys = ["quantised_teacher"] + list(tables)
    result = {
        "format": "c3s.sealed-evaluation/1",
        "sealed_family_sha256": digest,
        "stimuli": len(stims),
        "depends_on": {
            "decision_table_sha256": tj["table_sha256"],
            "teacher_calibration_file_sha256": sha(LOOM / "teacher-calibration.json"),
            "subgraph_file_sha256": sha(SUBGRAPH),
            "encoding": enc.name,
            "core_netlist_sha256": {k: hashlib.sha256(bytes.fromhex(json.loads((LOOM / "core-hand-abc.json").read_text())["tapeout_netlist_hex"][2:])).hexdigest() for k in ["core-hand-abc"]},
        },
        "summary": {k: summarise(k) for k in keys},
        "teacher_action_counts": {loom.CORE_ACTION_NAMES[a]: int(c) for a, c in zip(*np.unique([r["teacher"][0] for r in rows], return_counts=True))},
        "rows": rows,
    }
    (LOOM / "sealed-evaluation.json").write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result["summary"], indent=1))


if __name__ == "__main__":
    main()
