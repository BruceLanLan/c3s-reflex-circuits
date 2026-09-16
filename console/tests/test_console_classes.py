from reflex_token import json_headers as _json_headers
"""One agent, one circuit per class, and a shared halt — against the LIVE console.

Skips when the console at REFLEX_CONSOLE (default http://127.0.0.1:8765) is down.
Installing a policy resets every agent's state for that class; the module fixture puts
back whatever was installed before for the classes it touches.
"""

import json
import os
import urllib.error
import urllib.request
import uuid

import pytest

CONSOLE = os.environ.get("REFLEX_CONSOLE", "http://127.0.0.1:8765").rstrip("/")
TIMEOUT = 20


def api(path: str, payload=None) -> dict:
    req = urllib.request.Request(f"{CONSOLE}{path}", data=None if payload is None else json.dumps(payload).encode(),
                                 headers=_json_headers())
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read())


def api_error(path: str, payload: dict) -> dict:
    try:
        api(path, payload)
    except urllib.error.HTTPError as e:
        return json.loads(e.read())
    raise AssertionError("expected an error")


def request(agent: str, cls: str) -> dict:
    return api("/api/request", {"agent": agent, "intent": 1, "reason": f"test {cls}", "class": cls})


def arm(agent: str, **bits) -> dict:
    return api("/api/tool", {"agent": agent, **bits})


@pytest.fixture(scope="module", autouse=True)
def restore():
    try:
        state = api("/api/state")
    except (urllib.error.URLError, OSError) as e:
        pytest.skip(f"console not running at {CONSOLE}: {e}")
    touched = ("spend", "exec", "halt", "message")
    before = {c: state["policies"].get(c) for c in touched}
    # a permissive exec so the exec side of every scenario is a clean grant
    api("/api/policy", {"class": "exec", "min_gap_ticks": 0, "commit_ticks": 0, "forbid_when_blocked": True})
    yield
    for cls, summary in before.items():
        if summary is None:
            if cls != "exec":
                api("/api/policy", {"class": cls, "remove": True})
        elif summary.get("deny_all"):
            api("/api/policy", {"class": cls, "deny_all": True})
        else:
            api("/api/policy", dict(summary["settings"], **{"class": cls}))


def agent_name() -> str:
    return f"classes-test:{uuid.uuid4().hex[:8]}"


def test_state_lists_classes_and_keeps_the_old_shape():
    s = api("/api/state")
    assert s["classes"] == ["spend", "message", "exec", "files"]
    assert s["halt_class"] == "halt" and s["default_class"] == "exec"
    assert set(s["policies"]) == {"spend", "message", "exec", "files", "halt"}
    # the old page reads these
    assert s["policy"]["circuit"]["nand"] > 0 and "settings" in s["policy"]
    assert s["policy"] == dict(s["policies"]["exec"])


def test_unknown_class_is_refused_by_the_api():
    assert "class must be one of" in api_error("/api/request", {"agent": agent_name(), "class": "wallet"})["error"]
    assert "no such class" in api_error("/api/policy", {"class": "wallet", "min_gap_ticks": 1})["error"] or \
        "class must be one of" in api_error("/api/policy", {"class": "wallet", "min_gap_ticks": 1})["error"]


def test_exec_cannot_be_left_without_a_circuit():
    assert "always has a circuit" in api_error("/api/policy", {"class": "exec", "remove": True})["error"]


def test_a_spend_circuit_refuses_while_exec_grants():
    api("/api/policy", {"class": "spend", "max_grants": 1, "min_gap_ticks": 0, "forbid_when_blocked": True})
    a = agent_name()
    first = request(a, "spend")
    assert first["granted"] and first["class"] == "spend" and first["class_installed"]
    second = request(a, "spend")
    assert not second["granted"]
    assert second["why"] == ["budget: 1 of 1 grants used"]
    ex = request(a, "exec")
    assert ex["granted"] and ex["class"] == "exec"
    mine = [x for x in api("/api/state")["agents"] if x["agent"] == a][0]
    assert mine["by_class"]["spend"]["ticks"] == 2 and mine["by_class"]["exec"]["ticks"] == 1
    assert mine["ticks"] == 3


def test_max_grants_zero_means_unlimited_so_never_is_deny_all():
    """`max_grants=0` is *unlimited* in c3s (the rule is simply off), so the "never" a
    user means by "it must not move money" is a class denied outright, without a circuit."""
    unlimited = api("/api/policy", {"class": "spend", "max_grants": 0, "forbid_when_blocked": True})
    assert unlimited["rules"] == ["nothing is granted while blocked is high"]  # no budget rule at all
    a = agent_name()
    assert all(request(a, "spend")["granted"] for _ in range(3))

    denied = api("/api/policy", {"class": "spend", "deny_all": True})
    assert denied["deny_all"] is True and denied["rules"] == ["nothing is ever granted"]
    b = agent_name()
    r = request(b, "spend")
    assert not r["granted"] and r["deny_all"] is True
    assert r["why"] == ["class denied outright: no circuit grants here"]
    assert request(b, "exec")["granted"]  # other classes untouched
    assert api("/api/state")["policies"]["spend"]["deny_all"] is True


def test_uninstalled_class_is_not_gated():
    api("/api/policy", {"class": "message", "remove": True})
    a = agent_name()
    r = request(a, "message")
    assert r["granted"] and r["class_installed"] is False and r["why"] == []
    assert api("/api/state")["policies"]["message"] is None


def test_shared_halt_blocks_every_class_until_a_confirm():
    # The halt circuit is persisted with the rest; a run that dies half-way must not leave
    # it installed on the developer's console (it once did, and every later resume-all
    # test on this machine failed until someone looked).
    try:
        _shared_halt_scenario()
    finally:
        api("/api/policy", {"class": "halt", "remove": True})


def _shared_halt_scenario():
    api("/api/policy", {"class": "halt", "sticky_block": True, "forbid_when_blocked": True})
    api("/api/policy", {"class": "spend", "min_gap_ticks": 0, "forbid_when_blocked": True})
    a = agent_name()
    ok = request(a, "spend")
    assert ok["granted"] and ok["halt"] == {"granted": True, "why": [], "inputs": ok["halt"]["inputs"]}

    arm(a, blocked=1)
    r = request(a, "exec")
    assert not r["granted"]
    assert r["why"][0].startswith("halted (shared halt circuit): ")
    assert r["halt"]["granted"] is False
    assert r["inputs"]["blocked"] == 1

    arm(a, blocked=0)
    r = request(a, "spend")  # any class; the halt is sticky
    assert not r["granted"] and r["why"][0].startswith("halted (shared halt circuit): ")
    assert "halted until a confirm lifts it" in r["why"][0]
    assert r["inputs"]["blocked"] == 1, "the class circuit must see blocked high while halted"

    # an uninstalled class is not gated, but the halt still applies to it
    api("/api/policy", {"class": "message", "remove": True})
    r = request(a, "message")
    assert not r["granted"] and r["class_installed"] is False and r["why"][0].startswith("halted")

    arm(a, confirm=1)
    lifting = request(a, "exec")
    assert not lifting["granted"]  # the tick whose confirm lifts the halt grants nothing
    after = request(a, "exec")
    assert after["granted"] and after["halt"]["granted"] is True

    if after.get("chain") and "agrees" in after["chain"]:
        assert after["chain"]["agrees"] is True
        assert set(after["chain"]["circuits"]) == {"halt", "exec"}

    api("/api/policy", {"class": "halt", "remove": True})
    r = request(a, "exec")
    assert r["granted"] and r["halt"] is None
