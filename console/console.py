"""Boundary console: rules compiled to a circuit, and every decision made by it.

    C3S_REPO=~/work/c3s-reflex python console.py        # http://127.0.0.1:8765

You write rules. They compile to a NAND/LATCH circuit, which is checked against a
plain-Python statement of the same rules on every row of its domain, and each rule is
then proven by visiting every state the circuit can reach from reset. Agents make
requests against that circuit; it decides; optionally a public BNB Smart Chain node
re-evaluates the deciding row read-only, with nothing deployed.

The default rules are the ones the fruit-fly escape circuit was already proven to obey
(docs/PROPERTIES.md): at most one grant in any 8 ticks, four ticks of commitment before
one, nothing while blocked. That is where they come from; everything after that is the
user's to set.

Two endpoints on purpose, because the separation is the whole point:

    POST /api/request   request, intent   — what an agent may write
    POST /api/tool      blocked, confirm  — what only the layer above it may write

A rule resting on the agent's own bits is a cost it can choose to pay; a rule resting
on the tool layer's bits is a boundary. Nothing here can enforce which caller is which:
that is a property of how this is deployed, and the page says so.

No wallet, no key, nothing signed, nothing broadcast, no action performed.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(os.environ.get("C3S_REPO", Path.home() / "work" / "c3s-reflex")).expanduser()
HOST = os.environ.get("CONSOLE_HOST", "127.0.0.1")
PORT = int(os.environ.get("CONSOLE_PORT", "8765"))
VERIFY_ON_CHAIN = os.environ.get("VERIFY_ON_CHAIN", "1") not in ("0", "", "no")
BSC_RPC = os.environ.get("BSC_RPC", "https://bsc-rpc.publicnode.com")
CHAIN_ADDRESS = "0x000000000000000000000000000000000000c3f5"
STATIC = Path(__file__).resolve().parent / "static"
TRANSCRIPT = 200

sys.path.insert(0, str(REPO))
from c3s import exhaust  # noqa: E402
from c3s.netlist import to_bytes  # noqa: E402
from c3s.policy import AGENT_WRITABLE, INPUT_NAMES, MUST_COME_FROM_THE_TOOL_LAYER, Policy  # noqa: E402

# The rules the escape circuit itself was proven to obey; the starting point, not a law.
FLY_DEFAULT = Policy(min_gap_ticks=8, commit_ticks=4, forbid_when_blocked=True)
TRANSCRIPTS: deque = deque(maxlen=TRANSCRIPT)
STARTED_AT = time.time()


class Boundary:
    """One compiled policy, its proofs, and one circuit state per agent."""

    def __init__(self, policy: Policy) -> None:
        self.lock = threading.Lock()
        self.install(policy)

    def install(self, policy: Policy) -> dict:
        """Compile, check on every row, prove every rule, then switch to it."""
        t0 = time.time()
        circuit = policy.build()
        verify = policy.verify(circuit)
        proofs = policy.properties(circuit)
        outs, nxt = exhaust.step_table(circuit)
        netlist = to_bytes(circuit)
        summary = {
            "rules": policy.describe(),
            "settings": {
                "min_gap_ticks": policy.min_gap_ticks,
                "commit_ticks": policy.commit_ticks,
                "forbid_when_blocked": policy.forbid_when_blocked,
                "max_grants": policy.max_grants,
                "confirm_window_ticks": policy.confirm_window_ticks,
            },
            "circuit": {
                "nand": verify["metrics"]["nand"],
                "latch": verify["metrics"]["latch"],
                "bytes": len(netlist),
                "depth": verify["metrics"]["depth"],
                "netlist": "0x" + netlist.hex(),
                "inputs": list(INPUT_NAMES),
                "agent_writable": list(AGENT_WRITABLE),
                "tool_layer_only": list(MUST_COME_FROM_THE_TOOL_LAYER),
            },
            "checked": {
                "rows": verify["rows"],
                "domain_bits": verify["domain_bits"],
                "matches_reference": verify["outputs_match"] and verify["next_state_matches"],
                "reachable_states": proofs["reachable_states"],
                "states_possible": proofs["states_possible"],
                "configurations_visited": proofs["configurations_visited"],
                "rows_proven": proofs["rows_checked"],
                "every_rule_holds": proofs["holds"],
                "violations": proofs["violations"],
            },
            "compiled_in_ms": round((time.time() - t0) * 1000),
        }
        with self.lock:
            self.policy, self.circuit, self.outs, self.nxt = policy, circuit, outs, nxt
            self.netlist, self.summary = netlist, summary
            self.agents: dict[str, dict] = {}
        return summary

    def agent(self, name: str) -> dict:
        return self.agents.setdefault(
            name, {"state": 0, "ticks": 0, "grants": 0, "blocked": 0, "confirm": 0, "last_grant_tick": None}
        )

    def arm(self, name: str, blocked: int | None, confirm: int | None) -> dict:
        """The tool layer's bits, held until the agent's next request consumes them."""
        with self.lock:
            a = self.agent(name)
            if blocked is not None:
                a["blocked"] = int(bool(blocked))
            if confirm is not None:
                a["confirm"] = int(bool(confirm))
            entry = {
                "at": time.time(),
                "kind": "tool",
                "agent": name,
                "blocked": a["blocked"],
                "confirm": a["confirm"],
            }
            TRANSCRIPTS.appendleft(entry)
            return entry

    def request(self, name: str, intent: int, reason: str) -> dict:
        """One tick: the agent asks, the circuit answers."""
        n_in = len(INPUT_NAMES)
        with self.lock:
            a = self.agent(name)
            inputs = 1 | (int(bool(intent)) << 1) | (a["blocked"] << 2) | (a["confirm"] << 3)
            state_before = a["state"]
            row = inputs | (state_before << n_in)
            grant = int(self.outs[row])
            a["state"] = int(self.nxt[row])
            a["ticks"] += 1
            gap = None
            if grant:
                if a["last_grant_tick"] is not None:
                    gap = a["ticks"] - a["last_grant_tick"]
                a["last_grant_tick"] = a["ticks"]
                a["grants"] += 1
            a["confirm"] = 0  # a confirmation is consumed by the tick it applies to
            entry = {
                "at": time.time(),
                "kind": "request",
                "agent": name,
                "intent": int(bool(intent)),
                "blocked": (inputs >> 2) & 1,
                "confirm": (inputs >> 3) & 1,
                "reason": reason,
                "granted": bool(grant),
                "tick": a["ticks"],
                "ticks_since_previous_grant": gap,
                "why": self.why(name, inputs, state_before, grant),
                # Carried out of the lock so the chain re-evaluates the circuit that
                # actually decided, not whichever one is installed by the time it asks.
                "_decisive": (inputs, state_before, grant, a["state"], self.netlist, self.policy.state_bits),
            }
            TRANSCRIPTS.appendleft(entry)
            return entry

    def why(self, name: str, inputs: int, state: int, grant: int) -> list[str]:
        """Which rule refused, in the policy's own words. Descriptive only: the verdict
        is the circuit's, and this reads the same counters it reads."""
        if grant:
            return []
        p = self.policy
        gap, streak, spent, window = p.split_state(state)
        blocked, confirm = (inputs >> 2) & 1, (inputs >> 3) & 1
        out = []
        if p.forbid_when_blocked and blocked:
            out.append("blocked is high")
        if p.gap_bits and gap:
            out.append(f"cooldown: {gap} tick{'s' if gap != 1 else ''} left of {p.min_gap_ticks}")
        if p.commit_ticks and streak < p.commit_ticks:
            out.append(f"commitment: {streak} of {p.commit_ticks} consecutive intent ticks")
        if p.max_grants and spent >= p.max_grants:
            out.append(f"budget: {spent} of {p.max_grants} grants used")
        if p.confirm_window_ticks and not (confirm or window):
            out.append(f"no confirmation inside the last {p.confirm_window_ticks} ticks")
        return out

    def status(self) -> dict:
        with self.lock:
            p = self.policy
            agents = []
            for name, a in sorted(self.agents.items()):
                gap, streak, spent, window = p.split_state(a["state"])
                agents.append(
                    {
                        "agent": name,
                        "ticks": a["ticks"],
                        "grants": a["grants"],
                        "cooldown_left": gap,
                        "intent_streak": streak,
                        "grants_used": spent,
                        "confirm_window_left": window,
                        "armed_blocked": a["blocked"],
                        "armed_confirm": a["confirm"],
                    }
                )
            return {
                "policy": self.summary,
                "agents": agents,
                "transcript": [{k: v for k, v in e.items() if not k.startswith("_")} for e in list(TRANSCRIPTS)[:60]],
                "chain": {
                    "enabled": CHAIN is not None,
                    "rpc": BSC_RPC if CHAIN is not None else None,
                    "chain_id": CHAIN.chain_id if CHAIN is not None else None,
                    "deployed": False,
                },
                "started_at": STARTED_AT,
                "default_rules": FLY_DEFAULT.describe(),
            }


class Chain:
    """A public node asked to re-evaluate one row, read-only, with nothing deployed.

    The evaluator's bytecode and selector come from the circuits repository, but the
    netlist is whatever policy is loaded here: a user's own compiled rules go to the
    chain exactly as the published circuit does."""

    def __init__(self, rpc: str) -> None:
        self.rpc = rpc
        doc = json.loads((REPO / "docs" / "sim" / "onchain.json").read_text())
        self.code = doc["evaluator"]["runtime_bytecode"]
        self.selector = doc["evaluator"]["selector"].removeprefix("0x")
        self.chain_id: int | None = None

    def _call(self, method: str, params: list, timeout: int = 20):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        last: Exception | None = None
        for attempt in range(2):
            try:
                req = urllib.request.Request(
                    self.rpc, data=body, headers={"content-type": "application/json", "user-agent": "reflex-console"}
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    out = json.loads(resp.read())
                if "error" in out:
                    raise RuntimeError(out["error"].get("message", "rpc error"))
                return out["result"]
            except Exception as e:
                last = e
                if attempt == 0:
                    time.sleep(0.6)
        raise last  # type: ignore[misc]

    def chain(self) -> int:
        if self.chain_id is None:
            self.chain_id = int(self._call("eth_chainId", []), 16)
        return self.chain_id

    def evaluate(self, netlist: bytes, n_in: int, n_out: int, n_state: int, inputs: int, state: int) -> tuple[int, int]:
        word = lambda v: f"{v:064x}"  # noqa: E731
        head = word(6 * 32) + "".join(word(v) for v in (n_in, n_out, n_state, inputs, state))
        body = word(len(netlist)) + netlist.hex() + "00" * ((32 - len(netlist) % 32) % 32)
        data = "0x" + self.selector + head + body
        raw = self._call("eth_call", [{"to": CHAIN_ADDRESS, "data": data}, "latest", {CHAIN_ADDRESS: {"code": self.code}}])[2:]
        return int(raw[:64], 16), int(raw[64:128], 16)


def policy_from(payload: dict) -> Policy:
    """Rules are untrusted input: whole numbers in range, or nothing is installed."""
    def whole(key: str, limit: int) -> int:
        value = payload.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
            raise ValueError(f"{key} must be a whole number")
        value = int(value)
        if not 0 <= value <= limit:
            raise ValueError(f"{key}={value} outside 0..{limit}")
        return value

    def flag(key: str) -> bool:
        """Not bool(): the string "false" is truthy, and this one decides whether a
        rule that only the tool layer can satisfy is installed at all."""
        value = payload.get(key, True)
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
        return value

    return Policy(
        min_gap_ticks=whole("min_gap_ticks", 255),
        commit_ticks=whole("commit_ticks", 255),
        forbid_when_blocked=flag("forbid_when_blocked"),
        max_grants=whole("max_grants", 255),
        confirm_window_ticks=whole("confirm_window_ticks", 255),
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "reflex-console"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def _send(self, code: int, body: bytes, kind: str) -> None:
        self.send_response(code)
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, (json.dumps(obj) + "\n").encode(), "application/json")

    def _body(self) -> dict:
        raw = self.rfile.read(int(self.headers.get("content-length", "0"))) or b"{}"
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("expected a JSON object")
        return payload

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._json(200, BOUNDARY.status())
        elif self.path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self._json(404, {"error": "no such path"})

    def do_POST(self) -> None:
        try:
            payload = self._body()
        except Exception as e:
            self._json(400, {"error": f"malformed request: {e}"})
            return
        name = str(payload.get("agent", "anonymous"))[:40] or "anonymous"

        if self.path == "/api/policy":
            try:
                summary = BOUNDARY.install(policy_from(payload))
            except Exception as e:
                self._json(400, {"error": str(e)})
                return
            TRANSCRIPTS.appendleft({"at": time.time(), "kind": "policy", "rules": summary["rules"],
                                    "circuit": {k: summary["circuit"][k] for k in ("nand", "latch")},
                                    "rows": summary["checked"]["rows"],
                                    "every_rule_holds": summary["checked"]["every_rule_holds"]})
            self._json(200, summary)
        elif self.path == "/api/tool":
            self._json(200, BOUNDARY.arm(name, payload.get("blocked"), payload.get("confirm")))
        elif self.path == "/api/request":
            entry = BOUNDARY.request(name, payload.get("intent", 0), str(payload.get("reason", ""))[:160])
            decisive = entry.pop("_decisive", None)
            if CHAIN is not None and decisive is not None:
                inputs, state, grant, next_state, netlist, state_bits = decisive
                try:
                    got, got_state = CHAIN.evaluate(netlist, len(INPUT_NAMES), 1, state_bits, inputs, state)
                    entry["chain"] = {
                        "chain_id": CHAIN.chain(),
                        "grant": got,
                        "agrees": (got, got_state) == (grant, next_state),
                        "deployed": False,
                    }
                except Exception as e:
                    entry["chain"] = {"error": str(e)[:120]}
            self._json(200, entry)
        else:
            self._json(404, {"error": "no such path"})


if __name__ == "__main__":
    print(f"compiling the default rules (the ones the fly circuit obeys) from {REPO} …", flush=True)
    BOUNDARY = Boundary(FLY_DEFAULT)
    s = BOUNDARY.summary
    print(f"  {'; '.join(s['rules'])}")
    print(f"  {s['circuit']['nand']} NAND + {s['circuit']['latch']} LATCH · {s['checked']['rows']} rows checked · "
          f"every rule holds: {s['checked']['every_rule_holds']}", flush=True)
    CHAIN = None
    if VERIFY_ON_CHAIN:
        try:
            CHAIN = Chain(BSC_RPC)
            print(f"second opinion: {BSC_RPC} — read-only eth_call, nothing deployed, no wallet", flush=True)
        except Exception as e:
            print(f"no second opinion ({e}); decisions are checked locally only", flush=True)
    print(f"boundary console on http://{HOST}:{PORT}  (no wallet, no key, nothing signed)", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
