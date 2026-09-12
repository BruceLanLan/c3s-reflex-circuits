"""Controls: does the measured wiring constrain the escape behaviour?

    python scripts/run_controls.py

The connectome enters the teacher only through four synapse counts per side:
(GF <- LC4, GF <- LPLC2, parallel <- LC4, parallel <- LPLC2). The controls
reassign those measured counts to the wrong slots, exhaustively and without
randomness:

* all 23 non-identity permutations of the four slots (the same permutation on
  both sides), which include the GF/parallel swap and the LC4/LPLC2 swap;
* left/right symmetrisation of the measured counts.

Each control is evaluated twice:

1. **recalibrated** with exactly the same procedure and grid as the measured
   wiring (`c3s.calibrate`): are the literature-derived constraints C1-C3 still
   satisfiable, how many grid points pass, and how different is the resulting
   decision table and train-family behaviour?
2. **thresholds held fixed** at the measured calibration: how much does the
   behaviour change when only the wiring is wrong?

Interpretation, written before the results were computed:

* If most permutations remain satisfiable after recalibration, the measured
  wiring constrains the *ratios* of feature drive but not the qualitative
  behaviour; the thresholds (free parameters) absorb the difference. That is a
  legitimate finding about this teacher, not a failure of the pipeline.
* If few or no permutations are satisfiable, the measured counts carry
  behavioural constraint that the free parameters cannot absorb.
* The fixed-threshold variant separates "structure matters" from "thresholds
  absorb structure": large differences there with high recalibrated
  satisfiability would mean the constraint is real but not identifiable from
  C1-C3 alone.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

from c3s import calibrate, loom

ROOT = Path(__file__).resolve().parents[1]
SUBGRAPH = ROOT / "data" / "malecns-v1.0-gf-escape-subgraph.json"
OUT = ROOT / "circuits" / "loom-escape" / "controls.json"
SLOTS = ("gf_lc4", "gf_lplc2", "par_lc4", "par_lplc2")
NAMED = {
    (2, 3, 0, 1): "GF <-> parallel swap",
    (1, 0, 3, 2): "LC4 <-> LPLC2 swap",
    (3, 2, 1, 0): "GF/parallel and LC4/LPLC2 swap",
}


def measured_counts() -> dict:
    w = loom.load_weights(SUBGRAPH, loom.TeacherParams())
    return {s: asdict(w[s]) for s in ("L", "R")}


def train_outcomes(p: loom.TeacherParams, override: dict | None) -> list[tuple[int, int | None]]:
    w = loom.load_weights(SUBGRAPH, p, override)
    return [(o.action, o.tick) for o in (loom.run_teacher_episode(s, w, p) for s in loom.family("train"))]


def compare(table: np.ndarray, outcomes, ref_table: np.ndarray, ref_outcomes) -> dict:
    return {
        "table_rows_differing": int(np.count_nonzero(table != ref_table)),
        "train_episodes_mode_differing": int(sum(a[0] != b[0] for a, b in zip(outcomes, ref_outcomes))),
        "train_episodes_timing_differing": int(sum(a != b for a, b in zip(outcomes, ref_outcomes))),
    }


def run_variant(label: str, override: dict, p_measured: loom.TeacherParams, ref_table, ref_outcomes) -> dict:
    row: dict = {"label": label, "counts": override}
    cal = calibrate.calibrate(SUBGRAPH, override=override)
    row["recalibrated"] = {"points_satisfying": cal["points_satisfying"], "points_evaluated": cal["points_evaluated"]}
    if "selected" in cal:
        p = calibrate.params_from_dict(cal["selected"])
        t = loom.build_table(SUBGRAPH, p, loom.DEFAULT_ENCODING, override).table
        row["recalibrated"]["selected"] = {k: cal["selected"][k] for k in ("gf_threshold", "parallel_threshold", "wing_raise_ticks")}
        row["recalibrated"].update(compare(t, train_outcomes(p, override), ref_table, ref_outcomes))
    ev = calibrate.evaluate(p_measured, SUBGRAPH, override=override)
    t = loom.build_table(SUBGRAPH, p_measured, loom.DEFAULT_ENCODING, override).table
    row["fixed_thresholds"] = {"constraints": calibrate.satisfies(ev), **compare(t, train_outcomes(p_measured, override), ref_table, ref_outcomes)}
    return row


def main() -> None:
    cal = json.loads((ROOT / "circuits" / "loom-escape" / "teacher-calibration.json").read_text())
    p = calibrate.params_from_dict(cal["selected"])
    counts = measured_counts()
    ref_table = loom.build_table(SUBGRAPH, p, loom.DEFAULT_ENCODING).table
    ref_outcomes = train_outcomes(p, None)

    rows = []
    for perm in itertools.permutations(range(4)):
        if perm == (0, 1, 2, 3):
            continue
        override = {s: {SLOTS[i]: counts[s][SLOTS[perm[i]]] for i in range(4)} for s in ("L", "R")}
        label = NAMED.get(perm, "permutation " + "".join(map(str, perm)))
        rows.append(run_variant(label, override, p, ref_table, ref_outcomes) | {"permutation": list(perm)})
        print(label, rows[-1]["recalibrated"].get("points_satisfying"), rows[-1]["fixed_thresholds"]["table_rows_differing"])
    sym = {s: {k: (counts["L"][k] + counts["R"][k]) / 2 for k in SLOTS} for s in ("L", "R")}
    rows.append(run_variant("left/right symmetrised", sym, p, ref_table, ref_outcomes))
    print("symmetrised", rows[-1]["recalibrated"].get("points_satisfying"))

    perms = [r for r in rows if "permutation" in r]
    summary = {
        "measured_points_satisfying": cal["points_satisfying"],
        "permutations_satisfiable_after_recalibration": sum(r["recalibrated"]["points_satisfying"] > 0 for r in perms),
        "permutations_total": len(perms),
        "permutations_meeting_C1_C3_with_fixed_thresholds": sum(all(r["fixed_thresholds"]["constraints"].values()) for r in perms),
    }
    OUT.write_text(json.dumps({"format": "c3s.controls/1", "slots": SLOTS, "measured_counts": counts, "teacher_params": asdict(p), "summary": summary, "variants": rows}, indent=1) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
