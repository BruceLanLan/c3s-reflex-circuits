"""Boundary console: rules compiled to circuits, and every decision made by them.

    C3S_REPO=~/work/c3s-reflex python console.py        # http://127.0.0.1:8765

You write rules. They compile to a NAND/LATCH circuit, which is checked against a
plain-Python statement of the same rules on every row of its domain, and each rule is
then proven by visiting every state the circuit can reach from reset. Agents make
requests against that circuit; it decides; optionally a public BNB Smart Chain node
re-evaluates the deciding row read-only, with nothing deployed.

One agent, one circuit per class of tool. Rules for spending money, sending messages,
running commands and touching files are different rules, so they are different
circuits: `spend`, `message`, `exec`, `files`. Each is compiled and checked on its own
small domain — rows add rather than multiply — and a class with no circuit installed is
simply not gated (the decision says so). A fifth circuit, `halt`, is shared: it is
ticked first on every request with the same tool-layer bits, and when it refuses, every
class sees `blocked` high. Nothing here verifies a rule that spans two classes.

The default `exec` rules are the ones the fruit-fly escape circuit was already proven
to obey (docs/PROPERTIES.md): at most one grant in any 8 ticks, four ticks of
commitment before one, nothing while blocked. That is where they come from; everything
after that is the user's to set.

Two endpoints on purpose, because the separation is the whole point:

    POST /api/request   request, intent, class                — what an agent may write
    POST /api/tool      blocked, confirm, irreversible, failed,
                        heartbeat, confirm_b                  — what only the layer above it may write

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
from dataclasses import dataclass
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
from c3s.policy import AGENT_WRITABLE, MUST_COME_FROM_THE_TOOL_LAYER, Policy  # noqa: E402

# Of the tool layer's bits, only `blocked` is a level that stays where it was put. The
# rest are events, each consumed by the one tick it applies to: a confirm (either key)
# authorises the next request; `irreversible` describes the next request; a heartbeat
# is one beat; `failed` reports the outcome of the previous action to the next tick.
CONSUMED_BY_A_TICK = ("confirm", "confirm_b", "irreversible", "heartbeat", "failed")

# The classes of tool an agent's calls fall into, each with its own circuit, plus the
# one circuit every class shares. `exec` is where an unclassified call lands, so it
# always has a circuit.
TOOL_CLASSES = ("spend", "message", "exec", "files")
HALT = "halt"
CLASSES = TOOL_CLASSES + (HALT,)
DEFAULT_CLASS = "exec"

# The rules the escape circuit itself was proven to obey; the starting point, not a law.
FLY_DEFAULT = Policy(min_gap_ticks=8, commit_ticks=4, forbid_when_blocked=True)
TRANSCRIPTS: deque = deque(maxlen=TRANSCRIPT)
CHAIN = None  # set in __main__ when the chain second opinion is on
STARTED_AT = time.time()


def _settings(policy: Policy) -> dict:
    return {
        "min_gap_ticks": policy.min_gap_ticks,
        "commit_ticks": policy.commit_ticks,
        "forbid_when_blocked": policy.forbid_when_blocked,
        "max_grants": policy.max_grants,
        "confirm_window_ticks": policy.confirm_window_ticks,
        "sticky_block": policy.sticky_block,
        "heartbeat_ticks": policy.heartbeat_ticks,
        "confirm_per_irreversible": policy.confirm_per_irreversible,
        "trip_after_failures": policy.trip_after_failures,
        "two_key": policy.two_key,
    }


@dataclass
class Compiled:
    """One class's circuit: the policy, its step table, its bytes, and what was checked.

    `deny_all` is the one case with no circuit: the class refuses everything outright.
    It exists because `max_grants=0` means *unlimited* in c3s, so "never" needs saying
    some other way, and a rule that can be stated as "no circuit grants here" does not
    need a circuit to prove it."""

    policy: Policy | None
    outs: object
    nxt: object
    netlist: bytes
    n_in: int
    state_bits: int
    summary: dict
    deny_all: bool = False

    @staticmethod
    def build(policy: Policy) -> "Compiled":
        """Compile, check on every row, prove every rule."""
        t0 = time.time()
        circuit = policy.build()
        verify = policy.verify(circuit)
        proofs = policy.properties(circuit)
        outs, nxt = exhaust.step_table(circuit)
        netlist = to_bytes(circuit)
        summary = {
            "rules": policy.describe(),
            "settings": _settings(policy),
            "circuit": {
                "nand": verify["metrics"]["nand"],
                "latch": verify["metrics"]["latch"],
                "bytes": len(netlist),
                "depth": verify["metrics"]["depth"],
                "netlist": "0x" + netlist.hex(),
                "inputs": list(policy.input_names()),
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
            "deny_all": False,
            "compiled_in_ms": round((time.time() - t0) * 1000),
        }
        return Compiled(policy, outs, nxt, netlist, len(policy.input_names()), policy.state_bits, summary)

    @staticmethod
    def denied() -> "Compiled":
        # Shaped like a real summary so a page written for one does not have to care.
        summary = {
            "rules": ["nothing is ever granted"],
            "settings": _settings(Policy()),
            "circuit": {"nand": 0, "latch": 0, "bytes": 0, "depth": 0, "netlist": "0x", "inputs": [],
                        "agent_writable": list(AGENT_WRITABLE), "tool_layer_only": list(MUST_COME_FROM_THE_TOOL_LAYER)},
            "checked": {"rows": 0, "domain_bits": 0, "matches_reference": True, "reachable_states": 0,
                        "states_possible": 0, "configurations_visited": 0, "rows_proven": 0,
                        "every_rule_holds": True, "violations": {}},
            "deny_all": True,
            "compiled_in_ms": 0,
        }
        return Compiled(None, None, None, b"", 0, 0, summary, deny_all=True)


def _fresh_class_state() -> dict:
    return {"state": 0, "ticks": 0, "grants": 0, "last_grant_tick": None}


class Boundary:
    """Up to five compiled policies, one per class, and per-agent circuit state per class."""

    heartbeat_source = None  # callable -> bool, set by a tool-layer device (cardputer_relay)

    def __init__(self, default_exec: Policy) -> None:
        self.lock = threading.Lock()
        self.policies: dict[str, Compiled | None] = {c: None for c in CLASSES}
        self.agents: dict[str, dict] = {}
        self.install(DEFAULT_CLASS, default_exec)

    # -- what is installed ---------------------------------------------------

    def install(self, cls: str, policy: Policy) -> dict:
        compiled = Compiled.build(policy)  # the slow part, outside the lock
        return self._switch(cls, compiled)

    def deny_all(self, cls: str) -> dict:
        return self._switch(cls, Compiled.denied())

    def remove(self, cls: str) -> dict:
        if cls == DEFAULT_CLASS:
            raise ValueError(f"{DEFAULT_CLASS} always has a circuit: every unclassified call lands there; install other rules instead")
        self._switch(cls, None)
        return {"class": cls, "installed": False}

    def _switch(self, cls: str, compiled: Compiled | None) -> dict:
        if cls not in CLASSES:
            raise ValueError(f"no such class: {cls!r}; one of {', '.join(CLASSES)}")
        with self.lock:
            self.policies[cls] = compiled
            for a in self.agents.values():  # a new circuit starts every agent from reset
                a["classes"][cls] = _fresh_class_state()
        summary = dict(compiled.summary) if compiled else {"installed": False}
        summary["class"] = cls
        return summary

    # -- agents ----------------------------------------------------------------

    def agent(self, name: str) -> dict:
        return self.agents.setdefault(
            name,
            {"ticks": 0, "armed": {bit: 0 for bit in MUST_COME_FROM_THE_TOOL_LAYER},
             "classes": {c: _fresh_class_state() for c in CLASSES}},
        )

    def arm(self, name: str, bits: dict) -> dict:
        """The tool layer's bits, held until the agent's next request reads them. Bits no
        installed policy reads are stored all the same: they describe the agent, and a
        later policy may read them. Armed bits are per agent, shared by its classes."""
        unknown = [k for k in bits if k not in MUST_COME_FROM_THE_TOOL_LAYER]
        if unknown:
            raise ValueError(f"not a tool-layer bit: {', '.join(unknown)}")
        with self.lock:
            a = self.agent(name)
            for bit, value in bits.items():
                a["armed"][bit] = int(bool(value))
            entry = {
                "at": time.time(),
                "kind": "tool",
                "agent": name,
                "armed": dict(a["armed"]),
                "blocked": a["armed"]["blocked"],
                "confirm": a["armed"]["confirm"],
            }
            TRANSCRIPTS.appendleft(entry)
            return entry

    @staticmethod
    def _tick(compiled: Compiled, cs: dict, armed: dict, intent: int, blocked: int) -> tuple[int, dict, list, tuple]:
        """One tick of one circuit. Returns (grant, inputs read, reasons, decisive row)."""
        p = compiled.policy
        names = p.input_names()
        inp = {"request": 1, "intent": intent}
        for bit in names[2:]:
            inp[bit] = blocked if bit == "blocked" else armed[bit]
        inputs = sum(inp[bit] << i for i, bit in enumerate(names))
        state_before = cs["state"]
        row = inputs | (state_before << compiled.n_in)
        grant = int(compiled.outs[row])
        cs["state"] = int(compiled.nxt[row])
        cs["ticks"] += 1
        gap = None
        if grant:
            if cs["last_grant_tick"] is not None:
                gap = cs["ticks"] - cs["last_grant_tick"]
            cs["last_grant_tick"] = cs["ticks"]
            cs["grants"] += 1
        # Descriptive only: the verdict is the circuit's; this reads the same counters
        # it reads and says which rule, in the policy's own words.
        why = p.reasons(inp, state_before)
        decisive = (inputs, state_before, grant, cs["state"], compiled.netlist, compiled.n_in, compiled.state_bits)
        return grant, inp, why, decisive, gap

    def request(self, name: str, intent: int, reason: str, cls: str = DEFAULT_CLASS) -> dict:
        """One tick: the agent asks, the shared halt circuit answers first, then the
        class's circuit. Both verdicts are recorded; the class's is the decision."""
        if cls not in TOOL_CLASSES:
            raise ValueError(f"no such class: {cls!r}; one of {', '.join(TOOL_CLASSES)}")
        intent = int(bool(intent))
        with self.lock:
            a = self.agent(name)
            a["ticks"] += 1
            armed = a["armed"]
            # A present device is a heartbeat for every agent: a halt policy with a
            # heartbeat rule stops them all when it is unplugged or goes quiet.
            if self.heartbeat_source is not None and self.heartbeat_source():
                armed["heartbeat"] = 1
            decisive: dict[str, tuple] = {}

            halt_entry = None
            halt_ok = 1
            halt_c = self.policies[HALT]
            if halt_c is not None and not halt_c.deny_all:
                h_grant, h_inp, h_why, h_dec, _ = self._tick(halt_c, a["classes"][HALT], armed, 1, armed["blocked"])
                halt_ok = h_grant
                halt_entry = {"granted": bool(h_grant), "why": h_why, "inputs": h_inp}
                decisive[HALT] = h_dec

            compiled = self.policies[cls]
            cs = a["classes"][cls]
            blocked = int(armed["blocked"] or not halt_ok)
            gap = None
            if compiled is None:
                # Not gated: no circuit for this class, so nothing to refuse with — unless
                # the shared halt did.
                grant, inp, why = int(halt_ok), {"request": 1, "intent": intent, "blocked": blocked, "confirm": armed["confirm"]}, []
                cs["ticks"] += 1
            elif compiled.deny_all:
                grant, inp, why = 0, {"request": 1, "intent": intent, "blocked": blocked, "confirm": armed["confirm"]}, [
                    "class denied outright: no circuit grants here"]
                cs["ticks"] += 1
            else:
                grant, inp, why, dec, gap = self._tick(compiled, a["classes"][cls], armed, intent, blocked)
                decisive[cls] = dec
            if halt_entry is not None and not halt_ok:
                why = [f"halted (shared halt circuit): {'; '.join(halt_entry['why']) or 'no grant'}"] + why
                grant = 0

            for bit in CONSUMED_BY_A_TICK:
                armed[bit] = 0
            entry = {
                "at": time.time(),
                "kind": "request",
                "agent": name,
                "class": cls,
                "class_installed": compiled is not None,
                "deny_all": bool(compiled is not None and compiled.deny_all),
                "inputs": inp,
                "intent": inp.get("intent", intent),
                "blocked": inp.get("blocked", blocked),
                "confirm": inp.get("confirm", 0),
                "reason": reason,
                "granted": bool(grant),
                "tick": a["ticks"],
                "class_tick": cs["ticks"],
                "ticks_since_previous_grant": gap,
                "why": why,
                "halt": halt_entry,
                # Carried out of the lock so the chain re-evaluates the circuits that
                # actually decided, not whichever are installed by the time it asks.
                "_decisive": decisive,
            }
            TRANSCRIPTS.appendleft(entry)
            return entry

    # -- reporting ---------------------------------------------------------------

    def _class_view(self, cls: str, cs: dict) -> dict:
        compiled = self.policies[cls]
        counters = compiled.policy.fields(cs["state"]) if compiled and compiled.policy else {
            "gap": 0, "streak": 0, "spent": 0, "window": 0}
        return {
            "installed": compiled is not None,
            "deny_all": bool(compiled and compiled.deny_all),
            "ticks": cs["ticks"],
            "grants": cs["grants"],
            "cooldown_left": counters.get("gap", 0),
            "intent_streak": counters.get("streak", 0),
            "grants_used": counters.get("spent", 0),
            "confirm_window_left": counters.get("window", 0),
            "counters": counters,
        }

    def status(self) -> dict:
        with self.lock:
            agents = []
            for name, a in sorted(self.agents.items()):
                by_class = {c: self._class_view(c, a["classes"][c]) for c in CLASSES}
                flat = by_class[DEFAULT_CLASS]
                agents.append(
                    {
                        "agent": name,
                        "ticks": a["ticks"],
                        "grants": flat["grants"],
                        "cooldown_left": flat["cooldown_left"],
                        "intent_streak": flat["intent_streak"],
                        "grants_used": flat["grants_used"],
                        "confirm_window_left": flat["confirm_window_left"],
                        "counters": flat["counters"],
                        "by_class": by_class,
                        "armed": dict(a["armed"]),
                        "armed_blocked": a["armed"]["blocked"],
                        "armed_confirm": a["armed"]["confirm"],
                    }
                )
            policies = {c: (dict(p.summary, **{"class": c}) if p else None) for c, p in self.policies.items()}
            return {
                "policy": policies[DEFAULT_CLASS],
                "policies": policies,
                "classes": list(TOOL_CLASSES),
                "halt_class": HALT,
                "default_class": DEFAULT_CLASS,
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

    def flag(key: str, default: bool) -> bool:
        """Not bool(): the string "false" is truthy, and these decide whether a rule
        that only the tool layer can satisfy is installed at all."""
        value = payload.get(key, default)
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
        return value

    return Policy(
        min_gap_ticks=whole("min_gap_ticks", 255),
        commit_ticks=whole("commit_ticks", 255),
        forbid_when_blocked=flag("forbid_when_blocked", True),
        max_grants=whole("max_grants", 255),
        confirm_window_ticks=whole("confirm_window_ticks", 255),
        sticky_block=flag("sticky_block", False),
        heartbeat_ticks=whole("heartbeat_ticks", 255),
        confirm_per_irreversible=flag("confirm_per_irreversible", False),
        trip_after_failures=whole("trip_after_failures", 255),
        two_key=flag("two_key", False),
    )


def class_from(payload: dict, allowed: tuple[str, ...]) -> str:
    cls = payload.get("class", DEFAULT_CLASS)
    if not isinstance(cls, str) or cls not in allowed:
        raise ValueError(f"class must be one of {', '.join(allowed)}")
    return cls


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
        elif self.path.startswith("/api/manifest"):
            self._manifest()
        elif self.path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self._json(404, {"error": "no such path"})

    def _manifest(self) -> None:
        """GET /api/manifest?class=spend — the installed circuit of one class as an
        ERC-8004 boundary manifest (c3s/erc8004.py), with its keccak256 and the
        registration file that would advertise it. Built here, signed and sent nowhere."""
        from urllib.parse import parse_qs, urlsplit

        from c3s import erc8004

        cls = (parse_qs(urlsplit(self.path).query).get("class") or [DEFAULT_CLASS])[0]
        if cls not in CLASSES:
            self._json(400, {"error": f"no such class: {cls!r}"})
            return
        with BOUNDARY.lock:
            compiled = BOUNDARY.policies[cls]
        if compiled is None or compiled.deny_all or compiled.policy is None:
            why = "denied outright: there is no circuit to publish" if compiled is not None else "no circuit installed"
            self._json(400, {"error": f"{cls}: {why}"})
            return
        manifest = erc8004.build_manifest(compiled.policy)
        try:
            digest, hash_error = erc8004.manifest_hash(manifest), None
        except Exception as e:  # keccak256 needs Foundry's cast
            digest, hash_error = None, str(e)
        uri = "https://<where-you-host>/manifest.json"
        self._json(200, {
            "class": cls,
            "manifest": manifest,
            # The bytes that are hashed and must be hosted as they are: a pretty-printed
            # copy is a different file with a different hash.
            "manifest_canonical": erc8004.canonical_bytes(manifest).decode("utf-8"),
            "keccak256": digest,
            "hash_error": hash_error,
            "registration_file": erc8004.registration_file(
                f"my-agent-{cls}", "An agent whose actions pass through this compiled boundary.", uri, digest or "0x", None),
            "next": "host manifest.json byte for byte, then run scripts/boundary_manifest.py in c3s-reflex for the "
                    "cast send templates; anyone can re-check it with scripts/validate_boundary.py",
        })

    def do_POST(self) -> None:
        try:
            payload = self._body()
        except Exception as e:
            self._json(400, {"error": f"malformed request: {e}"})
            return
        name = str(payload.get("agent", "anonymous"))[:40] or "anonymous"

        if self.path == "/api/policy":
            try:
                cls = class_from(payload, CLASSES)
                if payload.get("remove") is True:
                    summary = BOUNDARY.remove(cls)
                elif payload.get("deny_all") is True:
                    summary = BOUNDARY.deny_all(cls)
                else:
                    summary = BOUNDARY.install(cls, policy_from(payload))
            except Exception as e:
                self._json(400, {"error": str(e)})
                return
            note = {"at": time.time(), "kind": "policy", "class": cls}
            if summary.get("installed") is False:
                note["rules"] = ["no circuit: not gated"]
            else:
                note.update({"rules": summary["rules"],
                             "circuit": {k: summary["circuit"][k] for k in ("nand", "latch")},
                             "rows": summary["checked"]["rows"],
                             "every_rule_holds": summary["checked"]["every_rule_holds"]})
            TRANSCRIPTS.appendleft(note)
            self._json(200, summary)
        elif self.path == "/api/tool":
            bits = {k: v for k, v in payload.items() if k != "agent"}
            try:
                self._json(200, BOUNDARY.arm(name, bits))
            except ValueError as e:
                self._json(400, {"error": str(e)})
        elif self.path == "/api/request":
            try:
                cls = class_from(payload, TOOL_CLASSES)
                entry = BOUNDARY.request(name, payload.get("intent", 0), str(payload.get("reason", ""))[:160], cls)
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            decisive = entry.pop("_decisive", None) or {}
            if CHAIN is not None and decisive:
                circuits, agrees_all, error = {}, True, None
                for label, (inputs, state, grant, next_state, netlist, n_in, state_bits) in decisive.items():
                    try:
                        got, got_state = CHAIN.evaluate(netlist, n_in, 1, state_bits, inputs, state)
                        ok = (got, got_state) == (grant, next_state)
                        circuits[label] = {"grant": got, "agrees": ok}
                        agrees_all = agrees_all and ok
                    except Exception as e:
                        error = str(e)[:120]
                        break
                if error is not None:
                    entry["chain"] = {"error": error}
                else:
                    decided = circuits.get(cls) or circuits.get(HALT) or {}
                    entry["chain"] = {
                        "chain_id": CHAIN.chain(),
                        "grant": decided.get("grant"),
                        "agrees": agrees_all,
                        "circuits": circuits,
                        "deployed": False,
                    }
            self._json(200, entry)
        else:
            self._json(404, {"error": "no such path"})


if __name__ == "__main__":
    print(f"compiling the default rules (the ones the fly circuit obeys) from {REPO} …", flush=True)
    BOUNDARY = Boundary(FLY_DEFAULT)
    s = BOUNDARY.policies[DEFAULT_CLASS].summary
    print(f"  {DEFAULT_CLASS}: {'; '.join(s['rules'])}")
    print(f"  {s['circuit']['nand']} NAND + {s['circuit']['latch']} LATCH · {s['checked']['rows']} rows checked · "
          f"every rule holds: {s['checked']['every_rule_holds']}", flush=True)
    print(f"  other classes ({', '.join(c for c in TOOL_CLASSES if c != DEFAULT_CLASS)}, {HALT}): no circuit until one is installed", flush=True)
    CHAIN = None
    if VERIFY_ON_CHAIN:
        try:
            CHAIN = Chain(BSC_RPC)
            print(f"second opinion: {BSC_RPC} — read-only eth_call, nothing deployed, no wallet", flush=True)
        except Exception as e:
            print(f"no second opinion ({e}); decisions are checked locally only", flush=True)
    if os.environ.get("REFLEX_CARDPUTER"):
        from cardputer_relay import Relay

        Relay(BOUNDARY, os.environ["REFLEX_CARDPUTER"], log=lambda m: print(m, flush=True)).start()
    print(f"boundary console on http://{HOST}:{PORT}  (no wallet, no key, nothing signed)", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
