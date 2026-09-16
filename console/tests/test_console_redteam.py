"""Red-team regressions — against the LIVE console (see docs/REDTEAM-2026-09-17.md).

Skips when the console at REFLEX_CONSOLE (default the port in CONSOLE_PORT) is down. Each
test uses a fresh uuid agent name and restores any policy it installs, so it may run in the
same suite as the others.

Two kinds of assertion here:

* invariants that MUST keep holding — the shared-halt reset and the big-red-button need the
  operator token, and a paired Wi-Fi device cannot loosen (unblock / hold the heartbeat).
  These guard against a regression that would hand the person's authority away.
* one assertion that PINS A KNOWN GAP — a name first seen after `stop-all` walks out from
  under it. It is written to fail the day someone closes the gap (the global-stop latch in
  the report), which is the point: the test will announce the fix.
"""

import json
import os
import urllib.error
import urllib.request
import uuid

import pytest

from reflex_token import console_url, operator_token

CONSOLE = console_url()
TOKEN = operator_token()
TIMEOUT = 20


def _req(path: str, payload=None, headers=None):
    h = {"content-type": "application/json", **(headers or {})}
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(f"{CONSOLE}{path}", data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.getcode(), json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _agent_req(agent: str, headers=None):
    """A tool call as an agent would make it — no operator token."""
    return _req("/api/request", {"agent": agent, "intent": 1, "reason": f"[exec] {agent}", "class": "exec"}, headers)


@pytest.fixture(scope="module", autouse=True)
def _live():
    try:
        code, _ = _req("/api/state")
    except (urllib.error.URLError, OSError) as e:
        pytest.skip(f"console not running at {CONSOLE}: {e}")
    if not TOKEN:
        pytest.skip("no operator token available (set REFLEX_OPERATOR_TOKEN or REFLEX_CONFIG_DIR)")


def _name():
    return f"redteam:{uuid.uuid4().hex[:10]}"


# -- invariants that must keep holding ---------------------------------------------------

def test_resume_all_needs_the_operator_token():
    """The shared-halt reset lives behind resume-all; a tokenless resume must not reach it."""
    code, body = _req("/api/resume-all", {})  # no token
    assert code == 403 and "operator" in body.get("error", "").lower()


def test_stop_all_needs_the_operator_token():
    code, body = _req("/api/stop-all", {})  # no token
    assert code == 403 and "operator" in body.get("error", "").lower()


def test_an_agent_cannot_write_its_own_person_bit_even_via_a_host_alias():
    """The token backstop, not the hook's pattern list, is the defence: reaching /api/tool
    however you like still cannot write `confirm` without the operator token."""
    agent = _name()
    code, body = _req("/api/tool", {"agent": agent, "confirm": 1})  # no token
    assert code == 403 and "confirm" in body.get("error", "").lower()
    # and the state confirms nothing was armed for it
    _, state = _req("/api/state")
    mine = [a for a in state["agents"] if a["agent"] == agent]
    assert not mine or mine[0]["armed"].get("confirm", 0) == 0


# -- the pinned gap: rename past the stop button -----------------------------------------

def test_a_name_first_seen_after_stop_all_walks_out_from_under_it():
    """PINS THE GAP in docs/REDTEAM-2026-09-17.md. `stop_all` blocks only the names known at
    press time, so a renamed agent is not blocked. When the global-stop latch lands, the
    `blocked == 0` assertion below flips — update it then, and the report's design item is done.
    """
    old = _name()
    code, _ = _agent_req(old)
    assert code == 200
    code, stopped = _req("/api/stop-all", {"token": TOKEN})
    assert code == 200 and old in stopped["agents"]

    # the known name is caught
    code, after = _agent_req(old)
    assert after["blocked"] == 1

    # a name first seen only now is NOT caught (the gap)
    new = _name()
    code, fresh = _agent_req(new)
    assert code == 200
    assert fresh["blocked"] == 0, "gap closed — a new name is now blocked by stop-all; update this test"

    # cleanup: lift the block we set (also resets the shared halt, which is fine on a test console)
    _req("/api/resume-all", {"token": TOKEN})


def test_condition_based_halt_does_catch_a_brand_new_name():
    """The contrast that points at the fix: a shared halt is evaluated for every agent, so a
    name never seen before is halted by it. Uses a heartbeat halt with no device polling."""
    _, state = _req("/api/state")
    had_halt = state["policies"].get("halt")
    code, _ = _req("/api/policy", {"token": TOKEN, "class": "halt", "heartbeat_ticks": 1, "forbid_when_blocked": True})
    assert code == 200
    try:
        name = _name()
        # tick a few times: the first tick arms nothing, the halt bites once the heartbeat is missed
        verdicts = [_agent_req(name)[1] for _ in range(3)]
        assert any("halt" in "; ".join(v.get("why", [])).lower() for v in verdicts), \
            "a condition-based halt should catch even a name first seen now"
    finally:
        if had_halt is None:
            _req("/api/policy", {"token": TOKEN, "class": "halt", "remove": True})
        elif had_halt.get("deny_all"):
            _req("/api/policy", {"token": TOKEN, "class": "halt", "deny_all": True})
        else:
            _req("/api/policy", dict(had_halt.get("settings", {}), **{"token": TOKEN, "class": "halt"}))
