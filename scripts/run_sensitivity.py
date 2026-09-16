"""Identifiability of the three free teacher parameters (v0.3.0, step S3).

    python scripts/run_sensitivity.py       # writes circuits/loom-escape/sensitivity.json

Question
--------
Calibration picked one point on a 1,105-point grid -- gf_threshold,
parallel_threshold, wing_raise_ticks -- by the constraints C1-C3 and then a
tie-break (short-mode fraction closest to 0.4). Many grid points satisfy those
constraints. So how much of the published circuit is pinned down by the measured
wiring plus the constraints, and how much by that tie-break?

Method
------
Re-enumerate the whole grid with `calibrate.evaluate`/`calibrate.satisfies`, keeping
the feasible points, and cross-check the count against the committed
teacher-calibration.json. For each feasible point build the decision table and the
escape core. The table depends only on the two thresholds, so tables are grouped by
SHA-256 and synthesised once per distinct table (ABC, the repository's five recipes,
each result re-verified against the table by `synth.synthesize_table`). Recorded per
point: its table's digest, the synthesised policy's NAND count, the core's NAND count
(which also moves with wing_raise_ticks, since it sets the wing counter's width), and
how many of the 65,536 rows differ from the selected point's table.

Pre-set judgement (committed before the run)
--------------------------------------------
  H1  invariant rows: the fraction of the 65,536 table rows on which every feasible
      point agrees.
        >= 95 %  behaviour is pinned by the wiring and the constraints, and the free
                 parameters are only weakly identified by the data
        <= 60 %  the parameters carry most of the behaviour, so the tie-break matters
        between: partial identification, reported as such
  H2  cost stability: over distinct feasible tables, (max - min) policy NAND as a
      fraction of the median.
        <= 10 %  circuit size is insensitive to the parameters
        >  10 %  size depends on the parameters, reported with the range
  H3  typicality of the published point: its policy-NAND percentile, and the
      percentile of its table's mean row distance to the other feasible tables.
        both inside the central 80 %  the published circuit is a typical feasible
                                      circuit rather than an outlier
        outside                        said plainly, with the percentiles

The numbers are reported whatever they turn out to be; these thresholds only fix in
advance which sentence gets written about them.

Policy NAND counts depend on the ABC build, so the artifact is informational, like
the other ABC-dependent outputs; scripts/verify.sh does not rerun this script.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

from c3s import calibrate, loom, reflex, synth

ROOT = Path(__file__).resolve().parents[1]
LOOM = ROOT / "circuits" / "loom-escape"
SUBGRAPH = ROOT / "data" / "malecns-v1.0-gf-escape-subgraph.json"
OUT = LOOM / "sensitivity.json"
INVARIANT_WEAK, INVARIANT_STRONG = 0.95, 0.60
COST_SPREAD = 0.10
CENTRAL = (10.0, 90.0)


def percentile_of(values: list[float], x: float) -> float:
    """Percentile rank of x among values, counting ties as half."""
    arr = np.asarray(values, dtype=float)
    below = float(np.count_nonzero(arr < x))
    equal = float(np.count_nonzero(arr == x))
    return round(100.0 * (below + 0.5 * equal) / arr.size, 1)


def main() -> None:
    tj = json.loads((LOOM / "decision-table.json").read_text())
    enc = loom.Encoding(**{k: tuple(v) if isinstance(v, list) else v for k, v in tj["encoding"].items()})
    selected = calibrate.params_from_dict(tj["params"])
    committed = json.loads((LOOM / "teacher-calibration.json").read_text())

    feasible: list[loom.TeacherParams] = []
    evaluated = 0
    for gt, pt, wr in itertools.product(*(calibrate.GRID[k] for k in ("gf_threshold", "parallel_threshold", "wing_raise_ticks"))):
        p = replace(selected, gf_threshold=gt, parallel_threshold=pt, wing_raise_ticks=wr)
        evaluated += 1
        if all(calibrate.satisfies(calibrate.evaluate(p, SUBGRAPH)).values()):
            feasible.append(p)
    print(f"grid {evaluated} points, feasible {len(feasible)}", flush=True)
    cross = {
        "committed_points_evaluated": committed.get("points_evaluated"),
        "committed_points_satisfying": committed.get("points_satisfying"),
        "recomputed_points_evaluated": evaluated,
        "recomputed_points_satisfying": len(feasible),
    }
    cross["equal"] = (cross["committed_points_evaluated"], cross["committed_points_satisfying"]) == (evaluated, len(feasible))
    if not cross["equal"]:
        raise SystemExit(f"re-enumeration disagrees with the committed calibration: {cross}")

    # One synthesis per distinct table; the table depends only on the two thresholds.
    groups: dict[str, dict] = {}
    point_digest: list[str] = []
    for p in feasible:
        table = loom.build_table(SUBGRAPH, p, enc).table
        digest = hashlib.sha256(table.tobytes()).hexdigest()
        point_digest.append(digest)
        g = groups.setdefault(digest, {"table": table, "gf_thresholds": set(), "parallel_thresholds": set(), "points": 0})
        g["gf_thresholds"].add(p.gf_threshold)
        g["parallel_thresholds"].add(p.parallel_threshold)
        g["points"] += 1
    print(f"distinct tables {len(groups)}", flush=True)

    for digest, g in groups.items():
        circuit, report = synth.synthesize_table(g["table"], enc.n_inputs, 2)
        g["circuit"] = circuit
        g["policy_nand"] = circuit.metrics()["nand"]
        g["policy_depth"] = circuit.metrics()["depth"]
        g["abc_recipe"] = report["selected"]
        print(f"table {digest[:12]} points {g['points']} policy {g['policy_nand']} NAND ({g['abc_recipe']})", flush=True)

    sel_digest = hashlib.sha256(loom.build_table(SUBGRAPH, selected, enc).table.tobytes()).hexdigest()
    if sel_digest not in groups:
        raise SystemExit("the published point is not in the feasible set")

    stack = np.stack([g["table"] for g in groups.values()])
    weights = np.array([g["points"] for g in groups.values()])
    invariant_rows = int(np.count_nonzero((stack == stack[0]).all(axis=0)))
    rows = stack.shape[1]
    for digest, g in groups.items():
        g["rows_differing_from_published"] = int(np.count_nonzero(g["table"] != groups[sel_digest]["table"]))
        others = np.count_nonzero(stack != g["table"], axis=1)
        g["mean_rows_differing_from_other_tables"] = round(float((others * weights).sum() / weights.sum()), 1)

    per_point = []
    for p, digest in zip(feasible, point_digest):
        core = reflex.escape_core(groups[digest]["circuit"], p)
        per_point.append(
            {
                "gf_threshold": p.gf_threshold,
                "parallel_threshold": p.parallel_threshold,
                "wing_raise_ticks": p.wing_raise_ticks,
                "table_sha256": digest,
                "policy_nand": groups[digest]["policy_nand"],
                "core_nand": core.metrics()["nand"],
                "core_latch": core.metrics()["latch"],
            }
        )

    nands = [g["policy_nand"] for g in groups.values()]
    spread = (max(nands) - min(nands)) / float(np.median(nands))
    dists = [g["mean_rows_differing_from_other_tables"] for g in groups.values()]
    h1 = invariant_rows / rows
    sel = groups[sel_digest]
    nand_pct = percentile_of(nands, sel["policy_nand"])
    dist_pct = percentile_of(dists, sel["mean_rows_differing_from_other_tables"])
    central = CENTRAL[0] <= nand_pct <= CENTRAL[1] and CENTRAL[0] <= dist_pct <= CENTRAL[1]

    result = {
        "format": "c3s.sensitivity/1",
        "question": "how much of the published circuit is fixed by the wiring and constraints C1-C3, and how much by the tie-break",
        "grid": calibrate.GRID,
        "points_evaluated": evaluated,
        "points_feasible": len(feasible),
        "calibration_cross_check": cross,
        "published_point": {
            "params": {k: asdict(selected)[k] for k in ("gf_threshold", "parallel_threshold", "wing_raise_ticks")},
            "table_sha256": sel_digest,
            "policy_nand": sel["policy_nand"],
        },
        "distinct_tables": sorted(
            (
                {
                    "table_sha256": d,
                    "points": g["points"],
                    "gf_thresholds": sorted(g["gf_thresholds"]),
                    "parallel_thresholds": sorted(g["parallel_thresholds"]),
                    "policy_nand": g["policy_nand"],
                    "policy_depth": g["policy_depth"],
                    "abc_recipe": g["abc_recipe"],
                    "rows_differing_from_published": g["rows_differing_from_published"],
                    "mean_rows_differing_from_other_tables": g["mean_rows_differing_from_other_tables"],
                }
                for d, g in groups.items()
            ),
            key=lambda e: (-e["points"], e["table_sha256"]),
        ),
        "per_point": per_point,
        "hypothesis": {
            "preregistered_in": "scripts/run_sensitivity.py docstring",
            "thresholds": {
                "invariant_rows_weakly_identified": INVARIANT_WEAK,
                "invariant_rows_parameters_dominate": INVARIANT_STRONG,
                "cost_spread_insensitive": COST_SPREAD,
                "typicality_central_percentiles": list(CENTRAL),
            },
            "H1": {
                "table_rows": rows,
                "invariant_rows": invariant_rows,
                "fraction": round(h1, 4),
                "verdict": "weakly identified" if h1 >= INVARIANT_WEAK else "parameters dominate" if h1 <= INVARIANT_STRONG else "partially identified",
            },
            "H2": {
                "policy_nand_min": min(nands),
                "policy_nand_median": float(np.median(nands)),
                "policy_nand_max": max(nands),
                "spread": round(spread, 4),
                "verdict": "insensitive" if spread <= COST_SPREAD else "size depends on the parameters",
            },
            "H3": {
                "policy_nand_percentile": nand_pct,
                "mean_row_distance_percentile": dist_pct,
                "verdict": "typical feasible circuit" if central else "not central; see the percentiles",
            },
        },
        "abc": synth.abc_version(),
        "bytes": "policy NAND counts depend on the ABC build; this artifact is informational",
    }
    OUT.write_text(json.dumps(result, indent=1) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}")
    for key in ("H1", "H2", "H3"):
        print(key, json.dumps(result["hypothesis"][key]))


if __name__ == "__main__":
    main()
