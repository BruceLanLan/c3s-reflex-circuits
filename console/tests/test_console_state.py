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
