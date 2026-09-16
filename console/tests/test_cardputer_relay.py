"""The Cardputer relay without a device: the frame it sends, the keys it accepts, and
the heartbeat that lets a halt policy stop every agent when the device goes away.

These drive console.Boundary in-process (no server, no serial port), so they run
whether or not the console or the device is up."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

console = pytest.importorskip("console")
from c3s.policy import Policy  # noqa: E402  (console put the circuits repo on the path)
from cardputer_relay import Relay, frame  # noqa: E402


@pytest.fixture
def boundary():
    return console.Boundary(Policy(forbid_when_blocked=True))


def relay_for(boundary):
    r = Relay(boundary, "none", log=lambda m: None)  # never started: no port is opened
    return r


def test_frame_carries_the_blocked_count_and_closes_with_the_item_count(boundary):
    boundary.request("a", 1, "x")
    boundary.arm("b", {"blocked": 1})
    lines, shown = frame(boundary.status())
    s = lines[0].split("|")
    assert s[0] == "S" and len(s) == 6 and s[5] == "1"
    assert lines[-1] == "E|0" and shown == []


def test_stop_all_blocks_every_known_agent_and_resume_all_lifts_it(boundary):
    for name in ("a", "b", "c"):
        boundary.request(name, 1, "x")
    r = relay_for(boundary)
    r._key("K|stop_all|*")
    assert all(a["armed"]["blocked"] == 1 for a in boundary.status()["agents"])
    assert all(not boundary.request(n, 1, "x")["granted"] for n in ("a", "b", "c"))
    r.last_key_at = 0  # a second press, not a bounce
    r._key("K|resume_all|*")
    assert all(a["armed"]["blocked"] == 0 for a in boundary.status()["agents"])
    tool_rows = [e for e in console.TRANSCRIPTS if e["kind"] == "tool" and e.get("source") == "cardputer"]
    assert len(tool_rows) >= 6


def test_a_confirm_for_an_agent_not_on_the_screen_is_ignored(boundary):
    boundary.install("files", Policy(confirm_per_irreversible=True, forbid_when_blocked=True))
    boundary.arm("shown", {"irreversible": 1})
    boundary.request("shown", 1, "rm", "files")
    boundary.request("hidden", 1, "ls", "files")
    r = relay_for(boundary)
    _, r.shown = frame(boundary.status())
    assert [it["agent"] for it in r.shown] == ["shown"]
    r._key("K|confirm|hidden")
    r._key("K|confirm|shown")
    armed = {a["agent"]: a["armed"] for a in boundary.status()["agents"]}
    assert armed["shown"]["confirm"] == 1 and armed["hidden"]["confirm"] == 0


def test_a_bounce_within_half_a_second_counts_once(boundary):
    boundary.request("a", 1, "x")
    r = relay_for(boundary)
    before = len(console.TRANSCRIPTS)
    r._key("K|stop_all|*")
    r._key("K|stop_all|*")
    assert len(console.TRANSCRIPTS) - before == 1


def test_the_device_heartbeat_keeps_a_heartbeat_halt_open_and_its_absence_closes_it(boundary):
    boundary.install("halt", Policy(heartbeat_ticks=3, forbid_when_blocked=True))
    alive = {"v": True}
    boundary.heartbeat_source = lambda: alive["v"]
    assert all(boundary.request("a", 1, "x")["granted"] for _ in range(6))
    alive["v"] = False  # unplugged
    verdicts = [boundary.request("a", 1, "x") for _ in range(4)]
    assert [v["granted"] for v in verdicts] == [True, True, False, False]
    assert verdicts[2]["why"][0].startswith("halted (shared halt circuit)")
    alive["v"] = True  # plugged back in: the halt stays until a person confirms
    assert not boundary.request("a", 1, "x")["granted"]
    boundary.arm("a", {"confirm": 1})
    boundary.request("a", 1, "x")  # the lifting tick grants nothing
    assert boundary.request("a", 1, "x")["granted"]


def test_present_needs_a_connection_and_a_fresh_beat(boundary):
    r = relay_for(boundary)
    assert boundary.heartbeat_source == r.present
    assert not r.present()
    r.connected, r.heard_at = True, __import__("time").time()
    assert r.present()
    r.heard_at -= Relay.HEARTBEAT_FRESH_S + 1
    assert not r.present()


def test_a_confirm_is_for_the_call_a_person_saw_not_the_next_one(boundary):
    """The mailbox run found it: confirming 'reply to Lena' must not let a trash through.
    A confirm bound to one reason waits, unspent, while other calls tick past it."""
    for cls in ("message", "files"):
        boundary.install(cls, Policy(confirm_per_irreversible=True, forbid_when_blocked=True))
    reply, trash = "[message] reply_email [irreversible]: id=m1", "[files] trash_email [irreversible]: id=m3"
    boundary.arm("mail", {"irreversible": 1})
    assert not boundary.request("mail", 1, reply, "message")["granted"]
    boundary.arm("mail", {"confirm": 1}, bind_to=reply)
    boundary.arm("mail", {"irreversible": 1})
    first = boundary.request("mail", 1, trash, "files")
    assert not first["granted"] and first["why"][0].startswith("irreversible")
    agent = next(a for a in boundary.status()["agents"] if a["agent"] == "mail")
    assert agent["armed"]["confirm"] == 1 and agent["bound"]["confirm"] == reply  # still waiting for its call
    boundary.arm("mail", {"irreversible": 1})
    assert boundary.request("mail", 1, reply, "message")["granted"]
    agent = next(a for a in boundary.status()["agents"] if a["agent"] == "mail")
    assert agent["armed"]["confirm"] == 0 and agent["bound"] == {}
    boundary.arm("mail", {"irreversible": 1})
    assert not boundary.request("mail", 1, reply, "message")["granted"]  # spent


def test_a_device_confirm_binds_to_the_item_it_had_selected(boundary):
    boundary.install("files", Policy(confirm_per_irreversible=True, forbid_when_blocked=True))
    boundary.install("message", Policy(confirm_per_irreversible=True, forbid_when_blocked=True))
    for reason, cls in (("[files] trash_email [irreversible]: id=m3", "files"), ("[message] reply_email [irreversible]: id=m1", "message")):
        boundary.arm("mail", {"irreversible": 1})
        boundary.request("mail", 1, reason, cls)
    r = relay_for(boundary)
    _, r.shown = frame(boundary.status())
    idx = next(i for i, it in enumerate(r.shown) if it["class"] == "files")
    r._key(f"K|confirm|mail|{idx}")
    agent = next(a for a in boundary.status()["agents"] if a["agent"] == "mail")
    assert agent["bound"]["confirm"] == "[files] trash_email [irreversible]: id=m3"


def test_a_refused_call_says_which_version_a_person_approved(boundary):
    boundary.install("message", Policy(confirm_per_irreversible=True, forbid_when_blocked=True))
    approved = "[message] reply_email [irreversible]: id=m1, body=Hi Lena, 15:00 works."
    boundary.arm("mail", {"confirm": 1}, bind_to=approved)
    boundary.arm("mail", {"irreversible": 1})
    reworded = boundary.request("mail", 1, "[message] reply_email [irreversible]: id=m1, body=Hello Lena, 15:00 is fine.", "message")
    assert not reworded["granted"] and reworded["confirm_waiting_for"] == [approved]
    boundary.arm("mail", {"irreversible": 1})
    exact = boundary.request("mail", 1, approved, "message")
    assert exact["granted"] and exact["confirm_waiting_for"] == []
