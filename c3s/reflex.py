"""Circuits for the LoomEscape decision and the stateful escape core.

`hand_policy` is the readable baseline: for each eye it looks up, per size bin,
the smallest speed bin that crosses a pathway's threshold, compares the actual
speed bin against it, and combines the eyes and the motor state with a few gates.

`escape_core` wraps any 15-input policy circuit into a machine that is ticked
once per frame on chain. It owns the motor state (a wing-raise counter and a
refractory timer) in latches, so a caller only supplies what the eyes see.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from . import components as cmp
from . import loom
from .netlist import Builder, Circuit

NEVER = 8  # threshold value meaning "no speed bin crosses"


def staircase(labels: np.ndarray) -> tuple[list[int], int]:
    """Per size bin, the smallest speed bin labelled 1 (NEVER if none).
    Returns (thresholds, number of (size, speed) cells the staircase gets wrong)."""
    thr, wrong = [], 0
    for s in range(8):
        on = np.flatnonzero(labels[s])
        t = int(on[0]) if on.size else NEVER
        thr.append(t)
        wrong += int(sum(labels[s, v] != (v >= t) for v in range(8)))
    return thr, wrong


def eye_detector(b: Builder, size: Sequence[int], speed: Sequence[int], thresholds: Sequence[int]) -> int:
    """speed >= thresholds[size], with a 4-bit threshold so NEVER is representable."""
    thr_bits = []
    for j in range(4):
        terms = []
        for s, t in enumerate(thresholds):
            if (t >> j) & 1:
                lits = [size[i] if (s >> i) & 1 else b.not_(size[i]) for i in range(3)]
                terms.append(b.and_all(lits))
        thr_bits.append(b.or_all(terms))
    return cmp.ge(b, list(speed) + [b.ZERO], thr_bits)


def wire_policy(b: Builder, ins: Sequence[int], thresholds: dict[str, dict[str, list[int]]]) -> list[int]:
    size_l, speed_l, size_r, speed_r = ins[0:3], ins[3:6], ins[6:9], ins[9:12]
    standing, wings, refractory = ins[12], ins[13], ins[14]
    gf_any = b.or_(
        eye_detector(b, size_l, speed_l, thresholds["L"]["gf"]),
        eye_detector(b, size_r, speed_r, thresholds["R"]["gf"]),
    )
    par_any = b.or_(
        eye_detector(b, size_l, speed_l, thresholds["L"]["parallel"]),
        eye_detector(b, size_r, speed_r, thresholds["R"]["parallel"]),
    )
    active = b.and_(standing, b.not_(refractory))
    short = b.and_(active, b.and_(gf_any, b.not_(wings)))
    long_ = b.and_(active, b.or_(b.and_(gf_any, wings), b.and_(b.not_(gf_any), par_any)))
    return [long_, short]  # action bit 0 = long-mode program, bit 1 = short-mode takeoff


def hand_policy(table: loom.DecisionTable) -> tuple[Circuit, dict]:
    thresholds, wrong = {}, {}
    for side, eye in table.eyes.items():
        g, gw = staircase(eye.gf)
        q, qw = staircase(eye.par)
        thresholds[side] = {"gf": g, "parallel": q}
        wrong[side] = {"gf": gw, "parallel": qw}
    b = Builder(loom.N_INPUTS)
    circuit = b.finish(wire_policy(b, b.inputs(), thresholds))
    return circuit, {"thresholds": thresholds, "staircase_cells_wrong": wrong}


# ---------------------------------------------------------------------------
# Stateful core
# ---------------------------------------------------------------------------

CORE_INPUTS = loom.INPUT_NAMES[:12] + ("standing",)
CORE_OUTPUTS = ("motor[0]", "motor[1]")


def _width(n: int) -> int:
    return max(1, n.bit_length())


def core_state_layout(p: loom.TeacherParams) -> dict:
    return {
        "raise_counter_bits": _width(p.wing_raise_ticks),
        "refractory_bits": _width(p.refractory_ticks),
        "order": "raise counter bits (LSB first), then refractory timer bits (LSB first)",
    }


def escape_core(policy: Circuit, p: loom.TeacherParams) -> Circuit:
    lay = core_state_layout(p)
    b = Builder(len(CORE_INPUTS))
    ins = b.inputs()
    raise_q = [b.latch() for _ in range(lay["raise_counter_bits"])]
    refr_q = [b.latch() for _ in range(lay["refractory_bits"])]
    wings = cmp.ge_const(b, raise_q, p.wing_raise_ticks)
    refractory = b.or_all(refr_q)
    long_prog, short = b.inline(policy, list(ins[:12]) + [ins[12], wings, refractory])
    motor0 = long_prog
    motor1 = b.or_(short, b.and_(long_prog, wings))
    took_off = motor1
    raising = b.and_(long_prog, b.not_(wings))
    bumped = cmp.increment_saturating(b, raise_q, b.ONE)
    next_raise = cmp.mux_bits(b, raising, cmp.const_bits(b, 0, len(raise_q)), bumped)
    next_refr = cmp.mux_bits(
        b, took_off, cmp.decrement_saturating(b, refr_q), cmp.const_bits(b, p.refractory_ticks, len(refr_q))
    )
    for q, d in zip(raise_q, next_raise):
        b.drive(q, d)
    for q, d in zip(refr_q, next_refr):
        b.drive(q, d)
    return b.finish([motor0, motor1])


def core_reference(policy_table: np.ndarray, p: loom.TeacherParams):
    """Step reference for the core built around a policy with this truth table."""
    lay = core_state_layout(p)
    rb = lay["raise_counter_bits"]

    def step(x: int, s: int) -> tuple[int, int]:
        raise_count = s & ((1 << rb) - 1)
        refr = s >> rb
        wings = int(raise_count >= p.wing_raise_ticks)
        row = (x & 0xFFF) | ((x >> 12) & 1) << 12 | wings << 13 | int(refr > 0) << 14
        action = int(policy_table[row])
        out, nr, nf = loom.core_step(action, raise_count, refr, p)
        return out, nr | (nf << rb)

    return step


def run_core_episode(step_outs: np.ndarray, step_next: np.ndarray, stim: loom.Stimulus, p: loom.TeacherParams, n_state_bits_in: int = 13) -> loom.Outcome:
    """Drive a core (via its verified step tables) with a quantised stimulus."""
    state = 0
    for i, (th, dth) in enumerate(loom.stimulus_samples(stim, p)):
        x = loom.encode_features(th, dth, stim.azimuth_deg) | (1 << 12)
        row = x | (state << n_state_bits_in)
        out = int(step_outs[row])
        state = int(step_next[row])
        if out in (loom.CORE_SHORT, loom.CORE_LONG):
            return loom.Outcome(out, i, th)
    return loom.Outcome(loom.CORE_HOLD, None, None)
