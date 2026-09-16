"""I-3, the agent's own token: one name, one program.

Three parts, and they check different things on purpose.

* The store, in process, against a temporary `agents.json`: trust on first use, a bound
  name refusing anything but its own token, rotation, and the file holding a hash and a
  time rather than a token.
* The live console over HTTP: the same rules where they matter, plus the property the
  whole feature exists for — a request under a bound name with the wrong token does not
  spend that agent's confirm, because it never reaches a circuit at all.
* The four adapters against a recording server: each sends `X-Reflex-Agent-Token` when
  `REFLEX_AGENT_TOKEN` is set, sends nothing when it is not, and works either way.

The live part skips when the console is not running. The bnbagent part skips unless the
SDK is importable (its own venv: ~/work/c3s-cache/bnbagent-venv).
"""

import http.client
import json
import os
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "adapters"))

# The console itself is importable only where the circuits repository and its dependencies
# are (the main venv). The adapter half of this file must still run in the bnbagent venv,
# which has the SDK and not numpy — so this is a soft import, and the tests that need the
# console say so one by one instead of skipping the whole file.
WHY_NOT = None
try:
    import console
except Exception as e:  # pragma: no cover - environment-dependent
    console, WHY_NOT = None, f"{type(e).__name__}: {e}"

needs_console = pytest.mark.skipif(console is None, reason=f"the console is not importable here ({WHY_NOT})")

HOST, PORT = "127.0.0.1", int(os.environ.get("CONSOLE_PORT", "8765"))


def name() -> str:
    """A fresh agent name per test: a binding is permanent until someone rotates it."""
    return f"tok-test:{uuid.uuid4().hex[:10]}"


# ---- the store ------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(console, "AGENTS_FILE", tmp_path / "agents.json")
    return tmp_path / "agents.json"


@needs_console
def test_an_unbound_name_works_without_a_token(store):
    assert console.check_agent_token("a", "") == "unbound"
    assert not store.exists()  # nothing is written for a name nobody claimed


@needs_console
def test_the_first_token_binds_and_then_the_name_requires_it(store):
    assert console.check_agent_token("a", "s3cret") == "bound"
    assert console.check_agent_token("a", "s3cret") == "ok"
    assert console.check_agent_token("a", "s3cret-not") == "spoof"
    # Silence is not permission: once bound, no token is as wrong as the wrong token.
    assert console.check_agent_token("a", "") == "spoof"
    assert console.check_agent_token("a", "   ") == "spoof"


@needs_console
def test_two_names_do_not_talk_to_each_other(store):
    assert console.check_agent_token("a", "token-a") == "bound"
    assert console.check_agent_token("b", "token-b") == "bound"
    assert console.check_agent_token("a", "token-b") == "spoof"
    assert console.check_agent_token("b", "token-a") == "spoof"
    assert console.check_agent_token("a", "token-a") == "ok"


@needs_console
def test_the_file_keeps_a_hash_and_a_time_and_not_the_token(store):
    console.check_agent_token("a", "s3cret")
    text = store.read_text()
    assert "s3cret" not in text
    record = json.loads(text)["a"]
    assert len(record["token_sha256"]) == 64 and isinstance(record["bound_at"], float)
    assert oct(store.stat().st_mode)[-3:] == "600"


@needs_console
def test_rotation_drops_the_binding_and_the_next_token_takes_it(store):
    console.check_agent_token("a", "old")
    assert console.token_rotate("a")["rotated"] is True
    assert console.check_agent_token("a", "new") == "bound"
    assert console.check_agent_token("a", "old") == "spoof"
    # Rotating a name nobody bound is not an error, and writes nothing.
    assert console.token_rotate("never-seen")["rotated"] is False


@needs_console
def test_a_binding_written_by_another_process_is_seen_at_once(store):
    """`c3s token rotate` runs in its own process. A store cached in memory would keep
    refusing the new token until the console restarted."""
    console.check_agent_token("a", "old")
    store.write_text(json.dumps({}))  # what rotation from another process looks like
    assert console.check_agent_token("a", "brand-new") == "bound"


@needs_console
def test_a_corrupt_store_refuses_rather_than_reading_as_nothing_bound(store):
    """The adversarial review's second finding. Reading an unparseable store as "{}" would
    turn one bad write into "every agent's token is gone" — silently, while serving on."""
    console.check_agent_token("a", "s3cret")
    store.write_text("{ not json")
    for given in ("", "s3cret", "anything"):
        with pytest.raises(console.BindingsUnreadable):
            console.check_agent_token("a", given)
    with pytest.raises(console.BindingsUnreadable):
        console.agent_bindings()
    # A store whose shape is wrong is corruption too, not an empty store.
    store.write_text(json.dumps({"a": "not-a-record"}))
    with pytest.raises(console.BindingsUnreadable):
        console.check_agent_token("a", "s3cret")
    # No file at all is the honest starting state: nobody is bound.
    store.unlink()
    assert console.agent_bindings() == {}
    assert console.check_agent_token("a", "") == "unbound"


@needs_console
def test_requiring_a_token_refuses_an_unbound_name_instead_of_trusting_it(store, monkeypatch):
    """The adversarial review's first finding: by default an unbound name is a name
    anything local can speak for, which inside the isolation container means the agent can
    act as any name nobody has bound. REFLEX_REQUIRE_AGENT_TOKEN shuts that door."""
    monkeypatch.setattr(console, "REQUIRE_AGENT_TOKEN", True)
    assert console.check_agent_token("a", "") == "required"
    assert console.check_agent_token("a", "   ") == "required"
    assert console.check_agent_token("a", "s3cret") == "bound"  # first use still binds
    assert console.check_agent_token("a", "s3cret") == "ok"
    assert console.check_agent_token("a", "other") == "spoof"
    # And the default is unchanged: compatibility is what I-3 promises in docs/API.md.
    monkeypatch.setattr(console, "REQUIRE_AGENT_TOKEN", False)
    assert console.check_agent_token("b", "") == "unbound"


# ---- the live console -----------------------------------------------------------------


def post(path, body, headers):
    c = http.client.HTTPConnection(HOST, PORT, timeout=10)
    try:
        c.request("POST", path, body=body, headers=headers)
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


def state():
    c = http.client.HTTPConnection(HOST, PORT, timeout=10)
    try:
        c.request("GET", "/api/state")
        return json.loads(c.getresponse().read())
    finally:
        c.close()


def agent_in_state(agent):
    return next((a for a in state()["agents"] if a["agent"] == agent), None)


def request_as(agent, token=None, reason="x", cls="exec"):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Reflex-Agent-Token"] = token
    return post("/api/request", json.dumps({"agent": agent, "class": cls, "intent": 1, "reason": reason}), headers)


@pytest.fixture
def live():
    """The console as it runs for real: its own config directory, its own agents.json.
    Every name this makes is rotated away again afterwards."""
    try:
        state()
    except OSError as e:
        pytest.skip(f"console not running: {e}")
    made: list[str] = []
    yield made.append
    for agent in made:
        console.token_rotate(agent)


@needs_console
def test_live_an_unbound_agent_works_and_is_marked_unbound(live):
    agent = name()
    live(agent)
    status, body = request_as(agent)
    assert status == 200, body
    assert agent_in_state(agent)["token_bound"] is False


@needs_console
def test_live_the_first_token_binds_and_a_wrong_one_is_refused_and_recorded(live):
    agent = name()
    live(agent)
    assert request_as(agent, "right-token")[0] == 200
    row = agent_in_state(agent)
    assert row["token_bound"] is True and row["token_bound_at"] > 0

    ticks_before = row["ticks"]
    status, body = request_as(agent, "wrong-token", reason="[exec] spoofed")
    assert status == 403
    assert b"bound to its own agent token" in body
    # Refused before any circuit was asked: the agent did not even tick.
    assert agent_in_state(agent)["ticks"] == ticks_before

    spoof = [e for e in state()["transcript"] if e.get("kind") == "spoof" and e.get("agent") == agent]
    assert spoof and spoof[0]["granted"] is False and spoof[0]["token"] == "wrong"
    assert spoof[0]["reason"] == "[exec] spoofed" and spoof[0]["tick"] is None

    # A bound name with no token at all: the same refusal, recorded as `missing`.
    assert request_as(agent)[0] == 403
    assert [e for e in state()["transcript"] if e.get("kind") == "spoof" and e.get("token") == "missing"]

    # And the right token still works.
    assert request_as(agent, "right-token")[0] == 200


@needs_console
def test_live_a_different_name_with_a_different_token_is_unaffected(live):
    a, b = name(), name()
    live(a), live(b)
    assert request_as(a, "token-a")[0] == 200
    assert request_as(b, "token-b")[0] == 200
    assert request_as(a, "token-b")[0] == 403
    assert request_as(b, "token-a")[0] == 403
    assert request_as(b, "token-b")[0] == 200
    assert agent_in_state(a)["token_bound"] and agent_in_state(b)["token_bound"]


@needs_console
def test_live_a_spoofed_request_does_not_spend_the_confirm(live):
    """The point of the whole feature. A person leaves one confirm for one call; another
    program on the machine asks under that name; the confirm must still be there."""
    from reflex_token import operator_token

    agent = name()
    live(agent)
    reason = "[exec] the one call a person approved"
    assert request_as(agent, "right-token", reason=reason)[0] == 200
    status, body = post("/api/tool", json.dumps({"agent": agent, "confirm": 1, "for_reason": reason}),
                        {"Content-Type": "application/json", "X-Reflex-Token": operator_token()})
    assert status == 200, body
    assert agent_in_state(agent)["armed"]["confirm"] == 1

    for _ in range(3):
        assert request_as(agent, "wrong-token", reason=reason)[0] == 403
    assert agent_in_state(agent)["armed"]["confirm"] == 1, "a spoofed request consumed the person's confirm"


# ---- the adapters send the header -----------------------------------------------------


class Recorder(BaseHTTPRequestHandler):
    """Stands in for the console and remembers what arrived."""

    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", "0")) or 0)
        Recorder.seen.append({"path": self.path,
                              "token": self.headers.get("X-Reflex-Agent-Token"),
                              "operator": self.headers.get("X-Reflex-Token"),
                              "body": json.loads(body or b"{}")})
        out = json.dumps({"granted": True, "tick": 1, "why": []} if self.path == "/api/request" else {}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture
def recorder():
    Recorder.seen = []
    srv = ThreadingHTTPServer((HOST, 0), Recorder)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://{HOST}:{srv.server_port}", Recorder.seen
    srv.shutdown()
    srv.server_close()


def run_hook(script, url, event, token):
    env = dict(os.environ, REFLEX_CONSOLE=url, REFLEX_AGENT="hook-test")
    env.pop("REFLEX_AGENT_TOKEN", None)
    if token is not None:
        env["REFLEX_AGENT_TOKEN"] = token
    return subprocess.run([sys.executable, str(ROOT / "adapters" / script)], input=json.dumps(event),
                          capture_output=True, text=True, timeout=30, env=env)


@pytest.mark.parametrize("token", ["hook-token", None])
def test_the_pre_hook_sends_the_agent_token_when_it_has_one(recorder, token):
    url, seen = recorder
    done = run_hook("claude_code_hook.py", url, {"tool_name": "Read", "tool_input": {"file_path": "/tmp/x"},
                                                 "cwd": "/tmp", "session_id": "s"}, token)
    assert done.returncode == 0, done.stdout + done.stderr  # granted: the hook says nothing
    call = next(c for c in seen if c["path"] == "/api/request")
    assert call["token"] == token
    assert call["operator"] is None  # the agent's side never holds the person's token


@pytest.mark.parametrize("token", ["hook-token", None])
def test_the_post_hook_sends_the_agent_token_when_it_has_one(recorder, token):
    url, seen = recorder
    done = run_hook("claude_code_post_hook.py", url,
                    {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                     "tool_response": {"exit_code": 1}, "session_id": "s"}, token)
    assert done.returncode == 0, done.stdout + done.stderr
    call = next(c for c in seen if c["path"] == "/api/tool")
    assert call["token"] == token and call["body"]["failed"] == 1


@pytest.mark.parametrize("token", ["proxy-token", None])
def test_the_mcp_proxy_sends_the_agent_token_when_it_has_one(recorder, token, monkeypatch):
    import mcp_proxy

    url, seen = recorder
    monkeypatch.delenv("REFLEX_AGENT_TOKEN", raising=False)
    if token is not None:
        monkeypatch.setenv("REFLEX_AGENT_TOKEN", token)
    proxy = mcp_proxy.Proxy(url, "proxy-test", ["*"], fail_open=False)
    line = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "trash_email", "arguments": {"id": "m3"}}}).encode() + b"\n"
    assert proxy.allow(line) is True  # the recorder grants, so the call goes downstream
    paths = [c for c in seen if c["path"] == "/api/request"]
    assert paths and all(c["token"] == token for c in seen)
    assert any(c["path"] == "/api/tool" and c["body"].get("irreversible") == 1 for c in seen)


@pytest.mark.parametrize("token", ["wallet-token", None])
def test_the_bnbagent_wallet_sends_the_agent_token_when_it_has_one(recorder, token, monkeypatch):
    pytest.importorskip("bnbagent", reason="the BNBAgent SDK lives in its own venv")
    import bnbagent_boundary as bb
    from bnbagent.wallets.wallet_provider import WalletProvider

    class FakeWallet(WalletProvider):
        kind = "fake"

        @property
        def address(self) -> str:
            return "0x1111111111111111111111111111111111111111"

        def sign_message(self, message):
            return {"signature": "0xdead"}

    url, seen = recorder
    monkeypatch.delenv("REFLEX_AGENT_TOKEN", raising=False)
    if token is not None:
        monkeypatch.setenv("REFLEX_AGENT_TOKEN", token)
    wallet = bb.BoundaryWalletProvider(FakeWallet(), agent="wallet-test", console=url)
    assert wallet.sign_message("hello")["signature"] == "0xdead"  # granted by the recorder
    call = next(c for c in seen if c["path"] == "/api/request")
    assert call["token"] == token and call["operator"] is None
