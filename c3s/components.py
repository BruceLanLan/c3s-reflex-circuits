"""Reusable reflex components.

Small, pure computations that recur across sensorimotor circuits. Each component
comes in two forms:

* a *wiring function* that adds gates to an existing `Builder` and returns
  signal handles, so components compose inside a larger circuit;
* a *standalone circuit* registered in `CATALOG`, with a Python reference model
  of its behaviour that the test suite checks exhaustively.

Multi-bit values are little-endian lists of signals (index 0 is the LSB).
Sequential components document their latch order; the reference step functions
use the same order, which is what makes the step relation checkable bit for bit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from .netlist import Builder, Circuit

Bits = list[int]


# ---------------------------------------------------------------------------
# Wiring functions
# ---------------------------------------------------------------------------


def const_bits(b: Builder, value: int, width: int) -> Bits:
    return [b.ONE if (value >> i) & 1 else b.ZERO for i in range(width)]


def ge(b: Builder, x: Sequence[int], y: Sequence[int]) -> int:
    """Unsigned x >= y. Constant operands fold away."""
    if len(x) != len(y):
        raise ValueError("width mismatch")
    acc = b.ONE  # equal so far => x >= y
    for xi, yi in zip(x, y):  # LSB to MSB; higher bits override
        gt_here = b.and_(xi, b.not_(yi))
        eq_here = b.not_(b.xor(xi, yi))
        acc = b.or_(gt_here, b.and_(eq_here, acc))
    return acc


def gt(b: Builder, x: Sequence[int], y: Sequence[int]) -> int:
    return b.not_(ge(b, y, x))


def ge_const(b: Builder, x: Sequence[int], k: int) -> int:
    if k <= 0:
        return b.ONE
    if k >= 1 << len(x):
        return b.ZERO
    return ge(b, x, const_bits(b, k, len(x)))


def mux_bits(b: Builder, sel: int, a: Sequence[int], c: Sequence[int]) -> Bits:
    """sel ? c : a"""
    return [b.mux(sel, ai, ci) for ai, ci in zip(a, c)]


def max2(b: Builder, x: Sequence[int], y: Sequence[int]) -> tuple[Bits, int]:
    """(max(x, y), 1 if y is strictly larger). Used to reduce left/right eyes to
    one magnitude plus a side bit."""
    y_wins = gt(b, y, x)
    return mux_bits(b, y_wins, x, y), y_wins


def add(b: Builder, x: Sequence[int], y: Sequence[int]) -> Bits:
    """Unsigned add with carry out (result width = max width + 1)."""
    n = max(len(x), len(y))
    x = list(x) + [b.ZERO] * (n - len(x))
    y = list(y) + [b.ZERO] * (n - len(y))
    out, carry = [], b.ZERO
    for xi, yi in zip(x, y):
        s = b.xor(xi, yi)
        out.append(b.xor(s, carry))
        carry = b.or_(b.and_(xi, yi), b.and_(s, carry))
    return out + [carry]


def popcount(b: Builder, xs: Sequence[int]) -> Bits:
    width = max(1, len(xs).bit_length())
    layer: list[Bits] = [[x] for x in xs]
    if not layer:
        return const_bits(b, 0, width)
    while len(layer) > 1:
        nxt = []
        for i in range(0, len(layer) - 1, 2):
            nxt.append(add(b, layer[i], layer[i + 1]))
        if len(layer) % 2:
            nxt.append(layer[-1])
        layer = nxt
    res = layer[0][:width]
    return res + [b.ZERO] * (width - len(res))


def argmax(b: Builder, values: Sequence[Sequence[int]]) -> Bits:
    """Index of the largest value; ties go to the lowest index."""
    idx_width = max(1, (len(values) - 1).bit_length())
    best = list(values[0])
    idx = const_bits(b, 0, idx_width)
    for j in range(1, len(values)):
        take = gt(b, values[j], best)
        best = mux_bits(b, take, best, values[j])
        idx = mux_bits(b, take, idx, const_bits(b, j, idx_width))
    return idx


def decrement_saturating(b: Builder, x: Sequence[int]) -> Bits:
    """max(x - 1, 0)."""
    is_zero = b.not_(b.or_all(x))
    out, borrow = [], b.ONE
    for xi in x:
        out.append(b.xor(xi, borrow))
        borrow = b.and_(borrow, b.not_(xi))
    return mux_bits(b, is_zero, out, list(x))


def increment_saturating(b: Builder, x: Sequence[int], en: int) -> Bits:
    """min(x + en, 2**w - 1)."""
    is_max = b.and_all(x)
    out, carry = [], en
    for xi in x:
        out.append(b.xor(xi, carry))
        carry = b.and_(xi, carry)
    return mux_bits(b, is_max, out, list(x))


def recent_pulse(b: Builder, pulse: int, window: int) -> int:
    """1 if `pulse` was high in this tick or any of the previous window-1 ticks.
    Latches, oldest last: latch j holds the pulse from j+1 ticks ago."""
    taps = [pulse]
    prev = pulse
    qs = []
    for _ in range(window - 1):
        q = b.latch()
        qs.append((q, prev))
        taps.append(q)
        prev = q
    for q, d in qs:
        b.drive(q, d)
    return b.or_all(taps)


def saturating_counter(b: Builder, inc: int, clear: int, width: int) -> Bits:
    """Counts ticks with `inc` high, saturating; `clear` wins. Returns the updated
    count, which is also what the latches store (latch i = bit i)."""
    qs = [b.latch() for _ in range(width)]
    bumped = increment_saturating(b, qs, inc)
    nxt = mux_bits(b, clear, bumped, const_bits(b, 0, width))
    for q, d in zip(qs, nxt):
        b.drive(q, d)
    return nxt


def refractory_gate(b: Builder, trigger: int, hold: int, width: int) -> tuple[int, Bits]:
    """Pass `trigger` only when the timer is idle; a pass reloads the timer with
    `hold` (constant, < 2**width) which then counts down one per tick.
    Latch i = timer bit i. Returns (fire, timer_after_tick)."""
    if not 0 < hold < 1 << width:
        raise ValueError("hold must fit in the timer")
    qs = [b.latch() for _ in range(width)]
    idle = b.not_(b.or_all(qs))
    fire = b.and_(trigger, idle)
    nxt = mux_bits(b, fire, decrement_saturating(b, qs), const_bits(b, hold, width))
    for q, d in zip(qs, nxt):
        b.drive(q, d)
    return fire, nxt


# ---------------------------------------------------------------------------
# Catalogue of standalone components with reference models
# ---------------------------------------------------------------------------

StepRef = Callable[[int, int], tuple[int, int]]  # (inputs, state) -> (outputs, next_state)


@dataclass(frozen=True)
class Component:
    name: str
    summary: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    n_state: int
    build: Callable[[], Circuit]
    reference: StepRef


def _bits_of(v: int, width: int) -> list[int]:
    return [(v >> i) & 1 for i in range(width)]


def _pack(bits: Sequence[int]) -> int:
    return sum((x & 1) << i for i, x in enumerate(bits))


def _field(v: int, lo: int, width: int) -> int:
    return (v >> lo) & ((1 << width) - 1)


def _comb(fn: Callable[[int], int]) -> StepRef:
    return lambda x, s: (fn(x), 0)


def _named(prefix: str, width: int) -> tuple[str, ...]:
    return tuple(f"{prefix}[{i}]" for i in range(width))


def _make_ge_const(width: int, k: int) -> Component:
    def build() -> Circuit:
        b = Builder(width)
        return b.finish([ge_const(b, b.inputs(), k)])

    return Component(
        f"threshold_ge{k}_w{width}",
        f"x >= {k} for a {width}-bit unsigned x",
        _named("x", width),
        ("above",),
        0,
        build,
        _comb(lambda x: int(x >= k)),
    )


def _make_max2(width: int) -> Component:
    def build() -> Circuit:
        b = Builder(2 * width)
        ins = b.inputs()
        m, side = max2(b, ins[:width], ins[width:])
        return b.finish(m + [side])

    def ref(x: int) -> int:
        l, r = _field(x, 0, width), _field(x, width, width)
        return max(l, r) | (int(r > l) << width)

    return Component(
        f"laterality_max2_w{width}",
        f"reduce two {width}-bit eye channels to max magnitude + side bit (1 = second input wins)",
        _named("left", width) + _named("right", width),
        _named("max", width) + ("right_wins",),
        0,
        build,
        _comb(ref),
    )


def _make_popcount(n: int) -> Component:
    width = n.bit_length()

    def build() -> Circuit:
        b = Builder(n)
        return b.finish(popcount(b, b.inputs()))

    return Component(
        f"popcount_{n}",
        f"number of set bits among {n} inputs",
        _named("x", n),
        _named("count", width),
        0,
        build,
        _comb(lambda x: bin(x).count("1")),
    )


def _make_argmax(n: int, width: int) -> Component:
    idx_width = max(1, (n - 1).bit_length())

    def build() -> Circuit:
        b = Builder(n * width)
        ins = b.inputs()
        return b.finish(argmax(b, [ins[i * width : (i + 1) * width] for i in range(n)]))

    def ref(x: int) -> int:
        vals = [_field(x, i * width, width) for i in range(n)]
        return vals.index(max(vals))

    names: tuple[str, ...] = ()
    for i in range(n):
        names += _named(f"v{i}", width)
    return Component(
        f"argmax_{n}x{width}",
        f"index of the largest of {n} {width}-bit values (ties -> lowest index)",
        names,
        _named("index", idx_width),
        0,
        build,
        _comb(ref),
    )


def _make_recent_pulse(window: int) -> Component:
    def build() -> Circuit:
        b = Builder(1)
        return b.finish([recent_pulse(b, b.input(0), window)])

    def ref(x: int, s: int) -> tuple[int, int]:
        hist = _bits_of(s, window - 1)
        out = int(bool(x & 1) or any(hist))
        nxt = [x & 1] + hist[:-1]
        return out, _pack(nxt)

    return Component(
        f"recent_pulse_{window}",
        f"pulse seen within the last {window} ticks (inclusive of the current tick)",
        ("pulse",),
        ("recent",),
        window - 1,
        build,
        ref,
    )


def _make_counter(width: int) -> Component:
    top = (1 << width) - 1

    def build() -> Circuit:
        b = Builder(2)
        return b.finish(saturating_counter(b, b.input(0), b.input(1), width))

    def ref(x: int, s: int) -> tuple[int, int]:
        inc, clear = x & 1, (x >> 1) & 1
        nxt = 0 if clear else min(s + inc, top)
        return nxt, nxt

    return Component(
        f"saturating_counter_w{width}",
        f"{width}-bit saturating event counter with synchronous clear",
        ("inc", "clear"),
        _named("count", width),
        width,
        build,
        ref,
    )


def _make_refractory(width: int, hold: int) -> Component:
    def build() -> Circuit:
        b = Builder(1)
        fire, _ = refractory_gate(b, b.input(0), hold, width)
        return b.finish([fire])

    def ref(x: int, s: int) -> tuple[int, int]:
        fire = int(bool(x & 1) and s == 0)
        nxt = hold if fire else max(s - 1, 0)
        return fire, nxt

    return Component(
        f"refractory_hold{hold}_w{width}",
        f"lets a trigger through, then blocks for {hold} ticks",
        ("trigger",),
        ("fire",),
        width,
        build,
        ref,
    )


CATALOG: dict[str, Component] = {
    c.name: c
    for c in [
        _make_ge_const(4, 5),
        _make_ge_const(8, 100),
        _make_max2(3),
        _make_max2(4),
        _make_popcount(8),
        _make_popcount(15),
        _make_argmax(3, 3),
        _make_argmax(4, 2),
        _make_recent_pulse(4),
        _make_recent_pulse(8),
        _make_counter(3),
        _make_counter(4),
        _make_refractory(3, 5),
        _make_refractory(2, 3),
    ]
}
