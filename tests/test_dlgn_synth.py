import shutil

import numpy as np
import pytest

from c3s import dlgn, exhaust, synth
from c3s.netlist import Builder


@pytest.mark.parametrize("g", range(16))
def test_wired_gate_matches_truth_row(g):
    b = Builder(2)
    c = b.finish([dlgn.wire_gate(b, g, b.input(0), b.input(1))])
    tt = exhaust.truth_table(c)  # row r: input0 = r&1 (a), input1 = r>>1 (b)
    for r in range(4):
        a, bb = r & 1, r >> 1
        assert int(tt[r]) == int(dlgn.TRUTH[g][(a << 1) | bb])


def test_hard_network_compiles_exactly():
    rng = np.random.default_rng(3)
    n_in, widths, n_classes = 7, [24, 24, 12], 3
    layers, prev = [], n_in
    for w in widths:
        layers.append((rng.integers(0, prev, w), rng.integers(0, prev, w), rng.integers(0, 16, w)))
        prev = w
    net = dlgn.HardNetwork(n_in, layers, n_classes)
    rows = np.arange(1 << n_in)
    x = ((rows[:, None] >> np.arange(n_in)) & 1).astype(np.uint8)
    circuit = net.to_circuit()
    assert exhaust.first_mismatch(exhaust.truth_table(circuit), net.predict(x).astype(np.uint64)) is None


@pytest.mark.skipif(not (shutil.which("yosys-abc") or shutil.which("abc")), reason="ABC not installed")
def test_abc_table_synthesis_is_verified():
    rng = np.random.default_rng(5)
    n_in = 8
    table = rng.integers(0, 4, 1 << n_in).astype(np.uint64)
    circuit, report = synth.synthesize_table(table, n_in, 2)
    assert exhaust.first_mismatch(exhaust.truth_table(circuit), table) is None
    assert report["selected"] in synth.RECIPES


def test_training_learns_a_small_function():
    pytest.importorskip("torch")
    rows = np.arange(1 << 6)
    x = ((rows[:, None] >> np.arange(6)) & 1).astype(np.uint8)
    y = ((x[:, 0] & x[:, 1]) | x[:, 2]).astype(np.int64)
    net, _ = dlgn.train(x, y, [32, 16], 2, epochs=300, lr=0.05, tau=2.0, batch=64, seed=0)
    assert (net.predict(x) == y).mean() >= 0.95
