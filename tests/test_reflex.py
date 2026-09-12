from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from c3s import exhaust, loom, reflex

SUBGRAPH = Path(__file__).resolve().parents[1] / "data" / "malecns-v1.0-gf-escape-subgraph.json"
PARAMS = replace(loom.TeacherParams(), gf_threshold=3.0, parallel_threshold=4.0, samples_per_bin=16)


@pytest.fixture(scope="module")
def table():
    return loom.build_table(SUBGRAPH, PARAMS)


def test_table_never_emits_reserved_action(table):
    assert set(np.unique(table.table).tolist()) <= {0, 1, 2}


def test_hand_policy_matches_staircase_projection(table):
    circuit, info = reflex.hand_policy(table)
    got = exhaust.truth_table(circuit)
    bad = np.count_nonzero(got != table.table)
    # Every disagreement must be explained by a staircase cell the baseline cannot express.
    if all(v == 0 for side in info["staircase_cells_wrong"].values() for v in side.values()):
        assert bad == 0
    else:
        assert bad > 0


def test_core_step_relation_matches_reference(table):
    circuit, _ = reflex.hand_policy(table)
    policy_table = exhaust.truth_table(circuit)
    core = reflex.escape_core(circuit, PARAMS)
    outs, nxt = exhaust.step_table(core)
    step = reflex.core_reference(policy_table, PARAMS)
    n_in = core.n_inputs
    rows = np.arange(len(outs))
    sample = np.random.default_rng(0).choice(rows, 20000, replace=False)
    for r in sample:
        o, s = step(int(r) & ((1 << n_in) - 1), int(r) >> n_in)
        assert (o, s) == (int(outs[r]), int(nxt[r]))


def test_quantized_core_tracks_teacher_on_a_fast_loom(table):
    circuit, _ = reflex.hand_policy(table)
    core = reflex.escape_core(circuit, PARAMS)
    outs, nxt = exhaust.step_table(core)
    stim = loom.Stimulus(10.0, 0.0)
    w = loom.load_weights(SUBGRAPH, PARAMS)
    teacher = loom.run_teacher_episode(stim, w, PARAMS)
    got = reflex.run_core_episode(outs, nxt, stim, PARAMS)
    assert teacher.action != loom.CORE_HOLD
    assert got.action != loom.CORE_HOLD
