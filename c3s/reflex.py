"""Circuits for the LoomEscape-16 policy and the stateful escape core.

`hand_policy` is the readable baseline: for each eye and each pathway it looks
up, per size bin, the smallest speed bin that crosses the pathway's threshold and
compares the actual speed bin against it; the two eyes are OR-ed.

`escape_core` wraps any policy circuit into a machine that is ticked once per
frame. It owns the motor state in latches (a wing-raise counter and a refractory
timer) and performs action selection from the relative timing of the two
pathways, so a caller only supplies what the eyes see and whether the fly stands.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from . import components as cmp
from . import loom
from .netlist import Builder, Circuit


def staircase(labels: np.ndarray) -> tuple[list[int], int]:
    """Per size bin, the smallest speed bin labelled 1 (levels = never).
    Returns (thresholds, number of (size, speed) cells the staircase gets wrong)."""
    levels = labels.shape[1]
    thr, wrong = [], 0
    for s in range(labels.shape[0]):
        on = np.flatnonzero(labels[s])
        t = int(on[0]) if on.size else levels
        thr.append(t)
        wrong += int(sum(bool(labels[s, v]) != (v >= t) for v in range(levels)))
    return thr, wrong


def eye_detector(b: Builder, size: Sequence[int], speed: Sequence[int], thresholds: Sequence[int]) -> int:
    """speed >= thresholds[size]; the threshold is one bit wider than speed so that
    "never" is representable."""
    width = len(speed) + 1
    thr_bits = []
    for j in range(width):
        terms = []
        for s, t in enumerate(thresholds):
            if (t >> j) & 1:
                lits = [size[i] if (s >> i) & 1 else b.not_(size[i]) for i in range(len(size))]
                terms.append(b.and_all(lits))
        thr_bits.append(b.or_all(terms))
    return cmp.ge(b, list(speed) + [b.ZERO], thr_bits)


def wire_policy(b: Builder, ins: Sequence[int], bits: int, thresholds: dict[str, dict[str, list[int]]]) -> list[int]:
    size_l, speed_l = ins[0:bits], ins[bits : 2 * bits]
    size_r, speed_r = ins[2 * bits : 3 * bits], ins[3 * bits : 4 * bits]
    gf = b.or_(
        eye_detector(b, size_l, speed_l, thresholds["L"]["gf"]),
        eye_detector(b, size_r, speed_r, thresholds["R"]["gf"]),
    )
    par = b.or_(
        eye_detector(b, size_l, speed_l, thresholds["L"]["parallel"]),
        eye_detector(b, size_r, speed_r, thresholds["R"]["parallel"]),
    )
    return [gf, par]


def hand_policy(table: loom.DecisionTable) -> tuple[Circuit, dict]:
    thresholds, wrong = {}, {}
    for side, eye in table.eyes.items():
        g, gw = staircase(eye.gf)
        q, qw = staircase(eye.par)
        thresholds[side] = {"gf": g, "parallel": q}
        wrong[side] = {"gf": gw, "parallel": qw}
    enc = table.encoding
    b = Builder(enc.n_inputs)
    circuit = b.finish(wire_policy(b, b.inputs(), enc.bits, thresholds))
    return circuit, {"thresholds": thresholds, "staircase_cells_wrong": wrong}


# ---------------------------------------------------------------------------
# Stateful core
# ---------------------------------------------------------------------------

CORE_OUTPUTS = ("motor[0]", "motor[1]")


def core_inputs(enc: loom.Encoding) -> tuple[str, ...]:
    return enc.input_names() + ("standing",)


def core_state_layout(p: loom.TeacherParams) -> dict:
    return {
        "raise_counter_bits": max(1, p.wing_raise_ticks.bit_length()),
        "refractory_bits": max(1, p.refractory_ticks.bit_length()),
        "order": "raise counter bits (LSB first), then refractory timer bits (LSB first)",
    }


def escape_core(policy: Circuit, p: loom.TeacherParams) -> Circuit:
    lay = core_state_layout(p)
    nf = policy.n_inputs
    b = Builder(nf + 1)
    ins = b.inputs()
    standing = ins[nf]
    raise_q = [b.latch() for _ in range(lay["raise_counter_bits"])]
    refr_q = [b.latch() for _ in range(lay["refractory_bits"])]
    wings = cmp.ge_const(b, raise_q, p.wing_raise_ticks)
    refractory = b.or_all(refr_q)
    gf, par = b.inline(policy, ins[:nf])

    active = b.and_(standing, b.not_(refractory))
    short = b.and_(active, b.and_(gf, b.not_(wings)))
    long_prog = b.and_(active, b.or_(b.and_(gf, wings), b.and_(b.not_(gf), par)))
    motor0 = long_prog
    motor1 = b.or_(short, b.and_(long_prog, wings))
    raising = b.and_(long_prog, b.not_(wings))

    next_raise = cmp.mux_bits(b, raising, cmp.const_bits(b, 0, len(raise_q)), cmp.increment_saturating(b, raise_q, b.ONE))
    next_refr = cmp.mux_bits(
        b, motor1, cmp.decrement_saturating(b, refr_q), cmp.const_bits(b, p.refractory_ticks, len(refr_q))
    )
    for q, d in zip(raise_q, next_raise):
        b.drive(q, d)
    for q, d in zip(refr_q, next_refr):
        b.drive(q, d)
    return b.finish([motor0, motor1])


def core_reference_tables(policy_table: np.ndarray, p: loom.TeacherParams, n_features: int) -> tuple[np.ndarray, np.ndarray]:
    """Vectorised step relation of `escape_core` for a policy with this truth table.
    Row bits: features, standing, raise counter, refractory timer."""
    lay = core_state_layout(p)
    rb, fb = lay["raise_counter_bits"], lay["refractory_bits"]
    rows = np.arange(1 << (n_features + 1 + rb + fb), dtype=np.int64)
    feat = rows & ((1 << n_features) - 1)
    standing = ((rows >> n_features) & 1).astype(bool)
    raise_c = (rows >> (n_features + 1)) & ((1 << rb) - 1)
    refr = rows >> (n_features + 1 + rb)
    pw = policy_table.astype(np.int64)[feat]
    gf, par = (pw & 1).astype(bool), ((pw >> 1) & 1).astype(bool)
    wings = raise_c >= p.wing_raise_ticks
    active = standing & (refr == 0)
    short = active & gf & ~wings
    long_prog = active & ((gf & wings) | (~gf & par))
    out = np.where(short, loom.CORE_SHORT, np.where(long_prog, np.where(wings, loom.CORE_LONG, loom.CORE_RAISING), loom.CORE_HOLD))
    took = out >= loom.CORE_SHORT
    next_raise = np.where(long_prog & ~wings, np.minimum(raise_c + 1, p.wing_raise_ticks), 0)
    next_refr = np.where(took, p.refractory_ticks, np.maximum(refr - 1, 0))
    return out.astype(np.uint64), (next_raise | (next_refr << rb)).astype(np.uint64)


def run_core_episode(step_outs: np.ndarray, step_next: np.ndarray, stim: loom.Stimulus, p: loom.TeacherParams, enc: loom.Encoding) -> loom.Outcome:
    """Drive a core (through its verified step tables) with a quantised stimulus."""
    nf = enc.n_inputs
    state = 0
    for i, (th, dth) in enumerate(loom.stimulus_samples(stim, p)):
        x = loom.encode_features(th, dth, stim.azimuth_deg, enc) | (1 << nf)
        row = x | (state << (nf + 1))
        out = int(step_outs[row])
        state = int(step_next[row])
        if out in (loom.CORE_SHORT, loom.CORE_LONG):
            return loom.Outcome(out, i, th)
    return loom.Outcome(loom.CORE_HOLD, None, None)
