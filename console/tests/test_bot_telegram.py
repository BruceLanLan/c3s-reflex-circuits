"""The Telegram bot against the live console, with Telegram itself replaced by a list:
an agent's chat can ask but not confirm, a person's chat can see what waits, confirm it,
stop everyone, and is told once when something new starts waiting.

The button tests are the ones that matter: a press has to be as narrow as a typed confirm,
so the discriminating case is a button for a call that has been superseded — pressing it
must refuse and leave the new call unapproved."""

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


class Recorder(list):
    """What was said, plus what each press was answered with and whose buttons were cleared."""

    def __init__(self):
        super().__init__()
        self.answers: list = []
        self.stripped: list = []


@pytest.fixture
def sent(monkeypatch):
    """Telegram, replaced by lists. No network here, exactly as `say` was faked before."""
    out = Recorder()
    monkeypatch.setattr(bot, "say", lambda chat, text, buttons=None: out.append((str(chat), text, buttons)))
    monkeypatch.setattr(bot, "answer", lambda cb, text, alert=False: out.answers.append((cb, text, alert)))
    monkeypatch.setattr(bot, "strip_keyboard", lambda chat, mid: out.stripped.append((str(chat), mid)))
    monkeypatch.setattr(bot, "TOOL_LAYER_CHATS", {"person-chat"})
    return out


def waiting_agent(reason: str = "rm") -> str:
    name = f"tg-test:{uuid.uuid4().hex[:8]}"
    return refuse_once(name, reason)


def refuse_once(name: str, reason: str) -> str:
    """One more refused irreversible call for this agent, which becomes its pending entry."""
    bot.call("/api/tool", {"agent": name, "irreversible": 1})
    v = bot.call("/api/request", {"agent": name, "intent": 1, "reason": reason, "class": "files"})
    assert not v["granted"] and v["why"][0].startswith("irreversible")
    return name


def item_for(name: str) -> dict:
    waiting = [it for it in bot.pending_items(bot.call("/api/state")) if it["agent"] == name]
    assert waiting, f"{name} is not waiting for a person"
    return waiting[0]


def press_data(out, name: str, which: str = "approve") -> str:
    """The callback_data behind a button the bot actually sent for *this* agent's item.

    Keyed on the agent because the console is shared: other agents are usually waiting too,
    and a test that pressed whichever button came last would prove nothing about binding."""
    buttons = next(b for _, text, b in reversed(out) if b and name in text)
    flat = [btn for row in buttons for btn in row]
    return next(b["callback_data"] for b in flat if which in b["text"])


def test_an_agent_chat_can_ask_but_not_confirm_or_stop(sent):
    bot.handle("agent-chat", "/ask files tidy up")
    assert "files" in sent[-1][1]
    for cmd in ("/confirm", "/pending", "/stop", "/block someone"):
        bot.handle("agent-chat", cmd)
        assert "agent's channel" in sent[-1][1], cmd


def test_a_person_sees_what_waits_and_confirms_it(sent):
    name = waiting_agent()
    bot.handle("person-chat", "/pending")
    # One message per waiting item now, each with its own buttons; other agents on this
    # shared console are listed too, so find the one this test is about.
    mine = next(text for _, text, _ in sent if name in text)
    assert "irreversible" in mine and "call: rm" in mine and f"code {item_for(name)['code']}" in mine
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


def test_a_press_approves_the_one_call_it_was_shown_against(sent):
    name = waiting_agent("rm one.txt")
    bot.handle("person-chat", "/pending")
    data = press_data(sent, name)
    assert bot.CB in data and item_for(name)["code"] in data

    bot.handle_press("person-chat", data, "cb-1", 77)
    assert "confirm written" in sent[-1][1] and "rm one.txt" in sent[-1][1]
    assert sent.answers[-1][0] == "cb-1"
    bot.call("/api/tool", {"agent": name, "irreversible": 1})
    assert bot.call("/api/request", {"agent": name, "intent": 1, "reason": "rm one.txt", "class": "files"})["granted"]


def test_a_button_for_a_superseded_call_approves_nothing(sent):
    """The whole point: a person approves the call they were shown, not what waits now."""
    name = waiting_agent("rm one.txt")
    bot.handle("person-chat", "/pending")
    old = press_data(sent, name)

    refuse_once(name, "rm -rf /")  # one entry per (agent, class): this replaces the old one
    assert item_for(name)["reason"] == "rm -rf /"

    bot.handle_press("person-chat", old, "cb-stale", 77)
    assert "not waiting for a person any more" in sent[-1][1]
    assert not item_for(name)["armed"]
    bot.call("/api/tool", {"agent": name, "irreversible": 1})
    assert not bot.call("/api/request", {"agent": name, "intent": 1, "reason": "rm -rf /", "class": "files"})["granted"]

    refuse_once(name, "rm -rf /")
    bot.handle("person-chat", "/pending")
    bot.handle_press("person-chat", press_data(sent, name), "cb-new", 78)
    assert "confirm written" in sent[-1][1] and "rm -rf /" in sent[-1][1]
    bot.call("/api/tool", {"agent": name, "irreversible": 1})
    assert bot.call("/api/request", {"agent": name, "intent": 1, "reason": "rm -rf /", "class": "files"})["granted"]


def test_a_press_with_the_wrong_two_digits_writes_nothing(sent):
    name = waiting_agent("rm two.txt")
    digest = bot.call_digest(name, "rm two.txt", "confirm")
    wrong = "99" if item_for(name)["code"] != "99" else "11"
    bot.handle_press("person-chat", f"{bot.CB}|confirm|{wrong}|{digest}", "cb-code", 1)
    assert "reissued the code" in sent[-1][1]
    assert not item_for(name)["armed"]


def test_pressing_twice_does_not_write_a_second_confirm(sent):
    name = waiting_agent("rm three.txt")
    bot.handle("person-chat", "/pending")
    data = press_data(sent, name)
    bot.handle_press("person-chat", data, "cb-a", 1)
    assert "confirm written" in sent[-1][1]
    bot.handle_press("person-chat", data, "cb-b", 1)
    assert "already waiting to be spent" in sent[-1][1]
    assert "already approved" in sent.answers[-1][1]


def test_an_unknown_digest_is_stale_and_not_an_approval(sent):
    waiting_agent("rm four.txt")
    bot.handle_press("person-chat", f"{bot.CB}|confirm|47|{'0' * 12}", "cb-unknown", 1)
    assert "not waiting for a person any more" in sent[-1][1]


def test_buttons_are_refused_in_an_agents_chat(sent):
    name = waiting_agent("rm five.txt")
    bot.handle("person-chat", "/pending")
    data = press_data(sent, name)
    bot.handle_press("agent-chat", data, "cb-agent", 1)
    assert "agent's channel" in sent[-1][1]
    assert sent.answers[-1][2] is True  # an alert, not a quiet tick
    assert not item_for(name)["armed"]


def test_ignore_writes_nothing_and_block_and_stop_need_no_code(sent):
    name = waiting_agent("rm six.txt")
    bot.handle("person-chat", "/pending")
    bot.handle_press("person-chat", press_data(sent, name, "ignore"), "cb-i", 1)
    assert "nothing was written" in sent.answers[-1][1]
    assert not item_for(name)["armed"]

    bot.handle("person-chat", "/pending")
    bot.handle_press("person-chat", press_data(sent, name, "block"), "cb-blk", 1)
    assert f"{name} blocked" in sent[-1][1]
    agent = next(a for a in bot.call("/api/state")["agents"] if a["agent"] == name)
    assert agent["armed"]["blocked"]
    bot.call("/api/tool", {"agent": name, "blocked": 0})


def test_new_waiting_items_are_announced_once():
    seen: set = set()
    bot.new_waiting(bot.call("/api/state"), seen)  # what was already there
    name = waiting_agent()
    fresh = bot.new_waiting(bot.call("/api/state"), seen)
    assert name in [it["agent"] for it in fresh]  # other agents may be waiting on a shared console
    assert name not in [it["agent"] for it in bot.new_waiting(bot.call("/api/state"), seen)]
