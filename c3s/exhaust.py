"""Whole-domain evaluation by bit-slicing.

Every signal is held as a vector of uint64 words, one bit per input pattern, so a
NAND over all 2**n patterns is a single numpy operation. This is the engine used
for exhaustive equivalence checks. It is intentionally independent of the scalar
`netlist.tick` evaluator; tests cross-check the two.

Sequential circuits are handled through their step relation: the Q of every
latch becomes an extra input and every latch's D an extra output, so a circuit
with n inputs and m latches is checked over all 2**(n+m) (input, state) pairs.
"""

from __future__ import annotations

import numpy as np

from .netlist import Circuit, Latch, Nand, Ref

MAX_EXHAUSTIVE_BITS = 26


def _patterns(n_bits: int) -> np.ndarray:
    """Array of shape (n_bits, words): bit j of word block = bit i of row index."""
    rows = 1 << n_bits
    words = (rows + 63) // 64
    idx = np.arange(words * 64, dtype=np.uint64)
    out = np.empty((n_bits, words), dtype=np.uint64)
    weights = np.left_shift(np.uint64(1), np.arange(64, dtype=np.uint64))
    for i in range(n_bits):
        bits = ((idx >> np.uint64(i)) & np.uint64(1)).reshape(words, 64)
        out[i] = (bits * weights).sum(axis=1, dtype=np.uint64)
    return out


def _unpack(vec: np.ndarray, rows: int) -> np.ndarray:
    as_bytes = vec.astype("<u8").view(np.uint8)
    return np.unpackbits(as_bytes, bitorder="little")[:rows].astype(np.uint8)


def step_table(circuit: Circuit) -> tuple[np.ndarray, np.ndarray]:
    """Evaluate the step relation over every (input, state) pattern.

    Row index r encodes inputs in bits 0..n-1 and latch state in bits n..n+m-1
    (latches numbered in cell order). Returns (outputs, next_state), each a
    uint64 array of length 2**(n+m) whose bit k is output k / latch k.
    """
    if any(isinstance(c, Ref) for c in circuit.cells):
        raise ValueError("flatten REF cells before exhaustive evaluation")
    n = circuit.n_inputs
    latches = [i for i, c in enumerate(circuit.cells) if isinstance(c, Latch)]
    m = len(latches)
    bits = n + m
    if bits > MAX_EXHAUSTIVE_BITS:
        raise ValueError(f"{bits} free bits is too many for exhaustive evaluation")
    rows = 1 << bits
    pat = _patterns(bits)
    words = pat.shape[1]
    ones = np.full(words, np.uint64(0xFFFFFFFFFFFFFFFF), dtype=np.uint64)
    sig = np.zeros((circuit.n_signals, words), dtype=np.uint64)
    sig[1] = ones
    sig[2 : 2 + n] = pat[:n]
    first = circuit.first_cell_signal
    k = 0
    for i, c in enumerate(circuit.cells):
        s = first + i
        if isinstance(c, Nand):
            sig[s] = ~(sig[c.a] & sig[c.b])
        else:
            sig[s] = pat[n + k]
            k += 1
    outs = np.zeros(rows, dtype=np.uint64)
    for j, s in enumerate(circuit.output_signals()):
        outs |= _unpack(sig[s], rows).astype(np.uint64) << np.uint64(j)
    nxt = np.zeros(rows, dtype=np.uint64)
    for j, i in enumerate(latches):
        d = circuit.cells[i].d  # type: ignore[union-attr]
        nxt |= _unpack(sig[d], rows).astype(np.uint64) << np.uint64(j)
    return outs, nxt


def truth_table(circuit: Circuit) -> np.ndarray:
    """Outputs for every input pattern of a combinational circuit."""
    if any(isinstance(c, Latch) for c in circuit.cells):
        raise ValueError("circuit has latches; use step_table")
    return step_table(circuit)[0]


def first_mismatch(got: np.ndarray, want: np.ndarray) -> int | None:
    if got.shape != want.shape:
        raise ValueError(f"shape mismatch {got.shape} vs {want.shape}")
    bad = np.flatnonzero(got != want)
    return int(bad[0]) if bad.size else None


def assert_equivalent(a: Circuit, b: Circuit) -> None:
    """Exhaustive equivalence of two circuits with identical ports and latch order."""
    if (a.n_inputs, a.n_outputs) != (b.n_inputs, b.n_outputs):
        raise AssertionError("port mismatch")
    la = sum(isinstance(c, Latch) for c in a.cells)
    lb = sum(isinstance(c, Latch) for c in b.cells)
    if la != lb:
        raise AssertionError(f"latch count differs ({la} vs {lb})")
    oa, na = step_table(a)
    ob, nb = step_table(b)
    for label, x, y in (("output", oa, ob), ("next-state", na, nb)):
        bad = first_mismatch(x, y)
        if bad is not None:
            raise AssertionError(f"{label} differs at row {bad}: {int(x[bad])} vs {int(y[bad])}")
