"""The five temporal properties of the escape core (scripts/check_properties.py) hold
for the committed netlist: always by the Python method, and by Yosys where it is
installed. Each property's negative control must fail in both methods, so a proof
cannot pass by being vacuous."""

import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_properties", ROOT / "scripts" / "check_properties.py")
cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp)  # type: ignore[union-attr]

has_yosys = pytest.mark.skipif(shutil.which("yosys") is None, reason="Yosys not installed")


@pytest.fixture(scope="module")
def setup():
    wing, refr = cp.teacher_timing()
    return cp.load("core-hand-abc"), cp.load("policy-hand-abc"), wing, refr


@pytest.fixture(scope="module")
def python_result(setup):
    return cp.python_method(*setup)


def test_spec_timing_matches_the_teacher():
    assert cp.spec_timing() == cp.teacher_timing()


@pytest.mark.parametrize("prop", cp.PROPERTIES)
def test_python_method_proves_the_property(python_result, prop):
    r = python_result[prop]
    assert r["counterexamples"] == 0
    assert r.get("reset_satisfies", True)
    assert r.get("rows", 0) in (1 << 23, 12 << 17)
    if "configurations" in r:
        assert r["configurations"] == 12


@pytest.mark.parametrize("prop", cp.PROPERTIES)
def test_python_control_fails(setup, prop):
    assert cp.python_method(*setup, mutate=prop)[prop]["counterexamples"] > 0


@has_yosys
def test_yosys_proves_every_property(setup):
    core, policy, _, _ = setup
    res = cp.yosys_method(core, policy)
    assert [p for p, r in res.items() if not r["proved"]] == []
    assert all(r["induction_depth"] is not None for r in res.values())


@has_yosys
@pytest.mark.parametrize("prop", cp.PROPERTIES)
def test_yosys_control_fails(setup, prop):
    core, policy, _, _ = setup
    assert not cp.yosys_method(core, policy, properties=(prop,), mutate=prop)[prop]["proved"]
