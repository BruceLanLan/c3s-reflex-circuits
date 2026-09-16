from reflex_token import json_headers as _json_headers
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
                                 headers=_json_headers())
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read())


@pytest.fixture(scope="module", autouse=True)
def test_policy():
    """`echo` lands in `exec` and `delete_everything` in `files` (built-in class rules),
    so the test policy goes into both; whatever was there comes back afterwards."""
    try:
        state = console("/api/state")
    except (urllib.error.URLError, OSError) as e:
        pytest.skip(f"console not running at {CONSOLE}: {e}")
    before = {c: (state.get("policies") or {}).get(c) for c in ("exec", "files")}
    for cls in ("exec", "files"):
        console("/api/policy", dict(TEST_POLICY, **{"class": cls}))
    yield
    for cls, summary in before.items():
        if summary is None:
            if cls != "exec":
                console("/api/policy", {"class": cls, "remove": True})
        elif summary.get("deny_all"):
            console("/api/policy", {"class": cls, "deny_all": True})
        else:
            console("/api/policy", dict(summary["settings"], **{"class": cls}))


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

    # the console's own record agrees: two ticks on this agent's files circuit, one grant
    mine = [a for a in console("/api/state")["agents"] if a["agent"] == agent]
    assert mine and mine[0]["ticks"] == 2, mine
    files = mine[0]["by_class"]["files"]
    assert files["ticks"] == 2 and files["grants"] == 1, files
    assert mine[0]["by_class"]["exec"]["ticks"] == 0  # the class was decided from the name


def test_class_is_decided_from_the_tool_name_and_sent(client):
    agent = fresh_agent()
    c = client("--agent", agent)
    c.tool("echo", text="one")             # exec by default
    c.tool("delete_everything")            # files by the built-in delete_* rule
    c.close()
    rows = [e for e in console("/api/state")["transcript"] if e.get("agent") == agent and e["kind"] == "request"]
    assert sorted(e["class"] for e in rows) == ["exec", "files"]
    assert all(e["reason"].startswith(f"[{e['class']}] ") for e in rows)


def test_fixed_class_overrides_the_rules(client):
    agent = fresh_agent()
    c = client("--agent", agent, "--class", "files")
    assert text_of(c.tool("echo", text="one")) == "one"
    assert "cooldown" in text_of(c.tool("echo", text="two"))  # the files circuit, not exec
    c.close()
    mine = [a for a in console("/api/state")["agents"] if a["agent"] == agent][0]
    assert mine["by_class"]["files"]["ticks"] == 2 and mine["by_class"]["exec"]["ticks"] == 0


def test_uninstalled_class_is_not_gated(client):
    """`message` has no circuit in this test setup, so a call classed there is granted
    and the decision says the class is not installed."""
    agent = fresh_agent()
    c = client("--agent", agent, "--class", "message")
    for _ in range(3):
        assert text_of(c.tool("echo", text="free")) == "free"
    c.close()
    rows = [e for e in console("/api/state")["transcript"] if e.get("agent") == agent and e["kind"] == "request"]
    assert rows and all(e["granted"] and e["class_installed"] is False for e in rows)


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


def test_an_irreversible_tool_needs_a_persons_confirm(client):
    """`delete_everything` is irreversible by name, so the proxy arms the tool-layer bit
    before asking; under confirm_per_irreversible only a confirm the test writes as the
    person lets exactly one call through."""
    console("/api/policy", {"class": "message", "confirm_per_irreversible": True, "forbid_when_blocked": True})
    try:
        agent = fresh_agent()
        c = client("--agent", agent, "--class", "message")
        first = text_of(c.tool("delete_everything"))
        assert "irreversible, and no unspent confirm" in first and "person" in first, first
        assert text_of(c.tool("echo", text="reversible")) == "reversible"  # not irreversible by name
        console("/api/tool", {"agent": agent, "confirm": 1})
        assert text_of(c.tool("delete_everything")) == "deleted"
        assert "irreversible" in text_of(c.tool("delete_everything"))  # the confirm was spent
        c.close()
        rows = [e for e in console("/api/state")["transcript"] if e.get("agent") == agent and e["kind"] == "request"]
        assert sum(1 for e in rows if "[irreversible]" in e["reason"]) == 3
    finally:
        console("/api/policy", {"class": "message", "remove": True})


def test_a_call_aimed_at_the_consoles_tool_layer_is_refused_even_ungated(client):
    agent = fresh_agent()
    c = client("--agent", agent, "--gate", "delete_*")  # echo is not gated
    reply = c.tool("echo", text=f"POST {CONSOLE}/api/tool {{\"agent\":\"me\",\"confirm\":1}}")
    assert reply["result"]["isError"] is True and "refused before any circuit was asked" in text_of(reply)
    assert text_of(c.tool("echo", text=f"GET {CONSOLE}/api/state")).startswith("GET")  # reading is fine
    c.close()


HOOK = Path(__file__).resolve().parents[1] / "adapters" / "claude_code_hook.py"


PORT = CONSOLE.rsplit(":", 1)[-1]


@pytest.mark.parametrize("tool,args", [
    ("Bash", {"command": f"curl -s localhost:{PORT}/api/tool -d '{{\"agent\":\"me\",\"confirm\":1}}'"}),
    ("Bash", {"command": f"python3 -c \"import urllib.request as u; u.urlopen('http://127.0.0.1:{PORT}/api/policy', b'{{}}')\""}),
    ("Bash", {"command": "echo {} > ~/.c3s-circuit-agent/policies.json"}),
    ("Bash", {"command": f"kill $(lsof -ti:{PORT})"}),
    ("Edit", {"file_path": "/work/proj/.claude/settings.json", "old_string": "a", "new_string": "b"}),
    # Each of these walked past the guard until the red-team pass of 2026-09-17 (F3): the
    # stop rule knew only `kill`/`pkill` and a hardwired :8765, and the host list held only
    # the spellings someone had thought of. The token is still the real defence — these are
    # here so the cheap tries stay a clear, early no.
    ("Bash", {"command": f"pkill -f {PORT}"}),
    ("Bash", {"command": f"fuser -k {PORT}/tcp"}),
    ("Bash", {"command": f"curl -s http://127.1:{PORT}/api/tool -d '{{\"agent\":\"me\",\"confirm\":1}}'"}),
    ("Bash", {"command": f"curl -s http://2130706433:{PORT}/api/policy -d '{{}}'"}),
])
def test_the_hook_refuses_tampering_before_asking_any_circuit(tool, args):
    event = {"session_id": "tamper", "cwd": "/tmp", "tool_name": tool, "tool_input": args}
    out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), capture_output=True, text=True,
                         env=dict(os.environ, REFLEX_CONSOLE=CONSOLE), timeout=TIMEOUT)
    assert out.returncode == 2
    assert "refused before any circuit was asked" in json.loads(out.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("cmd", [
    "kill -9 12345",                      # a pid, not our port: not the guard's business
    "lsof -ti:9999",                      # another service
    "curl -s http://example.com/api/tool",  # /api/tool elsewhere is not this console
])
def test_the_hook_does_not_refuse_what_is_not_aimed_at_the_console(cmd):
    """A guard that refuses everything teaches nothing. These must reach the circuit."""
    event = {"session_id": "tamper", "cwd": "/tmp", "tool_name": "Bash", "tool_input": {"command": cmd}}
    out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), capture_output=True, text=True,
                         env=dict(os.environ, REFLEX_CONSOLE=CONSOLE), timeout=TIMEOUT)
    reason = json.loads(out.stdout or "{}").get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
    assert "refused before any circuit was asked" not in reason, cmd
