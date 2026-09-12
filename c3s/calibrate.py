"""A fixed, published procedure for choosing the teacher's free parameters.

The connectome fixes the *ratios* of velocity to size drive in each pathway. It
does not fix thresholds or motor timing, so those are chosen here by grid search
against qualitative targets taken from the literature, evaluated on the `train`
stimulus family only. The `holdout` family is never used for calibration.

Constraints (all must hold):
  C1  escape rate across the family >= 0.9 (design target, not a literature value)
  C2  short-mode fraction is non-increasing in l/v, >= 0.6 at the fastest loom and
      <= 0.2 at the slowest (fast looms bias escapes towards the GF-mediated short
      mode; von Reyn et al. 2017)
  C3  silencing LPLC2 lowers the mean short-mode fraction to <= 0.7 x intact
      (LPLC2 is necessary for GF-mediated escape; Ache et al. 2019)

Selection among satisfying points (a heuristic, stated so it can be challenged):
  smallest |mean short-mode fraction - 0.4|, then fewer wing-raise ticks, then
  the lower GF threshold, then the lower parallel threshold.
"""

from __future__ import annotations

import itertools
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

from . import loom

GRID = {
    "gf_threshold": [round(float(x), 2) for x in np.arange(2.0, 6.01, 0.25)],
    "parallel_threshold": [round(float(x), 2) for x in np.arange(2.0, 8.01, 0.5)],
    "wing_raise_ticks": [2, 3, 4, 5, 6],
}


def evaluate(p: loom.TeacherParams, subgraph: Path | str, family: str = "train", override: dict | None = None) -> dict:
    stims = loom.family(family)
    lvs = sorted({s.l_over_v_ms for s in stims})

    def run(params: loom.TeacherParams) -> tuple[list[float], float]:
        w = loom.load_weights(subgraph, params, override)
        by_lv: dict[float, list[int]] = {}
        for s in stims:
            by_lv.setdefault(s.l_over_v_ms, []).append(loom.run_teacher_episode(s, w, params).action)
        short = [float(np.mean([a == loom.CORE_SHORT for a in by_lv[lv]])) for lv in lvs]
        esc = float(np.mean([a != loom.CORE_HOLD for v in by_lv.values() for a in v]))
        return short, esc

    short, esc = run(p)
    short_lesion, esc_lesion = run(replace(p, lesion=("LPLC2",)))
    return {
        "l_over_v_ms": lvs,
        "short_fraction": short,
        "escape_rate": esc,
        "lplc2_lesion_short_fraction": short_lesion,
        "lplc2_lesion_escape_rate": esc_lesion,
    }


def satisfies(ev: dict) -> dict[str, bool]:
    sh = ev["short_fraction"]
    return {
        "C1": ev["escape_rate"] >= 0.9,
        "C2": all(b <= a + 1e-9 for a, b in zip(sh, sh[1:])) and sh[0] >= 0.6 and sh[-1] <= 0.2,
        "C3": float(np.mean(ev["lplc2_lesion_short_fraction"])) <= 0.7 * float(np.mean(sh)),
    }


def calibrate(
    subgraph: Path | str, base: loom.TeacherParams | None = None, grid: dict | None = None, override: dict | None = None
) -> dict:
    base = base or loom.TeacherParams()
    grid = grid or GRID
    passing = []
    n = 0
    for gt, pt, wr in itertools.product(grid["gf_threshold"], grid["parallel_threshold"], grid["wing_raise_ticks"]):
        n += 1
        p = replace(base, gf_threshold=gt, parallel_threshold=pt, wing_raise_ticks=wr)
        ev = evaluate(p, subgraph, override=override)
        if all(satisfies(ev).values()):
            key = (abs(float(np.mean(ev["short_fraction"])) - 0.4), wr, gt, pt)
            passing.append((key, p, ev))
    result = {"grid": grid, "points_evaluated": n, "points_satisfying": len(passing)}
    if passing:
        passing.sort(key=lambda t: t[0])
        _, p, ev = passing[0]
        result["selected"] = asdict(p)
        result["selected_evaluation"] = ev
        result["runner_up"] = [asdict(q) for _, q, _ in passing[1:4]]
    return result


def params_from_dict(d: dict) -> loom.TeacherParams:
    d = dict(d)
    d["lesion"] = tuple(d.get("lesion", ()))
    return loom.TeacherParams(**d)
