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
    # one policy per tool class, plus the shared halt that feeds each class's blocked
    Policy(min_gap_ticks=8, max_grants=7, confirm_per_irreversible=True, two_key=True),
    Policy(min_gap_ticks=4, commit_ticks=4, confirm_per_irreversible=True, trip_after_failures=3),
    Policy(sticky_block=True, heartbeat_ticks=8, forbid_when_blocked=True),
    # the rule that reads the circuit's own verdict
    Policy(trip_after_refusals=3),
    Policy(min_gap_ticks=8, commit_ticks=4, trip_after_refusals=3, confirm_per_irreversible=True),
]
IDS = [
    f"gap{p.min_gap_ticks}-commit{p.commit_ticks}-block{int(p.forbid_when_blocked)}"
    f"-budget{p.max_grants}-confirm{p.confirm_window_ticks}"
    + (f"-sticky" if p.sticky_block else "")
    + (f"-heartbeat{p.heartbeat_ticks}" if p.heartbeat_ticks else "")
    + ("-oneshot" if p.confirm_per_irreversible else "")
    + (f"-breaker{p.trip_after_failures}" if p.trip_after_failures else "")
    + (f"-refusals{p.trip_after_refusals}" if p.trip_after_refusals else "")
    + ("-twokey" if p.two_key else "")
    for p in POLICIES
]


@pytest.mark.parametrize("policy", POLICIES, ids=IDS)
def test_compiled_circuit_equals_its_python_reference(policy):
    result = policy.verify()
    assert result["outputs_match"] and result["next_state_matches"], result
    assert result["rows"] == 1 << policy.domain_bits
    # The whole point is that this stays exhaustively checkable.
    assert policy.domain_bits <= 24
    assert result["metrics"]["latch"] == policy.state_bits


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
        (Policy(forbid_when_blocked=True), Policy(forbid_when_blocked=True, sticky_block=True), "halted"),
        (Policy(heartbeat_ticks=8, forbid_when_blocked=False), Policy(heartbeat_ticks=4, forbid_when_blocked=False), "halted"),
        (
            Policy(confirm_window_ticks=4, forbid_when_blocked=False),
            Policy(confirm_window_ticks=4, forbid_when_blocked=False, confirm_per_irreversible=True),
            "one_shot",
        ),
        (Policy(trip_after_failures=3, forbid_when_blocked=False), Policy(trip_after_failures=2, forbid_when_blocked=False), "breaker"),
        (Policy(forbid_when_blocked=False), Policy(forbid_when_blocked=False, two_key=True), "two_key"),
        # `blocked` stays enforced here on purpose: the window where the circuit still grants
        # while a stricter claim says it should be halted is a tick where blocked drops. With a
        # rule that refuses for ever (a spent budget) the stricter claim is vacuously true.
        (Policy(trip_after_refusals=3), Policy(trip_after_refusals=2), "refusal_breaker"),
    ],
    ids=[
        "stricter-rate-limit", "longer-commitment", "smaller-budget", "unenforced-block", "shorter-confirm-window",
        "sticky-claimed-on-stateless-block", "shorter-heartbeat", "one-shot-claimed-on-a-window", "breaker-two-vs-three",
        "two-key-claimed-on-none", "fewer-refusals-before-halt",
    ],
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
    from c3s.policy import AGENT_WRITABLE, INPUT_NAMES, MUST_COME_FROM_THE_TOOL_LAYER, OPTIONAL_INPUTS

    assert set(AGENT_WRITABLE) | set(MUST_COME_FROM_THE_TOOL_LAYER) == set(INPUT_NAMES) | set(OPTIONAL_INPUTS)
    assert not set(AGENT_WRITABLE) & set(MUST_COME_FROM_THE_TOOL_LAYER)
    assert "confirm" in MUST_COME_FROM_THE_TOOL_LAYER and "intent" in AGENT_WRITABLE
    # every optional input is a boundary's: none of them may be the agent's to write
    assert set(OPTIONAL_INPUTS) <= set(MUST_COME_FROM_THE_TOOL_LAYER)


# -- the four rules that rest on the tool layer's inputs -----------------------------


def drive(policy, ticks):
    """Run the compiled circuit tick by tick from reset; each tick is a dict of the
    inputs that are high. Returns one bool per tick."""
    from c3s import exhaust

    outs, nxt = exhaust.step_table(policy.build())
    names = policy.input_names()
    state, granted = 0, []
    for high in ticks:
        assert set(high) <= set(names), f"{set(high) - set(names)} not read by this policy"
        inputs = sum(1 << names.index(n) for n in high)
        row = inputs | (state << len(names))
        granted.append(bool(int(outs[row])))
        state = int(nxt[row])
    return granted


R = {"request"}  # the agent asks, nothing else


def test_a_sticky_block_holds_until_a_confirm_and_lifts_on_the_next_tick():
    policy = Policy(sticky_block=True, forbid_when_blocked=False)
    ticks = [R, R | {"blocked"}, R, R, R | {"confirm"}, R]
    assert drive(policy, ticks) == [True, False, False, False, False, True]


def test_a_stateless_block_lifts_by_itself_for_comparison():
    assert drive(Policy(forbid_when_blocked=True), [R, R | {"blocked"}, R]) == [True, False, True]


def test_eight_silent_ticks_halt_and_a_heartbeat_alone_does_not_lift_it():
    policy = Policy(heartbeat_ticks=8, forbid_when_blocked=False)
    ticks = [R | {"heartbeat"}] + [R] * 8 + [R | {"heartbeat"}, R | {"confirm"}, R]
    #        tick 1              2..9        10                 11               12
    got = drive(policy, ticks)
    assert got[:8] == [True] * 8, "seven silent ticks after a heartbeat are still fine"
    assert got[8] is False, "the eighth silent tick is halted"
    assert got[9] is False, "a heartbeat does not lift a halt"
    assert got[10] is False, "the tick whose confirm lifts it grants nothing"
    assert got[11] is True


def test_from_reset_the_halt_comes_exactly_at_the_heartbeat_limit():
    got = drive(Policy(heartbeat_ticks=4, forbid_when_blocked=False), [R] * 6)
    assert got == [True, True, True, False, False, False]


def test_an_irreversible_grant_needs_its_own_confirm_and_spends_it():
    policy = Policy(confirm_per_irreversible=True, forbid_when_blocked=False)
    I = R | {"irreversible"}
    ticks = [I, I | {"confirm"}, I, R, R | {"confirm"}, I, I]
    assert drive(policy, ticks) == [False, True, False, True, True, True, False]


def test_a_confirmation_window_is_not_one_shot():
    """The rule above exists because this one authorises many grants per confirm."""
    policy = Policy(confirm_window_ticks=4, forbid_when_blocked=False)
    assert drive(policy, [R | {"confirm"}, R, R]) == [True, True, True]


def test_three_consecutive_failures_trip_the_breaker_and_a_confirm_resets_it():
    policy = Policy(trip_after_failures=3, forbid_when_blocked=False)
    F = R | {"failed"}
    ticks = [F, F, F, R, R | {"confirm"}, R]
    assert drive(policy, ticks) == [True, True, False, False, False, True]


def test_a_success_between_failures_resets_the_count():
    policy = Policy(trip_after_failures=3, forbid_when_blocked=False)
    F = R | {"failed"}
    assert drive(policy, [F, F, R, F, F, R]) == [True] * 6


def test_two_keys_are_both_needed_and_both_spent():
    policy = Policy(two_key=True, forbid_when_blocked=False)
    ticks = [R, R | {"confirm"}, R | {"confirm_b"}, R, R | {"confirm", "confirm_b"}, R]
    assert drive(policy, ticks) == [False, False, True, False, True, False]


def test_the_optional_inputs_come_in_a_fixed_order_after_the_base_four():
    from c3s.policy import INPUT_NAMES, OPTIONAL_INPUTS

    everything = Policy(confirm_per_irreversible=True, trip_after_failures=1, heartbeat_ticks=1, two_key=True)
    assert everything.input_names() == INPUT_NAMES + OPTIONAL_INPUTS
    assert Policy(two_key=True, heartbeat_ticks=2).input_names() == INPUT_NAMES + ("heartbeat", "confirm_b")
    assert Policy().input_names() == INPUT_NAMES


def test_the_original_rules_compile_to_the_same_bytes_as_before_the_new_ones_existed():
    """Adding inputs only when a rule reads them keeps every earlier policy's domain
    and netlist unchanged: the published 81 NAND + 6 LATCH figure still holds."""
    import hashlib

    from c3s.netlist import to_bytes

    circuit = Policy(min_gap_ticks=8, commit_ticks=4).build()
    assert hashlib.sha256(to_bytes(circuit)).hexdigest().startswith("f2afa9e0bc1ac937")
    assert (circuit.metrics()["nand"], circuit.metrics()["latch"]) == (81, 6)


def test_reasons_are_the_policy_speaking_about_its_own_counters():
    policy = Policy(trip_after_failures=3, two_key=True, forbid_when_blocked=False)
    inp = {"request": 1, "intent": 0, "blocked": 0, "confirm": 0, "failed": 1, "confirm_b": 0}
    tripped = policy.pack({"tripped": 1})
    assert policy.reasons(inp, tripped) == ["breaker tripped until a confirm resets it", "two keys needed, missing confirm and confirm_b"]
    two_fails = policy.pack({"fails": 2, "key_a": 1})
    assert policy.reasons(inp, two_fails) == ["breaker: 3 consecutive failures", "two keys needed, missing confirm_b"]
    assert policy.reasons({**inp, "failed": 0, "confirm_b": 1}, policy.pack({"key_a": 1})) == []
