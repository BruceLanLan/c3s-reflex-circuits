"""The WeChat channel against the live console, with the messenger replaced by a queue.

No account exists, so the transport under test is the simulated one; the WeCom transport is
only checked for the two things that can be checked without an account — that it refuses to
exist without a credential and never asks for one, and that it admits it cannot be polled.

The discriminating test is the same one as for Telegram's buttons: the two digits are the
binding, so digits from a call that has since been superseded must approve nothing.
"""

import os
import sys
import urllib.error
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters import wechat_relay as wr  # noqa: E402

PERSON = "person-wx"
AGENT = "agent-wx"


@pytest.fixture(autouse=True)
def console_up():
    try:
        before = wr.call("/api/state")["policies"].get("files")
    except (urllib.error.URLError, OSError) as e:
        pytest.skip(f"console not running: {e}")
    wr.call("/api/policy", {"class": "files", "confirm_per_irreversible": True, "forbid_when_blocked": True})
    yield
    if before is None:
        wr.call("/api/policy", {"class": "files", "remove": True})
    elif before.get("deny_all"):
        wr.call("/api/policy", {"class": "files", "deny_all": True})
    else:
        wr.call("/api/policy", dict(before["settings"], **{"class": "files"}))


@pytest.fixture
def relay():
    return wr.Relay(wr.SimulatedTransport(), {PERSON})


def refuse_once(name: str, reason: str) -> str:
    wr.call("/api/tool", {"agent": name, "irreversible": 1})
    v = wr.call("/api/request", {"agent": name, "intent": 1, "reason": reason, "class": "files"})
    assert not v["granted"] and v["why"][0].startswith("irreversible")
    return name


def waiting_agent(reason: str = "rm") -> str:
    return refuse_once(f"wx-test:{uuid.uuid4().hex[:8]}", reason)


def item_for(name: str) -> dict:
    waiting = [it for it in wr.pending_items(wr.call("/api/state")) if it["agent"] == name]
    assert waiting, f"{name} is not waiting for a person"
    return waiting[0]


def test_the_person_is_shown_the_call_and_its_digits(relay):
    name = waiting_agent("rm notes.txt")
    fresh = relay.announce()
    assert name in [it["agent"] for it in fresh]
    card = next(t for t in relay.transport.shown(PERSON) if name in t)
    assert "rm notes.txt" in card and "反射弧在等人" in card
    assert f"确认 {item_for(name)['code']}" in card
    # Announced once, exactly as the Telegram bot does it.
    assert name not in [it["agent"] for it in relay.announce()]


def test_typing_the_digits_confirms_that_one_call(relay):
    name = waiting_agent("rm notes.txt")
    code = item_for(name)["code"]
    said = relay.on_message(PERSON, f"确认 {code}")
    assert name in said and "只对这一次调用有效" in said
    wr.call("/api/tool", {"agent": name, "irreversible": 1})
    assert wr.call("/api/request", {"agent": name, "intent": 1, "reason": "rm notes.txt", "class": "files"})["granted"]


def test_digits_from_a_superseded_call_approve_nothing(relay):
    """The binding, tested the only way that counts: the call changes under the digits."""
    name = waiting_agent("rm one.txt")
    old = item_for(name)["code"]
    refuse_once(name, "rm -rf /")  # one entry per (agent, class), so this replaces it
    assert item_for(name)["reason"] == "rm -rf /"

    if item_for(name)["code"] != old:  # the usual case: the digits were reissued
        said = relay._decide(PERSON, f"确认 {old}")
        assert "什么都没有写" in said
        assert not item_for(name)["armed"]
        wr.call("/api/tool", {"agent": name, "irreversible": 1})
        assert not wr.call("/api/request",
                           {"agent": name, "intent": 1, "reason": "rm -rf /", "class": "files"})["granted"]

    # And the digits that are live now approve the call they are shown against, not the old one.
    live = item_for(name)
    relay.on_message(PERSON, f"确认 {live['code']}")
    wr.call("/api/tool", {"agent": name, "irreversible": 1})
    assert wr.call("/api/request", {"agent": name, "intent": 1, "reason": "rm -rf /", "class": "files"})["granted"]


def test_an_agents_id_cannot_confirm_block_or_stop(relay):
    name = waiting_agent("rm two.txt")
    code = item_for(name)["code"]
    for text in (f"确认 {code}", f"拦截 {code}", "全停", "待办"):
        said = relay._decide(AGENT, text)
        assert "WECHAT_TOOL_LAYER_USERS" in said, text
    assert not item_for(name)["armed"]
    assert not next(a for a in wr.call("/api/state")["agents"] if a["agent"] == name)["armed"].get("blocked")


def test_unknown_digits_and_missing_digits_write_nothing(relay):
    name = waiting_agent("rm three.txt")
    live = {it.get("code") for it in relay.waiting()}
    unknown = next(f"{n:02d}" for n in range(100) if f"{n:02d}" not in live)
    assert "什么都没有写" in relay._decide(PERSON, f"确认 {unknown}")
    assert "什么都没有写" in relay._decide(PERSON, "确认")
    assert not item_for(name)["armed"]


def test_confirming_twice_does_not_write_a_second_one(relay):
    name = waiting_agent("rm four.txt")
    code = item_for(name)["code"]
    assert "只对这一次调用有效" in relay._decide(PERSON, f"确认 {code}")
    assert "没有再写一个" in relay._decide(PERSON, f"确认 {code}")


def test_asking_for_the_second_key_when_one_key_is_waiting_is_refused(relay):
    name = waiting_agent("rm five.txt")
    said = relay._decide(PERSON, f"二次确认 {item_for(name)['code']}")
    assert "不是 confirm_b" in said and "什么都没有写" in said
    assert not item_for(name)["armed"]


def test_block_needs_the_digits_but_not_the_matching_bit(relay):
    name = waiting_agent("rm six.txt")
    said = relay._decide(PERSON, f"拦截 {item_for(name)['code']}")
    assert name in said
    assert next(a for a in wr.call("/api/state")["agents"] if a["agent"] == name)["armed"]["blocked"]
    wr.call("/api/tool", {"agent": name, "blocked": 0})


def test_stop_needs_no_digits(relay):
    name = waiting_agent("rm seven.txt")
    said = relay._decide(PERSON, "全停")
    assert "已拦住" in said
    assert not wr.call("/api/request", {"agent": name, "intent": 1, "reason": "ls", "class": "files"})["granted"]
    wr.call("/api/resume-all", {"source": "test"})


def test_the_simulated_transport_is_a_queue_in_both_directions():
    t = wr.SimulatedTransport()
    relay = wr.Relay(t, {PERSON})
    t.as_person(PERSON, "帮助")
    delivered = t.poll(timeout=0.1)
    assert delivered == [(PERSON, "帮助")]
    relay.on_message(*delivered[0])
    assert "确认 <两位数>" in t.shown(PERSON)[-1]
    assert t.poll(timeout=0.01) == []  # nothing arrives on its own


def test_the_real_transport_refuses_to_exist_without_a_credential_and_never_asks(monkeypatch):
    for var in ("WECOM_CORP_ID", "WECOM_AGENT_ID", "WECOM_SECRET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("a transport must never prompt for a credential"))
    with pytest.raises(RuntimeError) as e:
        wr.WeComTransport()
    assert "never been run" in str(e.value) and "WECOM_CORP_ID" in str(e.value)
    assert wr.WeComTransport.UNTESTED is True


def test_the_real_transport_admits_it_cannot_be_polled(monkeypatch):
    monkeypatch.setenv("WECOM_CORP_ID", "x")
    monkeypatch.setenv("WECOM_AGENT_ID", "1")
    monkeypatch.setenv("WECOM_SECRET", "y")
    t = wr.WeComTransport()  # constructing it talks to nothing
    with pytest.raises(NotImplementedError) as e:
        t.poll()
    assert "callback" in str(e.value) and "on_message" in str(e.value)


def test_the_cli_refuses_the_real_transport_rather_than_asking_for_an_account(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("the CLI must not prompt"))
    with pytest.raises(SystemExit):
        wr.main([])  # no --sim


def test_the_operator_token_is_never_printed_or_sent_anywhere_else(capsys, relay):
    """The token goes in one header, to this console, and nowhere into the person's view."""
    name = waiting_agent("rm eight.txt")
    relay.announce()
    relay.on_message(PERSON, f"确认 {item_for(name)['code']}")
    everything = "\n".join(relay.transport.shown()) + capsys.readouterr().out
    assert wr.OPERATOR_TOKEN and wr.OPERATOR_TOKEN not in everything
