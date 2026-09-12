"""Compare sensory encodings by how faithfully an exact circuit reproduces the
continuous teacher's escape episodes.

    python scripts/compare_encodings.py

An exact circuit behaves like the teacher seen through the encoding (the
"quantised teacher"), so encoding loss can be measured before any circuit is
built. Selection rule, applied to the train family only: among encodings with at
most 16 input bits, highest mode agreement, then lowest mean takeoff-tick error,
then fewer bits.

Disclosure: this comparison was written after the first 3-bit build showed low
mode agreement, and holdout numbers were printed during that exploration. The
holdout family therefore is not blind with respect to the encoding choice; the
sealed family (see docs/EVALUATION.md) is.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from c3s import calibrate, loom

ROOT = Path(__file__).resolve().parents[1]
SUBGRAPH = ROOT / "data" / "malecns-v1.0-gf-escape-subgraph.json"
OUT = ROOT / "circuits" / "loom-escape" / "encoding-comparison.json"
CANDIDATES = [loom.ENCODING_3BIT, loom.ENCODING_4BIT, loom.ENCODING_4BIT_SPEED50, loom.ENCODING_5BIT]


def score(table: loom.DecisionTable, weights, fam: str) -> dict:
    agree, escape, dt = [], [], []
    for s in loom.family(fam):
        t = loom.run_teacher_episode(s, weights, table.params)
        q = loom.run_quantized_teacher_episode(s, table)
        agree.append(t.action == q.action)
        escape.append((t.action == loom.CORE_HOLD) == (q.action == loom.CORE_HOLD))
        if t.tick is not None and q.tick is not None:
            dt.append(abs(t.tick - q.tick))
    return {
        "mode_agreement": round(float(np.mean(agree)), 4),
        "escape_agreement": round(float(np.mean(escape)), 4),
        "mean_abs_takeoff_tick_difference": round(float(np.mean(dt)), 3) if dt else None,
    }


def main() -> None:
    cal = json.loads((ROOT / "circuits" / "loom-escape" / "teacher-calibration.json").read_text())
    p = calibrate.params_from_dict(cal["selected"])
    weights = loom.load_weights(SUBGRAPH, p)
    rows = []
    for enc in CANDIDATES:
        table = loom.build_table(SUBGRAPH, p, enc)
        rows.append({"encoding": asdict(enc), "n_inputs": enc.n_inputs, "train": score(table, weights, "train"), "holdout": score(table, weights, "holdout")})
        print(enc.name, rows[-1]["train"], rows[-1]["holdout"])
    eligible = [r for r in rows if r["n_inputs"] <= 16]
    best = min(eligible, key=lambda r: (-r["train"]["mode_agreement"], r["train"]["mean_abs_takeoff_tick_difference"], r["n_inputs"]))
    selected = best["encoding"]["name"]
    OUT.write_text(json.dumps({"rule": "train only: max mode agreement, then min mean |tick error|, then fewer bits; <=16 inputs", "selected": selected, "candidates": rows}, indent=1) + "\n")
    print("selected:", selected)
    if selected != loom.DEFAULT_ENCODING.name:
        raise SystemExit(f"DEFAULT_ENCODING ({loom.DEFAULT_ENCODING.name}) disagrees with the selection rule ({selected})")


if __name__ == "__main__":
    main()
