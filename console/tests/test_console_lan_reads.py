"""Who may read what over the network (docs/REDTEAM-2026-09-17.md F1 and F2).

The console's own handler on an ephemeral port, with `_from_loopback` patched to answer
False so that every connection is treated as one from the Wi-Fi. No real console, no
device, no serial port; the agent-token and device stores point at temporary files.

F2: a bound agent's own token used to read the whole console — every other agent's
pending `reason`, the transcript, their armed bits. An agent identity now sees its own
rows and the rules. The string the red team read back (`[exec] wire 50000 USDC to 0xBEEF`)
must not appear anywhere in the reply, because that string is what a confirm is bound to.
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

import cardputer_relay as cr  # noqa: E402

OPERATOR = "lan-test-operator-token"
READER_TOKEN = "reader-secret-token"
WIRE = "[exec] wire 50000 USDC to 0xBEEF"


@pytest.fixture
def boundary():
    console.TRANSCRIPTS.clear()
    b = console.Boundary(Policy(forbid_when_blocked=True), None)
    b.install("exec", Policy(confirm_per_irreversible=True, forbid_when_blocked=True))
    yield b
    console.TRANSCRIPTS.clear()


@pytest.fixture
def lan(boundary, monkeypatch, tmp_path):
    """The handler, with every connection read as coming from the network."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), console.Handler)
    port = server.server_address[1]
    monkeypatch.setattr(console, "BOUNDARY", boundary, raising=False)
    monkeypatch.setattr(console, "TOKEN", OPERATOR)
    monkeypatch.setattr(console, "PORT", port)
    monkeypatch.setattr(console, "CHAIN", None)
    monkeypatch.setattr(console, "AGENTS_FILE", tmp_path / "agents.json")
    monkeypatch.setattr(cr, "DEVICES_FILE", tmp_path / "devices.json")
    cr._LAST_POLL.clear()
    monkeypatch.setattr(console.Handler, "_from_loopback", lambda self: False)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def get(url, path, headers=None):
    host, port = url.split(":")
    c = http.client.HTTPConnection(host, int(port), timeout=10)
    try:
        c.request("GET", path, headers=headers or {})
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")
    finally:
        c.close()


def post(url, path, body, headers=None):
    host, port = url.split(":")
    c = http.client.HTTPConnection(host, int(port), timeout=10)
    try:
        c.request("POST", path, body=json.dumps(body), headers={"Content-Type": "application/json", **(headers or {})})
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")
    finally:
        c.close()


def two_agents(boundary):
    """`reader`, bound to its own token, and `treasury-bot` with a sensitive call waiting."""
    console.token_bind("reader", READER_TOKEN)
    boundary.request("reader", 1, "[exec] reader boot")
    boundary.arm("treasury-bot", {"irreversible": 1})
    boundary.request("treasury-bot", 1, WIRE)  # refused: irreversible, no confirm — so it is pending
    boundary.arm("treasury-bot", {"heartbeat": 1}, note="treasury's own note")
    assert any(p["agent"] == "treasury-bot" for p in boundary.status()["pending"])


# -- F2: an agent's token reads its own rows -----------------------------------------------


def test_nobody_reads_the_state_over_the_lan_without_a_token(lan, boundary):
    two_agents(boundary)
    status, body = get(lan, "/api/state")
    assert status == 403 and "token" in body["error"]


def test_an_agents_token_reads_its_own_rows_and_not_another_agents_call(lan, boundary):
    two_agents(boundary)
    status, body = get(lan, "/api/state", {"X-Reflex-Agent-Token": READER_TOKEN})
    assert status == 200
    assert [a["agent"] for a in body["agents"]] == ["reader"]
    assert all(p["agent"] == "reader" for p in body["pending"])
    assert WIRE not in json.dumps(body)                     # the string a confirm binds to
    assert "treasury-bot" not in json.dumps(body)           # nor the name, nor its armed bits or note
    assert body["view"] == {"scope": "agent", "agent": "reader"}
    assert body["policies"]["exec"]["rules"]                # the rules, and what was checked, stay
    assert body["policies"]["exec"]["checked"]["every_rule_holds"] is True
    assert body["agent_tokens"]["bound"] == 1


def test_an_agents_token_still_sees_its_own_transcript_and_the_rules_entries(lan, boundary):
    two_agents(boundary)
    console.TRANSCRIPTS.appendleft({"at": 1.0, "kind": "policy", "class": "spend", "rules": ["deny"]})
    boundary.stop_all(True, "page")
    boundary.stop_all(False, "page")
    status, body = get(lan, "/api/state", {"X-Reflex-Agent-Token": READER_TOKEN})
    kinds = {(e["kind"], e.get("agent")) for e in body["transcript"]}
    assert ("request", "reader") in kinds and ("policy", None) in kinds and ("stop", None) in kinds
    assert ("request", "treasury-bot") not in kinds and ("tool", "treasury-bot") not in kinds
    assert all("agents" not in e for e in body["transcript"] if e["kind"] == "stop")  # the press, not the names


def test_the_operator_token_keeps_the_whole_view_over_the_lan(lan, boundary):
    two_agents(boundary)
    status, body = get(lan, "/api/state", {"X-Reflex-Token": OPERATOR})
    assert status == 200 and "view" not in body
    assert {a["agent"] for a in body["agents"]} == {"reader", "treasury-bot"}
    assert WIRE in json.dumps(body)


def test_loopback_keeps_the_whole_view(lan, boundary, monkeypatch):
    two_agents(boundary)
    monkeypatch.setattr(console.Handler, "_from_loopback", lambda self: True)
    status, body = get(lan, "/api/state")
    assert status == 200 and "view" not in body and WIRE in json.dumps(body)


# -- F1: the phone has a token of its own, and the operator token stays off the reads --------


def paired_cardputer() -> str:
    """A Cardputer paired the W11 way: window, claim, four digits. Its token is a device
    token with no `kind`, so it is not a viewer."""
    cr.device_pair_begin()
    claim = cr.device_pair_claim("cardputer-1", "desk")
    assert cr.device_pair_confirm(claim["code"])["paired"]
    return claim["token"]


def test_a_viewer_token_is_a_device_record_of_kind_phone_with_only_its_hash_on_disk(lan):
    issued = console.viewer_issue("my phone")
    assert issued["device_id"].startswith("phone:") and issued["kind"] == "phone"
    assert console.viewer_auth(issued["token"]) == issued["device_id"]
    assert cr.device_auth(issued["token"]) == issued["device_id"]  # the same store, the same device path
    on_disk = cr.DEVICES_FILE.read_text()
    assert issued["token"] not in on_disk and issued["device_id"] in on_disk and '"kind": "phone"' in on_disk


def test_issuing_a_viewer_token_is_the_operators_act(lan):
    status, body = post(lan, "/api/device/viewer", {"name": "phone"})
    assert status == 403 and "operator" in body["error"]
    status, body = post(lan, "/api/device/viewer", {"name": "phone"}, {"X-Reflex-Token": OPERATOR})
    assert status == 200 and body["token"] and console.viewer_auth(body["token"]) == body["device_id"]


def test_a_phones_token_reads_the_whole_state_over_the_lan_and_a_cardputers_does_not(lan, boundary):
    two_agents(boundary)
    phone = console.viewer_issue()["token"]
    status, body = get(lan, "/api/state", {"X-Reflex-Device-Token": phone})
    assert status == 200 and "view" not in body                          # the person's own phone: the full view
    assert {a["agent"] for a in body["agents"]} == {"reader", "treasury-bot"}
    assert get(lan, "/api/manifest?class=exec", {"X-Reflex-Device-Token": phone})[0] == 200
    assert get(lan, "/api/tasks", {"X-Reflex-Device-Token": phone})[0] == 200
    cardputer = paired_cardputer()
    assert get(lan, "/api/state", {"X-Reflex-Device-Token": cardputer})[0] == 403   # the frame is all it reads
    assert get(lan, "/api/classes", {"X-Reflex-Device-Token": phone})[0] == 403     # names local paths: operator only


def test_a_phones_read_is_not_a_heartbeat(lan, boundary):
    """A phone tab left open in a pocket must not hold a dead-man's halt open. Only a
    device polling its own frame is present."""
    phone = console.viewer_issue()["token"]
    assert get(lan, "/api/state", {"X-Reflex-Device-Token": phone})[0] == 200
    assert cr.device_present() is False


def test_a_phone_confirms_what_it_can_see_and_blocks_but_nothing_the_operator_does(lan, boundary):
    two_agents(boundary)
    phone = console.viewer_issue()["token"]
    hdr = {"X-Reflex-Device-Token": phone}
    # not the operator's acts
    assert post(lan, "/api/policy", {"class": "spend", "deny_all": True}, hdr)[0] == 403
    assert post(lan, "/api/stop-all", {}, hdr)[0] == 403
    assert post(lan, "/api/resume-all", {}, hdr)[0] == 403
    assert post(lan, "/api/task", {"text": "a job"}, hdr)[0] == 403
    assert post(lan, "/api/tool", {"agent": "treasury-bot", "blocked": 0}, hdr)[0] == 403    # never unblock
    assert post(lan, "/api/tool", {"agent": "treasury-bot", "heartbeat": 1}, hdr)[0] == 403  # never the heartbeat
    # the device's acts: confirm the waiting call, with its code, and block
    it = next(p for p in get(lan, "/api/state", hdr)[1]["pending"] if p["agent"] == "treasury-bot")
    status, body = post(lan, "/api/tool", {"agent": "treasury-bot", "confirm": 1, "for_reason": WIRE, "code": it["code"]}, hdr)
    assert status == 200 and body["armed"]["confirm"] == 1
    assert console.TRANSCRIPTS[0]["source"] == "phone"  # not "cardputer-wifi": nobody's Cardputer pressed this
    status, body = post(lan, "/api/tool", {"agent": "treasury-bot", "blocked": 1, "code": it["code"]}, hdr)
    assert status == 200 and body["armed"]["blocked"] == 1
    assert boundary.status()["operator_stop"]["on"] is False  # a block, not the stop button


def test_a_forgotten_phone_reads_nothing(lan, boundary):
    issued = console.viewer_issue()
    hdr = {"X-Reflex-Device-Token": issued["token"]}
    assert get(lan, "/api/state", hdr)[0] == 200
    assert post(lan, "/api/device/forget", {"device_id": issued["device_id"]}, {"X-Reflex-Token": OPERATOR})[1]["forgotten"]
    assert get(lan, "/api/state", hdr)[0] == 403


def test_the_page_never_sends_the_operator_token_on_a_read():
    """The hole itself (F1): the page attached `x-reflex-token` to its two-second poll. Every
    GET the page makes is found here and checked; the operator token may ride only a POST."""
    import re

    html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text()
    calls = []
    for m in re.finditer(r"\bfetch\(", html):  # each call, from its open paren to the matching close
        depth, i = 0, m.end() - 1
        while i < len(html):
            depth += (html[i] == "(") - (html[i] == ")")
            i += 1
            if depth == 0:
                break
        calls.append(html[m.start():i])
    gets = [c for c in calls if '"POST"' not in c]
    assert len(gets) >= 4, gets  # /api/state, /api/tasks, /api/manifest, /api/classes
    offenders = [g for g in gets if "x-reflex-token" in g or "TOKEN" in g]
    assert not offenders, offenders
    assert "readHeaders()" in "".join(gets)


def test_a_narrowed_view_is_not_a_write_credential(lan, boundary):
    """Narrowing the read changed nothing about writes: the agent token still writes no
    person's bit, and its own request path is unchanged."""
    two_agents(boundary)
    status, body = post(lan, "/api/tool", {"agent": "treasury-bot", "confirm": 1, "for_reason": WIRE},
                        {"X-Reflex-Agent-Token": READER_TOKEN})
    assert status == 403 and "the person's" in body["error"]
    status, body = post(lan, "/api/request", {"agent": "reader", "class": "exec", "intent": 1, "reason": "[exec] again"},
                        {"X-Reflex-Agent-Token": READER_TOKEN})
    assert status == 200 and body["agent"] == "reader"
