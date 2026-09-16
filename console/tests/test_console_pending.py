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


# -- the source ---------------------------------------------------------------------


def test_a_source_says_which_channel_wrote_the_bit_and_is_not_a_bit(url, boundary):
    """A Telegram button and a click on the page write the same bit; `source` is how
    Activity tells them apart, as the Wi-Fi Cardputer already stamps `cardputer-wifi`."""
    refused_irreversible(boundary)
    status, body = call(url, "/api/tool", {"agent": "mail", "confirm": 1, "for_reason": TRASH, "source": "telegram"})
    assert status == 200, body
    assert body["source"] == "telegram"
    assert "source" not in body["armed"]  # a label, not a tool-layer bit
    assert console.TRANSCRIPTS[0]["source"] == "telegram"  # the same entry Activity reads


def test_a_source_is_cut_to_twenty_characters_and_absent_when_not_given(url, boundary):
    status, body = call(url, "/api/tool", {"agent": "mail", "heartbeat": 1, "source": "s" * 40})
    assert status == 200 and body["source"] == "s" * 20
    status, body = call(url, "/api/tool", {"agent": "mail", "heartbeat": 1})
    assert status == 200 and "source" not in body  # no default: an adapter's write is not "the page"


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
    assert status == 200 and body == {"stopped": True, "operator_stop": True, "agents": ["a", "b", "c"], "count": 3, "source": "page"}
    assert all(a["armed"]["blocked"] == 1 for a in boundary.status()["agents"])
    new = list(console.TRANSCRIPTS)[:len(console.TRANSCRIPTS) - before]
    written = [e for e in new if e["kind"] == "tool"]
    assert len(written) == 3 and {e["agent"] for e in written} == {"a", "b", "c"}
    assert all(e["source"] == "page" for e in written)
    press = [e for e in new if e["kind"] == "stop"]
    assert len(press) == 1 and press[0]["stopped"] is True and press[0]["agents"] == ["a", "b", "c"]  # the press itself is on the record
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


def test_an_agent_first_seen_after_the_stop_is_refused_by_it(url, boundary):
    """This test used to assert the opposite — that an agent first seen after the press was
    NOT blocked, "which is why the page counts N of M". That assertion described the
    implementation (stop-all wrote `blocked` for the names it knew) and called it the
    behaviour. The red team then renamed an agent past the button and was granted on tick 4
    while the page said "stopped" (docs/REDTEAM-2026-09-17.md). A stop that a new name
    walks out of is not a stop: the button now sets a latch that refuses every request,
    from any name, before a circuit is asked, until a person resumes. The cost is stated in
    the commit: a session started during a stop is refused until someone resumes."""
    boundary.request("old", 1, "x")
    call(url, "/api/stop-all", {})
    r = boundary.request("new", 1, "x")
    assert not r["granted"] and r["why"] == [console.OPERATOR_STOP_SAYS] and r["operator_stop"] is True
    assert r["blocked"] == 1 and r["halt"] is None  # no circuit was asked
    assert boundary.status()["operator_stop"]["on"] is True
    # It waits, and nothing a person can write lifts it — only the resume.
    it = next(p for p in boundary.status()["pending"] if p["agent"] == "new")
    assert it["bit"] is None and it["waiting_on_time"] is True and "code" not in it
    call(url, "/api/resume-all", {})
    assert boundary.status()["operator_stop"]["on"] is False
    assert boundary.request("new", 1, "x")["granted"]


def test_a_stop_refuses_an_ungated_class_and_moves_no_circuit(url, boundary):
    """The UX pass measured this: on a default console only `exec` has a circuit, so after
    stop-all a `files` call was still granted (an ungated class read `blocked` through
    nothing) while the page said the next request would be refused. The latch is decided
    before any circuit, so what is installed does not matter — and nothing behind the wall
    moves: no cooldown advances, no latch changes, nothing armed is consumed."""
    boundary.request("old", 1, "x")
    exec_ticks = boundary.agents["old"]["classes"]["exec"]["ticks"]
    boundary.arm("old", {"confirm": 1})
    call(url, "/api/stop-all", {})
    r = boundary.request("old", 1, "[files] trash", "files")
    assert not r["granted"] and r["class_installed"] is False and r["why"] == [console.OPERATOR_STOP_SAYS]
    r = boundary.request("old", 1, "x", "exec")
    assert not r["granted"]
    assert boundary.agents["old"]["classes"]["exec"]["ticks"] == exec_ticks  # the circuit was not ticked
    assert boundary.agents["old"]["armed"]["confirm"] == 1                    # and the person's confirm was not spent


def test_stop_all_with_nobody_known_still_stops_everything(url, boundary):
    status, body = call(url, "/api/stop-all", {})
    assert status == 200 and body["count"] == 0 and body["operator_stop"] is True
    assert not boundary.request("first-ever", 1, "x")["granted"]
    assert console.TRANSCRIPTS[1]["kind"] == "stop"  # the press is on the record even with nobody to block


def test_the_operator_stop_survives_a_restart(tmp_path):
    state = tmp_path / "policies.json"
    plain = Policy(forbid_when_blocked=True)  # no commitment rule, so a first request can be granted
    first = console.Boundary(plain, state)
    first.request("a", 1, "x")
    first.stop_all(True, "page")
    again = console.Boundary(plain, state)
    assert again.operator_stop["on"] is True and again.operator_stop["source"] == "page"
    assert not again.request("renamed-after-the-crash", 1, "x")["granted"]
    again.stop_all(False, "page")
    third = console.Boundary(plain, state)
    assert third.operator_stop["on"] is False
    assert third.request("renamed-after-the-crash", 1, "x")["granted"]


@pytest.mark.parametrize("stop", ['"no"', '{"on": "false"}', '{"on": 1}', '[]'])
def test_a_mangled_stop_latch_refuses_to_start_rather_than_guess(tmp_path, stop):
    """bool("false") is True: a corrupt field must not decide either way about whether
    the person said stop, so the loader refuses it as it refuses unreadable rules."""
    state = tmp_path / "policies.json"
    state.write_text('{"classes": {}, "agents": {}, "operator_stop": %s}' % stop)
    with pytest.raises(SystemExit) as e:
        console.Boundary(console.FLY_DEFAULT, state)
    assert "operator_stop" in str(e.value)


def test_resume_all_also_lifts_a_sticky_halt(url, boundary):
    """A sticky halt waits for "a confirm"; but the person's resume is that act. Arming a
    confirm to lift it would hand the same bit to a call waiting in a class, so resume
    resets the halt state instead — what installing the halt circuit does to everyone.

    The halt is latched here with a per-name block rather than stop-all: under the
    operator stop no circuit ticks, so the press itself no longer latches anything."""
    boundary.install("halt", Policy(sticky_block=True, forbid_when_blocked=True))
    boundary.request("a", 1, "x")
    boundary.arm("a", {"blocked": 1})
    assert not boundary.request("a", 1, "x")["granted"]           # blocked, and the halt latched
    boundary.arm("a", {"blocked": 0})
    r = boundary.request("a", 1, "x")
    assert not r["granted"] and "halted until a confirm lifts it" in r["why"][0]  # the latch outlives a plain unblock
    status, body = call(url, "/api/resume-all", {})
    assert status == 200 and body["stopped"] is False
    r = boundary.request("a", 1, "x")
    assert r["granted"] and r["halt"]["granted"] is True, r["why"]


def test_a_halt_refusal_offers_the_confirm_that_lifts_it(boundary):
    """When the shared halt refuses, the class's circuit also says "blocked is high" — the
    halt made it so. Reading the class's words made every halted call a dead end on the
    page: listed as waiting for a person, with no bit and no code, and no button that could
    lift it. The halt's own words say a confirm lifts it, so the item carries that."""
    boundary.install("halt", Policy(heartbeat_ticks=1, forbid_when_blocked=True))
    boundary.request("quiet", 1, "x")
    r = boundary.request("quiet", 1, "x")
    assert not r["granted"] and r["halt"]["granted"] is False and any(w.startswith(console.BLOCKED_SAYS) for w in r["why"])
    it = next(p for p in boundary.status()["pending"] if p["agent"] == "quiet")
    assert it["bit"] == "confirm" and it["waiting_on_time"] is False and len(it["code"]) == 2
    # But a halt that is holding because the person's own block is high is not lifted by a
    # confirm; that one still waits on the person lifting the block.
    boundary.arm("quiet", {"blocked": 1})
    boundary.request("quiet", 1, "x")
    it = next(p for p in boundary.status()["pending"] if p["agent"] == "quiet")
    assert it["bit"] is None and it["waiting_on_time"] is True
