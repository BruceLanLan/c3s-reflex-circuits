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

The first five rules generalise a measured circuit rather than inventing new ones: that
is the whole reason for their shape. Four more are the shapes that recur in the tools
people actually put around agents — a halt that stays down until a person lifts it, a
confirmation that is spent by the one irreversible action it was given for, a breaker
that trips after repeated failure, and a grant that needs two keys. Each rests on an
input the agent cannot write. What none of them do is described in docs/AGENT.md, and
the important half is there: nothing here binds wall-clock time, nothing here makes
the inputs mean what they are named, and a grant is a verdict, never an effect.

    policy = Policy(min_gap_ticks=8, commit_ticks=4, forbid_when_blocked=True, max_grants=3)
    circuit = policy.build()             # 4 inputs, 1 output, a few latches
    policy.verify(circuit)               # every row, against the Python reference
    policy.properties(circuit)           # every reachable state, one entry per rule
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import numpy as np

from . import components as cmp
from . import exhaust
from .netlist import Builder, Circuit

# Who is allowed to write each input decides whether a rule is a boundary or merely a
# cost. `request` and `intent` come from the agent, so any rule resting on them alone
# is something the agent can satisfy by choosing to — a commitment cost, not a limit.
# Every other input must come from the layer the agent cannot write (the tool runner,
# a human, a signed feed); a rule resting on those is a boundary. Nothing in this
# module can enforce that distinction: it is a property of how it is wired up, and it
# is the single most important thing to get right when using it.
#
# The first four inputs are always present, in this order, so every policy that uses
# only them compiles to exactly what it did before the others existed. The rest are
# appended, in this fixed order, only when the rule that reads them is on.
INPUT_NAMES = ("request", "intent", "blocked", "confirm")
OPTIONAL_INPUTS = ("irreversible", "failed", "heartbeat", "confirm_b")
AGENT_WRITABLE = ("request", "intent")
MUST_COME_FROM_THE_TOOL_LAYER = ("blocked", "confirm") + OPTIONAL_INPUTS
OUTPUT_NAMES = ("grant",)

# When a claim mentions an input the circuit under test does not have, the monitor reads
# it at the value that makes the claim hardest to satisfy: every action irreversible,
# every tick a failure, no heartbeat ever, no second key ever. A circuit without the
# rule then fails the claim, which is what a negative control is for.
_ABSENT_INPUT = {"irreversible": 1, "failed": 1, "heartbeat": 0, "confirm_b": 0}


def _width(limit: int) -> int:
    """Bits needed to count up to `limit` inclusive."""
    return max(1, int(limit).bit_length())


def _cap(bits: int) -> int:
    return (1 << bits) - 1 if bits else 0


@dataclass(frozen=True)
class Policy:
    """Boundaries for one agent, as counters and latches over ticks.

    min_gap_ticks       a grant needs this many ticks since the previous one (0: off)
    commit_ticks        a grant needs this many consecutive `intent` ticks first (0: off)
    forbid_when_blocked while `blocked` is high, nothing is granted
    max_grants          at most this many grants ever (0: unlimited)
    confirm_window_ticks a grant needs a `confirm` in the last this-many ticks (0: off)

    sticky_block        a tick with `blocked` high halts the agent until a later tick
                        with `confirm` high; the halted ticks grant nothing, and so
                        does the tick whose confirm lifts the halt
    heartbeat_ticks     this many consecutive ticks without `heartbeat` halt the agent
                        the same way (0: off)
    confirm_per_irreversible
                        a tick with `irreversible` high is granted only if a `confirm`
                        arrived since the last irreversible grant (or arrives now), and
                        that grant spends it; reversible grants neither need nor spend
    trip_after_failures this many consecutive ticks with `failed` high trip a breaker
                        that grants nothing until a `confirm` resets it (0: off)
    trip_after_refusals this many consecutive refused requests halt the agent until a
                        `confirm` resets it (0: off). The only rule that reads the
                        circuit's own verdict rather than an input: an agent that keeps
                        asking for what it cannot have is stopped instead of left to
                        hammer the boundary. A grant or a confirm clears the count; a
                        tick with no request is not a refusal and leaves it alone.
    two_key             every grant needs both `confirm` and `confirm_b` to have
                        arrived since the last grant (or to arrive now); a grant
                        spends both

    Which of these are boundaries in the strict sense depends only on who writes the
    input each one reads. `commit_ticks` rests on the agent's own `intent`, so it is a
    cost it can always pay. `min_gap_ticks` and `max_grants` bound the agent whatever it
    does. Everything else rests on inputs the agent must not be able to write.
    """

    min_gap_ticks: int = 0
    commit_ticks: int = 0
    forbid_when_blocked: bool = True
    max_grants: int = 0
    confirm_window_ticks: int = 0
    sticky_block: bool = False
    heartbeat_ticks: int = 0
    confirm_per_irreversible: bool = False
    trip_after_failures: int = 0
    trip_after_refusals: int = 0
    two_key: bool = False

    def __post_init__(self) -> None:
        for name in ("min_gap_ticks", "commit_ticks", "max_grants", "confirm_window_ticks", "heartbeat_ticks",
                     "trip_after_failures", "trip_after_refusals"):
            value = getattr(self, name)
            if value < 0 or value > 255:
                raise ValueError(f"{name}={value} outside 0..255")

    # -- shape ---------------------------------------------------------------

    def input_names(self) -> tuple[str, ...]:
        names = list(INPUT_NAMES)
        if self.confirm_per_irreversible:
            names.append("irreversible")
        if self.trip_after_failures:
            names.append("failed")
        if self.heartbeat_ticks:
            names.append("heartbeat")
        if self.two_key:
            names.append("confirm_b")
        return tuple(names)

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
    def window_bits(self) -> int:
        # Ticks of confirmation left, so a confirm at this very tick also authorises.
        return _width(self.confirm_window_ticks - 1) if self.confirm_window_ticks > 1 else 0

    @property
    def halt_bits(self) -> int:
        return 1 if (self.sticky_block or self.heartbeat_ticks) else 0

    @property
    def silence_bits(self) -> int:
        # Consecutive ticks without a heartbeat so far. Counting up from zero keeps the
        # reset state permissive, which a countdown loaded at reset could not be.
        return _width(self.heartbeat_ticks - 1) if self.heartbeat_ticks > 1 else 0

    @property
    def token_bits(self) -> int:
        return 1 if self.confirm_per_irreversible else 0

    @property
    def fails_bits(self) -> int:
        return _width(self.trip_after_failures - 1) if self.trip_after_failures > 1 else 0

    @property
    def trip_bits(self) -> int:
        return 1 if self.trip_after_failures else 0

    @property
    def refused_bits(self) -> int:
        # Consecutive refused requests so far, like `fails`: the stored value only has to
        # reach N-1, because the tick that reaches N trips the latch.
        return _width(self.trip_after_refusals - 1) if self.trip_after_refusals > 1 else 0

    @property
    def refused_trip_bits(self) -> int:
        return 1 if self.trip_after_refusals else 0

    @property
    def key_bits(self) -> int:
        return 2 if self.two_key else 0

    def layout(self) -> list[tuple[str, int]]:
        """State fields in latch order, LSB first; `fields` and `build` both follow it."""
        return [
            ("gap", self.gap_bits),
            ("streak", self.streak_bits),
            ("spent", self.spent_bits),
            ("window", self.window_bits),
            ("halted", self.halt_bits),
            ("silence", self.silence_bits),
            ("token", self.token_bits),
            ("fails", self.fails_bits),
            ("tripped", self.trip_bits),
            ("refused", self.refused_bits),
            ("refused_tripped", self.refused_trip_bits),
            ("key_a", self.key_bits // 2),
            ("key_b", self.key_bits // 2),
        ]

    @property
    def state_bits(self) -> int:
        return sum(bits for _, bits in self.layout())

    @property
    def domain_bits(self) -> int:
        """Inputs plus state: the width exhaustive verification has to cover."""
        return len(self.input_names()) + self.state_bits

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
        if self.confirm_window_ticks:
            out.append(f"a grant needs a confirm within the last {self.confirm_window_ticks} ticks")
        if self.sticky_block:
            out.append("a blocked tick halts everything until a confirm lifts it")
        if self.heartbeat_ticks:
            out.append(f"{self.heartbeat_ticks} ticks without a heartbeat halt everything until a confirm lifts it")
        if self.confirm_per_irreversible:
            out.append("an irreversible grant needs its own confirm, and spends it")
        if self.trip_after_failures:
            out.append(f"{self.trip_after_failures} consecutive failures trip a breaker until a confirm resets it")
        if self.trip_after_refusals:
            out.append(f"{self.trip_after_refusals} refusals in a row halt everything until a confirm resets it")
        if self.two_key:
            out.append("a grant needs both confirm and confirm_b, and spends both")
        return out or ["every request is granted"]

    # -- the policy, written twice ------------------------------------------

    def fields(self, state: int) -> dict[str, int]:
        """Every state field by name, from a packed state."""
        out, shift = {}, 0
        for name, bits in self.layout():
            out[name] = (state >> shift) & _cap(bits)
            shift += bits
        return out

    def pack(self, fields: dict[str, int]) -> int:
        state, shift = 0, 0
        for name, bits in self.layout():
            state |= (fields.get(name, 0) & _cap(bits)) << shift
            shift += bits
        return state

    def split_state(self, state: int) -> tuple[int, int, int, int]:
        """The four counters of the original rules: (gap, streak, spent, window)."""
        f = self.fields(state)
        return f["gap"], f["streak"], f["spent"], f["window"]

    def reference(self, inp: dict[str, int], st: dict[str, int]) -> tuple[int, dict[str, int]]:
        """(grant, next state fields), in plain Python. Inputs the policy does not read
        may be absent from `inp`."""
        request, intent, blocked, confirm = inp["request"], inp["intent"], inp["blocked"], inp["confirm"]
        irreversible, failed = inp.get("irreversible", 0), inp.get("failed", 0)
        heartbeat, confirm_b = inp.get("heartbeat", 0), inp.get("confirm_b", 0)
        H, k = self.heartbeat_ticks, self.trip_after_failures

        allowed = bool(request)
        if self.forbid_when_blocked and blocked:
            allowed = False
        if self.gap_bits and st["gap"] != 0:  # gap is the cooldown left, not ticks elapsed
            allowed = False
        if self.commit_ticks and st["streak"] < self.commit_ticks:
            allowed = False
        if self.max_grants and st["spent"] >= self.max_grants:
            allowed = False
        if self.confirm_window_ticks and not (confirm or st["window"]):
            allowed = False
        # A halt begins on the tick that causes it and includes that tick.
        trip_now = bool(self.sticky_block and blocked) or bool(H and not heartbeat and (H == 1 or st["silence"] >= H - 1))
        if self.halt_bits and (st["halted"] or trip_now):
            allowed = False
        if self.confirm_per_irreversible and irreversible and not (st["token"] or confirm):
            allowed = False
        break_now = bool(k and failed and (k == 1 or st["fails"] >= k - 1))
        if k and (st["tripped"] or break_now):
            allowed = False
        have_a, have_b = bool(st.get("key_a", 0) or confirm), bool(st.get("key_b", 0) or confirm_b)
        if self.two_key and not (have_a and have_b):
            allowed = False
        # The refusal breaker reads only its own latch here: the tick that trips it is a
        # refusal anyway, so gating on the latch alone keeps grant free of its own count.
        if self.trip_after_refusals and st["refused_tripped"]:
            allowed = False
        grant = int(allowed)

        R = self.trip_after_refusals
        refusal_now = bool(R and request and not grant)
        trip_refusals = bool(refusal_now and (R == 1 or st["refused"] >= R - 1))

        nxt = {
            "gap": (min(self.min_gap_ticks - 1, _cap(self.gap_bits)) if self.gap_bits else 0) if grant else max(st["gap"] - 1, 0),
            "streak": min(st["streak"] + 1, _cap(self.streak_bits)) if intent else 0,
            "spent": min(st["spent"] + 1, _cap(self.spent_bits)) if grant else st["spent"],
            "window": (min(self.confirm_window_ticks - 1, _cap(self.window_bits)) if confirm else max(st["window"] - 1, 0)) if self.window_bits else 0,
            # a confirm lifts a halt, unless this very tick causes one
            "halted": int(trip_now or (st["halted"] and not confirm)) if self.halt_bits else 0,
            "silence": (0 if heartbeat else min(st["silence"] + 1, _cap(self.silence_bits))) if self.silence_bits else 0,
            # a confirm arms the token; an irreversible grant spends it, even one confirmed on the same tick
            "token": int((confirm or st["token"]) and not (grant and irreversible)) if self.token_bits else 0,
            "fails": (0 if (confirm or not failed) else min(st["fails"] + 1, _cap(self.fails_bits))) if self.fails_bits else 0,
            "tripped": int(break_now or (st["tripped"] and not confirm)) if self.trip_bits else 0,
            "refused": (0 if (grant or confirm) else (min(st["refused"] + 1, _cap(self.refused_bits)) if refusal_now
                                                       else st["refused"])) if self.refused_bits else 0,
            "refused_tripped": int(trip_refusals or (st["refused_tripped"] and not confirm)) if self.refused_trip_bits else 0,
            "key_a": int(have_a and not grant) if self.two_key else 0,
            "key_b": int(have_b and not grant) if self.two_key else 0,
        }
        return grant, nxt

    def build(self) -> Circuit:
        """The same policy as NAND gates and latches, state in `layout` order."""
        names = self.input_names()
        b = Builder(len(names))
        inp = dict(zip(names, b.inputs()))
        request, intent, blocked, confirm = (inp[n] for n in INPUT_NAMES)
        H, k = self.heartbeat_ticks, self.trip_after_failures

        q = {name: [b.latch() for _ in range(bits)] for name, bits in self.layout()}
        gap_q, streak_q, spent_q, window_q = q["gap"], q["streak"], q["spent"], q["window"]

        allowed = request
        if self.forbid_when_blocked:
            allowed = b.and_(allowed, b.not_(blocked))
        if self.gap_bits:
            allowed = b.and_(allowed, b.not_(b.or_all(gap_q)))  # the cooldown has run out
        if self.commit_ticks:
            allowed = b.and_(allowed, cmp.ge_const(b, streak_q, self.commit_ticks))
        if self.max_grants:
            allowed = b.and_(allowed, b.not_(cmp.ge_const(b, spent_q, self.max_grants)))
        if self.confirm_window_ticks:
            # confirmed now, or still inside the window a recent confirm opened
            allowed = b.and_(allowed, b.or_(confirm, b.or_all(window_q)) if window_q else confirm)

        trip_now = b.ZERO
        if self.sticky_block:
            trip_now = b.or_(trip_now, blocked)
        if H:
            silent = b.not_(inp["heartbeat"])
            trip_now = b.or_(trip_now, b.and_(silent, cmp.ge_const(b, q["silence"], H - 1)) if H > 1 else silent)
        if self.halt_bits:
            halted = q["halted"][0]
            allowed = b.and_(allowed, b.not_(b.or_(halted, trip_now)))
        if self.confirm_per_irreversible:
            token = q["token"][0]
            allowed = b.and_(allowed, b.or_(b.not_(inp["irreversible"]), b.or_(token, confirm)))
        break_now = b.ZERO
        if k:
            failed = inp["failed"]
            break_now = b.and_(failed, cmp.ge_const(b, q["fails"], k - 1)) if k > 1 else failed
            tripped = q["tripped"][0]
            allowed = b.and_(allowed, b.not_(b.or_(tripped, break_now)))
        if self.two_key:
            have_a = b.or_(q["key_a"][0], confirm)
            have_b = b.or_(q["key_b"][0], inp["confirm_b"])
            allowed = b.and_(allowed, b.and_(have_a, have_b))
        R = self.trip_after_refusals
        if R:
            refused_tripped = q["refused_tripped"][0]
            allowed = b.and_(allowed, b.not_(refused_tripped))
        grant = allowed
        # Only now, once the verdict exists: a refusal of an actual request. `grant` reaches
        # the next state and never a condition of this tick, so there is no loop.
        refusal_now = b.and_(request, b.not_(grant)) if R else b.ZERO
        trip_refusals = (b.and_(refusal_now, cmp.ge_const(b, q["refused"], R - 1)) if R > 1 else refusal_now) if R else b.ZERO

        def drive(name: str, d: list[int]) -> None:
            for qi, di in zip(q[name], d):
                b.drive(qi, di)

        reload_gap = cmp.const_bits(b, min(self.min_gap_ticks - 1, _cap(self.gap_bits)) if self.gap_bits else 0, self.gap_bits)
        drive("gap", cmp.mux_bits(b, grant, cmp.decrement_saturating(b, gap_q), reload_gap))
        drive("streak", cmp.mux_bits(b, intent, cmp.const_bits(b, 0, self.streak_bits), cmp.increment_saturating(b, streak_q, b.ONE)))
        drive("spent", cmp.mux_bits(b, grant, list(spent_q), cmp.increment_saturating(b, spent_q, b.ONE)))
        reload_window = cmp.const_bits(b, min(self.confirm_window_ticks - 1, _cap(self.window_bits)) if self.window_bits else 0, self.window_bits)
        drive("window", cmp.mux_bits(b, confirm, cmp.decrement_saturating(b, window_q), reload_window))
        if self.halt_bits:
            drive("halted", [b.or_(trip_now, b.and_(halted, b.not_(confirm)))])
        if self.silence_bits:
            zeros = cmp.const_bits(b, 0, self.silence_bits)
            drive("silence", cmp.mux_bits(b, inp["heartbeat"], cmp.increment_saturating(b, q["silence"], b.ONE), zeros))
        if self.confirm_per_irreversible:
            drive("token", [b.and_(b.or_(confirm, token), b.not_(b.and_(grant, inp["irreversible"])))])
        if self.fails_bits:
            clear = b.or_(confirm, b.not_(inp["failed"]))
            drive("fails", cmp.mux_bits(b, clear, cmp.increment_saturating(b, q["fails"], b.ONE), cmp.const_bits(b, 0, self.fails_bits)))
        if k:
            drive("tripped", [b.or_(break_now, b.and_(tripped, b.not_(confirm)))])
        if self.refused_bits:
            clear = b.or_(grant, confirm)
            stay = cmp.mux_bits(b, refusal_now, list(q["refused"]), cmp.increment_saturating(b, q["refused"], b.ONE))
            drive("refused", cmp.mux_bits(b, clear, stay, cmp.const_bits(b, 0, self.refused_bits)))
        if R:
            drive("refused_tripped", [b.or_(trip_refusals, b.and_(refused_tripped, b.not_(confirm)))])
        if self.two_key:
            drive("key_a", [b.and_(have_a, b.not_(grant))])
            drive("key_b", [b.and_(have_b, b.not_(grant))])
        return b.finish([grant])

    # -- explaining ----------------------------------------------------------

    def reasons(self, inp: dict[str, int], state: int) -> list[str]:
        """Why this row is refused, in the policy's own words; empty if it is granted.
        Descriptive only: the verdict is the circuit's, and this reads the same
        counters it reads."""
        grant, _ = self.reference(inp, self.fields(state))
        if grant:
            return []
        st, out = self.fields(state), []
        H, k = self.heartbeat_ticks, self.trip_after_failures
        if not inp["request"]:
            out.append("no request")
        if self.forbid_when_blocked and inp["blocked"]:
            out.append("blocked is high")
        if self.gap_bits and st["gap"]:
            out.append(f"cooldown: {st['gap']} tick{'s' if st['gap'] != 1 else ''} left of {self.min_gap_ticks}")
        if self.commit_ticks and st["streak"] < self.commit_ticks:
            out.append(f"commitment: {st['streak']} of {self.commit_ticks} consecutive intent ticks")
        if self.max_grants and st["spent"] >= self.max_grants:
            out.append(f"budget: {st['spent']} of {self.max_grants} grants used")
        if self.confirm_window_ticks and not (inp["confirm"] or st["window"]):
            out.append(f"no confirmation inside the last {self.confirm_window_ticks} ticks")
        if self.halt_bits:
            if st["halted"]:
                out.append("halted until a confirm lifts it" + (" (this confirm lifts it for the next tick)" if inp["confirm"] else ""))
            elif self.sticky_block and inp["blocked"]:
                out.append("halted: blocked is high, and the halt stays until a confirm")
            elif H and not inp.get("heartbeat", 0) and (H == 1 or st["silence"] >= H - 1):
                out.append(f"halted: {H} ticks without a heartbeat")
        if self.confirm_per_irreversible and inp.get("irreversible", 0) and not (st["token"] or inp["confirm"]):
            out.append("irreversible, and no unspent confirm")
        if k:
            if st["tripped"]:
                out.append("breaker tripped until a confirm resets it")
            elif inp.get("failed", 0) and (k == 1 or st["fails"] >= k - 1):
                out.append(f"breaker: {k} consecutive failures")
        if self.trip_after_refusals and st["refused_tripped"]:
            out.append(f"halted after {self.trip_after_refusals} refusals in a row; a confirm resets it")
        if self.two_key:
            missing = [n for n, have in (("confirm", st["key_a"] or inp["confirm"]), ("confirm_b", st["key_b"] or inp.get("confirm_b", 0))) if not have]
            if missing:
                out.append("two keys needed, missing " + " and ".join(missing))
        return out

    # -- checking ------------------------------------------------------------

    def rows(self) -> Iterator[tuple[dict[str, int], int, int]]:
        """Every (inputs, state, row index) of the domain, in step_table order."""
        names = self.input_names()
        n_in = len(names)
        patterns = [dict(zip(names, ((inputs >> i) & 1 for i in range(n_in)))) for inputs in range(1 << n_in)]
        for state in range(1 << self.state_bits):
            for inputs, inp in enumerate(patterns):
                yield inp, state, inputs | (state << n_in)

    def verify(self, circuit: Circuit | None = None) -> dict:
        """Exhaustive equality between the compiled circuit and the Python reference."""
        circuit = circuit if circuit is not None else self.build()
        outs, nxt = exhaust.step_table(circuit)
        want_out = np.zeros(len(outs), dtype=np.uint64)
        want_nxt = np.zeros(len(nxt), dtype=np.uint64)
        st, last_state = None, None
        for inp, state, row in self.rows():
            if state != last_state:
                st, last_state = self.fields(state), state
            grant, nstate = self.reference(inp, st)
            want_out[row] = grant
            want_nxt[row] = self.pack(nstate)
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
        names = self.input_names()
        n_in = len(names)
        if circuit.n_inputs != n_in:
            raise ValueError(f"circuit has {circuit.n_inputs} inputs, policy reads {n_in}")

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
        # latches: ticks since the last grant, consecutive intent ticks, grants so far,
        # ticks since a confirm, whether a halt or a breaker is open, silent ticks,
        # consecutive failures, an unspent token, and the two keys. Anything the circuit
        # can reach from reset under any inputs is visited.
        violations = {
            name: 0
            for name in ("rate_limit", "commitment", "forbidden", "budget", "confirm_window", "halted", "one_shot",
                         "breaker", "refusal_breaker", "two_key")
        }
        H, k, R = claim.heartbeat_ticks, claim.trip_after_failures, claim.trip_after_refusals
        gap_cap = max(claim.min_gap_ticks, 1)
        streak_cap = max(claim.commit_ticks, 1)
        spent_cap = claim.max_grants + 1 if claim.max_grants else 1
        window_cap = max(claim.confirm_window_ticks, 1)
        patterns = []
        for inputs in range(1 << n_in):
            inp = dict(zip(names, ((inputs >> i) & 1 for i in range(n_in))))
            for name in OPTIONAL_INPUTS:
                inp.setdefault(name, _ABSENT_INPUT[name])
            patterns.append(inp)

        # state, ticks since a grant (none yet), intent streak, grants, ticks since a
        # confirm (none yet), halt open, silent ticks, token held, consecutive failures,
        # breaker open, key a held, key b held, consecutive refusals, refusal halt open
        start = (0, gap_cap, 0, 0, window_cap, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        seen, frontier, checked = {start}, [start], 0
        while frontier:
            new = []
            for (state, since, streak, spent, since_confirm, halt_open, silence, token, consec, broken, key_a, key_b,
                 refusals, refusal_halt) in frontier:
                for inputs, inp in enumerate(patterns):
                    request, intent, blocked, confirm = inp["request"], inp["intent"], inp["blocked"], inp["confirm"]
                    irreversible, failed, heartbeat, confirm_b = inp["irreversible"], inp["failed"], inp["heartbeat"], inp["confirm_b"]
                    row = inputs | (state << n_in)
                    grant = int(outs[row])
                    checked += 1
                    trip_now = (claim.sticky_block and blocked) or (H and not heartbeat and silence >= H - 1)
                    break_now = k and failed and consec >= k - 1
                    have_a, have_b = key_a or confirm, key_b or confirm_b
                    if grant:
                        if claim.min_gap_ticks and since < claim.min_gap_ticks:
                            violations["rate_limit"] += 1
                        if claim.commit_ticks and streak < claim.commit_ticks:
                            violations["commitment"] += 1
                        if claim.forbid_when_blocked and blocked:
                            violations["forbidden"] += 1
                        if claim.max_grants and spent >= claim.max_grants:
                            violations["budget"] += 1
                        # a confirm on this very tick counts, so the monitor reads 0
                        if claim.confirm_window_ticks and not confirm and since_confirm >= claim.confirm_window_ticks:
                            violations["confirm_window"] += 1
                        if (claim.sticky_block or H) and (halt_open or trip_now):
                            violations["halted"] += 1
                        if claim.confirm_per_irreversible and irreversible and not (token or confirm):
                            violations["one_shot"] += 1
                        if k and (broken or break_now):
                            violations["breaker"] += 1
                        if R and refusal_halt:
                            violations["refusal_breaker"] += 1
                        if claim.two_key and not (have_a and have_b):
                            violations["two_key"] += 1
                    nxt_state = int(nxt[row])
                    # One tick has elapsed by the next row, so a grant leaves 1, not 0.
                    # Starting from 0 reads one tick short and reports a violation on
                    # the first legal grant — the same off-by-one P1's monitor had.
                    t = (
                        nxt_state,
                        1 if grant else min(since + 1, gap_cap),
                        min(streak + 1, streak_cap) if intent else 0,
                        min(spent + 1, spent_cap) if grant else spent,
                        1 if confirm else min(since_confirm + 1, window_cap),
                        int(bool(trip_now or (halt_open and not confirm))) if (claim.sticky_block or H) else 0,
                        (0 if heartbeat else min(silence + 1, max(H - 1, 0))) if H else 0,
                        int(bool((confirm or token) and not (grant and irreversible))) if claim.confirm_per_irreversible else 0,
                        (0 if (confirm or not failed) else min(consec + 1, max(k - 1, 0))) if k else 0,
                        int(bool(break_now or (broken and not confirm))) if k else 0,
                        int(bool(have_a and not grant)) if claim.two_key else 0,
                        int(bool(have_b and not grant)) if claim.two_key else 0,
                        # a refusal is a request that was not granted; a grant or a confirm
                        # clears the run, a tick with no request neither adds nor clears
                        (0 if (grant or confirm) else (min(refusals + 1, max(R - 1, 0)) if request else refusals)) if R else 0,
                        int(bool((R and request and not grant and (R == 1 or refusals >= R - 1))
                                 or (refusal_halt and not confirm))) if R else 0,
                    )
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
