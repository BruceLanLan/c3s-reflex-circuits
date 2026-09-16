"""End-to-end tests for adapters/mcp_proxy.py against the LIVE console.

The proxy is driven as a subprocess with a scripted client; the downstream is
tests/fake_mcp_server.py. The console at REFLEX_CONSOLE (default http://127.0.0.1:8765)
must be running: the module fixture installs the test policy (cooldown 8, nothing
else), and restores whatever was installed before when the module is done. Note that
installing a policy resets every agent's circuit state on that console, both times.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PROXY = HERE.parent / "adapters" / "mcp_proxy.py"
FAKE = HERE / "fake_mcp_server.py"
CONSOLE = os.environ.get("REFLEX_CONSOLE", "http://127.0.0.1:8765").rstrip("/")
TEST_POLICY = {"min_gap_ticks": 8, "commit_ticks": 0, "forbid_when_blocked": True,
               "max_grants": 0, "confirm_window_ticks": 0}
TIMEOUT = 5


def console(path: str, payload=None) -> dict:
    req = urllib.request.Request(f"{CONSOLE}{path}", data=None if payload is None else json.dumps(payload).encode(),
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read())


@pytest.fixture(scope="module", autouse=True)
def test_policy():
    try:
        before = console("/api/state")["policy"]["settings"]
    except (urllib.error.URLError, OSError) as e:
        pytest.skip(f"console not running at {CONSOLE}: {e}")
    console("/api/policy", TEST_POLICY)
    yield
    console("/api/policy", before)


class Client:
    """Launches the proxy around the fake server and talks JSON-RPC lines to it. A
    reader thread feeds stdout lines into a queue so a proxy bug cannot hang a test."""

    def __init__(self, *proxy_args: str, env: dict = None):
        cmd = [sys.executable, str(PROXY), *proxy_args, "--", sys.executable, str(FAKE)]
        full_env = dict(os.environ)
        full_env.pop("REFLEX_FAIL_OPEN", None)
        full_env.update(env or {})
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=full_env)
        self.lines: "queue.Queue[bytes]" = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self.next_id = 0

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.put(line)

    def send_raw(self, line: bytes):
        self.proc.stdin.write(line)
        self.proc.stdin.flush()

    def call(self, method: str, params=None) -> dict:
        self.next_id += 1
        msg = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            msg["params"] = params
        self.send_raw(json.dumps(msg).encode() + b"\n")
        raw = self.lines.get(timeout=TIMEOUT)
        reply = json.loads(raw)
        assert reply["id"] == self.next_id
        return reply

    def tool(self, name: str, **arguments) -> dict:
        return self.call("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> tuple[int, str]:
        self.proc.stdin.close()
        rc = self.proc.wait(timeout=TIMEOUT)  # the pump thread keeps draining stdout
        return rc, self.proc.stderr.read().decode("utf-8", "replace")


def fresh_agent() -> str:
    return f"mcp-test:{uuid.uuid4().hex[:8]}"


@pytest.fixture
def client():
    made = []

    def make(*proxy_args, **kw):
        c = Client(*proxy_args, **kw)
        made.append(c)
        return c

    yield make
    for c in made:
        if c.proc.poll() is None:
            c.proc.kill()


def text_of(reply: dict) -> str:
    return reply["result"]["content"][0]["text"]


def test_initialize_and_tools_list_pass_through(client):
    c = client("--agent", fresh_agent(), "--gate", "delete_everything")
    init = c.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "test", "version": "0"}})
    assert init["result"]["serverInfo"]["name"] == "fake-mcp-server"
    assert init["result"]["protocolVersion"] == "2025-06-18"
    c.send_raw(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
    tools = c.call("tools/list")
    assert [t["name"] for t in tools["result"]["tools"]] == ["echo", "delete_everything"]
    rc, _ = c.close()
    assert rc == 0


def test_ungated_call_is_forwarded(client):
    c = client("--agent", fresh_agent(), "--gate", "delete_everything")
    reply = c.tool("echo", text="hello")
    assert reply["result"]["isError"] is False
    assert text_of(reply) == "hello"
    c.close()


def test_gated_call_granted_then_refused_in_cooldown(client):
    agent = fresh_agent()
    c = client("--agent", agent, "--gate", "delete_*")
    first = c.tool("delete_everything")
    assert first["result"]["isError"] is False, first
    assert text_of(first) == "deleted"

    second = c.tool("delete_everything")
    assert second["result"]["isError"] is True, second
    assert "error" not in second  # a tool result, not a protocol error
    text = text_of(second)
    assert text.startswith("refused by the boundary at tick 2:")
    assert "cooldown" in text

    # the echo tool is not gated, so the refusal did not touch it
    assert text_of(c.tool("echo", text="still here")) == "still here"
    c.close()

    # the console's own record agrees: two ticks on this agent, one grant
    mine = [a for a in console("/api/state")["agents"] if a["agent"] == agent]
    assert mine and mine[0]["ticks"] == 2 and mine[0]["grants"] == 1, mine


def test_gate_all_is_the_default(client):
    c = client("--agent", fresh_agent())
    assert text_of(c.tool("echo", text="one")) == "one"
    refused = c.tool("echo", text="two")
    assert refused["result"]["isError"] is True
    assert "cooldown" in text_of(refused)
    c.close()


def test_gate_file(client, tmp_path):
    gates = tmp_path / "gates.txt"
    gates.write_text("# dangerous tools\ndelete_*   # glob\n\n")
    c = client("--agent", fresh_agent(), "--gate-file", str(gates))
    assert text_of(c.tool("delete_everything")) == "deleted"
    assert "cooldown" in text_of(c.tool("delete_everything"))
    assert text_of(c.tool("echo", text="free")) == "free"  # not in the file
    c.close()


def test_console_unreachable_refuses_unless_fail_open(client):
    c = client("--agent", fresh_agent(), env={"REFLEX_CONSOLE": "http://127.0.0.1:1"})
    refused = c.tool("delete_everything")
    assert refused["result"]["isError"] is True
    assert "unreachable" in text_of(refused)
    assert "REFLEX_FAIL_OPEN" in text_of(refused)
    # ungated traffic is untouched by a dead console
    assert c.call("tools/list")["result"]["tools"]
    c.close()

    c = client("--agent", fresh_agent(), env={"REFLEX_CONSOLE": "http://127.0.0.1:1", "REFLEX_FAIL_OPEN": "1"})
    allowed = c.tool("delete_everything")
    assert allowed["result"]["isError"] is False
    assert text_of(allowed) == "deleted"
    c.close()


def test_malformed_line_is_forwarded_unchanged(client):
    c = client("--agent", fresh_agent())
    c.send_raw(b"this is not json {\n")
    c.send_raw(b"\xff\xfe not even utf-8\n")
    # a real request afterwards still works, proving the stream was not derailed
    assert c.call("tools/list")["result"]["tools"]
    rc, err = c.close()
    assert rc == 0
    assert "unparsed: this is not json {" in err
    assert "unparsed: �� not even utf-8" in err


def test_exit_code_is_forwarded_and_stdout_stays_clean(client):
    c = client("--agent", fresh_agent())
    c.call("tools/list")
    rc, err = c.close()
    assert rc == 0
    assert c.lines.empty()  # nothing but answered requests ever reached stdout
    assert "Traceback" not in err
