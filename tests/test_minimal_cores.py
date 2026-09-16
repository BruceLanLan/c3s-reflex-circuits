"""The minimal cores from scripts/build_minimal_cores.py meet their specifications,
checked from the committed bytes rather than by rebuilding (bytes depend on ABC):

* core-hand-abc-irr equals core-hand-abc on every (input, state) row and has no
  undetectable single stuck-at fault on those rows;
* core-reach behaves like core-hand-abc from reset (product-machine search), its
  reachable set is closed, it has no undetectable fault on reachable rows, and it
  replays the train, holdout and published sealed episodes tick for tick.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from c3s import calibrate, exhaust, loom, reach
from c3s.netlist import from_bytes

ROOT = Path(__file__).resolve().parents[1]
LOOM = ROOT / "circuits" / "loom-escape"
MIN = ROOT / "circuits" / "loom-escape-min"


def load(path: Path):
    m = json.loads(path.read_text())
    return m, from_bytes(bytes.fromhex(m["tapeout_netlist_hex"][2:]), len(m["inputs"]), len(m["outputs"]))


@pytest.fixture(scope="module")
def source():
    return load(LOOM / "core-hand-abc.json")[1]


@pytest.fixture(scope="module")
def source_tables(source):
    return exhaust.step_table(source)


def test_irr_equals_the_source_on_every_row_and_is_irredundant(source, source_tables):
    m, irr = load(MIN / "core-hand-abc-irr.json")
    outs, nxt = exhaust.step_table(irr)
    assert np.array_equal(outs, source_tables[0]) and np.array_equal(nxt, source_tables[1])
    assert m["equivalence"]["rows_checked"] == len(outs) == 1 << 23
    assert reach.undetectable_faults(irr, reach.full_domain(irr)) == []
    assert m["redundancy"]["undetectable"] == 0
    assert reach.nand_count(irr) <= reach.nand_count(source)


def test_reach_core_behaves_like_the_source_from_reset(source):
    m, core = load(MIN / "core-reach.json")
    eq = reach.sequential_equivalence(source, core)
    assert eq["equivalent"]
    assert eq["rows_checked"] == m["equivalence"]["rows_checked"]
    states = reach.reachable_states(core)
    assert states == m["reachable_states"]["states"]
    assert reach.is_closed(core, states)
    assert reach.undetectable_faults(core, reach.states_domain(core, states)) == []


def test_reach_core_replays_every_published_episode(source_tables):
    tj = json.loads((LOOM / "decision-table.json").read_text())
    p = calibrate.params_from_dict(tj["params"])
    enc = loom.Encoding(**{k: tuple(v) if isinstance(v, list) else v for k, v in tj["encoding"].items()})
    _, core = load(MIN / "core-reach.json")
    outs, nxt = exhaust.step_table(core)
    sealed = json.loads((LOOM / "sealed-family.json").read_text())["stimuli"]
    stims = loom.family("train") + loom.family("holdout") + [loom.Stimulus(s["l_over_v_ms"], s["azimuth_deg"]) for s in sealed]
    assert len(stims) == 125
    for s in stims:
        assert reach.motor_trace(outs, nxt, s, p, enc) == reach.motor_trace(*source_tables, s, p, enc), s


def test_hypothesis_record_matches_the_netlists():
    _, irr = load(MIN / "core-hand-abc-irr.json")
    m, core = load(MIN / "core-reach.json")
    h = m["hypothesis"]
    assert h["thresholds"] == {"significant_min_reduction": 0.10, "weakened_max_reduction": 0.02}
    prim = h["primary"]
    assert (prim["irr_nand"], prim["reach_nand"]) == (reach.nand_count(irr), reach.nand_count(core))
    r = (prim["irr_nand"] - prim["reach_nand"]) / prim["irr_nand"]
    assert prim["reduction"] == round(r, 4)
    assert prim["verdict"] == ("significant" if r >= 0.10 else "weakened" if r <= 0.02 else "modest")
