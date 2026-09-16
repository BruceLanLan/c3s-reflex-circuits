"""What waits for a person, computed in one place (I-2), and the effect a card leads with
(I-1): the two-digit matching code, the note, the big red button.

These run the console in-process — a `Boundary` for the derivation, and a
ThreadingHTTPServer on an ephemeral port for the endpoints — so they need no console
running, no serial port, no chain node, and never touch the real saved-rules file.
"""

import http.client
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

console = pytest.importorskip("console")
from c3s.policy import Policy  # noqa: E402  (console put the circuits repo on the path)

TOKEN = "pending-test-token"
IRREVERSIBLE = Policy(confirm_per_irreversible=True, forbid_when_blocked=True)
TRASH = "[files] trash_email [irreversible]: id=m3"
REPLY = "[message] reply_email [irreversible]: id=m1"
EFFECT = {"kind": "delete", "target": "inbox/m3", "summary": "Move the seed-phrase mail to Trash",
          "path": "/Users/me/Mail/m3.eml", "reversible": False, "details": {"from": "support@seed-help.example"}}


@pytest.fixture
def boundary():
    console.TRANSCRIPTS.clear()  # the log is module-wide; other in-process tests leave decisions in it
    yield console.Boundary(Policy(forbid_when_blocked=True), None)  # state_file None: nothing is written
    console.TRANSCRIPTS.clear()


@pytest.fixture
def url(boundary, monkeypatch, tmp_path):
    """The console's own handler on an ephemeral port, with the globals it reads set."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), console.Handler)
    port = server.server_address[1]
    monkeypatch.setattr(console, "BOUNDARY", boundary, raising=False)
    monkeypatch.setattr(console, "TOKEN", TOKEN)
    monkeypatch.setattr(console, "PORT", port)  # the Host check is built from this
    monkeypatch.setattr(console, "CHAIN", None)
    monkeypatch.setattr(console, "AGENTS_FILE", tmp_path / "agents.json")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def call(url, path, body=None, token=True, method=None):
    host, port = url.split(":")
    c = http.client.HTTPConnection(host, int(port), timeout=10)
    try:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Reflex-Token"] = TOKEN
        c.request(method or ("GET" if body is None else "POST"), path,
                  body=None if body is None else json.dumps(body), headers=headers)
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")
    finally:
        c.close()


def refused_irreversible(boundary, agent="mail", reason=TRASH, cls="files", effect=None):
    """One refused request of the shape a person has to answer: irreversible, no confirm."""
    boundary.install(cls, IRREVERSIBLE)
    boundary.arm(agent, {"irreversible": 1})
    entry = boundary.request(agent, 1, reason, cls, effect)
    assert not entry["granted"]
    return entry


# -- the list itself -------------------------------------------------------------


def test_a_refusal_a_person_can_lift_names_the_bit_and_carries_two_digits(boundary):
    refused_irreversible(boundary, effect=EFFECT)
    pending = boundary.status()["pending"]
    assert len(pending) == 1
    it = pending[0]
    assert (it["agent"], it["class"], it["bit"], it["waiting_on_time"], it["armed"]) == ("mail", "files", "confirm", False, False)
    assert it["reason"] == TRASH and it["why"][0].startswith("irreversible")
    assert it["code"].isdigit() and len(it["code"]) == 2
    assert it["effect"] == EFFECT  # I-1: stored and handed on verbatim


def test_the_code_is_stable_while_it_waits_and_differs_per_call(boundary):
    refused_irreversible(boundary)
    first = boundary.status()["pending"][0]["code"]
    for _ in range(3):
        assert boundary.status()["pending"][0]["code"] == first  # generated once, not per poll
    refused_irreversible(boundary, reason=REPLY, cls="message")
    codes = {it["reason"]: it["code"] for it in boundary.status()["pending"]}
    assert codes[TRASH] == first
    assert codes[REPLY] != first  # bound to (agent, reason, bit): two calls, two codes


def test_a_code_is_dropped_once_its_call_stops_waiting(boundary):
    refused_irreversible(boundary)
    code = boundary.status()["pending"][0]["code"]
    assert boundary.code_for_call("mail", TRASH, "confirm") == code
    boundary.arm("mail", {"confirm": 1}, bind_to=TRASH)
    boundary.arm("mail", {"irreversible": 1})
    assert boundary.request("mail", 1, TRASH, "files")["granted"]
    assert boundary.status()["pending"] == []
    assert boundary.code_for_call("mail", TRASH, "confirm") is None


def test_a_confirm_bound_to_the_call_shows_as_armed(boundary):
    refused_irreversible(boundary)
    assert boundary.status()["pending"][0]["armed"] is False
    boundary.arm("mail", {"confirm": 1}, bind_to=TRASH)
    it = boundary.status()["pending"][0]
    assert it["armed"] is True and it["code"] == boundary.code_for_call("mail", TRASH, "confirm")


def test_a_confirm_bound_to_another_call_does_not_arm_this_one(boundary):
    refused_irreversible(boundary)
    boundary.arm("mail", {"confirm": 1}, bind_to=REPLY)
    assert boundary.status()["pending"][0]["armed"] is False


def test_only_time_can_fix_a_cooldown_so_it_has_no_code(boundary):
    boundary.install("exec", Policy(min_gap_ticks=8, forbid_when_blocked=True))
    assert boundary.request("slow", 1, "again")["granted"]
    assert not boundary.request("slow", 1, "again")["granted"]
    it = boundary.status()["pending"][0]
    assert it["waiting_on_time"] is True and it["bit"] is None and "code" not in it
    assert any(w.startswith("cooldown") for w in it["why"])


def test_a_blocked_agent_is_not_offered_a_confirm_that_cannot_help(boundary):
    refused_irreversible(boundary)
    boundary.arm("mail", {"blocked": 1})
    boundary.arm("mail", {"irreversible": 1})
    boundary.request("mail", 1, TRASH, "files")
    it = boundary.status()["pending"][0]
    assert it["bit"] is None and it["waiting_on_time"] is True and "code" not in it
    assert console.BLOCKED_SAYS in " ".join(it["why"])


def test_one_entry_per_agent_and_class_and_only_while_it_stands_refused(boundary):
    refused_irreversible(boundary)
    refused_irreversible(boundary, reason=REPLY, cls="message")
    assert {(it["agent"], it["class"]) for it in boundary.status()["pending"]} == {("mail", "files"), ("mail", "message")}
    boundary.arm("mail", {"confirm": 1}, bind_to=REPLY)
    boundary.arm("mail", {"irreversible": 1})
    assert boundary.request("mail", 1, REPLY, "message")["granted"]
    assert [(it["agent"], it["class"]) for it in boundary.status()["pending"]] == [("mail", "files")]


# -- the code, enforced ----------------------------------------------------------


def test_the_right_code_arms_the_bit_and_a_wrong_one_is_refused(url, boundary):
    refused_irreversible(boundary, effect=EFFECT)
    code = call(url, "/api/state")[1]["pending"][0]["code"]
    wrong = str((int(code) - 10 + 1) % 90 + 10)  # the next two digits along: a plausible misread
    assert wrong != code

    status, body = call(url, "/api/tool", {"agent": "mail", "confirm": 1, "for_reason": TRASH, "code": wrong})
    assert status == 403 and "digits" in body["error"]
    assert "token" not in body["error"].lower()  # so the page does not re-ask for the operator token
    assert boundary.status()["agents"][0]["armed"]["confirm"] == 0

    status, body = call(url, "/api/tool", {"agent": "mail", "confirm": 1, "for_reason": TRASH, "code": code})
    assert status == 200 and body["armed"]["confirm"] == 1 and body["bound"]["confirm"] == TRASH


def test_a_code_for_a_call_nothing_is_waiting_on_is_refused(url, boundary):
    refused_irreversible(boundary)
    call(url, "/api/state")
    status, _ = call(url, "/api/tool", {"agent": "mail", "confirm": 1, "for_reason": "some other call", "code": "42"})
    assert status == 403


def test_a_code_without_the_call_it_belongs_to_is_a_bad_request(url, boundary):
    refused_irreversible(boundary)
    call(url, "/api/state")
    status, body = call(url, "/api/tool", {"agent": "mail", "confirm": 1, "code": "42"})
    assert status == 400 and "for_reason" in body["error"]


def test_the_code_stays_optional_from_a_screen_already_in_the_persons_hands(url, boundary):
    refused_irreversible(boundary)
    status, body = call(url, "/api/tool", {"agent": "mail", "confirm": 1, "for_reason": TRASH})
    assert status == 200 and body["armed"]["confirm"] == 1


def test_a_full_cycle_grants_the_approved_call_and_not_another(url, boundary):
    """The one a person read is granted once; anything else still needs its own nod."""
    refused_irreversible(boundary, effect=EFFECT)
    code = call(url, "/api/state")[1]["pending"][0]["code"]
    assert call(url, "/api/tool", {"agent": "mail", "confirm": 1, "for_reason": TRASH, "code": code,
                                   "note": "yes, that one is a scam"})[0] == 200
    call(url, "/api/tool", {"agent": "mail", "irreversible": 1}, token=False)
    status, granted = call(url, "/api/request", {"agent": "mail", "class": "files", "intent": 1, "reason": TRASH})
    assert status == 200 and granted["granted"] is True
    call(url, "/api/tool", {"agent": "mail", "irreversible": 1}, token=False)
    other = call(url, "/api/request", {"agent": "mail", "class": "files", "intent": 1,
                                       "reason": "[files] trash_email [irreversible]: id=m9"})[1]
    assert other["granted"] is False  # a confirm is for the call it was given for, and is spent


# -- the note ---------------------------------------------------------------------


def test_the_note_is_stored_on_the_write_and_is_not_a_bit(url, boundary):
    refused_irreversible(boundary)
    status, body = call(url, "/api/tool", {"agent": "mail", "confirm": 1, "for_reason": TRASH,
                                           "note": "  it is the scam one  "})
    assert status == 200 and body["note"] == "it is the scam one"
    assert set(body["armed"]) == set(console.MUST_COME_FROM_THE_TOOL_LAYER)  # note did not become a bit
    assert console.TRANSCRIPTS[0]["note"] == "it is the scam one"


def test_a_long_note_is_cut_to_two_hundred_characters_and_a_wrong_type_refused(url, boundary):
    status, body = call(url, "/api/tool", {"agent": "mail", "heartbeat": 1, "note": "x" * 500})
    assert status == 200 and len(body["note"]) == 200
    assert call(url, "/api/tool", {"agent": "mail", "heartbeat": 1, "note": 7})[0] == 400


# -- the effect -------------------------------------------------------------------


def test_an_effect_rides_along_untouched_and_decides_nothing(url, boundary):
    boundary.install("spend", Policy(forbid_when_blocked=True))
    pay = {"kind": "pay", "target": "0xabc", "amount": "12.5", "asset": "USDT", "summary": "pay the invoice",
           "reversible": False, "path": None}
    status, entry = call(url, "/api/request", {"agent": "payer", "class": "spend", "intent": 1,
                                               "reason": "[spend] transfer", "effect": pay})
    assert status == 200 and entry["granted"] is True  # an effect never changes a verdict
    assert entry["effect"] == pay
    assert console.TRANSCRIPTS[0]["effect"] == pay


def test_a_request_without_an_effect_looks_exactly_as_it_did(url):
    status, entry = call(url, "/api/request", {"agent": "plain", "class": "exec", "intent": 1, "reason": "ls"})
    assert status == 200 and "effect" not in entry


@pytest.mark.parametrize("effect, says", [
    ("not an object", "effect must be an object"),
    ({"kind": "explode"}, "effect.kind"),
    ({"kind": "send", "target": 7}, "effect.target"),
    ({"kind": "send", "summary": "s" * 201}, "effect.summary"),
    ({"kind": "send", "reversible": "maybe"}, "effect.reversible"),
    ({"kind": "send", "details": {"blob": "x" * 1100}}, "effect.details"),
])
def test_a_wrong_effect_shape_is_refused_rather_than_trimmed(url, effect, says):
    status, body = call(url, "/api/request", {"agent": "shapes", "class": "exec", "intent": 1,
                                              "reason": "probe", "effect": effect})
    assert status == 400 and says in body["error"]


# -- the big red button ------------------------------------------------------------


def test_stop_all_blocks_every_known_agent_and_records_one_entry_each(url, boundary):
    for name in ("a", "b", "c"):
        boundary.request(name, 1, "x")
    before = len(console.TRANSCRIPTS)
    status, body = call(url, "/api/stop-all", {})
    assert status == 200 and body == {"stopped": True, "agents": ["a", "b", "c"], "count": 3, "source": "page"}
    assert all(a["armed"]["blocked"] == 1 for a in boundary.status()["agents"])
    written = [e for e in list(console.TRANSCRIPTS)[:len(console.TRANSCRIPTS) - before] if e["kind"] == "tool"]
    assert len(written) == 3 and {e["agent"] for e in written} == {"a", "b", "c"}
    assert all(e["source"] == "page" for e in written)
    assert all(not boundary.request(n, 1, "x")["granted"] for n in ("a", "b", "c"))


def test_the_block_latches_until_resume_all_and_both_need_the_token(url, boundary):
    boundary.request("a", 1, "x")
    assert call(url, "/api/stop-all", {}, token=False)[0] == 403
    assert call(url, "/api/resume-all", {}, token=False)[0] == 403
    call(url, "/api/stop-all", {})
    for _ in range(3):  # nothing lifts it on its own: blocked is a level, not an event
        assert not boundary.request("a", 1, "x")["granted"]
    status, body = call(url, "/api/resume-all", {"note": "drill over", "source": "page"})
    assert status == 200 and body["stopped"] is False and body["agents"] == ["a"]
    assert boundary.request("a", 1, "x")["granted"]
    assert console.TRANSCRIPTS[1]["note"] == "drill over"  # [0] is the request just made


def test_an_agent_first_seen_after_the_stop_is_not_blocked_by_it(url, boundary):
    boundary.request("old", 1, "x")
    call(url, "/api/stop-all", {})
    assert boundary.request("new", 1, "x")["granted"]  # which is why the page counts N of M
    blocked = [a["agent"] for a in boundary.status()["agents"] if a["armed"]["blocked"]]
    assert blocked == ["old"]
    call(url, "/api/stop-all", {})  # pressed again: it catches up with the newcomer
    assert not boundary.request("new", 1, "x")["granted"]


def test_resume_all_also_lifts_a_sticky_halt(url, boundary):
    """A sticky halt waits for "a confirm"; but the person's resume is that act. Arming a
    confirm to lift it would hand the same bit to a call waiting in a class, so resume
    resets the halt state instead — what installing the halt circuit does to everyone."""
    boundary.install("halt", Policy(sticky_block=True, forbid_when_blocked=True))
    boundary.request("a", 1, "x")
    call(url, "/api/stop-all", {})
    assert not boundary.request("a", 1, "x")["granted"]           # blocked, and the halt latched
    boundary.arm("a", {"blocked": 0})
    r = boundary.request("a", 1, "x")
    assert not r["granted"] and "halted until a confirm lifts it" in r["why"][0]  # the latch outlives a plain unblock
    status, body = call(url, "/api/resume-all", {})
    assert status == 200 and body["stopped"] is False
    r = boundary.request("a", 1, "x")
    assert r["granted"] and r["halt"]["granted"] is True, r["why"]
