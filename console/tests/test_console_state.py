"""Installed rules survive a restart, are recompiled and re-proven rather than trusted
from disk, and a saved-rules file that cannot be read stops the console instead of letting
it start with no rules — a boundary must not reset open."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
console = pytest.importorskip("console")
from c3s.policy import Policy  # noqa: E402


@pytest.fixture(autouse=True)
def clean_log():
    console.TRANSCRIPTS.clear()
    yield
    console.TRANSCRIPTS.clear()


def test_rules_come_back_after_a_restart(tmp_path):
    state = tmp_path / "policies.json"
    first = console.Boundary(console.FLY_DEFAULT, state)
    first.deny_all("spend")
    first.install("files", Policy(confirm_per_irreversible=True, forbid_when_blocked=True))
    first.install("halt", Policy(sticky_block=True, forbid_when_blocked=True))
    first.remove("halt")

    again = console.Boundary(console.FLY_DEFAULT, state)
    assert again.policies["spend"].deny_all
    files = again.policies["files"]
    assert files.policy.confirm_per_irreversible and files.summary["checked"]["every_rule_holds"]
    assert files.netlist == first.policies["files"].netlist  # recompiled to the same bytes
    assert again.policies["halt"] is None and again.policies["message"] is None
    assert not again.request("a", 1, "pay", "spend")["granted"]


def test_no_file_means_the_defaults(tmp_path):
    b = console.Boundary(console.FLY_DEFAULT, tmp_path / "missing.json")
    assert b.policies["exec"].policy == console.FLY_DEFAULT
    assert all(b.policies[c] is None for c in ("spend", "message", "files", "halt"))


@pytest.mark.parametrize("content", ["{not json", '{"classes": {"wallets": {"deny_all": true}}}', '{"nothing": 1}'])
def test_an_unreadable_rules_file_stops_the_console(tmp_path, content):
    state = tmp_path / "policies.json"
    state.write_text(content)
    with pytest.raises(SystemExit) as e:
        console.Boundary(console.FLY_DEFAULT, state)
    assert "refusing to start" in str(e.value)


def test_the_file_is_written_whole(tmp_path):
    state = tmp_path / "policies.json"
    b = console.Boundary(console.FLY_DEFAULT, state)
    b.deny_all("spend")
    data = json.loads(state.read_text())
    assert data["classes"]["spend"] == {"deny_all": True} and "exec" in data["classes"]
    assert not state.with_suffix(".tmp").exists()


def test_a_spent_budget_a_halt_and_a_block_survive_a_restart(tmp_path):
    state = tmp_path / "policies.json"
    b = console.Boundary(console.FLY_DEFAULT, state)
    b.install("spend", Policy(max_grants=2, forbid_when_blocked=True))
    b.install("halt", Policy(sticky_block=True, forbid_when_blocked=True))
    assert [b.request("payer", 1, "pay", "spend")["granted"] for _ in range(3)] == [True, True, False]
    b.arm("haltee", {"blocked": 1})
    b.request("haltee", 1, "x", "exec")  # trips the sticky halt
    b.arm("haltee", {"blocked": 0})
    b.arm("blockee", {"blocked": 1})

    again = console.Boundary(console.FLY_DEFAULT, state)
    refused = again.request("payer", 1, "pay", "spend")
    assert not refused["granted"] and any(w.startswith("budget") for w in refused["why"])
    assert not again.request("haltee", 1, "x", "exec")["granted"]  # still halted, blocked is 0
    blockee = next(a for a in again.status()["agents"] if a["agent"] == "blockee")
    assert blockee["armed"]["blocked"] == 1


def test_a_saved_state_is_not_loaded_into_a_different_circuit(tmp_path):
    state = tmp_path / "policies.json"
    b = console.Boundary(console.FLY_DEFAULT, state)
    b.install("spend", Policy(max_grants=2, forbid_when_blocked=True))
    for _ in range(2):
        b.request("payer", 1, "pay", "spend")
    data = json.loads(state.read_text())
    data["classes"]["spend"]["settings"]["max_grants"] = 3  # the rules changed while it was down
    state.write_text(json.dumps(data))
    again = console.Boundary(console.FLY_DEFAULT, state)
    payer = next(a for a in again.status()["agents"] if a["agent"] == "payer")
    assert payer["by_class"]["spend"]["ticks"] == 0  # a different circuit starts from reset, as an install does


def test_forgetting_old_agents_never_unblocks_one(tmp_path, monkeypatch):
    state = tmp_path / "policies.json"
    b = console.Boundary(console.FLY_DEFAULT, state)
    b.arm("old-blocked", {"blocked": 1})
    b.request("old-free", 1, "x", "exec")
    for name in ("old-blocked", "old-free"):
        b.agents[name]["seen"] = 0  # long ago
    b.request("new", 1, "x", "exec")  # triggers a save
    kept = json.loads(state.read_text())["agents"]
    assert "old-blocked" in kept and "new" in kept and "old-free" not in kept
