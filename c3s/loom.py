"""LoomEscape: an explicit teacher for the giant-fiber escape decision.

Everything in this module is a *model*. Where a modelling choice comes from the
literature or from the MaleCNS measurement it is cited; everything else is a
labelled assumption of this repository and lives in `TeacherParams`.

Structure of the teacher (one eye shown; the two eyes are independent):

    angular size  theta   --> LPLC2 drive  r_s = exp(-(theta - mu)^2 / (2 sigma^2))
    angular speed theta'  --> LC4 drive    r_v = theta' / v_scale

    V_GF = (n_LC4->GF * r_v + n_LPLC2->GF * r_s) / 1000     # synapse counts, MaleCNS v1.0
    V_P  = (n_LC4->P  * r_v + n_LPLC2->P  * r_s) / 1000     # P = candidate parallel DNs

    GF fires when V_GF >= gf_threshold; the parallel pathway engages when
    V_P >= parallel_threshold.

Literature basis (abstract-level): GF looming responses are well described by a
sum of a linear function of angular velocity (LC4) and a Gaussian function of
angular size (LPLC2) [Ache et al. 2019]; integration of the two features in the
GF is linear [von Reyn et al. 2017]; the GF has a higher activation threshold
than parallel escape circuits and its spike timing relative to them selects
short- versus long-mode takeoff [von Reyn et al. 2014].

Assumptions of this repository (not claims about the fly): instantaneous
drive with no synaptic delay; synapse count as a proxy for weight; the choice of
DNp02/DNp04/DNp06/DNp11 as the parallel pathway; all numeric parameters in
`TeacherParams`; binocular visibility rule; the motor state machine.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Encoding (15 input bits, 2 output bits)
# ---------------------------------------------------------------------------

# Size bin k (1..7) covers [SIZE_EDGES[k-1], SIZE_EDGES[k]) degrees; bin 0 is
# anything below 10 degrees (treated as "no loom yet").
SIZE_EDGES_DEG = (10.0, 15.0, 22.0, 33.0, 50.0, 75.0, 110.0, 180.0)
# Speed bin k (1..7) covers [VEL_EDGES[k-1], VEL_EDGES[k]) deg/s; bin 0 is below
# 50 deg/s; bin 7 is open-ended.
VEL_EDGES_DPS = (50.0, 100.0, 200.0, 400.0, 800.0, 1600.0, 3200.0, 12800.0)
SIZE_FLOOR_DEG = 1.0
VEL_FLOOR_DPS = 5.0

INPUT_NAMES = (
    "size_L[0]", "size_L[1]", "size_L[2]",
    "speed_L[0]", "speed_L[1]", "speed_L[2]",
    "size_R[0]", "size_R[1]", "size_R[2]",
    "speed_R[0]", "speed_R[1]", "speed_R[2]",
    "standing", "wings_raised", "refractory",
)
N_INPUTS = len(INPUT_NAMES)
OUTPUT_NAMES = ("action[0]", "action[1]")

HOLD, LONG_PROGRAM, SHORT_TAKEOFF = 0, 1, 2
ACTION_NAMES = {HOLD: "hold", LONG_PROGRAM: "long-mode program", SHORT_TAKEOFF: "short-mode takeoff"}


def size_bin(theta_deg: float) -> int:
    if theta_deg < SIZE_EDGES_DEG[0]:
        return 0
    for k in range(1, 8):
        if theta_deg < SIZE_EDGES_DEG[k]:
            return k
    return 7


def speed_bin(dtheta_dps: float) -> int:
    if dtheta_dps < VEL_EDGES_DPS[0]:
        return 0
    for k in range(1, 7):
        if dtheta_dps < VEL_EDGES_DPS[k]:
            return k
    return 7


def size_range(k: int) -> tuple[float, float]:
    return (SIZE_FLOOR_DEG, SIZE_EDGES_DEG[0]) if k == 0 else (SIZE_EDGES_DEG[k - 1], SIZE_EDGES_DEG[k])


def speed_range(k: int) -> tuple[float, float]:
    return (VEL_FLOOR_DPS, VEL_EDGES_DPS[0]) if k == 0 else (VEL_EDGES_DPS[k - 1], VEL_EDGES_DPS[k])


def pack_row(size_l: int, speed_l: int, size_r: int, speed_r: int, standing: int, wings: int, refr: int) -> int:
    return size_l | speed_l << 3 | size_r << 6 | speed_r << 9 | standing << 12 | wings << 13 | refr << 14


def unpack_row(r: int) -> tuple[int, int, int, int, int, int, int]:
    return (r & 7, (r >> 3) & 7, (r >> 6) & 7, (r >> 9) & 7, (r >> 12) & 1, (r >> 13) & 1, (r >> 14) & 1)


# ---------------------------------------------------------------------------
# Looming geometry
# ---------------------------------------------------------------------------


def theta_at(l_over_v_s: float, time_to_contact_s: float) -> float:
    """Full angular size (deg) of a disc of half-size l approaching at speed v."""
    return math.degrees(2.0 * math.atan(l_over_v_s / max(time_to_contact_s, 1e-9)))


def dtheta_at(l_over_v_s: float, time_to_contact_s: float) -> float:
    """Angular expansion speed (deg/s)."""
    a, t = l_over_v_s, max(time_to_contact_s, 1e-9)
    return math.degrees(2.0 * a / (t * t + a * a))


def l_over_v_from_snapshot(theta_deg: float, dtheta_dps: float) -> float:
    """For a constant-velocity approach, (theta, theta') determines l/v exactly:
    l/v = 2 sin^2(theta/2) / theta'   (theta in rad, theta' in rad/s)."""
    return 2.0 * math.sin(math.radians(theta_deg) / 2.0) ** 2 / math.radians(dtheta_dps)


# ---------------------------------------------------------------------------
# Teacher
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Weights:
    """Synapse counts per eye side (ipsilateral inputs only)."""

    gf_lc4: float
    gf_lplc2: float
    par_lc4: float
    par_lplc2: float


@dataclass(frozen=True)
class TeacherParams:
    size_mu_deg: float = 60.0
    size_sigma_deg: float = 30.0
    speed_scale_dps: float = 1000.0
    gf_threshold: float = 2.0
    parallel_threshold: float = 2.0
    lesion: tuple[str, ...] = ()  # cell types silenced, e.g. ("LPLC2",)
    symmetric: bool = False  # average left/right synapse counts
    samples_per_bin: int = 64
    seed: int = 0
    # motor state machine (units: ticks)
    tick_ms: float = 5.0
    wing_raise_ticks: int = 4
    refractory_ticks: int = 7


def load_weights(subgraph_json: Path | str, params: TeacherParams) -> dict[str, Weights]:
    d = json.loads(Path(subgraph_json).read_text())
    w: dict[str, dict[str, float]] = {}
    for g in d["giant_fibers"]:
        side = g["instance"].rsplit("_", 1)[-1]
        w[side] = {
            "gf_lc4": g["inputs"]["LC4"]["synapses"],
            "gf_lplc2": g["inputs"]["LPLC2"]["synapses"],
        }
    for side, entry in d["parallel_dn_candidates"]["by_postsynaptic_side"].items():
        w[side]["par_lc4"] = entry["LC4"]["ipsilateral"]
        w[side]["par_lplc2"] = entry["LPLC2"]["ipsilateral"]
    if params.symmetric:
        avg = {k: (w["L"][k] + w["R"][k]) / 2 for k in w["L"]}
        w = {"L": dict(avg), "R": dict(avg)}
    for side in w:
        if "LC4" in params.lesion:
            w[side]["gf_lc4"] = w[side]["par_lc4"] = 0.0
        if "LPLC2" in params.lesion:
            w[side]["gf_lplc2"] = w[side]["par_lplc2"] = 0.0
    return {s: Weights(**v) for s, v in w.items()}


def drives(theta_deg: np.ndarray, dtheta_dps: np.ndarray, w: Weights, p: TeacherParams) -> tuple[np.ndarray, np.ndarray]:
    r_s = np.exp(-((theta_deg - p.size_mu_deg) ** 2) / (2.0 * p.size_sigma_deg**2))
    r_v = np.maximum(dtheta_dps, 0.0) / p.speed_scale_dps
    v_gf = (w.gf_lc4 * r_v + w.gf_lplc2 * r_s) / 1000.0
    v_par = (w.par_lc4 * r_v + w.par_lplc2 * r_s) / 1000.0
    return v_gf, v_par


@dataclass
class EyeTable:
    """Per-eye decision for each (size bin, speed bin): majority label and purity."""

    gf: np.ndarray  # (8, 8) bool
    par: np.ndarray  # (8, 8) bool
    gf_purity: np.ndarray  # (8, 8) fraction of samples agreeing with the majority
    par_purity: np.ndarray


def eye_table(w: Weights, p: TeacherParams, side: str) -> EyeTable:
    rng = np.random.default_rng([p.seed, 0 if side == "L" else 1])
    gf = np.zeros((8, 8), bool)
    par = np.zeros((8, 8), bool)
    gfp = np.ones((8, 8))
    parp = np.ones((8, 8))
    n = p.samples_per_bin
    for s in range(8):
        lo, hi = size_range(s)
        for v in range(8):
            vlo, vhi = speed_range(v)
            th = np.exp(rng.uniform(math.log(lo), math.log(hi), n))
            dth = np.exp(rng.uniform(math.log(vlo), math.log(vhi), n))
            if s == 0:  # below loom onset: no drive by definition
                g_on = np.zeros(n, bool)
                p_on = np.zeros(n, bool)
            else:
                v_gf, v_par = drives(th, dth, w, p)
                g_on = v_gf >= p.gf_threshold
                p_on = v_par >= p.parallel_threshold
            gf[s, v] = g_on.mean() >= 0.5
            par[s, v] = p_on.mean() >= 0.5
            gfp[s, v] = max(g_on.mean(), 1 - g_on.mean())
            parp[s, v] = max(p_on.mean(), 1 - p_on.mean())
    return EyeTable(gf, par, gfp, parp)


def policy(gf_any: bool, par_any: bool, standing: int, wings: int, refr: int) -> int:
    if not standing or refr:
        return HOLD
    if gf_any:
        return LONG_PROGRAM if wings else SHORT_TAKEOFF
    if par_any:
        return LONG_PROGRAM
    return HOLD


@dataclass
class DecisionTable:
    params: TeacherParams
    weights: dict[str, Weights]
    eyes: dict[str, EyeTable]
    table: np.ndarray  # uint64, length 2**15, value = action

    def to_json(self) -> dict:
        return {
            "format": "c3s.decision-table/1",
            "inputs": list(INPUT_NAMES),
            "outputs": list(OUTPUT_NAMES),
            "actions": {str(k): v for k, v in ACTION_NAMES.items()},
            "params": asdict(self.params),
            "weights": {s: asdict(w) for s, w in self.weights.items()},
            "eye_tables": {
                s: {
                    "gf": e.gf.astype(int).tolist(),
                    "parallel": e.par.astype(int).tolist(),
                    "gf_min_purity": round(float(e.gf_purity.min()), 4),
                    "parallel_min_purity": round(float(e.par_purity.min()), 4),
                    "gf_mean_purity": round(float(e.gf_purity.mean()), 4),
                    "parallel_mean_purity": round(float(e.par_purity.mean()), 4),
                }
                for s, e in self.eyes.items()
            },
            "table_hex": self.table.astype(np.uint8).tobytes().hex(),
        }


def build_table(subgraph_json: Path | str, p: TeacherParams) -> DecisionTable:
    w = load_weights(subgraph_json, p)
    eyes = {s: eye_table(w[s], p, s) for s in ("L", "R")}
    table = np.zeros(1 << N_INPUTS, dtype=np.uint64)
    for r in range(1 << N_INPUTS):
        sl, vl, sr, vr, st, wg, rf = unpack_row(r)
        gf_any = bool(eyes["L"].gf[sl, vl] or eyes["R"].gf[sr, vr])
        par_any = bool(eyes["L"].par[sl, vl] or eyes["R"].par[sr, vr])
        table[r] = policy(gf_any, par_any, st, wg, rf)
    return DecisionTable(p, w, eyes, table)


# ---------------------------------------------------------------------------
# Motor state machine and episodes
# ---------------------------------------------------------------------------

CORE_HOLD, CORE_RAISING, CORE_SHORT, CORE_LONG = 0, 1, 2, 3
CORE_ACTION_NAMES = {0: "hold", 1: "raising wings", 2: "short-mode takeoff", 3: "long-mode takeoff"}


def core_step(action: int, raise_count: int, refr: int, p: TeacherParams) -> tuple[int, int, int]:
    """Shared motor state machine. Returns (core output, next raise count, next refractory)."""
    wings_raised = raise_count >= p.wing_raise_ticks
    if action == SHORT_TAKEOFF:
        out = CORE_SHORT
    elif action == LONG_PROGRAM:
        out = CORE_LONG if wings_raised else CORE_RAISING
    else:
        out = CORE_HOLD
    took_off = out in (CORE_SHORT, CORE_LONG)
    nxt_raise = 0 if (action == HOLD or took_off) else min(raise_count + 1, p.wing_raise_ticks)
    nxt_refr = p.refractory_ticks if took_off else max(refr - 1, 0)
    return out, nxt_raise, nxt_refr


def visible(azimuth_deg: float) -> tuple[bool, bool]:
    """Binocular visibility rule (assumption): each eye covers its own hemifield
    plus a 30-degree overlap across the midline. Negative azimuth = left."""
    return azimuth_deg <= 30.0, azimuth_deg >= -30.0


@dataclass(frozen=True)
class Stimulus:
    l_over_v_ms: float
    azimuth_deg: float
    start_deg: float = 10.0
    end_deg: float = 170.0


def stimulus_samples(stim: Stimulus, p: TeacherParams) -> list[tuple[float, float]]:
    """(theta, theta') at each tick from onset until end size."""
    a = stim.l_over_v_ms / 1000.0
    t_start = a / math.tan(math.radians(stim.start_deg) / 2.0)
    t_end = a / math.tan(math.radians(stim.end_deg) / 2.0)
    dt = p.tick_ms / 1000.0
    out = []
    t = t_start
    while t > t_end:
        out.append((theta_at(a, t), dtheta_at(a, t)))
        t -= dt
    return out


@dataclass
class Outcome:
    action: int  # CORE_* of the first takeoff, or CORE_HOLD if none
    tick: int | None
    size_at_takeoff_deg: float | None


def run_teacher_episode(stim: Stimulus, w: dict[str, Weights], p: TeacherParams) -> Outcome:
    """Continuous (unquantised) teacher, ticked with the shared state machine."""
    see_l, see_r = visible(stim.azimuth_deg)
    raise_count = refr = 0
    for i, (th, dth) in enumerate(stimulus_samples(stim, p)):
        onset = th >= SIZE_EDGES_DEG[0]
        gf_any = par_any = False
        for side, seen in (("L", see_l), ("R", see_r)):
            if seen and onset:
                g, q = drives(np.array([th]), np.array([dth]), w[side], p)
                gf_any |= bool(g[0] >= p.gf_threshold)
                par_any |= bool(q[0] >= p.parallel_threshold)
        action = policy(gf_any, par_any, 1, int(raise_count >= p.wing_raise_ticks), int(refr > 0))
        out, raise_count, refr = core_step(action, raise_count, refr, p)
        if out in (CORE_SHORT, CORE_LONG):
            return Outcome(out, i, th)
    return Outcome(CORE_HOLD, None, None)


def encode_features(th: float, dth: float, azimuth_deg: float) -> int:
    """The 12 feature bits for one tick of a stimulus."""
    see_l, see_r = visible(azimuth_deg)
    sb, vb = size_bin(th), speed_bin(dth)
    sl, vl = (sb, vb) if see_l else (0, 0)
    sr, vr = (sb, vb) if see_r else (0, 0)
    return sl | vl << 3 | sr << 6 | vr << 9


def family(name: str) -> list[Stimulus]:
    """Stimulus families. `train` and `holdout` do not overlap in l/v or azimuth."""
    if name == "train":
        lvs = [10, 15, 20, 30, 40, 60, 80]
        azs = [-60, -20, 0, 20, 60]
    elif name == "holdout":
        lvs = [12, 25, 35, 50, 70, 100, 140]
        azs = [-75, -40, -10, 10, 40, 75]
    else:
        raise KeyError(name)
    return [Stimulus(float(lv), float(az)) for lv in lvs for az in azs]


def with_params(p: TeacherParams, **kw) -> TeacherParams:
    return replace(p, **kw)
