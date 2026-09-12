from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from c3s import exhaust, loom, reflex

SUBGRAPH = Path(__file__).resolve().parents[1] / "data" / "malecns-v1.0-gf-escape-subgraph.json"
PARAMS = replace(loom.TeacherParams(), gf_threshold=4.25, parallel_threshold=6.0, samples_per_bin=8)
ENC = loom.ENCODING_3BIT  # small domain keeps these tests fast; the build uses the default encoding


@pytest.fixture(scope="module")
def table():
    return loom.build_table(SUBGRAPH, PARAMS, ENC)


def test_encoding_bins_are_consistent_with_ranges():
    for enc in (loom.ENCODING_3BIT, loom.ENCODING_4BIT):
        for k in range(enc.levels):
            lo, hi = enc.size_range(k)
            assert enc.size_bin((lo * hi) ** 0.5) == k
            lo, hi = enc.speed_range(k)
            assert enc.speed_bin((lo * hi) ** 0.5) == k


def test_vectorised_table_matches_scalar_lookup(table):
    rng = np.random.default_rng(1)
    b, m = ENC.bits, ENC.levels - 1
    for r in rng.integers(0, 1 << ENC.n_inputs, 500):
        sl, vl, sr, vr = r & m, (r >> b) & m, (r >> 2 * b) & m, (r >> 3 * b) & m
        gf = table.eyes["L"].gf[sl, vl] or table.eyes["R"].gf[sr, vr]
        par = table.eyes["L"].par[sl, vl] or table.eyes["R"].par[sr, vr]
        assert int(table.table[r]) == int(gf) | int(par) << 1


def test_hand_policy_is_exact_when_staircases_are(table):
    circuit, info = reflex.hand_policy(table)
    bad = np.count_nonzero(exhaust.truth_table(circuit) != table.table)
    exact = all(v == 0 for side in info["staircase_cells_wrong"].values() for v in side.values())
    assert (bad == 0) == exact


def test_core_matches_vectorised_reference_and_scalar_state_machine(table):
    circuit, _ = reflex.hand_policy(table)
    policy_tt = exhaust.truth_table(circuit)
    core = reflex.escape_core(circuit, PARAMS)
    outs, nxt = exhaust.step_table(core)
    ro, rn = reflex.core_reference_tables(policy_tt, PARAMS, ENC.n_inputs)
    assert exhaust.first_mismatch(outs, ro) is None
    assert exhaust.first_mismatch(nxt, rn) is None
    # the vectorised reference agrees with the scalar state machine used by the teacher
    lay = reflex.core_state_layout(PARAMS)
    rb, nf = lay["raise_counter_bits"], ENC.n_inputs
    for r in np.random.default_rng(2).integers(0, len(outs), 3000):
        r = int(r)
        feat, standing = r & ((1 << nf) - 1), (r >> nf) & 1
        raise_c, refr = (r >> (nf + 1)) & ((1 << rb) - 1), r >> (nf + 1 + rb)
        pw = int(policy_tt[feat])
        action = loom.select_action(bool(pw & 1), bool(pw & 2), standing, int(raise_c >= PARAMS.wing_raise_ticks), int(refr > 0))
        out, nr, nfr = loom.core_step(action, raise_c, refr, PARAMS)
        assert (out, nr | nfr << rb) == (int(outs[r]), int(nxt[r]))


def test_default_encoding_policy_has_16_inputs_and_matches_its_table():
    p = replace(PARAMS, samples_per_bin=4)
    t = loom.build_table(SUBGRAPH, p, loom.DEFAULT_ENCODING)
    circuit, info = reflex.hand_policy(t)
    assert circuit.n_inputs == 16 and circuit.n_outputs == 2
    bad = np.count_nonzero(exhaust.truth_table(circuit) != t.table)
    exact = all(v == 0 for side in info["staircase_cells_wrong"].values() for v in side.values())
    assert (bad == 0) == exact


def test_circuit_episode_equals_quantised_teacher(table):
    circuit, _ = reflex.hand_policy(table)
    assert np.array_equal(exhaust.truth_table(circuit), table.table)
    core = reflex.escape_core(circuit, PARAMS)
    outs, nxt = exhaust.step_table(core)
    for stim in loom.family("train")[::3]:
        a = reflex.run_core_episode(outs, nxt, stim, PARAMS, ENC)
        q = loom.run_quantized_teacher_episode(stim, table)
        assert (a.action, a.tick) == (q.action, q.tick)
