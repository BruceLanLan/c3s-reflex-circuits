"""Gate-level circuit IR shared by every stage of the pipeline.

A circuit is an ordered list of cells over a flat signal space:

    signal 0            constant 0
    signal 1            constant 1
    signal 2 .. 2+n-1   primary inputs
    then one signal per cell output, in cell order (a Ref cell yields several)

Cell kinds:

    Nand(a, b)                  out = not (a and b); a, b must be earlier signals
    Latch(d)                    out = the bit stored at the end of the previous tick
                                (0 before the first tick); stores signal d at the end
                                of the current tick. d may point anywhere.
    Ref(cpu, circuit_id, ins, n_out)
                                calls another deployed circuit; its outputs occupy
                                n_out consecutive signals.

The primary outputs are the last n_outputs signals. This matches the byte layout
and tick semantics of the TapeOut on-chain netlist format, so a Circuit here can
be serialised to bytes with no translation step (see `to_bytes`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence, Union

U24_MAX = (1 << 24) - 1

OP_NAND = 0x00
OP_LATCH = 0x01
OP_REF = 0x02


@dataclass(frozen=True)
class Nand:
    a: int
    b: int


@dataclass(frozen=True)
class Latch:
    d: int


@dataclass(frozen=True)
class Ref:
    cpu: str  # 20-byte address, 0x-prefixed hex
    circuit_id: int
    ins: tuple[int, ...]
    n_out: int


Cell = Union[Nand, Latch, Ref]


@dataclass
class Circuit:
    n_inputs: int
    n_outputs: int
    cells: list[Cell] = field(default_factory=list)

    @property
    def first_cell_signal(self) -> int:
        return 2 + self.n_inputs

    @property
    def n_signals(self) -> int:
        return self.first_cell_signal + sum(_width(c) for c in self.cells)

    def output_signals(self) -> list[int]:
        n = self.n_signals
        return list(range(n - self.n_outputs, n))

    def cell_signals(self) -> list[int]:
        """First output signal of each cell."""
        out, s = [], self.first_cell_signal
        for c in self.cells:
            out.append(s)
            s += _width(c)
        return out

    def validate(self) -> None:
        if self.n_inputs < 0 or self.n_outputs < 0:
            raise ValueError("negative port count")
        if self.n_outputs > self.n_signals - self.first_cell_signal:
            raise ValueError("fewer cell outputs than primary outputs")
        s = self.first_cell_signal
        for i, c in enumerate(self.cells):
            if isinstance(c, Nand):
                if not (0 <= c.a < s and 0 <= c.b < s):
                    raise ValueError(f"cell {i}: NAND reads a signal not yet defined")
            elif isinstance(c, Latch):
                pass  # checked below once the full signal count is known
            elif isinstance(c, Ref):
                if any(not (0 <= x < s) for x in c.ins):
                    raise ValueError(f"cell {i}: REF reads a signal not yet defined")
                if not (0 <= len(c.ins) <= 255 and 0 <= c.n_out <= 255):
                    raise ValueError(f"cell {i}: REF port count exceeds u8")
            else:
                raise TypeError(f"cell {i}: unknown cell {c!r}")
            s += _width(c)
        for i, c in enumerate(self.cells):
            if isinstance(c, Latch) and not (0 <= c.d < s):
                raise ValueError(f"cell {i}: LATCH d={c.d} outside signal space")
        if s - 1 > U24_MAX:
            raise ValueError("signal index exceeds u24")

    # ---- metrics ---------------------------------------------------------

    def metrics(self) -> dict:
        nand = sum(isinstance(c, Nand) for c in self.cells)
        latch = sum(isinstance(c, Latch) for c in self.cells)
        ref = sum(isinstance(c, Ref) for c in self.cells)
        return {
            "nand": nand,
            "latch": latch,
            "ref": ref,
            "cells": len(self.cells),
            "bytes": len(to_bytes(self)),
            "depth": logic_depth(self),
        }


def _width(c: Cell) -> int:
    return c.n_out if isinstance(c, Ref) else 1


# ---------------------------------------------------------------------------
# Byte codec (TapeOut netlist layout)
# ---------------------------------------------------------------------------


def _u24(v: int) -> bytes:
    if not 0 <= v <= U24_MAX:
        raise ValueError(f"signal {v} does not fit in u24")
    return v.to_bytes(3, "big")


def to_bytes(circuit: Circuit) -> bytes:
    out = bytearray()
    for c in circuit.cells:
        if isinstance(c, Nand):
            out += bytes([OP_NAND]) + _u24(c.a) + _u24(c.b)
        elif isinstance(c, Latch):
            out += bytes([OP_LATCH]) + _u24(c.d)
        elif isinstance(c, Ref):
            addr = bytes.fromhex(c.cpu.removeprefix("0x"))
            if len(addr) != 20:
                raise ValueError("REF cpu must be a 20-byte address")
            out += bytes([OP_REF]) + addr + c.circuit_id.to_bytes(8, "big")
            out += bytes([len(c.ins), c.n_out]) + b"".join(_u24(x) for x in c.ins)
        else:
            raise TypeError(c)
    return bytes(out)


def to_hex(circuit: Circuit) -> str:
    return "0x" + to_bytes(circuit).hex()


def from_bytes(data: bytes, n_inputs: int, n_outputs: int) -> Circuit:
    cells: list[Cell] = []
    p = 0
    s = 2 + n_inputs

    def take(k: int) -> bytes:
        nonlocal p
        if p + k > len(data):
            raise ValueError(f"truncated netlist at byte {p}")
        chunk = data[p : p + k]
        p += k
        return chunk

    while p < len(data):
        op = take(1)[0]
        if op == OP_NAND:
            a = int.from_bytes(take(3), "big")
            b = int.from_bytes(take(3), "big")
            if a >= s or b >= s:
                raise ValueError(f"NAND at signal {s} reads a future signal")
            cells.append(Nand(a, b))
            s += 1
        elif op == OP_LATCH:
            cells.append(Latch(int.from_bytes(take(3), "big")))
            s += 1
        elif op == OP_REF:
            cpu = "0x" + take(20).hex()
            cid = int.from_bytes(take(8), "big")
            n_in, n_out = take(2)
            ins = tuple(int.from_bytes(take(3), "big") for _ in range(n_in))
            if any(x >= s for x in ins):
                raise ValueError(f"REF at signal {s} reads a future signal")
            cells.append(Ref(cpu, cid, ins, n_out))
            s += n_out
        else:
            raise ValueError(f"unknown opcode 0x{op:02x} at byte {p - 1}")
    circuit = Circuit(n_inputs, n_outputs, cells)
    circuit.validate()
    return circuit


# ---------------------------------------------------------------------------
# Reference evaluator (scalar, one tick)
# ---------------------------------------------------------------------------

Resolver = Callable[[str, int], "tuple[Circuit, int]"]  # -> (circuit, n_state)


def state_size(circuit: Circuit, resolve: Resolver | None = None) -> int:
    n = 0
    for c in circuit.cells:
        if isinstance(c, Latch):
            n += 1
        elif isinstance(c, Ref):
            if resolve is None:
                raise ValueError("circuit contains REF; a resolver is required")
            sub, _ = resolve(c.cpu, c.circuit_id)
            n += state_size(sub, resolve)
    return n


def tick(
    circuit: Circuit,
    inputs: Sequence[int],
    state: Sequence[int] | None = None,
    resolve: Resolver | None = None,
) -> tuple[list[int], list[int]]:
    """Evaluate one tick. Returns (outputs, next_state)."""
    n_state = state_size(circuit, resolve)
    old = list(state) if state is not None else [0] * n_state
    if len(old) != n_state:
        raise ValueError(f"state has {len(old)} bits, circuit needs {n_state}")
    if len(inputs) != circuit.n_inputs:
        raise ValueError(f"expected {circuit.n_inputs} inputs, got {len(inputs)}")
    sig = [0] * circuit.n_signals
    sig[1] = 1
    for i, v in enumerate(inputs):
        sig[2 + i] = 1 if v else 0
    new = [0] * n_state
    s, k = circuit.first_cell_signal, 0
    latch_slots: list[tuple[int, int]] = []
    for c in circuit.cells:
        if isinstance(c, Nand):
            sig[s] = 0 if (sig[c.a] and sig[c.b]) else 1
            s += 1
        elif isinstance(c, Latch):
            sig[s] = old[k]
            latch_slots.append((k, c.d))
            k += 1
            s += 1
        else:
            sub, _ = resolve(c.cpu, c.circuit_id)  # type: ignore[misc]
            m = state_size(sub, resolve)
            outs, nxt = tick(sub, [sig[x] for x in c.ins], old[k : k + m], resolve)
            new[k : k + m] = nxt
            for j in range(c.n_out):
                sig[s + j] = outs[j]
            s += c.n_out
            k += m
    for k_, d in latch_slots:
        new[k_] = sig[d]
    outs = sig[len(sig) - circuit.n_outputs :]
    return outs, new


# ---------------------------------------------------------------------------
# Structure helpers
# ---------------------------------------------------------------------------


def logic_depth(circuit: Circuit) -> int:
    """Longest NAND chain between a source (input, constant, latch Q) and a sink
    (primary output or latch D). REF cells count as depth 1 (opaque)."""
    depth = [0] * circuit.n_signals
    s = circuit.first_cell_signal
    for c in circuit.cells:
        if isinstance(c, Nand):
            depth[s] = 1 + max(depth[c.a], depth[c.b])
            s += 1
        elif isinstance(c, Latch):
            depth[s] = 0
            s += 1
        else:
            d = 1 + max((depth[x] for x in c.ins), default=0)
            for j in range(c.n_out):
                depth[s + j] = d
            s += c.n_out
    sinks = circuit.output_signals() + [c.d for c in circuit.cells if isinstance(c, Latch)]
    return max((depth[x] for x in sinks), default=0)


class Builder:
    """Incremental construction with automatic output placement.

    Signals are plain ints. Latches are allocated first-class so their Q can be
    read before their D is known: `q = b.latch()` then later `b.drive(q, d)`.
    `finish(outputs)` removes dead cells and arranges the primary outputs to be
    the last signals, duplicating a NAND or inserting a double inversion only
    where the layout forces it.
    """

    ZERO = 0
    ONE = 1

    def __init__(self, n_inputs: int):
        self.n_inputs = n_inputs
        self._cells: list[Cell] = []
        self._latch_d: dict[int, int | None] = {}
        self._memo: dict[tuple[int, int], int] = {}

    # -- primitives --
    def input(self, i: int) -> int:
        if not 0 <= i < self.n_inputs:
            raise IndexError(i)
        return 2 + i

    def inputs(self) -> list[int]:
        return [2 + i for i in range(self.n_inputs)]

    def _next(self) -> int:
        return 2 + self.n_inputs + len(self._cells)

    def nand(self, a: int, b: int) -> int:
        key = (min(a, b), max(a, b))
        hit = self._memo.get(key)
        if hit is not None:
            return hit
        s = self._next()
        self._cells.append(Nand(*key))
        self._memo[key] = s
        return s

    def latch(self) -> int:
        s = self._next()
        self._cells.append(Latch(0))
        self._latch_d[s] = None
        return s

    def drive(self, q: int, d: int) -> None:
        if q not in self._latch_d:
            raise ValueError(f"signal {q} is not a latch")
        if self._latch_d[q] is not None:
            raise ValueError(f"latch {q} already driven")
        self._latch_d[q] = d

    # -- derived gates (constant-folding where it is free) --
    def not_(self, a: int) -> int:
        if a == self.ZERO:
            return self.ONE
        if a == self.ONE:
            return self.ZERO
        return self.nand(a, a)

    def and_(self, a: int, b: int) -> int:
        if self.ZERO in (a, b):
            return self.ZERO
        if a == self.ONE:
            return b
        if b == self.ONE or a == b:
            return a
        return self.not_(self.nand(a, b))

    def or_(self, a: int, b: int) -> int:
        if self.ONE in (a, b):
            return self.ONE
        if a == self.ZERO:
            return b
        if b == self.ZERO or a == b:
            return a
        return self.nand(self.not_(a), self.not_(b))

    def xor(self, a: int, b: int) -> int:
        if a == self.ZERO:
            return b
        if b == self.ZERO:
            return a
        if a == self.ONE:
            return self.not_(b)
        if b == self.ONE:
            return self.not_(a)
        if a == b:
            return self.ZERO
        n = self.nand(a, b)
        return self.nand(self.nand(a, n), self.nand(b, n))

    def mux(self, sel: int, a: int, b: int) -> int:
        """sel ? b : a"""
        if sel == self.ZERO or a == b:
            return a
        if sel == self.ONE:
            return b
        return self.nand(self.nand(a, self.not_(sel)), self.nand(b, sel))

    def and_all(self, xs: Iterable[int]) -> int:
        acc = self.ONE
        for x in xs:
            acc = self.and_(acc, x)
        return acc

    def or_all(self, xs: Iterable[int]) -> int:
        acc = self.ZERO
        for x in xs:
            acc = self.or_(acc, x)
        return acc

    def inline(self, circuit: Circuit, inputs: Sequence[int]) -> list[int]:
        """Copy a combinational circuit into this builder; returns its outputs."""
        if len(inputs) != circuit.n_inputs:
            raise ValueError("input count mismatch")
        remap = {0: self.ZERO, 1: self.ONE}
        for i, s in enumerate(inputs):
            remap[2 + i] = s
        sig = circuit.first_cell_signal
        for c in circuit.cells:
            if not isinstance(c, Nand):
                raise ValueError("inline supports NAND-only circuits")
            remap[sig] = self.nand(remap[c.a], remap[c.b])
            sig += 1
        return [remap[s] for s in circuit.output_signals()]

    # -- finalisation --
    def finish(self, outputs: Sequence[int]) -> Circuit:
        for q, d in self._latch_d.items():
            if d is None:
                raise ValueError(f"latch {q} was never driven")
        first = 2 + self.n_inputs
        cells = list(self._cells)
        latch_d = dict(self._latch_d)

        def src(s: int) -> Cell:
            return cells[s - first]

        # Liveness: everything reachable from outputs and from live latches' D.
        live: set[int] = set()
        stack = [s for s in outputs if s >= first]
        while stack:
            s = stack.pop()
            if s in live:
                continue
            live.add(s)
            c = src(s)
            if isinstance(c, Nand):
                stack.extend(x for x in (c.a, c.b) if x >= first)
            elif isinstance(c, Latch):
                d = latch_d[s]
                if d is not None and d >= first:
                    stack.append(d)

        readers: dict[int, int] = {}
        for s in live:
            c = src(s)
            if isinstance(c, Nand):
                for x in {c.a, c.b}:
                    readers[x] = readers.get(x, 0) + 1
            elif isinstance(c, Latch):
                d = latch_d[s]
                readers[d] = readers.get(d, 0) + 1  # type: ignore[index]

        # Decide, per output, whether its driving cell can simply move to the tail.
        movable: list[bool] = []
        claimed: set[int] = set()
        for s in outputs:
            ok = (
                s >= first
                and isinstance(src(s), Nand)
                and s not in claimed
                and readers.get(s, 0) == 0
            )
            movable.append(ok)
            if ok:
                claimed.add(s)

        body = [s for s in sorted(live) if s not in claimed]
        remap: dict[int, int] = {0: 0, 1: 1}
        for i in range(self.n_inputs):
            remap[2 + i] = 2 + i
        new_cells: list[Cell | None] = []
        pending_latch: list[tuple[int, int]] = []  # (new index, old d)

        def emit(cell: Cell | None) -> int:
            new_cells.append(cell)
            return first + len(new_cells) - 1

        for s in body:
            c = src(s)
            if isinstance(c, Nand):
                remap[s] = emit(Nand(remap[c.a], remap[c.b]))
            else:
                idx = emit(None)
                remap[s] = idx
                pending_latch.append((idx, latch_d[s]))  # type: ignore[arg-type]

        # Tail: one cell per output, in output order.
        tail_prep: list[Cell] = []
        for s, ok in zip(outputs, movable):
            if ok:
                c = src(s)
                assert isinstance(c, Nand)
                tail_prep.append(Nand(remap[c.a], remap[c.b]))
            elif s >= first and isinstance(src(s), Nand):
                c = src(s)
                assert isinstance(c, Nand)
                tail_prep.append(Nand(remap[c.a], remap[c.b]))  # duplicate, 1 cell
            else:
                tail_prep.append(("buffer", s))  # type: ignore[arg-type]

        # Buffers need an inverter placed in the body before the tail starts.
        inv_for: dict[int, int] = {}
        for item in tail_prep:
            if isinstance(item, tuple):
                s = item[1]
                if s not in inv_for:
                    inv_for[s] = emit(Nand(remap[s], remap[s]))
        for s, ok in zip(outputs, movable):
            if ok:
                remap[s] = -1  # placeholder; set when emitted below
        for item, s in zip(tail_prep, outputs):
            if isinstance(item, tuple):
                t = emit(Nand(inv_for[item[1]], inv_for[item[1]]))
            else:
                t = emit(item)
            if remap.get(s) == -1:
                remap[s] = t

        for idx, d in pending_latch:
            new_cells[idx - first] = Latch(remap[d])
        circuit = Circuit(self.n_inputs, len(outputs), [c for c in new_cells])  # type: ignore[misc]
        circuit.validate()
        return circuit
