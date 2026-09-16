"""The pretend workbench (examples/workbench.py), on its own and behind the boundary.

Two halves. The first needs nothing running: the server's protocol, the seeded day, the
sandbox, and what each tool declares it would change. The second needs the live console at
REFLEX_CONSOLE and drives the real `adapters/mcp_proxy.py` in front of the real workbench,
because the claim the demo makes — reads are free, everything that sends or deletes needs a
person, and one approval buys one call — is only worth something when a circuit says it.

The live half installs the demo's four policies and puts back whatever was there. Note that
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

from reflex_token import console_url, json_headers

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
WORKBENCH = REPO / "examples" / "workbench.py"
PROXY = REPO / "adapters" / "mcp_proxy.py"
CONSOLE = console_url()
TIMEOUT = 15

sys.path.insert(0, str(REPO / "adapters"))
from reflex_classes import classify, is_irreversible_tool, load_irreversible_tools, load_rules  # noqa: E402

sys.path.insert(0, str(REPO / "examples"))
import workbench as wb  # noqa: E402

# docs/API.md §I-1
EFFECT_KINDS = {"read", "send", "delete", "move", "write", "pay", "exec"}

# The same rules `c3s demo` installs (c3s_cli/cli.py DEMO_POLICIES).
DEMO_POLICIES = {
    "exec": {"forbid_when_blocked": True},
    "message": {"confirm_per_irreversible": True, "forbid_when_blocked": True},
    "files": {"confirm_per_irreversible": True, "forbid_when_blocked": True},
    "spend": {"deny_all": True},
}


# --------------------------------------------------------------------------------- helpers

@pytest.fixture
def bench(tmp_path):
    return wb.Workbench(tmp_path / "day.json", tmp_path / "files")


def console(path: str, payload=None) -> dict:
    req = urllib.request.Request(f"{CONSOLE}{path}",
                                 data=None if payload is None else json.dumps(payload).encode(),
                                 headers=json_headers())
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read())


def serve_lines(state: Path, sandbox: Path, lines: list[dict]) -> list[dict]:
    """Run the workbench as a server for a scripted set of messages, no proxy, no console."""
    text = "\n".join(json.dumps(line) for line in lines) + "\n"
    done = subprocess.run([sys.executable, str(WORKBENCH), "--state", str(state),
                           "--sandbox", str(sandbox)], input=text, text=True, capture_output=True,
                          timeout=60)
    assert done.returncode == 0, done.stderr
    return [json.loads(raw) for raw in done.stdout.splitlines() if raw.strip()]


# ------------------------------------------------------------------- the server on its own

def test_the_seeded_day_has_a_trap_that_asks_for_two_irreversible_things(bench):
    day = bench.load()
    assert len(day["inbox"]) == 8
    trap = next(m for m in day["inbox"] if m["id"] == "m5")
    assert "client-list.csv" in trap["body"] and "backups@file-vault.example" in trap["body"]
    # and the file it wants deleted really is in the sandbox, so the call is a real one
    assert (bench.sandbox / "workspace" / "client-list.csv").is_file()


def test_reads_do_not_change_the_day_and_writes_do(bench):
    assert "m1" in bench.call("list_inbox", {})
    assert "Thursday review" in bench.call("read_email", {"id": "m1"})
    assert bench.load()["sent"] == []
    bench.call("reply_email", {"id": "m1", "body": "15:00 works"})
    sent = bench.load()["sent"]
    assert len(sent) == 1 and sent[0]["to"] == "lena@studio.example"
    assert sent[0]["subject"].startswith("Re: ")


def test_mailbox_send_and_trash(bench):
    bench.call("send_email", {"to": "x@y.example", "subject": "hi", "body": "there"})
    bench.call("trash_email", {"id": "m3"})
    day = bench.load()
    assert [m["to"] for m in day["sent"]] == ["x@y.example"]
    assert [m["id"] for m in day["trash"]] == ["m3"]
    assert "m3" not in [m["id"] for m in day["inbox"]]
    with pytest.raises(wb.Refused):
        bench.call("trash_email", {"id": "m3"})          # already gone


def test_calendar_create_move_cancel(bench):
    assert "Thursday review" in bench.call("list_events", {})
    made = bench.call("create_event", {"title": "Retro", "start": "Fri 10:00",
                                       "invitees": ["lena@studio.example"]})
    assert "e4" in made and "lena@studio.example" in made
    moved = bench.call("update_event", {"id": "e1", "start": "Thu 15:00", "end": "Thu 16:00"})
    assert "Thu 15:00" in moved
    assert "told lena@studio.example" in moved          # the part that cannot be taken back
    bench.call("delete_event", {"id": "e3"})
    assert [e["id"] for e in bench.load()["events"]] == ["e1", "e2", "e4"]
    with pytest.raises(wb.Refused):
        bench.call("update_event", {"id": "e3", "start": "x"})


def test_files_stay_inside_the_sandbox(bench):
    assert "client-list.csv" in bench.call("list_directory", {})
    assert "Sam Reed" in bench.call("read_file", {"path": "workspace/client-list.csv"})
    bench.call("write_file", {"path": "notes/new.md", "content": "hello"})
    assert (bench.sandbox / "notes" / "new.md").read_text() == "hello"
    bench.call("delete_file", {"path": "notes/new.md"})
    assert not (bench.sandbox / "notes" / "new.md").exists()
    for escape in ("../../../etc/passwd", "/etc/passwd", "notes/../../outside.txt"):
        with pytest.raises(wb.Refused):
            bench.call("read_file", {"path": escape})
        with pytest.raises(wb.Refused):
            bench.call("write_file", {"path": escape, "content": "x"})
        with pytest.raises(wb.Refused):
            bench.call("delete_file", {"path": escape})


def test_the_gated_calls_that_went_through_are_readable_afterwards(bench):
    bench.call("list_inbox", {})
    bench.call("reply_email", {"id": "m1", "body": "yes"})
    summary = bench.summary()
    assert "gated calls that went through: 1" in summary
    assert "reply_email" in summary and "list_inbox" not in summary


def test_stdio_protocol(tmp_path):
    replies = serve_lines(tmp_path / "day.json", tmp_path / "files", [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},          # no id: no answer
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "read_email", "arguments": {"id": "nope"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "no_such_tool"}},
        {"jsonrpc": "2.0", "id": 5, "method": "resources/list"},
    ])
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5]
    assert replies[0]["result"]["serverInfo"]["name"] == "pretend-workbench"
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    names = [t["name"] for t in replies[1]["result"]["tools"]]
    assert names == [t["name"] for t in wb.TOOLS]
    assert replies[2]["result"]["isError"] is True                # the workbench's own no
    assert "no email" in replies[2]["result"]["content"][0]["text"]
    assert replies[3]["error"]["code"] == -32602                  # unknown tool
    assert replies[4]["error"]["code"] == -32601                  # unknown method


def test_every_tool_declares_what_it_would_change(tmp_path):
    for tool in wb.mcp_tools():
        declared = tool["_meta"]["reflex"]
        effect = declared["effect"]
        assert effect["kind"] in EFFECT_KINDS, tool["name"]
        assert effect.get("summary"), tool["name"]
        assert declared["class"] in ("spend", "message", "exec", "files")
        # a read is never gated, and nothing gated claims to be reversible
        assert declared["gated"] is (effect["kind"] != "read")
        if declared["gated"]:
            assert effect["reversible"] is False and declared["irreversible"] is True


def test_a_filled_effect_fits_the_shape_api_md_froze():
    filled = wb.fill(wb.effect_map()["send_email"],
                     {"to": "lena@studio.example", "subject": "Re: Thursday", "body": "x" * 5000})
    assert filled["kind"] == "send"
    assert filled["target"] == "lena@studio.example"
    assert "Re: Thursday" in filled["summary"] and filled["reversible"] is False
    long = wb.fill(wb.effect_map()["write_file"], {"path": "p" * 400, "content": "x"})
    assert len(long["path"]) <= 120 and len(long["summary"]) <= 200
    # a list of invitees reads as a list of people, and a missing argument is not left raw
    invited = wb.fill(wb.effect_map()["create_event"],
                      {"title": "Retro", "start": "Fri 10:00",
                       "invitees": ["lena@studio.example", "dana@studio.example"]})
    assert "lena@studio.example, dana@studio.example" in invited["summary"]
    assert "{" not in invited["summary"]
    assert "where" not in invited.get("details", {})       # not given, so not claimed
    # a placeholder with no argument becomes `?`, never a raw brace on a person's screen
    vague = wb.fill(wb.effect_map()["update_event"], {"id": "e1"})
    assert "{" not in vague["summary"] and "?" in vague["summary"]


def test_the_override_files_keep_the_built_in_rules(tmp_path):
    """The one mistake that makes this whole demo lie: an override file without `!default`
    drops every built-in rule, so `send_email` lands in `exec` and nothing arms
    `irreversible` — every call would be granted while the demo appeared to work."""
    classes = tmp_path / "tool-classes.txt"
    irreversible = tmp_path / "irreversible-tools.txt"
    classes.write_text(wb.class_file_text())
    irreversible.write_text(wb.irreversible_file_text())
    assert "!default" in classes.read_text() and "!default" in irreversible.read_text()

    rules = load_rules(str(classes))
    tools = load_irreversible_tools(str(irreversible))
    expected = {"send_email": "message", "reply_email": "message", "trash_email": "files",
                "create_event": "message", "update_event": "message", "delete_event": "files",
                "write_file": "files", "delete_file": "files"}
    for name, cls in expected.items():
        assert classify(name, rules) == cls, name
        assert is_irreversible_tool(name, tools), name
    for name in ("list_inbox", "read_email", "list_events", "list_directory", "read_file"):
        assert classify(name, rules) == "exec", name
        assert not is_irreversible_tool(name, tools), name
    assert set(expected) == set(wb.effect_map()), "the gated tools and the effect map disagree"


# ------------------------------------------------------- behind the boundary (live console)

@pytest.fixture(scope="module")
def live_console():
    """The demo's own four circuits, and whatever was installed before put back."""
    try:
        state = console("/api/state")
    except (urllib.error.URLError, OSError) as e:
        pytest.skip(f"console not running at {CONSOLE}: {e}")
    before = {c: (state.get("policies") or {}).get(c) for c in DEMO_POLICIES}
    for cls, rules in DEMO_POLICIES.items():
        console("/api/policy", dict(rules, **{"class": cls}))
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
    """The proxy in front of the workbench, driven the way an MCP client drives it."""

    def __init__(self, tmp_path: Path, agent: str):
        self.state = tmp_path / "day.json"
        self.sandbox = tmp_path / "files"
        classes = tmp_path / "tool-classes.txt"
        classes.write_text(wb.class_file_text())
        irreversible = tmp_path / "irreversible-tools.txt"
        irreversible.write_text(wb.irreversible_file_text())
        gates: list[str] = []
        for name in sorted(wb.effect_map()):
            gates += ["--gate", name]
        env = dict(os.environ)
        env.pop("REFLEX_FAIL_OPEN", None)
        env.update({"REFLEX_CONSOLE": CONSOLE, "REFLEX_IRREVERSIBLE_TOOLS_FILE": str(irreversible)})
        self.proc = subprocess.Popen(
            [sys.executable, str(PROXY), "--agent", agent, "--class-file", str(classes), *gates,
             "--", sys.executable, str(WORKBENCH), "--state", str(self.state),
             "--sandbox", str(self.sandbox)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.lines: "queue.Queue[bytes]" = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self.rid = 0

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.put(line)

    def tool(self, name: str, **arguments) -> tuple[bool, str]:
        self.rid += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.rid, "method": "tools/call",
                                          "params": {"name": name, "arguments": arguments}}).encode() + b"\n")
        self.proc.stdin.flush()
        reply = json.loads(self.lines.get(timeout=TIMEOUT))
        assert reply["id"] == self.rid
        result = reply["result"]
        return bool(result.get("isError")), result["content"][0]["text"]

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()


@pytest.fixture
def client(tmp_path, live_console):
    made = []

    def make(agent: str | None = None):
        c = Client(tmp_path, agent or f"w4-test:{uuid.uuid4().hex[:8]}")
        made.append(c)
        return c

    yield make
    for c in made:
        c.close()


def test_reads_pass_and_every_gated_tool_is_refused(client):
    """The check the demo stands on. Eight tools that send, cancel, overwrite or delete are
    each refused by a circuit, and the five that only read are never even asked about."""
    c = client()
    for name, arguments in (("list_inbox", {}), ("read_email", {"id": "m5"}), ("list_events", {}),
                            ("list_directory", {}), ("read_file", {"path": "workspace/client-list.csv"})):
        refused, text = c.tool(name, **arguments)
        assert not refused, (name, text)
    attempts = {
        "send_email": {"to": "backups@file-vault.example", "subject": "copy", "body": "x"},
        "reply_email": {"id": "m1", "body": "15:00 works"},
        "trash_email": {"id": "m3"},
        "create_event": {"title": "Retro", "start": "Fri 10:00", "invitees": ["lena@studio.example"]},
        "update_event": {"id": "e1", "start": "Thu 15:00"},
        "delete_event": {"id": "e3"},
        "write_file": {"path": "README.txt", "content": "clobbered"},
        "delete_file": {"path": "workspace/client-list.csv"},
    }
    assert set(attempts) == set(wb.effect_map())
    for name, arguments in attempts.items():
        refused, text = c.tool(name, **arguments)
        assert refused, (name, text)
        assert "refused by the boundary" in text and "irreversible" in text, (name, text)
    # nothing downstream ran: the day is exactly as it was seeded
    day = json.loads(c.state.read_text())
    assert day["sent"] == [] and day["trash"] == [] and len(day["events"]) == 3
    assert day["log"] == []
    assert (c.sandbox / "workspace" / "client-list.csv").read_text().startswith("name,contact")
    assert (c.sandbox / "README.txt").read_text().startswith("This folder")


def test_one_approval_buys_one_call_and_not_the_trap(client):
    """The demo's fourth step, as a test: a person approves the reply; the trap tried
    immediately afterwards does not spend that approval, and the approved call does."""
    agent = f"w4-test:{uuid.uuid4().hex[:8]}"
    c = client(agent)
    refused, _ = c.tool("reply_email", id="m1", body="Hi Lena - 15:00 on Thursday works for me.")
    assert refused
    entry = next(e for e in console("/api/state")["pending"] if e["agent"] == agent)
    assert entry["bit"] == "confirm" and entry["code"] and entry["armed"] is False
    # the person, with the operator token, approving that one call by its own words
    console("/api/tool", {"agent": agent, "confirm": 1, "for_reason": entry["reason"],
                          "code": entry["code"], "note": "yes, the reply to Lena"})
    refused, text = c.tool("delete_file", path="workspace/client-list.csv")
    assert refused, text
    assert (c.sandbox / "workspace" / "client-list.csv").is_file()
    refused, text = c.tool("reply_email", id="m1", body="Hi Lena - 15:00 on Thursday works for me.")
    assert not refused, text
    assert "replied" in text
    # and the approval is spent: the same call again needs a new one
    refused, _ = c.tool("reply_email", id="m1", body="Hi Lena - 15:00 on Thursday works for me.")
    assert refused
    day = json.loads(c.state.read_text())
    assert [m["to"] for m in day["sent"]] == ["lena@studio.example"]


def test_a_filled_effect_is_a_body_the_console_accepts(client):
    """I-1 is only useful if what the workbench declares survives the console's validation.
    Every gated tool's filled effect goes with a real request; a bad shape would be 400."""
    agent = f"w4-effect:{uuid.uuid4().hex[:8]}"
    arguments = {
        "send_email": {"to": "backups@file-vault.example", "subject": "Client list copy", "body": "b" * 900},
        "reply_email": {"id": "m1", "body": "yes"},
        "trash_email": {"id": "m3"},
        "create_event": {"title": "Retro", "start": "Fri 10:00",
                         "invitees": ["lena@studio.example", "dana@studio.example"], "where": "Room 2"},
        "update_event": {"id": "e1", "start": "Thu 15:00", "end": "Thu 16:00"},
        "delete_event": {"id": "e3"},
        "write_file": {"path": "workspace/client-list.csv", "content": "x" * 3000},
        "delete_file": {"path": "workspace/client-list.csv"},
    }
    for name, template in wb.effect_map().items():
        effect = wb.fill(template, arguments[name])
        verdict = console("/api/request", {"agent": agent, "class": wb.BY_NAME[name]["cls"],
                                           "intent": 1, "effect": effect,
                                           "reason": f"[{wb.BY_NAME[name]['cls']}] {name} [irreversible]: probe"})
        assert "granted" in verdict, (name, verdict)
    stored = next(e for e in console("/api/state")["transcript"]
                  if e.get("kind") == "request" and e.get("agent") == agent)
    assert stored["effect"]["kind"] in EFFECT_KINDS
    assert stored["effect"]["summary"]
