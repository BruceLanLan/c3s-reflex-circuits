"""Mechanics of c3s.reach on small sequential circuits: reachability and closure,
equivalence from reset with replayable counterexamples, fault analysis against
brute force, redundancy removal, the latch cut, and the ABC round trips."""

import shutil

import numpy as np
import pytest

from c3s import components as cmp
from c3s import exhaust, reach
from c3s.netlist import Builder, Nand

has_abc = pytest.mark.skipif(not (shutil.which("yosys-abc") or shutil.which("abc")), reason="ABC not installed")


def counter(limit: int = 4, width: int = 3):
    """Counts enabled ticks up to `limit`, clears on `clr`. Only states 0..limit are
    reachable, and `at_limit` is a full comparator, so it is redundant from reset."""
    b = Builder(2)
    en, clr = b.inputs()
    q = [b.latch() for _ in range(width)]
    at_limit = cmp.ge_const(b, q, limit)
    inc = cmp.increment_saturating(b, q, b.and_(en, b.not_(at_limit)))
    nxt = cmp.mux_bits(b, clr, inc, cmp.const_bits(b, 0, width))
    for qi, d in zip(q, nxt):
        b.drive(qi, d)
    return b.finish([at_limit, b.xor(q[0], en)])


def same_step_relation(a, b) -> bool:
    oa, na = exhaust.step_table(a)
    ob, nb = exhaust.step_table(b)
    return np.array_equal(oa, ob) and np.array_equal(na, nb)


def test_reachable_states_and_closure():
    c = counter()
    assert reach.reachable_states(c) == [0, 1, 2, 3, 4]
    assert reach.is_closed(c, [0, 1, 2, 3, 4])
    assert not reach.is_closed(c, [0, 1, 2, 3])


def test_block_step_agrees_with_step_table():
    c = counter()
    outs, nxt = exhaust.step_table(c)
    for s in range(8):
        o, n = reach.block_step(c, s)
        assert np.array_equal(o, outs[s << 2 : (s + 1) << 2])
        assert np.array_equal(n, nxt[s << 2 : (s + 1) << 2])


def test_equivalence_from_reset_and_replayable_counterexample():
    c = counter()
    same = reach.sequential_equivalence(c, reach.rebuild(c))
    assert same["equivalent"] and same["reachable_state_pairs"] == 5 and same["rows_checked"] == 20
    diff = reach.sequential_equivalence(c, counter(limit=3))
    assert not diff["equivalent"]
    cex = diff["counterexample"]
    # replaying the trace from reset reproduces the reported difference
    tables = [exhaust.step_table(c), exhaust.step_table(counter(limit=3))]
    states, last = [0, 0], None
    for x in cex["inputs_from_reset"]:
        last = [int(t[0][x | (s << 2)]) for t, s in zip(tables, states)]
        states = [int(t[1][x | (s << 2)]) for t, s in zip(tables, states)]
    assert last == [cex["output_a"], cex["output_b"]] and last[0] != last[1]


def _observed_with_fault(circuit, dom, g, v):
    sig = np.zeros((circuit.n_signals, dom.mask.shape[0]), dtype=np.uint64)
    sig[1] = ~np.uint64(0)
    sig[2 : 2 + circuit.n_inputs] = dom.inputs
    k = 0
    for i, c in enumerate(circuit.cells):
        s = circuit.first_cell_signal + i
        if s == g:
            sig[s] = dom.mask if v else 0
        elif isinstance(c, Nand):
            sig[s] = ~(sig[c.a] & sig[c.b])
        else:
            sig[s] = dom.state[k]
            k += 1
    return np.stack([sig[s] & dom.mask for s in reach.observed_signals(circuit)])


@pytest.mark.parametrize("which", ["full", "reachable"])
def test_undetectable_faults_match_brute_force(which):
    c = counter()
    dom = reach.full_domain(c) if which == "full" else reach.states_domain(c, reach.reachable_states(c))
    good = _observed_with_fault(c, dom, -1, 0)
    brute = set()
    for i, cell in enumerate(c.cells):
        if isinstance(cell, Nand):
            g = c.first_cell_signal + i
            for v in (0, 1):
                if np.array_equal(_observed_with_fault(c, dom, g, v), good):
                    brute.add((g, v))
    assert set(reach.undetectable_faults(c, dom)) == brute
    if which == "reachable":
        assert brute  # the comparator is redundant from reset


def test_redundancy_removal_keeps_the_specification():
    c = counter()
    full, _ = reach.remove_redundancy(c, reach.full_domain)
    assert same_step_relation(full, c)
    assert reach.undetectable_faults(full, reach.full_domain(full)) == []

    def reachable(x):
        return reach.states_domain(x, reach.reachable_states(x))

    small, log = reach.remove_redundancy(full, reachable)
    assert log and reach.nand_count(small) < reach.nand_count(full)
    assert reach.sequential_equivalence(c, small)["equivalent"]
    assert reach.undetectable_faults(small, reachable(small)) == []


def test_latch_cut_round_trip():
    c = counter()
    cut = reach.cut_latches(c)
    outs, nxt = exhaust.step_table(c)
    assert np.array_equal(exhaust.truth_table(cut), outs | (nxt << np.uint64(c.n_outputs)))
    assert same_step_relation(reach.wrap_latches(cut, c.n_inputs, c.n_outputs), c)


def test_parse_rejects_nonzero_latch_init():
    text = ".model t\n.inputs a\n.outputs y\n.latch a q 1\n.gate buf A=q Y=y\n.end\n"
    with pytest.raises(ValueError, match="initial value"):
        reach.parse_seq_blif(text, 1, 1)


@has_abc
def test_abc_round_trips_are_checked_not_trusted():
    c = counter()
    comb, errors = reach.abc_candidates(c, sequential=False, recipes=["resyn2"])
    assert not errors and same_step_relation(comb["cut+resyn2"], c)
    seq, errors = reach.abc_candidates(c, sequential=True, recipes=["resyn2"])
    assert not errors and reach.sequential_equivalence(c, seq["scorr+resyn2"])["equivalent"]
