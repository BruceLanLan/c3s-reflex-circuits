"""LoomEscape: an explicit teacher for the giant-fiber escape decision.

Everything in this module is a *model*. Where a modelling choice comes from the
literature or from the MaleCNS measurement it is cited; everything else is a
labelled assumption of this repository and lives in `TeacherParams` or in the
`Encoding`.

Structure of the teacher (one eye shown; the two eyes are independent):

    angular size  theta   --> LPLC2 drive  r_s = exp(-(theta - mu)^2 / (2 sigma^2))
    angular speed theta'  --> LC4 drive    r_v = theta' / v_scale

    V_GF = (n_LC4->GF * r_v + n_LPLC2->GF * r_s) / 1000     # synapse counts, MaleCNS v1.0
    V_P  = (n_LC4->P  * r_v + n_LPLC2->P  * r_s) / 1000     # P = candidate parallel DNs

    the GF pathway crosses when V_GF >= gf_threshold; the parallel pathway crosses
    when V_P >= parallel_threshold.

Literature basis (abstract-level): GF looming responses are well described by a
sum of a linear function of angular velocity (LC4) and a Gaussian function of
angular size (LPLC2) [Ache et al. 2019]; integration of the two features in the
GF is linear [von Reyn et al. 2017]; the GF has a higher activation threshold
than parallel escape circuits, and its spike timing relative to them selects
short- versus long-mode takeoff [von Reyn et al. 2014].

Assumptions of this repository (not claims about the fly): instantaneous drive
with no synaptic delay; synapse count as a proxy for weight; DNp02/DNp04/DNp06/
DNp11 as the parallel pathway; all numeric parameters in `TeacherParams`; the
binocular visibility rule; the motor state machine; the sensory encoding.

The split into circuits mirrors that structure:

    LoomEscape-16 policy   16 sensory bits -> 2 pathway bits (GF crosses, parallel crosses)
    escape core            policy + standing + motor latches -> motor command
"""

from __future__ import annotations

import json
import math
from bisect import bisect_right
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Sensory encoding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Encoding:
    """Per-eye (size, speed) quantisation. With `bits` per feature there are
    2**bits bins: bin 0 is below the first edge, bin k covers [edge k-1, edge k),
    and the top bin also absorbs everything above the last edge."""

    name: str
    bits: int
    size_edges_deg: tuple[float, ...]
    speed_edges_dps: tuple[float, ...]
    size_floor_deg: float = 1.0
    speed_floor_dps: float = 5.0

    @property
    def levels(self) -> int:
        return 1 << self.bits

    @property
    def n_inputs(self) -> int:
        return 4 * self.bits

    def size_bin(self, theta_deg: float) -> int:
        return min(bisect_right(self.size_edges_deg, theta_deg), self.levels - 1)

    def speed_bin(self, dtheta_dps: float) -> int:
        return min(bisect_right(self.speed_edges_dps, dtheta_dps), self.levels - 1)

    def size_range(self, k: int) -> tuple[float, float]:
        e = self.size_edges_deg
        return (self.size_floor_deg, e[0]) if k == 0 else (e[k - 1], e[k])

    def speed_range(self, k: int) -> tuple[float, float]:
        e = self.speed_edges_dps
        return (self.speed_floor_dps, e[0]) if k == 0 else (e[k - 1], e[k])

    def input_names(self) -> tuple[str, ...]:
        names: tuple[str, ...] = ()
        for field in ("size_L", "speed_L", "size_R", "speed_R"):
            names += tuple(f"{field}[{i}]" for i in range(self.bits))
        return names


def _log_edges(lo: float, hi: float, n: int) -> tuple[float, ...]:
    return tuple(round(lo * (hi / lo) ** (k / (n - 1)), 6) for k in range(n))


ENCODING_3BIT = Encoding(
    "3bit-hand",
    3,
    (10.0, 15.0, 22.0, 33.0, 50.0, 75.0, 110.0, 180.0),
    (50.0, 100.0, 200.0, 400.0, 800.0, 1600.0, 3200.0, 12800.0),
)
ENCODING_4BIT = Encoding("4bit-log", 4, _log_edges(10.0, 180.0, 16), _log_edges(25.0, 12800.0, 16))
ENCODING_4BIT_SPEED50 = Encoding("4bit-log-speed50", 4, _log_edges(10.0, 180.0, 16), _log_edges(50.0, 12800.0, 16))
ENCODING_5BIT = Encoding("5bit-log", 5, _log_edges(10.0, 180.0, 32), _log_edges(25.0, 12800.0, 32))

DEFAULT_ENCODING = ENCODING_4BIT  # chosen by scripts/compare_encodings.py (train family only)

OUTPUT_NAMES = ("gf", "parallel")
HOLD, LONG_PROGRAM, SHORT_TAKEOFF = 0, 1, 2

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
    """Ipsilateral synapse counts onto one side's GF and parallel candidates."""

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


def load_weights(subgraph_json: Path | str, params: TeacherParams, override: dict | None = None) -> dict[str, Weights]:
    d = json.loads(Path(subgraph_json).read_text())
    w: dict[str, dict[str, float]] = {}
    for g in d["giant_fibers"]:
        side = g["instance"].rsplit("_", 1)[-1]
        w[side] = {"gf_lc4": g["inputs"]["LC4"]["synapses"], "gf_lplc2": g["inputs"]["LPLC2"]["synapses"]}
    for side, entry in d["parallel_dn_candidates"]["by_postsynaptic_side"].items():
        w[side]["par_lc4"] = entry["LC4"]["ipsilateral"]
        w[side]["par_lplc2"] = entry["LPLC2"]["ipsilateral"]
    if override:  # used by controls: replace counts wholesale
        w = {s: dict(override[s]) for s in ("L", "R")}
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


def select_action(gf_any: bool, par_any: bool, standing: int, wings_raised: int, refractory: int) -> int:
    """Action selection by relative pathway timing (von Reyn et al. 2014, as modelled here)."""
    if not standing or refractory:
        return HOLD
    if gf_any:
        return LONG_PROGRAM if wings_raised else SHORT_TAKEOFF
    if par_any:
        return LONG_PROGRAM
    return HOLD


@dataclass
class EyeTable:
    """Per-eye pathway crossing for each (size bin, speed bin): majority label and purity."""

    gf: np.ndarray  # (levels, levels) bool
    par: np.ndarray
    gf_purity: np.ndarray
    par_purity: np.ndarray


def eye_table(w: Weights, p: TeacherParams, side: str, enc: Encoding) -> EyeTable:
    rng = np.random.default_rng([p.seed, 0 if side == "L" else 1])
    n, L = p.samples_per_bin, enc.levels
    gf = np.zeros((L, L), bool)
    par = np.zeros((L, L), bool)
    gfp = np.ones((L, L))
    parp = np.ones((L, L))
    for s in range(L):
        lo, hi = enc.size_range(s)
        for v in range(L):
            vlo, vhi = enc.speed_range(v)
            th = np.exp(rng.uniform(math.log(lo), math.log(hi), n))
            dth = np.exp(rng.uniform(math.log(vlo), math.log(vhi), n))
            if s == 0:  # below loom onset: no drive by definition
                continue
            v_gf, v_par = drives(th, dth, w, p)
            g_on, p_on = (v_gf >= p.gf_threshold).mean(), (v_par >= p.parallel_threshold).mean()
            gf[s, v], par[s, v] = g_on >= 0.5, p_on >= 0.5
            gfp[s, v], parp[s, v] = max(g_on, 1 - g_on), max(p_on, 1 - p_on)
    return EyeTable(gf, par, gfp, parp)


@dataclass
class DecisionTable:
    params: TeacherParams
    encoding: Encoding
    weights: dict[str, Weights]
    eyes: dict[str, EyeTable]
    table: np.ndarray  # uint64, length 2**n_inputs, value = gf | parallel << 1

    def to_json(self) -> dict:
        return {
            "format": "c3s.decision-table/2",
            "inputs": list(self.encoding.input_names()),
            "outputs": list(OUTPUT_NAMES),
            "encoding": asdict(self.encoding),
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


def build_table(subgraph_json: Path | str, p: TeacherParams, enc: Encoding = DEFAULT_ENCODING, override: dict | None = None) -> DecisionTable:
    w = load_weights(subgraph_json, p, override)
    eyes = {s: eye_table(w[s], p, s, enc) for s in ("L", "R")}
    b, m = enc.bits, enc.levels - 1
    rows = np.arange(1 << enc.n_inputs, dtype=np.int64)
    sl, vl, sr, vr = rows & m, (rows >> b) & m, (rows >> 2 * b) & m, (rows >> 3 * b) & m
    gf = eyes["L"].gf[sl, vl] | eyes["R"].gf[sr, vr]
    par = eyes["L"].par[sl, vl] | eyes["R"].par[sr, vr]
    table = (gf.astype(np.uint64) | (par.astype(np.uint64) << np.uint64(1))).astype(np.uint64)
    return DecisionTable(p, enc, w, eyes, table)


# ---------------------------------------------------------------------------
# Motor state machine and episodes
# ---------------------------------------------------------------------------

CORE_HOLD, CORE_RAISING, CORE_SHORT, CORE_LONG = 0, 1, 2, 3
CORE_ACTION_NAMES = {0: "hold", 1: "raising wings", 2: "short-mode takeoff", 3: "long-mode takeoff"}


def core_step(action: int, raise_count: int, refr: int, p: TeacherParams) -> tuple[int, int, int]:
    """Shared motor state machine. Returns (motor command, next raise count, next refractory)."""
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
    """(theta, theta') at each tick from onset until the end size."""
    a = stim.l_over_v_ms / 1000.0
    t = a / math.tan(math.radians(stim.start_deg) / 2.0)
    t_end = a / math.tan(math.radians(stim.end_deg) / 2.0)
    dt = p.tick_ms / 1000.0
    out = []
    while t > t_end:
        # The onset sample is the start size by definition. Recomputed through tan and
        # atan it lands one ulp either side of the first size-bin edge (10 degrees),
        # depending on the platform's libm.
        out.append((stim.start_deg if not out else theta_at(a, t), dtheta_at(a, t)))
        t -= dt
    return out


@dataclass
class Outcome:
    action: int  # CORE_* of the first takeoff, or CORE_HOLD if none
    tick: int | None
    size_at_takeoff_deg: float | None


def _episode(stim: Stimulus, p: TeacherParams, pathways) -> Outcome:
    raise_count = refr = 0
    for i, (th, dth) in enumerate(stimulus_samples(stim, p)):
        gf_any, par_any = pathways(th, dth)
        action = select_action(gf_any, par_any, 1, int(raise_count >= p.wing_raise_ticks), int(refr > 0))
        out, raise_count, refr = core_step(action, raise_count, refr, p)
        if out in (CORE_SHORT, CORE_LONG):
            return Outcome(out, i, th)
    return Outcome(CORE_HOLD, None, None)


def run_teacher_episode(stim: Stimulus, w: dict[str, Weights], p: TeacherParams) -> Outcome:
    """Continuous (unquantised) teacher."""
    see = dict(zip(("L", "R"), visible(stim.azimuth_deg)))

    def pathways(th: float, dth: float) -> tuple[bool, bool]:
        gf_any = par_any = False
        if th >= 10.0:
            for side in ("L", "R"):
                if see[side]:
                    g, q = drives(np.array([th]), np.array([dth]), w[side], p)
                    gf_any |= bool(g[0] >= p.gf_threshold)
                    par_any |= bool(q[0] >= p.parallel_threshold)
        return gf_any, par_any

    return _episode(stim, p, pathways)


def run_quantized_teacher_episode(stim: Stimulus, table: DecisionTable) -> Outcome:
    """The teacher as seen through the encoding: what an exact circuit will do."""
    enc, p = table.encoding, table.params
    see = dict(zip(("L", "R"), visible(stim.azimuth_deg)))

    def pathways(th: float, dth: float) -> tuple[bool, bool]:
        s, v = enc.size_bin(th), enc.speed_bin(dth)
        gf_any = any(see[x] and table.eyes[x].gf[s, v] for x in ("L", "R"))
        par_any = any(see[x] and table.eyes[x].par[s, v] for x in ("L", "R"))
        return bool(gf_any), bool(par_any)

    return _episode(stim, p, pathways)


def encode_features(th: float, dth: float, azimuth_deg: float, enc: Encoding = DEFAULT_ENCODING) -> int:
    """Sensory bits for one tick: size_L, speed_L, size_R, speed_R."""
    see_l, see_r = visible(azimuth_deg)
    sb, vb, b = enc.size_bin(th), enc.speed_bin(dth), enc.bits
    sl, vl = (sb, vb) if see_l else (0, 0)
    sr, vr = (sb, vb) if see_r else (0, 0)
    return sl | vl << b | sr << 2 * b | vr << 3 * b


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
