#!/usr/bin/env python3
"""A stdio MCP proxy that asks the boundary console before a gated tool call reaches
the server.

The MCP client launches this instead of the server; this launches the server:

    python3 mcp_proxy.py [--agent NAME] [--gate TOOL ...] [--gate-all] [--gate-file PATH]
                         [--console URL] -- <downstream server command...>

Everything is relayed verbatim in both directions except one thing: a client->server
`tools/call` request whose tool name matches the gate list is one tick on the agent's
channel (request high, intent high, the tool name and a short line of its arguments as
the reason). If the circuit grants, the original line goes downstream unchanged. If it
refuses, the line never reaches the server and the client gets a JSON-RPC *result* in
the tool-result shape with `isError: true` and the rule's own words as the text — the
spec files tool execution failures under `isError` so the model can read the reason and
stop. A protocol error would be the wrong claim: it says the request was malformed, and
this one was well-formed — it was simply not allowed. The boundary only ever narrows
what may run, never widens it.

Framing, per https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
("stdio"): messages are individual JSON-RPC requests, notifications or responses,
UTF-8, "delimited by newlines, and MUST NOT contain embedded newlines"; the server MAY
log to stderr; nothing that is not a valid MCP message may go to stdout or the server's
stdin. So a line is the unit here. Lines are relayed as raw bytes and only *parsed* to
decide whether they are a gated call; anything unparseable is forwarded as it came,
because dropping it would be this proxy deciding protocol matters that are not its
business. The downstream's stderr is inherited, not captured, so its logs pass through.

`blocked` and `confirm` are not touched here on purpose. They belong to a layer the
agent cannot write: a person arms them from the console page or POST /api/tool. A
refusal for want of confirmation says so, and the person, not the model, resolves it.

Environment:
    REFLEX_CONSOLE   console URL (default http://127.0.0.1:8765); --console overrides
    REFLEX_AGENT     agent name (default mcp:<downstream basename>); --agent overrides
    REFLEX_FAIL_OPEN set to 1 to forward gated calls when the console is unreachable.
                     Off by default: a boundary that fails open is not one.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reflex_classes import CLASSES, classify, load_rules  # noqa: E402  (sibling file: which circuit answers)

LAUNCHERS =("python", "python3", "node", "npx", "uvx", "uv", "bun", "bunx", "deno", "npm", "pnpm", "yarn")


def describe(args: dict) -> str:
    """`k=v` for the first few scalar arguments, like the hook's describe()."""
    return ", ".join(f"{k}={v}" for k, v in list(args.items())[:3] if isinstance(v, (str, int, float)))


def default_agent(command: list[str]) -> str:
    """mcp:<downstream basename>. Launchers (npx, uvx, python...) are skipped so two
    servers started through the same launcher do not share one channel; pass --agent
    when even that guess is wrong."""
    for word in command:
        base = os.path.basename(word)
        if word.startswith("-") or base in LAUNCHERS or base.startswith("python"):
            continue
        return f"mcp:{base}"
    return f"mcp:{os.path.basename(command[0])}"


def load_gates(args: argparse.Namespace) -> list[str]:
    gates = list(args.gate or [])
    if args.gate_file:
        with open(args.gate_file, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.split("#", 1)[0].strip()
                if line:
                    gates.append(line)
    if args.gate_all or not gates:
        gates.append("*")
    return gates


class Proxy:
    def __init__(self, console: str, agent: str, gates: list[str], fail_open: bool,
                 fixed_class: Optional[str] = None, class_file: Optional[str] = None) -> None:
        self.console = console.rstrip("/")
        self.agent = agent
        self.gates = gates
        self.fail_open = fail_open
        # Which circuit answers a gated call: one class for everything (--class), or by
        # the tool's name (built-in rules, or --class-file). Decided here, from the name
        # the server published, never from the model.
        self.fixed_class = fixed_class
        self.class_rules = load_rules(class_file)
        self.out_lock = threading.Lock()  # relayed lines and refusals share one stdout

    # -- the two directions ---------------------------------------------------------

    def to_client(self, line: bytes) -> None:
        with self.out_lock:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()

    def client_to_server(self, proc: subprocess.Popen) -> None:
        """Runs in a daemon thread: a dead downstream must not leave us blocked here."""
        stdin = sys.stdin.buffer
        try:
            while True:
                line = stdin.readline()
                if not line:
                    break
                if self.allow(line):
                    proc.stdin.write(line)
                    proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass  # downstream went away; the relay thread reports its exit code
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()

    def server_to_client(self, proc: subprocess.Popen) -> None:
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            self.to_client(line)

    # -- the gate -------------------------------------------------------------------

    def allow(self, line: bytes) -> bool:
        """True if the line may go downstream. Only a gated `tools/call` can be held
        back; every other line, malformed ones included, is forwarded as it came."""
        try:
            msg = json.loads(line)
        except ValueError:  # includes UnicodeDecodeError
            return True
        if not isinstance(msg, dict) or msg.get("method") != "tools/call":
            return True
        params = msg.get("params")
        if not isinstance(params, dict):
            params = {}
        name = str(params.get("name", ""))
        if not any(fnmatch.fnmatchcase(name, g) for g in self.gates):
            return True
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        cls = self.fixed_class or classify(name, self.class_rules)
        reason = f"[{cls}] {name}: {describe(arguments)}"[:160]

        refusal = self.ask(reason, cls)
        if refusal is None:
            return True
        if "id" not in msg:
            # A tools/call without an id is a notification; nothing can answer it.
            # Refusing means not forwarding, and saying so where a person can see it.
            sys.stderr.write(f"mcp_proxy: dropped un-answerable tools/call {name!r}: {refusal}\n")
            sys.stderr.flush()
            return False
        self.to_client(json.dumps({
            "jsonrpc": "2.0",
            "id": msg["id"],
            "result": {"content": [{"type": "text", "text": refusal}], "isError": True},
        }, ensure_ascii=False).encode("utf-8") + b"\n")
        return False

    def ask(self, reason: str, cls: str) -> Optional[str]:
        """One tick of the class's circuit. None if granted, else the refusal text."""
        body = json.dumps({"agent": self.agent, "intent": 1, "reason": reason, "class": cls}).encode()
        req = urllib.request.Request(f"{self.console}/api/request", data=body,
                                     headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                verdict = json.loads(resp.read())
        except (urllib.error.URLError, OSError, ValueError) as e:
            if self.fail_open:
                return None
            return (f"refused by the boundary: console unreachable ({e}); refusing rather than "
                    f"running unchecked. Start it, or set REFLEX_FAIL_OPEN=1 to accept running without it.")
        if verdict.get("granted"):
            return None
        why_list = verdict.get("why") or []
        why = "; ".join(why_list) or "the circuit did not grant"
        hint = ""
        if any(w.startswith("no confirmation") for w in why_list):
            hint = " A person can arm a confirmation from the console page; the model cannot."
        if any(w.startswith("blocked") for w in why_list):
            hint = " The tool layer has blocked this agent; only it can lift that."
        return f"refused by the boundary at tick {verdict.get('tick')}: {why}.{hint}"


def parse(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    # Split at the first `--` ourselves: the downstream has flags of its own, and
    # argparse.REMAINDER would try to own them.
    if "--" in argv:
        cut = argv.index("--")
        ours, command = argv[:cut], argv[cut + 1:]
    else:
        ours, command = argv, []
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0],
                                usage="%(prog)s [options] -- <downstream server command...>")
    p.add_argument("--agent", help="agent name on the console (default mcp:<downstream basename>)")
    p.add_argument("--gate", action="append", metavar="NAME",
                   help="tool name or fnmatch glob to gate; repeatable")
    p.add_argument("--gate-all", action="store_true", help="gate every tool (the default with no --gate)")
    p.add_argument("--gate-file", metavar="PATH", help="one tool name or glob per line, # comments")
    p.add_argument("--console", default=os.environ.get("REFLEX_CONSOLE", "http://127.0.0.1:8765"))
    p.add_argument("--class", dest="fixed_class", choices=CLASSES, metavar="CLASS",
                   help="answer every gated call with this class's circuit (spend|message|exec|files)")
    p.add_argument("--class-file", metavar="PATH",
                   help="one `glob class` per line deciding the class by tool name; default: built-in rules")
    args = p.parse_args(ours)
    if not command:
        p.error("no downstream command: put it after `--`")
    return args, command


def main() -> None:
    args, command = parse(sys.argv[1:])
    agent = args.agent or os.environ.get("REFLEX_AGENT") or default_agent(command)
    proxy = Proxy(args.console, agent, load_gates(args), os.environ.get("REFLEX_FAIL_OPEN") == "1",
                  fixed_class=args.fixed_class, class_file=args.class_file)
    try:
        # stderr=None: the server's stderr is ours, so its logs pass straight through.
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None)
    except OSError as e:
        sys.stderr.write(f"mcp_proxy: cannot start {command[0]!r}: {e}\n")
        sys.exit(127)
    threading.Thread(target=proxy.client_to_server, args=(proc,), daemon=True).start()
    proxy.server_to_client(proc)
    rc = proc.wait()
    sys.exit(rc if rc >= 0 else 128 - rc)


if __name__ == "__main__":
    main()
