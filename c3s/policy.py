"""Rules an agent must obey, compiled into a circuit and proven about.

The escape core already proves three things about itself, and each is the shape of a
boundary someone would want to put around an agent:

    no takeoff within 7 ticks of a takeoff        a rate limit
    long mode needs 4 consecutive raising ticks   a commitment cost
    not standing, or refractory, means hold       a forbidding condition

This module makes those shapes writable. A `Policy` is a handful of rules; `build`
compiles it to a NAND/LATCH `Circuit` with no hidden state, `reference` is the same
policy written directly in Python, and the two are checked against each other on every
row of the circuit's domain. `properties` then proves, by searching every reachable
state, that the compiled circuit really obeys each rule — not that it was built from a
template that should.

The rules generalise a measured circuit rather than inventing new ones: that is the
whole reason for their shape. What they do NOT do is described in docs/AGENT.md, and
the important half is there: nothing here binds wall-clock time, nothing here makes
the inputs mean what they are named, and a grant is a verdict, never an effect.

    policy = Policy(min_gap_ticks=8, commit_ticks=4, forbid_when_blocked=True, max_grants=3)
    circuit = policy.build()             # 3 inputs, 1 output, a few latches
    policy.verify(circuit)               # every row, against the Python reference
    policy.properties(circuit)           # every reachable state, one entry per rule
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

from . import components as cmp
from . import exhaust
from .netlist import Builder, Circuit

INPUT_NAMES = ("request", "intent", "blocked")
OUTPUT_NAMES = ("grant",)


def _width(limit: int) -> int:
    """Bits needed to count up to `limit` inclusive."""
    return max(1, int(limit).bit_length())


@dataclass(frozen=True)
class Policy:
    """Boundaries for one agent, as counters over ticks.

    min_gap_ticks       a grant needs this many ticks since the previous one (0: off)
    commit_ticks        a grant needs this many consecutive `intent` ticks first (0: off)
    forbid_when_blocked while `blocked` is high, nothing is granted
    max_grants          at most this many grants ever (0: unlimited)
    """

    min_gap_ticks: int = 0
    commit_ticks: int = 0
    forbid_when_blocked: bool = True
    max_grants: int = 0

    def __post_init__(self) -> None:
        for name in ("min_gap_ticks", "commit_ticks", "max_grants"):
            value = getattr(self, name)
            if value < 0 or value > 255:
                raise ValueError(f"{name}={value} outside 0..255")

    # -- shape ---------------------------------------------------------------

    @property
    def gap_bits(self) -> int:
        # A countdown of cooldown ticks left, not ticks elapsed: it starts at zero, so
        # the first grant is allowed, and grants come out exactly min_gap_ticks apart.
        # Counting elapsed ticks instead blocks the first grant and spaces the rest by
        # one tick too many — the same off-by-one that P1's monitor had.
        return _width(self.min_gap_ticks - 1) if self.min_gap_ticks > 1 else 0

    @property
    def streak_bits(self) -> int:
        return _width(self.commit_ticks) if self.commit_ticks else 0

    @property
    def spent_bits(self) -> int:
        return _width(self.max_grants) if self.max_grants else 0

    @property
    def state_bits(self) -> int:
        return self.gap_bits + self.streak_bits + self.spent_bits

    @property
    def domain_bits(self) -> int:
        """Inputs plus state: the width exhaustive verification has to cover."""
        return len(INPUT_NAMES) + self.state_bits

    def describe(self) -> list[str]:
        out = []
        if self.min_gap_ticks:
            out.append(f"at most one grant in any {self.min_gap_ticks} ticks")
        if self.commit_ticks:
            out.append(f"a grant needs {self.commit_ticks} consecutive intent ticks")
        if self.forbid_when_blocked:
            out.append("nothing is granted while blocked is high")
        if self.max_grants:
            out.append(f"at most {self.max_grants} grants in total")
        return out or ["every request is granted"]

    # -- the policy, written twice ------------------------------------------

    def reference(self, request: int, intent: int, blocked: int, gap: int, streak: int, spent: int) -> tuple[int, int, int, int]:
        """(grant, next gap, next streak, next spent), in plain Python."""
        allowed = bool(request)
        if self.forbid_when_blocked and blocked:
            allowed = False
        if self.gap_bits and gap != 0:  # gap is the cooldown left, not ticks elapsed
            allowed = False
        if self.commit_ticks and streak < self.commit_ticks:
            allowed = False
        if self.max_grants and spent >= self.max_grants:
            allowed = False
        grant = int(allowed)
        gap_cap = (1 << self.gap_bits) - 1 if self.gap_bits else 0
        streak_cap = (1 << self.streak_bits) - 1 if self.streak_bits else 0
        spent_cap = (1 << self.spent_bits) - 1 if self.spent_bits else 0
        next_gap = (min(self.min_gap_ticks - 1, gap_cap) if self.gap_bits else 0) if grant else max(gap - 1, 0)
        next_streak = min(streak + 1, streak_cap) if intent else 0
        next_spent = min(spent + 1, spent_cap) if grant else spent
        return grant, next_gap, next_streak, next_spent

    def build(self) -> Circuit:
        """The same policy as NAND gates and latches, state in the order
        gap, streak, spent (each LSB first), which is what `split_state` assumes."""
        b = Builder(len(INPUT_NAMES))
        request, intent, blocked = b.inputs()
        gap_q = [b.latch() for _ in range(self.gap_bits)]
        streak_q = [b.latch() for _ in range(self.streak_bits)]
        spent_q = [b.latch() for _ in range(self.spent_bits)]

        allowed = request
        if self.forbid_when_blocked:
            allowed = b.and_(allowed, b.not_(blocked))
        if self.gap_bits:
            allowed = b.and_(allowed, b.not_(b.or_all(gap_q)))  # the cooldown has run out
        if self.commit_ticks:
            allowed = b.and_(allowed, cmp.ge_const(b, streak_q, self.commit_ticks))
        if self.max_grants:
            allowed = b.and_(allowed, b.not_(cmp.ge_const(b, spent_q, self.max_grants)))
        grant = allowed

        reload_gap = cmp.const_bits(b, min(self.min_gap_ticks - 1, (1 << self.gap_bits) - 1) if self.gap_bits else 0, self.gap_bits)
        for q, d in zip(gap_q, cmp.mux_bits(b, grant, cmp.decrement_saturating(b, gap_q), reload_gap)):
            b.drive(q, d)
        for q, d in zip(streak_q, cmp.mux_bits(b, intent, cmp.const_bits(b, 0, self.streak_bits), cmp.increment_saturating(b, streak_q, b.ONE))):
            b.drive(q, d)
        for q, d in zip(spent_q, cmp.mux_bits(b, grant, list(spent_q), cmp.increment_saturating(b, spent_q, b.ONE))):
            b.drive(q, d)
        return b.finish([grant])

    # -- checking ------------------------------------------------------------

    def split_state(self, state: int) -> tuple[int, int, int]:
        gap = state & ((1 << self.gap_bits) - 1)
        streak = (state >> self.gap_bits) & ((1 << self.streak_bits) - 1)
        spent = (state >> (self.gap_bits + self.streak_bits)) & ((1 << self.spent_bits) - 1)
        return gap, streak, spent

    def rows(self) -> Iterator[tuple[int, int, int, int, int]]:
        """Every (request, intent, blocked, state) row, in step_table order."""
        for state in range(1 << self.state_bits):
            for inputs in range(1 << len(INPUT_NAMES)):
                yield inputs & 1, (inputs >> 1) & 1, (inputs >> 2) & 1, state, inputs | (state << len(INPUT_NAMES))

    def verify(self, circuit: Circuit | None = None) -> dict:
        """Exhaustive equality between the compiled circuit and the Python reference."""
        circuit = circuit if circuit is not None else self.build()
        outs, nxt = exhaust.step_table(circuit)
        want_out = np.zeros(len(outs), dtype=np.uint64)
        want_nxt = np.zeros(len(nxt), dtype=np.uint64)
        for request, intent, blocked, state, row in self.rows():
            gap, streak, spent = self.split_state(state)
            grant, ngap, nstreak, nspent = self.reference(request, intent, blocked, gap, streak, spent)
            want_out[row] = grant
            want_nxt[row] = ngap | (nstreak << self.gap_bits) | (nspent << (self.gap_bits + self.streak_bits))
        bad_out = exhaust.first_mismatch(outs, want_out)
        bad_nxt = exhaust.first_mismatch(nxt, want_nxt)
        return {
            "rows": len(outs),
            "domain_bits": self.domain_bits,
            "outputs_match": bad_out is None,
            "next_state_matches": bad_nxt is None,
            "first_mismatch": bad_out if bad_out is not None else bad_nxt,
            "metrics": circuit.metrics(),
        }

    def properties(self, circuit: Circuit | None = None, claim: "Policy | None" = None) -> dict:
        """Prove each rule by walking every reachable state with every input.

        This is the part that matters: the circuit is not trusted because a template
        built it, but because from reset, under every input sequence the state machine
        admits, no rule is ever broken.

        `claim` checks the circuit against a *different* set of rules than the one it
        was built from, which is how a negative control is written: claiming a stronger
        limit than the circuit enforces must produce violations, or the check is
        vacuous."""
        circuit = circuit if circuit is not None else self.build()
        claim = claim if claim is not None else self
        outs, nxt = exhaust.step_table(circuit)
        n_in = len(INPUT_NAMES)

        reachable, frontier = {0}, [0]
        while frontier:
            new = []
            for state in frontier:
                for inputs in range(1 << n_in):
                    t = int(nxt[inputs | (state << n_in)])
                    if t not in reachable:
                        reachable.add(t)
                        new.append(t)
            frontier = new

        # The rules about time are properties of traces, not of single rows, so they are
        # checked against monitors that count independently of the circuit's own
        # latches: ticks since the last grant, consecutive intent ticks, grants so far.
        # Anything the circuit can reach from reset under any inputs is visited.
        violations = {name: 0 for name in ("rate_limit", "commitment", "forbidden", "budget")}
        gap_cap = max(claim.min_gap_ticks, 1)
        streak_cap = max(claim.commit_ticks, 1)
        spent_cap = claim.max_grants + 1 if claim.max_grants else 1
        start = (0, gap_cap, 0, 0)  # state, ticks since a grant (none yet), intent streak, grants
        seen, frontier, checked = {start}, [start], 0
        while frontier:
            new = []
            for state, since, streak, spent in frontier:
                for inputs in range(1 << n_in):
                    request, intent, blocked = inputs & 1, (inputs >> 1) & 1, (inputs >> 2) & 1
                    row = inputs | (state << n_in)
                    grant = int(outs[row])
                    checked += 1
                    if grant:
                        if claim.min_gap_ticks and since < claim.min_gap_ticks:
                            violations["rate_limit"] += 1
                        if claim.commit_ticks and streak < claim.commit_ticks:
                            violations["commitment"] += 1
                        if claim.forbid_when_blocked and blocked:
                            violations["forbidden"] += 1
                        if claim.max_grants and spent >= claim.max_grants:
                            violations["budget"] += 1
                    nxt_state = int(nxt[row])
                    # One tick has elapsed by the next row, so a grant leaves 1, not 0.
                    # Starting from 0 reads one tick short and reports a violation on
                    # the first legal grant — the same off-by-one P1's monitor had.
                    nxt_since = 1 if grant else min(since + 1, gap_cap)
                    nxt_streak = min(streak + 1, streak_cap) if intent else 0
                    nxt_spent = min(spent + 1, spent_cap) if grant else spent
                    t = (nxt_state, nxt_since, nxt_streak, nxt_spent)
                    if t not in seen:
                        seen.add(t)
                        new.append(t)
            frontier = new
        return {
            "reachable_states": len(reachable),
            "states_possible": 1 << self.state_bits,
            "configurations_visited": len(seen),
            "rows_checked": checked,
            "violations": violations,
            "holds": all(v == 0 for v in violations.values()),
            "rules": claim.describe(),
            "claimed": claim != self,
        }
