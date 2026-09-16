"""Agent duty console: a model proposes, the verified circuit decides, this watches.

    C3S_REPO=~/work/c3s-reflex python console.py        # http://127.0.0.1:8765

Holds one circuit state per agent name, records every proposal with the circuit's
verdict, and serves a page that shows them. It enforces nothing of its own: the
refusals come from the escape core, whose behaviour is checked on all 8,388,608 of
its rows and five of whose temporal properties are proven (see docs/PROPERTIES.md and
docs/AGENT.md in the circuits repository).

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
# Optional second opinion: a public BNB Smart Chain node re-evaluates the decisive
# tick through a read-only eth_call whose state override installs the compiled
# evaluator for the duration of that one call. Nothing is deployed, no wallet or key
# is involved, and no gas is spent. Set VERIFY_ON_CHAIN=0 to keep everything local.
VERIFY_ON_CHAIN = os.environ.get("VERIFY_ON_CHAIN", "1") not in ("0", "", "no")
BSC_RPC = os.environ.get("BSC_RPC", "https://bsc-rpc.publicnode.com")
CHAIN_ADDRESS = "0x000000000000000000000000000000000000c3f5"
STATIC = Path(__file__).resolve().parent / "static"
REFRACTORY_TICKS = 7  # P1, proven; reported here, enforced by the circuit itself
TRANSCRIPT = 200

sys.path.insert(0, str(REPO))
from c3s import calibrate, exhaust, loom  # noqa: E402
from c3s.netlist import from_bytes  # noqa: E402

LOOM = REPO / "circuits" / "loom-escape"


class Circuit:
    """The committed escape core, its step tables, and one latch state per agent."""

    def __init__(self) -> None:
        manifest = json.loads((LOOM / "core-hand-abc.json").read_text())
        core = from_bytes(bytes.fromhex(manifest["tapeout_netlist_hex"][2:]), len(manifest["inputs"]), len(manifest["outputs"]))
        self.name = manifest["name"]
        self.sha256 = manifest["netlist_sha256"]
        self.metrics = manifest["metrics"]
        self.outs, self.nxt = exhaust.step_table(core)
        table = json.loads((LOOM / "decision-table.json").read_text())
        self.params = calibrate.params_from_dict(table["params"])
        self.enc = loom.Encoding(**{k: tuple(v) if isinstance(v, list) else v for k, v in table["encoding"].items()})
        self.lock = threading.Lock()
        self.agents: dict[str, dict] = {}

    def agent(self, name: str) -> dict:
        return self.agents.setdefault(
            name, {"state": 0, "tick": 0, "last_authorised_tick": None, "last_authorised_at": None, "authorisations": 0}
        )

    def propose(self, name: str, lv: float, az: float, reason: str, hold: int | None = None) -> dict:
        """Drive the circuit with the proposed stimulus.

        Without `hold` the episode stops at the first takeoff, which is the natural
        unit of a proposal. With it, the same stimulus keeps being presented for that
        many ticks, which is how the refractory limit becomes visible: the second
        takeoff cannot come sooner than 8 ticks after the first, whatever is fed in."""
        nf = self.enc.n_inputs
        with self.lock:
            a = self.agent(name)
            samples = loom.stimulus_samples(loom.Stimulus(lv, az), self.params)
            motor, ticks, authorised_at, gaps, actions = loom.CORE_HOLD, 0, [], [], []
            decisive: list[tuple[int, int, int, int]] = []
            first_gap = None
            while True:
                theta, dtheta = samples[min(ticks, len(samples) - 1)]
                x = loom.encode_features(theta, dtheta, az, self.enc) | (1 << nf)
                state_before = a["state"]
                row = x | (state_before << (nf + 1))
                motor = int(self.outs[row])
                a["state"] = int(self.nxt[row])
                a["tick"] += 1
                ticks += 1
                if motor in (loom.CORE_SHORT, loom.CORE_LONG):
                    if a["last_authorised_tick"] is not None:
                        gap = a["tick"] - a["last_authorised_tick"]
                        gaps.append(gap)
                        if first_gap is None:
                            first_gap = gap
                    a["last_authorised_tick"] = a["tick"]
                    a["last_authorised_at"] = time.time()  # wall clock, for the page only
                    a["authorisations"] += 1
                    authorised_at.append(a["tick"])
                    actions.append(loom.CORE_ACTION_NAMES[motor])
                    decisive.append((x, state_before, motor, a["state"]))
                    if hold is None:
                        break
                if hold is not None and ticks >= hold:
                    break
                if hold is None and ticks >= len(samples):
                    break
            entry = {
                "at": time.time(),
                "agent": name,
                "l_over_v_ms": lv,
                "azimuth_deg": az,
                "reason": reason,
                "held_ticks": hold,
                "ticks_used": ticks,
                "motor": motor,
                # what was authorised, not what the last tick happened to be doing
                "action": actions[-1] if actions else loom.CORE_ACTION_NAMES[motor],
                "authorised_actions": sorted(set(actions)),
                "authorised": bool(authorised_at),
                "authorisations": len(authorised_at),
                "smallest_gap": min(gaps) if gaps else None,
                "ticks_since_previous_authorisation": first_gap,
                "agent_tick": a["tick"],
                # the tick a public node is asked to re-evaluate, once the lock is free
                "_decisive": decisive[0] if decisive else None,
            }
            TRANSCRIPTS.appendleft(entry)
            return entry

    def status(self) -> dict:
        with self.lock:
            agents = []
            for name, a in sorted(self.agents.items()):
                since = None if a["last_authorised_tick"] is None else a["tick"] - a["last_authorised_tick"]
                agents.append(
                    {
                        "agent": name,
                        "ticks": a["tick"],
                        "authorisations": a["authorisations"],
                        "ticks_since_authorisation": since,
                        "refractory_ticks_left": None if since is None else max(0, REFRACTORY_TICKS - since),
                        "last_authorised_at": a["last_authorised_at"],
                    }
                )
            return {
                "circuit": {"name": self.name, "sha256": self.sha256, **self.metrics},
                "refractory_ticks": REFRACTORY_TICKS,
                # One tick of circuit time, from the calibration the netlist was built
                # against. The circuit's clock advances only when a proposal drives it.
                "tick_ms": self.params.tick_ms,
                # Whether a public node is asked for a second opinion. chain_id is the
                # cached value from the first successful call, so no network round trip
                # happens here; null means no node has answered yet this session.
                "chain": {
                    "enabled": CHAIN is not None,
                    "rpc": BSC_RPC if CHAIN is not None else None,
                    "chain_id": CHAIN.chain_id if CHAIN is not None else None,
                    "deployed": False,
                },
                "started_at": STARTED_AT,
                "agents": agents,
                "transcript": list(TRANSCRIPTS)[:60],
            }


TRANSCRIPTS: deque = deque(maxlen=TRANSCRIPT)
STARTED_AT = time.time()
CHAIN = None  # set in __main__ once the circuit is loaded


class Chain:
    """A public BNB Smart Chain node asked for a second opinion on one tick.

    Read-only: an eth_call whose state override installs the compiled evaluator at a
    throwaway address for the duration of that single call. Nothing is deployed, no
    address of yours appears, no wallet or key is involved and no gas is spent. The
    bytecode, the selector and the netlist all come from the circuits repository's
    docs/sim/onchain.json, which its tests check against the Foundry artifact."""

    def __init__(self, rpc: str) -> None:
        self.rpc = rpc
        doc = json.loads((REPO / "docs" / "sim" / "onchain.json").read_text())
        self.code = doc["evaluator"]["runtime_bytecode"]
        self.selector = doc["evaluator"]["selector"].removeprefix("0x")
        self.core = doc["core"]
        self.netlist = self.core["netlist"].removeprefix("0x")
        self.chain_id: int | None = None

    def _call(self, method: str, params: list, timeout: int = 20):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        last: Exception | None = None
        for attempt in range(2):  # a public endpoint drops a connection now and then
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

    def evaluate(self, inputs: int, state: int) -> tuple[int, int]:
        word = lambda v: f"{v:064x}"  # noqa: E731
        size = len(self.netlist) // 2
        head = word(6 * 32) + "".join(word(v) for v in (self.core["n_inputs"], self.core["n_outputs"], self.core["n_state"], inputs, state))
        data = "0x" + self.selector + head + word(size) + self.netlist + "00" * ((32 - size % 32) % 32)
        raw = self._call("eth_call", [{"to": CHAIN_ADDRESS, "data": data}, "latest", {CHAIN_ADDRESS: {"code": self.code}}])[2:]
        return int(raw[:64], 16), int(raw[64:128], 16)


def validated(payload: dict) -> tuple[str, float, float, str, int | None]:
    """A proposal is untrusted input: a name and two numbers in range, or nothing."""
    name = str(payload.get("agent", "anonymous"))[:40] or "anonymous"
    lv = float(payload["l_over_v_ms"])
    az = float(payload["azimuth_deg"])
    if not 5.0 <= lv <= 400.0:
        raise ValueError(f"l_over_v_ms {lv} outside 5..400 ms")
    if not -90.0 <= az <= 90.0:
        raise ValueError(f"azimuth_deg {az} outside -90..90 degrees")
    hold = payload.get("hold_ticks")
    if hold is not None:
        hold = int(hold)
        if not 1 <= hold <= 400:
            raise ValueError(f"hold_ticks {hold} outside 1..400")
    return name, lv, az, str(payload.get("reason", ""))[:160], hold


class Handler(BaseHTTPRequestHandler):
    server_version = "reflex-console"

    def log_message(self, fmt, *args):  # one line per request, not two
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def _send(self, code: int, body: bytes, kind: str) -> None:
        self.send_response(code)
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, (json.dumps(obj) + "\n").encode(), "application/json")

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._json(200, CIRCUIT.status())
        elif self.path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self._json(404, {"error": "no such path"})

    def do_POST(self) -> None:
        if self.path != "/api/propose":
            self._json(404, {"error": "no such path"})
            return
        payload: dict = {}
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))) or b"{}")
            name, lv, az, reason, hold = validated(payload)
        except Exception as e:
            # A malformed proposal never reaches the circuit, but it still belongs in
            # the duty record: who asked for what, and why it was thrown out.
            refused = {
                "at": time.time(),
                "agent": str(payload.get("agent", "anonymous"))[:40] if isinstance(payload, dict) else "anonymous",
                "l_over_v_ms": (payload or {}).get("l_over_v_ms") if isinstance(payload, dict) else None,
                "azimuth_deg": (payload or {}).get("azimuth_deg") if isinstance(payload, dict) else None,
                "reason": str((payload or {}).get("reason", ""))[:160] if isinstance(payload, dict) else "",
                "error": str(e),
                "authorised": False,
            }
            TRANSCRIPTS.appendleft(refused)
            self._json(400, refused)
            return
        entry = CIRCUIT.propose(name, lv, az, reason, hold)
        decisive = entry.pop("_decisive", None)
        if CHAIN is not None and decisive is not None:
            inputs, state, motor, next_state = decisive
            try:
                got_motor, got_state = CHAIN.evaluate(inputs, state)
                entry["chain"] = {
                    "chain_id": CHAIN.chain(),
                    "inputs": inputs,
                    "state": state,
                    "motor": got_motor,
                    "agrees": (got_motor, got_state) == (motor, next_state),
                    "deployed": False,
                }
            except Exception as e:  # the console works without a node; it just says so
                entry["chain"] = {"error": str(e)[:120]}
        self._json(200, entry)


if __name__ == "__main__":
    print(f"loading the circuit from {REPO} …", flush=True)
    CIRCUIT = Circuit()
    print(f"{CIRCUIT.name}: {CIRCUIT.metrics['nand']} NAND + {CIRCUIT.metrics['latch']} LATCH, sha256 {CIRCUIT.sha256[:16]}…")
    CHAIN = None
    if VERIFY_ON_CHAIN:
        try:
            # No network call here: one bad moment at startup must not switch the
            # second opinion off for the session. The chain id is fetched on first use.
            CHAIN = Chain(BSC_RPC)
            print(f"second opinion: {BSC_RPC} — read-only eth_call, nothing deployed, no wallet", flush=True)
        except Exception as e:
            print(f"no second opinion ({e}); the console runs on the local circuit alone", flush=True)
    print(f"duty console on http://{HOST}:{PORT}  (no wallet, no key, nothing signed)", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
