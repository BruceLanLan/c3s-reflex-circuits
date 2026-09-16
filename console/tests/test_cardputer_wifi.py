"""W11: the Cardputer over Wi-Fi — pairing by matching a code, and what a device token
is allowed to do.

No device and no server: the pairing store is pointed at a temporary file and the two
HTTP handlers are driven with a stub that records what they answered, so these run
anywhere. The point of every test here is a *refusal*: the device is a physical key, and
the network must not be able to press it.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

console = pytest.importorskip("console")
from c3s.policy import Policy  # noqa: E402  (console put the circuits repo on the path)

import cardputer_relay as cr  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "DEVICES_FILE", tmp_path / "devices.json")
    cr._LAST_POLL.clear()
    return cr.DEVICES_FILE


@pytest.fixture
def boundary():
    console.TRANSCRIPTS.clear()
    b = console.Boundary(Policy(forbid_when_blocked=True))
    b.install("files", Policy(confirm_per_irreversible=True, forbid_when_blocked=True))
    return b


def waiting_call(boundary, agent="mail", reason="[files] trash_email [irreversible]: id=m3"):
    """One refused irreversible call, which is what a person is asked to answer."""
    boundary.arm(agent, {"irreversible": 1})
    boundary.request(agent, 1, reason, "files")
    item = next(it for it in boundary.status()["pending"] if it["agent"] == agent)
    return item


class Stub:
    """Only what the two handlers touch of a BaseHTTPRequestHandler."""

    def __init__(self, path, token=None, operator=True):
        self.path = path
        self.headers = {"X-Reflex-Device-Token": token} if token else {}
        self.operator = operator
        self.status, self.body, self.kind = None, None, None

    def _json(self, code, obj):
        self.status, self.body = code, obj

    def _send(self, code, body, kind):
        self.status, self.body, self.kind = code, body, kind

    def _has_token(self, payload):
        return self.operator


def pair(store, device_id="c3s-abc123", name="cardputer"):
    """The whole flow: a person opens a window, the device claims it, the person types
    the digits the device shows."""
    cr.device_pair_begin()
    claim = cr.device_pair_claim(device_id, name)
    assert cr.device_pair_confirm(claim["code"])["paired"] is True
    return claim["token"]


# -- pairing ---------------------------------------------------------------------------


def test_no_token_is_handed_out_unless_a_person_opened_a_window(store):
    assert "error" in cr.device_pair_claim("c3s-abc123", "cardputer")
    assert cr.device_auth("anything") is None


def test_the_code_is_derived_from_the_token_so_matching_digits_mean_the_same_token(store):
    cr.device_pair_begin()
    claim = cr.device_pair_claim("c3s-abc123", "cardputer")
    assert claim["code"] == cr.pair_code(claim["token"]) and len(claim["code"]) == 4
    # Provisional: the token is worth nothing until the person confirms the match.
    assert cr.device_auth(claim["token"]) is None
    cr.device_pair_confirm(claim["code"])
    assert cr.device_auth(claim["token"]) == "c3s-abc123"


def test_a_wrong_code_cancels_the_pairing_rather_than_asking_again(store):
    cr.device_pair_begin()
    claim = cr.device_pair_claim("c3s-abc123", "cardputer")
    wrong = f"{(int(claim['code']) + 1) % 10000:04d}"
    assert "error" in cr.device_pair_confirm(wrong)
    assert "error" in cr.device_pair_confirm(claim["code"])  # the window is gone: no second guess
    assert cr.device_auth(claim["token"]) is None


def test_a_second_device_cannot_take_a_window_that_is_already_claimed(store):
    cr.device_pair_begin()
    first = cr.device_pair_claim("c3s-abc123", "mine")
    assert "error" in cr.device_pair_claim("c3s-someone-else", "theirs")
    cr.device_pair_confirm(first["code"])
    assert [d["device_id"] for d in cr.device_list()] == ["c3s-abc123"]


def test_the_console_never_shows_the_code_itself(store):
    """One side shows the digits, the other asks for them. If the console printed them,
    "do they match" could be answered without looking at the device."""
    cr.device_pair_begin()
    claim = cr.device_pair_claim("c3s-abc123", "cardputer")
    assert claim["code"] not in repr(cr.device_pair_status())


def test_forgetting_a_device_drops_its_token(store):
    token = pair(store)
    assert cr.device_forget("c3s-abc123")["forgotten"] is True
    assert cr.device_auth(token) is None


def test_pairing_is_the_operators_and_a_claim_is_not(store, monkeypatch):
    for path in ("/api/device/pair/begin", "/api/device/pair/confirm", "/api/device/forget"):
        h = Stub(path, operator=False)
        cr.device_post(h, {}, None)
        assert h.status == 403 and "operator" in h.body["error"]
    h = Stub("/api/device/pair", operator=False)  # the device has no operator token, by design
    cr.device_post(h, {"device_id": "x"}, None)
    assert h.status == 403 and "window" in h.body["error"]


# -- the frame the device polls ---------------------------------------------------------


def test_the_frame_is_only_served_to_a_paired_device(store, boundary):
    h = Stub("/api/device/frame")
    cr.device_get(h, boundary)
    assert h.status == 403 and not cr.device_present()
    token = pair(store)
    h = Stub("/api/device/frame", token=token)
    cr.device_get(h, boundary)
    assert h.status == 200 and h.kind.startswith("text/plain")
    assert cr.device_present()  # the poll is the heartbeat


def test_the_wide_frame_carries_the_code_and_the_exact_agent_and_reason(store, boundary):
    """The display fields are cleaned and truncated; a confirm binds on the reason
    exactly. So the write fields travel as JSON literals in their own line."""
    item = waiting_call(boundary, agent="邮件助手", reason="[files] trash_email [irreversible]: 主题=种子词诈骗")
    lines, _ = cr.frame(boundary.status(), wide=True)
    shown = next(line for line in lines if line.startswith("I|"))
    write = next(line for line in lines if line.startswith("C|"))
    assert "?" in shown  # the screen gets ASCII
    fields = write.split("|")
    assert fields[1] == "0" and fields[2] == item["code"]
    import json

    assert json.loads(fields[3]) == item["agent"] and json.loads(fields[4]) == item["reason"]
    assert not any(line.startswith("C|") for line in cr.frame(boundary.status())[0])  # USB: unchanged


def test_a_pipe_in_a_reason_cannot_break_the_line_apart(store, boundary):
    waiting_call(boundary, reason="[exec] sh [irreversible]: cat a | grep b")
    write = next(line for line in cr.frame(boundary.status(), wide=True)[0] if line.startswith("C|"))
    import json

    assert len(write.split("|")) == 5 and "|" in json.loads(write.split("|")[4])


# -- what a device token may write ------------------------------------------------------


def tool(token, payload, boundary):
    h = Stub("/api/tool", token=token)
    cr.device_tool(h, payload, boundary)
    return h


def test_a_token_that_is_not_paired_writes_nothing(store, boundary):
    item = waiting_call(boundary)
    h = tool("not-a-paired-token", {"agent": "mail", "confirm": 1, "for_reason": item["reason"],
                                    "code": item["code"]}, boundary)
    assert h.status == 403
    assert boundary.status()["agents"][0]["armed"]["confirm"] == 0


def test_a_paired_device_may_only_confirm_a_call_that_is_waiting(store, boundary):
    token = pair(store)
    item = waiting_call(boundary)
    h = tool(token, {"agent": "somebody-else", "confirm": 1, "for_reason": item["reason"],
                     "code": item["code"]}, boundary)
    assert h.status == 403 and "waiting" in h.body["error"]


def test_a_wrong_code_is_refused_and_a_right_one_is_recorded_as_the_device(store, boundary):
    token = pair(store)
    item = waiting_call(boundary)
    wrong = f"{(int(item['code']) + 1) % 90 + 10:02d}"
    assert tool(token, {"agent": "mail", "confirm": 1, "for_reason": item["reason"], "code": wrong},
                boundary).status == 403
    assert tool(token, {"agent": "mail", "confirm": 1, "code": item["code"]}, boundary).status == 403  # no for_reason
    h = tool(token, {"agent": "mail", "confirm": 1, "for_reason": item["reason"], "code": item["code"]}, boundary)
    assert h.status == 200 and h.body["source"] == "cardputer-wifi" and h.body["device"] == "c3s-abc123"
    agent = next(a for a in boundary.status()["agents"] if a["agent"] == "mail")
    assert agent["armed"]["confirm"] == 1 and agent["bound"]["confirm"] == item["reason"]


def test_a_confirm_is_bound_to_the_call_the_device_was_shown(store, boundary):
    """The code belongs to one call, so a device cannot spend it on another."""
    token = pair(store)
    item = waiting_call(boundary)
    boundary.install("message", Policy(confirm_per_irreversible=True, forbid_when_blocked=True))
    other = "[message] reply_email [irreversible]: id=m1"
    boundary.arm("mail", {"irreversible": 1})
    boundary.request("mail", 1, other, "message")
    assert tool(token, {"agent": "mail", "confirm": 1, "for_reason": other, "code": item["code"]},
                boundary).status == 403


def test_a_device_may_block_an_agent_it_can_see_but_not_unblock_one(store, boundary):
    token = pair(store)
    item = waiting_call(boundary)
    assert tool(token, {"agent": "mail", "blocked": 1, "code": item["code"]}, boundary).status == 200
    h = tool(token, {"agent": "mail", "blocked": 0, "code": item["code"]}, boundary)
    assert h.status == 403 and "a block is lifted on the console or over USB" in h.body["error"]
    agent = next(a for a in boundary.status()["agents"] if a["agent"] == "mail")
    assert agent["armed"]["blocked"] == 1


def test_a_device_cannot_hold_the_dead_mans_heartbeat_open_with_a_write(store, boundary):
    token = pair(store)
    item = waiting_call(boundary)
    h = tool(token, {"agent": "mail", "heartbeat": 1, "code": item["code"]}, boundary)
    assert h.status == 403 and "presence" in h.body["error"]


def test_a_device_token_is_no_use_for_the_adapters_bits_or_anything_else(store, boundary):
    token = pair(store)
    item = waiting_call(boundary)
    for bits in ({"irreversible": 1}, {"failed": 1}, {"nonsense": 1}, {}):
        h = tool(token, {"agent": "mail", "code": item["code"], **bits}, boundary)
        assert h.status == 403 and "and nothing else" in h.body["error"]


def test_presence_is_a_recent_poll_and_stops_when_the_radio_does(store, boundary):
    token = pair(store)
    cr.device_get(Stub("/api/device/frame", token=token), boundary)
    assert cr.device_present()
    cr._LAST_POLL["c3s-abc123"] -= cr.DEVICE_FRESH_S + 1
    assert not cr.device_present()  # Wi-Fi dropping is not "assume the person is there"


def test_wifi_presence_is_added_to_the_cable_never_instead_of_it(store, boundary):
    cable = {"v": False}
    boundary.heartbeat_source = lambda: cable["v"]
    cr.install_device_presence(boundary)
    assert not boundary.heartbeat_source()
    cable["v"] = True
    assert boundary.heartbeat_source()
    cable["v"] = False
    cr._LAST_POLL["c3s-abc123"] = __import__("time").time()
    assert boundary.heartbeat_source()
