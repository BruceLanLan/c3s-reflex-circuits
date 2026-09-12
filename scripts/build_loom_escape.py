"""End-to-end build of the LoomEscape-16 circuits.

    python scripts/build_loom_escape.py [--params circuits/loom-escape/teacher-calibration.json]

Every stage writes its evidence into circuits/loom-escape/, and every circuit is
checked exhaustively against the stage before it:

    teacher decision table (2^16 rows)
      vs hand-written policy         (rows that differ are reported)
      = ABC re-synthesis of it        (0 rows different, or the build stops)
      vs ABC synthesis of the table   (0 rows different, or the build stops)
    escape core step relation (2^(17 + latches) rows) = vectorised reference
    core episodes = quantised teacher episodes (must agree exactly)
    core episodes vs continuous teacher episodes (train and holdout families)
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from c3s import calibrate, exhaust, loom, reflex, synth
from c3s.netlist import Circuit, to_bytes

ROOT = Path(__file__).resolve().parents[1]
SUBGRAPH = ROOT / "data" / "malecns-v1.0-gf-escape-subgraph.json"
OUT = ROOT / "circuits" / "loom-escape"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest(name: str, circuit: Circuit, inputs, outputs, extra: dict) -> dict:
    raw = to_bytes(circuit)
    m = {
        "format": "c3s.circuit/1",
        "name": name,
        "inputs": list(inputs),
        "outputs": list(outputs),
        "metrics": circuit.metrics(),
        "netlist_sha256": sha256(raw),
        "tapeout_netlist_hex": "0x" + raw.hex(),
    }
    m.update(extra)
    return m


def episode_fidelity(outs, nxt, table: loom.DecisionTable, weights) -> dict:
    p, enc = table.params, table.encoding
    res = {}
    for fam in ("train", "holdout"):
        rows, quantised_equal = [], True
        for s in loom.family(fam):
            t = loom.run_teacher_episode(s, weights, p)
            c = reflex.run_core_episode(outs, nxt, s, p, enc)
            q = loom.run_quantized_teacher_episode(s, table)
            quantised_equal &= (q.action, q.tick) == (c.action, c.tick)
            rows.append(
                {
                    "l_over_v_ms": s.l_over_v_ms,
                    "azimuth_deg": s.azimuth_deg,
                    "teacher": loom.CORE_ACTION_NAMES[t.action],
                    "teacher_tick": t.tick,
                    "circuit": loom.CORE_ACTION_NAMES[c.action],
                    "circuit_tick": c.tick,
                }
            )
        agree = [r["teacher"] == r["circuit"] for r in rows]
        escape = [(r["teacher"] == "hold") == (r["circuit"] == "hold") for r in rows]
        dt = [abs(r["teacher_tick"] - r["circuit_tick"]) for r in rows if r["teacher_tick"] is not None and r["circuit_tick"] is not None]
        res[fam] = {
            "episodes": len(rows),
            "circuit_equals_quantised_teacher": bool(quantised_equal),
            "escape_agreement": round(float(np.mean(escape)), 4),
            "mode_agreement": round(float(np.mean(agree)), 4),
            "mean_abs_takeoff_tick_difference": round(float(np.mean(dt)), 3) if dt else None,
            "max_abs_takeoff_tick_difference": int(max(dt)) if dt else None,
            "rows": rows,
        }
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--params", type=Path, help="reuse a teacher-calibration.json instead of re-running the grid")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    subgraph_sha = sha256(SUBGRAPH.read_bytes())
    enc = loom.DEFAULT_ENCODING

    # 1. teacher calibration (train family only)
    if args.params:
        cal = json.loads(args.params.read_text())
    else:
        cal = calibrate.calibrate(SUBGRAPH)
        cal["subgraph_sha256"] = subgraph_sha
        (OUT / "teacher-calibration.json").write_text(json.dumps(cal, indent=1) + "\n")
    if "selected" not in cal:
        raise SystemExit("no teacher parameters satisfy the calibration constraints")
    p = calibrate.params_from_dict(cal["selected"])
    print("teacher:", {k: v for k, v in asdict(p).items() if k in ("gf_threshold", "parallel_threshold", "wing_raise_ticks")}, "encoding:", enc.name)

    # 2. decision table
    table = loom.build_table(SUBGRAPH, p, enc)
    tj = table.to_json()
    tj["subgraph_sha256"] = subgraph_sha
    tj["table_sha256"] = sha256(table.table.astype(np.uint8).tobytes())
    (OUT / "decision-table.json").write_text(json.dumps(tj, indent=1) + "\n")
    vals, cnts = np.unique(table.table, return_counts=True)
    pathway_counts = {f"gf={int(v) & 1},parallel={int(v) >> 1}": int(c) for v, c in zip(vals, cnts)}
    print("table:", pathway_counts)

    # 3. policies
    hand, info = reflex.hand_policy(table)
    hand_tt = exhaust.truth_table(hand)
    hand_diff = int(np.count_nonzero(hand_tt != table.table))
    hand_abc, hand_abc_report = synth.optimize_circuit(hand)
    if not np.array_equal(exhaust.truth_table(hand_abc), hand_tt):
        raise SystemExit("ABC re-synthesis of the hand policy is not equivalent")
    table_abc, table_abc_report = synth.synthesize_table(table.table, enc.n_inputs, 2)
    table_abc_diff = int(np.count_nonzero(exhaust.truth_table(table_abc) != table.table))
    if table_abc_diff:
        raise SystemExit("ABC synthesis of the table is not equivalent")
    print("hand:", hand.metrics(), "rows differing from teacher:", hand_diff)
    print("hand+abc:", hand_abc.metrics(), hand_abc_report["selected"])
    print("table+abc:", table_abc.metrics(), table_abc_report["selected"])

    policies = {
        "policy-hand": (hand, {"source": "hand-written staircase baseline", "baseline_info": info}),
        "policy-hand-abc": (hand_abc, {"source": "ABC re-synthesis of policy-hand, exhaustively equivalent to it", "synthesis": hand_abc_report}),
        "policy-table-abc": (table_abc, {"source": "ABC synthesis of the full teacher table", "synthesis": table_abc_report}),
    }
    weights = loom.load_weights(SUBGRAPH, p)
    summary = {
        "teacher_params": asdict(p),
        "encoding": asdict(enc),
        "subgraph_sha256": subgraph_sha,
        "table_sha256": tj["table_sha256"],
        "pathway_counts": pathway_counts,
        "circuits": {},
    }
    for name, (circuit, extra) in policies.items():
        tt = exhaust.truth_table(circuit)
        extra["rows_differing_from_teacher"] = int(np.count_nonzero(tt != table.table))
        extra["encoding"] = enc.name
        m = manifest(name, circuit, enc.input_names(), loom.OUTPUT_NAMES, extra)
        (OUT / f"{name}.json").write_text(json.dumps(m, indent=1) + "\n")
        summary["circuits"][name] = {"metrics": m["metrics"], "rows_differing_from_teacher": extra["rows_differing_from_teacher"]}

        core = reflex.escape_core(circuit, p)
        outs, nxt = exhaust.step_table(core)
        ro, rn = reflex.core_reference_tables(tt, p, enc.n_inputs)
        mismatches = int(np.count_nonzero(outs != ro) + np.count_nonzero(nxt != rn))
        fid = episode_fidelity(outs, nxt, table, weights)
        core_name = name.replace("policy", "core")
        cm = manifest(
            core_name,
            core,
            reflex.core_inputs(enc),
            reflex.CORE_OUTPUTS,
            {
                "policy": name,
                "motor_codes": {str(k): v for k, v in loom.CORE_ACTION_NAMES.items()},
                "state_layout": reflex.core_state_layout(p),
                "step_relation_rows_checked": int(len(outs)),
                "step_relation_mismatches": mismatches,
                "episode_fidelity": fid,
            },
        )
        (OUT / f"{core_name}.json").write_text(json.dumps(cm, indent=1) + "\n")
        brief = {f: {k: fid[f][k] for k in ("circuit_equals_quantised_teacher", "escape_agreement", "mode_agreement", "mean_abs_takeoff_tick_difference")} for f in fid}
        summary["circuits"][core_name] = {"metrics": cm["metrics"], "step_relation_rows_checked": int(len(outs)), "step_relation_mismatches": mismatches, "episodes": brief}
        print(core_name, cm["metrics"], "rows", len(outs), "mismatches", mismatches, brief)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")


if __name__ == "__main__":
    main()
