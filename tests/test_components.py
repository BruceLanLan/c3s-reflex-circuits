import numpy as np
import pytest

from c3s import exhaust
from c3s.components import CATALOG


@pytest.mark.parametrize("name", sorted(CATALOG))
def test_component_matches_reference_over_full_step_relation(name):
    comp = CATALOG[name]
    circuit = comp.build()
    assert circuit.n_inputs == len(comp.inputs)
    assert circuit.n_outputs == len(comp.outputs)
    outs, nxt = exhaust.step_table(circuit)
    n_state = comp.n_state
    assert circuit.metrics()["latch"] == n_state
    n_in = circuit.n_inputs
    rows = 1 << (n_in + n_state)
    want_o = np.empty(rows, dtype=np.uint64)
    want_s = np.empty(rows, dtype=np.uint64)
    mask = (1 << n_in) - 1
    for r in range(rows):
        o, s = comp.reference(r & mask, r >> n_in)
        want_o[r], want_s[r] = o, s
    assert exhaust.first_mismatch(outs, want_o) is None
    if n_state:
        assert exhaust.first_mismatch(nxt, want_s) is None
