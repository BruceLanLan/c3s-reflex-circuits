"""Rules compiled to circuits (c3s/policy.py): each compiled policy equals its Python
reference on every row of its domain, every rule holds in every state reachable from
reset under every input, and claiming a rule the circuit does not enforce is caught —
otherwise "proven" would mean nothing."""

import pytest

from c3s.policy import Policy

POLICIES = [
    Policy(),
    Policy(forbid_when_blocked=False),
    Policy(min_gap_ticks=8),
    Policy(commit_ticks=4, forbid_when_blocked=False),
    Policy(max_grants=3, forbid_when_blocked=False),
    Policy(min_gap_ticks=8, commit_ticks=4, forbid_when_blocked=True, max_grants=3),
    Policy(min_gap_ticks=60, commit_ticks=16, max_grants=15),
]
IDS = [
    f"gap{p.min_gap_ticks}-commit{p.commit_ticks}-block{int(p.forbid_when_blocked)}-budget{p.max_grants}"
    for p in POLICIES
]


@pytest.mark.parametrize("policy", POLICIES, ids=IDS)
def test_compiled_circuit_equals_its_python_reference(policy):
    result = policy.verify()
    assert result["outputs_match"] and result["next_state_matches"], result
    assert result["rows"] == 1 << policy.domain_bits
    # The whole point is that this stays exhaustively checkable.
    assert policy.domain_bits <= 24


@pytest.mark.parametrize("policy", POLICIES, ids=IDS)
def test_every_rule_holds_from_reset(policy):
    result = policy.properties()
    assert result["holds"], result["violations"]
    assert result["rows_checked"] >= 8
    assert result["reachable_states"] <= result["states_possible"]


@pytest.mark.parametrize(
    "built,claimed,expected",
    [
        (Policy(min_gap_ticks=8), Policy(min_gap_ticks=9), "rate_limit"),
        (Policy(commit_ticks=4, forbid_when_blocked=False), Policy(commit_ticks=5, forbid_when_blocked=False), "commitment"),
        (Policy(max_grants=3, forbid_when_blocked=False), Policy(max_grants=2, forbid_when_blocked=False), "budget"),
        (Policy(forbid_when_blocked=False), Policy(forbid_when_blocked=True), "forbidden"),
    ],
    ids=["stricter-rate-limit", "longer-commitment", "smaller-budget", "unenforced-block"],
)
def test_claiming_more_than_the_circuit_enforces_is_caught(built, claimed, expected):
    result = built.properties(built.build(), claim=claimed)
    assert not result["holds"], "a stronger claim must not pass"
    assert result["violations"][expected] > 0, result["violations"]


def test_a_rate_limited_policy_grants_exactly_on_the_boundary():
    """8 ticks apart means 8, not 7 and not 9: the tick after the cooldown runs out."""
    policy = Policy(min_gap_ticks=8, forbid_when_blocked=False)
    circuit = policy.build()
    from c3s import exhaust

    outs, nxt = exhaust.step_table(circuit)
    state, grants = 0, []
    for tick in range(1, 40):
        row = 0b001 | (state << 3)  # request high, no intent, not blocked
        if int(outs[row]):
            grants.append(tick)
        state = int(nxt[row])
    assert grants[:4] == [1, 9, 17, 25]
    assert all(b - a == 8 for a, b in zip(grants, grants[1:]))
