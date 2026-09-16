"""The Telegram bot against the live console, with Telegram itself replaced by a list:
an agent's chat can ask but not confirm, a person's chat can see what waits, confirm it,
stop everyone, and is told once when something new starts waiting."""

import os
import sys
import urllib.error
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("TOOL_LAYER_CHATS", "person-chat")

import bot_telegram as bot  # noqa: E402


@pytest.fixture(autouse=True)
def console_up():
    try:
        before = bot.call("/api/state")["policies"].get("files")
    except (urllib.error.URLError, OSError) as e:
        pytest.skip(f"console not running: {e}")
    bot.call("/api/policy", {"class": "files", "confirm_per_irreversible": True, "forbid_when_blocked": True})
    yield
    if before is None:
        bot.call("/api/policy", {"class": "files", "remove": True})
    elif before.get("deny_all"):
        bot.call("/api/policy", {"class": "files", "deny_all": True})
    else:
        bot.call("/api/policy", dict(before["settings"], **{"class": "files"}))


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(bot, "say", lambda chat, text: out.append((str(chat), text)))
    monkeypatch.setattr(bot, "TOOL_LAYER_CHATS", {"person-chat"})
    return out


def waiting_agent() -> str:
    name = f"tg-test:{uuid.uuid4().hex[:8]}"
    bot.call("/api/tool", {"agent": name, "irreversible": 1})
    v = bot.call("/api/request", {"agent": name, "intent": 1, "reason": "rm", "class": "files"})
    assert not v["granted"] and v["why"][0].startswith("irreversible")
    return name


def test_an_agent_chat_can_ask_but_not_confirm_or_stop(sent):
    bot.handle("agent-chat", "/ask files tidy up")
    assert "files" in sent[-1][1]
    for cmd in ("/confirm", "/pending", "/stop", "/block someone"):
        bot.handle("agent-chat", cmd)
        assert "agent's channel" in sent[-1][1], cmd


def test_a_person_sees_what_waits_and_confirms_it(sent):
    name = waiting_agent()
    bot.handle("person-chat", "/pending")
    assert name in sent[-1][1] and "irreversible" in sent[-1][1] and "call: rm" in sent[-1][1]
    bot.handle("person-chat", f"/confirm {name}")
    assert "confirm written" in sent[-1][1] and "for this call only" in sent[-1][1]
    bot.call("/api/tool", {"agent": name, "irreversible": 1})
    assert bot.call("/api/request", {"agent": name, "intent": 1, "reason": "rm", "class": "files"})["granted"]


def test_stop_blocks_everyone_and_resume_lifts_it(sent):
    name = waiting_agent()
    bot.handle("person-chat", "/stop")
    assert sent[-1][1].startswith("blocked ")
    assert not bot.call("/api/request", {"agent": name, "intent": 1, "reason": "ls", "class": "files"})["granted"]
    bot.handle("person-chat", "/resume")
    assert sent[-1][1].startswith("unblocked ")
    assert bot.call("/api/request", {"agent": name, "intent": 1, "reason": "ls", "class": "files"})["granted"]


def test_new_waiting_items_are_announced_once():
    seen: set = set()
    bot.new_waiting(bot.call("/api/state"), seen)  # what was already there
    name = waiting_agent()
    fresh = bot.new_waiting(bot.call("/api/state"), seen)
    assert name in [it["agent"] for it in fresh]  # other agents may be waiting on a shared console
    assert name not in [it["agent"] for it in bot.new_waiting(bot.call("/api/state"), seen)]
