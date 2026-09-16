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
    Policy(confirm_window_ticks=8, forbid_when_blocked=False),
    Policy(min_gap_ticks=8, commit_ticks=4, forbid_when_blocked=True, max_grants=3),
    Policy(min_gap_ticks=60, commit_ticks=16, max_grants=15, confirm_window_ticks=8),
]
IDS = [
    f"gap{p.min_gap_ticks}-commit{p.commit_ticks}-block{int(p.forbid_when_blocked)}"
    f"-budget{p.max_grants}-confirm{p.confirm_window_ticks}"
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
        (
            Policy(confirm_window_ticks=8, forbid_when_blocked=False),
            Policy(confirm_window_ticks=4, forbid_when_blocked=False),
            "confirm_window",
        ),
    ],
    ids=["stricter-rate-limit", "longer-commitment", "smaller-budget", "unenforced-block", "shorter-confirm-window"],
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
        row = 0b0001 | (state << 4)  # request high; no intent, not blocked, no confirm
        if int(outs[row]):
            grants.append(tick)
        state = int(nxt[row])
    assert grants[:4] == [1, 9, 17, 25]
    assert all(b - a == 8 for a, b in zip(grants, grants[1:]))


def test_a_confirmation_opens_a_window_and_then_closes_it():
    """A confirm authorises for exactly the next W ticks, including the tick it arrives."""
    policy = Policy(confirm_window_ticks=4, forbid_when_blocked=False)
    circuit = policy.build()
    from c3s import exhaust

    outs, nxt = exhaust.step_table(circuit)
    state, granted = 0, []
    for tick in range(1, 9):
        confirm = 1 if tick == 1 else 0
        row = 0b0001 | (confirm << 3) | (state << 4)
        granted.append(bool(int(outs[row])))
        state = int(nxt[row])
    assert granted == [True, True, True, True, False, False, False, False]


def test_the_inputs_say_who_may_write_them():
    """The distinction that decides whether a rule is a boundary or a cost the agent
    can always pay; nothing in the module can enforce it, so it is at least named."""
    from c3s.policy import AGENT_WRITABLE, INPUT_NAMES, MUST_COME_FROM_THE_TOOL_LAYER

    assert set(AGENT_WRITABLE) | set(MUST_COME_FROM_THE_TOOL_LAYER) == set(INPUT_NAMES)
    assert not set(AGENT_WRITABLE) & set(MUST_COME_FROM_THE_TOOL_LAYER)
    assert "confirm" in MUST_COME_FROM_THE_TOOL_LAYER and "intent" in AGENT_WRITABLE
