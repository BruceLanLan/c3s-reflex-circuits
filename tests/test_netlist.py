import random

import numpy as np
import pytest

from c3s import exhaust
from c3s.netlist import Builder, Circuit, Latch, Nand, Ref, from_bytes, tick, to_bytes


def random_circuit(rng: random.Random, n_in: int, n_cells: int, n_out: int, p_latch: float) -> Circuit:
    cells = []
    first = 2 + n_in
    latch_idx = []
    for i in range(n_cells):
        s = first + i
        if rng.random() < p_latch:
            latch_idx.append(i)
            cells.append(Latch(0))
        else:
            cells.append(Nand(rng.randrange(s), rng.randrange(s)))
    total = first + n_cells
    for i in latch_idx:
        cells[i] = Latch(rng.randrange(total))
    return Circuit(n_in, n_out, cells)


def test_byte_layout_matches_spec():
    c = Circuit(2, 1, [Nand(2, 3), Latch(4), Nand(4, 5)])
    assert to_bytes(c).hex() == "00000002000003" + "01000004" + "00000004000005"
    r = Circuit(1, 2, [Ref("0x" + "ab" * 20, 7, (2, 1), 2)])
    assert to_bytes(r).hex() == "02" + "ab" * 20 + "0000000000000007" + "0202" + "000002" + "000001"


def test_codec_roundtrip_random():
    rng = random.Random(1)
    for _ in range(200):
        c = random_circuit(rng, rng.randrange(0, 6), rng.randrange(1, 40), 1, 0.2)
        assert from_bytes(to_bytes(c), c.n_inputs, 1) == c


def test_decoder_rejects_forward_nand():
    with pytest.raises(ValueError):
        from_bytes(bytes.fromhex("00000005000002"), 1, 1)


def test_scalar_and_bitsliced_agree_on_random_sequential_circuits():
    rng = random.Random(2)
    for _ in range(60):
        n_in = rng.randrange(1, 6)
        c = random_circuit(rng, n_in, rng.randrange(3, 30), rng.randrange(1, 3), 0.25)
        m = sum(isinstance(x, Latch) for x in c.cells)
        outs, nxt = exhaust.step_table(c)
        for r in range(1 << (n_in + m)):
            ins = [(r >> i) & 1 for i in range(n_in)]
            st = [(r >> (n_in + j)) & 1 for j in range(m)]
            o, ns = tick(c, ins, st)
            assert sum(b << k for k, b in enumerate(o)) == int(outs[r])
            assert sum(b << k for k, b in enumerate(ns)) == int(nxt[r])


def test_latch_holds_previous_tick():
    # Toggle flip-flop: q' = q xor t.
    b = Builder(1)
    q = b.latch()
    b.drive(q, b.xor(q, b.input(0)))
    c = b.finish([q])
    state, seen = [0], []
    for t in [1, 0, 1, 1, 0]:
        out, state = tick(c, [t], state)
        seen.append(out[0])
    assert seen == [0, 1, 1, 0, 1]


@pytest.mark.parametrize(
    "fn, py",
    [
        ("and_", lambda a, b: a & b),
        ("or_", lambda a, b: a | b),
        ("xor", lambda a, b: a ^ b),
    ],
)
def test_builder_gates(fn, py):
    b = Builder(2)
    c = b.finish([getattr(b, fn)(b.input(0), b.input(1))])
    tt = exhaust.truth_table(c)
    assert [int(v) for v in tt] == [py(r & 1, r >> 1) for r in range(4)]


def test_finish_places_outputs_and_buffers_when_needed():
    b = Builder(3)
    x, y, z = b.inputs()
    n = b.nand(x, y)
    m = b.nand(n, z)  # reads n, so n cannot simply move to the tail
    c = b.finish([n, m, x, n])
    tt = exhaust.truth_table(c)
    for r in range(8):
        xv, yv, zv = r & 1, (r >> 1) & 1, (r >> 2) & 1
        nv = 1 - (xv & yv)
        mv = 1 - (nv & zv)
        assert int(tt[r]) == nv | (mv << 1) | (xv << 2) | (nv << 3)
    # body: n, inverter for x; tail: copy of n, m, buffer for x, copy of n
    assert c.metrics()["nand"] == 2 + 4


def test_dead_cells_removed():
    b = Builder(2)
    b.nand(b.input(0), b.input(1))
    keep = b.not_(b.input(0))
    c = b.finish([keep])
    assert c.metrics()["nand"] == 1


def test_equivalence_detects_difference():
    b1 = Builder(2)
    c1 = b1.finish([b1.and_(2, 3)])
    b2 = Builder(2)
    c2 = b2.finish([b2.or_(2, 3)])
    exhaust.assert_equivalent(c1, c1)
    with pytest.raises(AssertionError):
        exhaust.assert_equivalent(c1, c2)
