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
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(os.environ.get("C3S_REPO", Path.home() / "work" / "c3s-reflex")).expanduser()
HOST = os.environ.get("CONSOLE_HOST", "127.0.0.1")
PORT = int(os.environ.get("CONSOLE_PORT", "8765"))
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
        return self.agents.setdefault(name, {"state": 0, "tick": 0, "last_authorised_tick": None, "authorisations": 0})

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
            motor, ticks, authorised_at, gaps = loom.CORE_HOLD, 0, [], []
            first_gap = None
            while True:
                theta, dtheta = samples[min(ticks, len(samples) - 1)]
                row = loom.encode_features(theta, dtheta, az, self.enc) | (1 << nf) | (a["state"] << (nf + 1))
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
                    a["authorisations"] += 1
                    authorised_at.append(a["tick"])
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
                "action": loom.CORE_ACTION_NAMES[motor],
                "authorised": bool(authorised_at),
                "authorisations": len(authorised_at),
                "smallest_gap": min(gaps) if gaps else None,
                "ticks_since_previous_authorisation": first_gap,
                "agent_tick": a["tick"],
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
                    }
                )
            return {
                "circuit": {"name": self.name, "sha256": self.sha256, **self.metrics},
                "refractory_ticks": REFRACTORY_TICKS,
                "agents": agents,
                "transcript": list(TRANSCRIPTS)[:60],
            }


TRANSCRIPTS: deque = deque(maxlen=TRANSCRIPT)


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
        else:
            self._json(404, {"error": "no such path"})

    def do_POST(self) -> None:
        if self.path != "/api/propose":
            self._json(404, {"error": "no such path"})
            return
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))) or b"{}")
            name, lv, az, reason, hold = validated(payload)
        except Exception as e:
            self._json(400, {"error": str(e), "authorised": False})
            return
        self._json(200, CIRCUIT.propose(name, lv, az, reason, hold))


if __name__ == "__main__":
    print(f"loading the circuit from {REPO} …", flush=True)
    CIRCUIT = Circuit()
    print(f"{CIRCUIT.name}: {CIRCUIT.metrics['nand']} NAND + {CIRCUIT.metrics['latch']} LATCH, sha256 {CIRCUIT.sha256[:16]}…")
    print(f"duty console on http://{HOST}:{PORT}  (no wallet, no key, nothing signed)", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
