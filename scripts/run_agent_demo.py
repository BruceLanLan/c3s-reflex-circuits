"""An agent whose actions a verified circuit has to authorise (demo, nothing on chain).

    python scripts/run_agent_demo.py                      # built-in model, offline
    python scripts/run_agent_demo.py --model-url http://localhost:11434/c3s
    python scripts/run_agent_demo.py --rounds 8 --json run.json

The point of this demo is the trust boundary, not the agent. A model -- yours, over
HTTP, or the built-in one that needs no network -- may only *propose* a looming
stimulus and say why. It never authorises anything. The proposal is encoded by the
repository's own encoder, the escape core is stepped tick by tick, and an action
counts as authorised only on a tick where the circuit itself commands a takeoff.

What the circuit's proofs give this arrangement, for any proposals whatsoever
(docs/PROPERTIES.md, and contracts/src/ReflexGuard.sol for the on-chain form):

  * P1  no takeoff within 7 ticks of a takeoff, so at most one authorisation in any
        8 consecutive ticks -- a rate limit the agent cannot argue its way past;
  * P2  a long-mode authorisation is preceded by four consecutive raising ticks, so
        the costly action needs four ticks of commitment first;
  * P3  while not standing, or during the refractory period, the answer is hold.

What it does not give, and what this demo therefore does not claim:

  * nothing binds wall-clock time; the limit is in ticks, and a caller can spend
    them as fast as it likes (the on-chain guard's block spacing is ordinary code);
  * the model writes the stimulus, so it can choose one that leads to a takeoff --
    P2 is a commitment cost, not a security property;
  * the actuator is outside all of this. Authorisation is a verdict, not an effect.

Nothing here touches a chain, a wallet or a key. `--rpc` (a BSC testnet endpoint)
only asks a node to reproduce the same verdict through a read-only eth_call, and
still deploys nothing.
"""

from __future__ import annotations

import argparse
import json
import random
import urllib.request
from pathlib import Path

from c3s import calibrate, exhaust, loom
from c3s.netlist import from_bytes

ROOT = Path(__file__).resolve().parents[1]
LOOM = ROOT / "circuits" / "loom-escape"
REFRACTORY_TICKS = 7  # P1, proven; the runner only enforces what the circuit proves


def load_core():
    m = json.loads((LOOM / "core-hand-abc.json").read_text())
    return from_bytes(bytes.fromhex(m["tapeout_netlist_hex"][2:]), len(m["inputs"]), len(m["outputs"])), m


def builtin_model(history: list[dict], rng: random.Random) -> dict:
    """A stand-in so the demo runs with no network and no keys. It is deliberately
    naive: it asks for closer and faster looms until something is authorised."""
    last = next((h for h in reversed(history) if "l_over_v_ms" in h), None)
    if last is None:
        return {"l_over_v_ms": 120.0, "azimuth_deg": 0.0, "reason": "opening move"}
    if last["authorised"]:
        lv, why = min(400.0, last["l_over_v_ms"] * 1.6), "backing off after one went through"
    else:
        lv, why = max(10.0, last["l_over_v_ms"] * 0.6), "closing in until the reflex fires"
    return {"l_over_v_ms": round(lv, 1), "azimuth_deg": float(rng.choice([-60, -30, 0, 30, 60])), "reason": why}


def ask_model(url: str, history: list[dict], timeout: int) -> dict:
    body = json.dumps({"history": history, "asks_for": ["l_over_v_ms", "azimuth_deg", "reason"]}).encode()
    req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def validated(proposal: dict) -> tuple[float, float, str]:
    """The model's output is untrusted input: numbers in range, or the round is refused."""
    lv = float(proposal["l_over_v_ms"])
    az = float(proposal["azimuth_deg"])
    if not (5.0 <= lv <= 400.0):
        raise ValueError(f"l_over_v_ms {lv} outside 5..400 ms")
    if not (-90.0 <= az <= 90.0):
        raise ValueError(f"azimuth_deg {az} outside -90..90")
    return lv, az, str(proposal.get("reason", ""))[:120]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model-url", help="your model, as an HTTP endpoint taking and returning JSON")
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--json", type=Path, help="write the transcript here")
    args = ap.parse_args()

    core, manifest = load_core()
    outs, nxt = exhaust.step_table(core)
    tj = json.loads((LOOM / "decision-table.json").read_text())
    p = calibrate.params_from_dict(tj["params"])
    enc = loom.Encoding(**{k: tuple(v) if isinstance(v, list) else v for k, v in tj["encoding"].items()})
    nf = enc.n_inputs
    rng = random.Random(args.seed)

    print(f"circuit {manifest['name']} · {manifest['metrics']['nand']} NAND + {manifest['metrics']['latch']} LATCH")
    print(f"model   {args.model_url or 'built-in (offline)'}")
    print("the model proposes; the circuit authorises\n")

    state, tick, last_authorised_tick, history = 0, 0, None, []
    for round_no in range(1, args.rounds + 1):
        try:
            proposal = ask_model(args.model_url, history, args.timeout) if args.model_url else builtin_model(history, rng)
            lv, az, reason = validated(proposal)
        except Exception as e:  # a model that answers nonsense simply loses its turn
            print(f"round {round_no}: proposal refused ({e})")
            history.append({"round": round_no, "refused": str(e), "authorised": False})
            continue

        stim = loom.Stimulus(lv, az)
        motor, authorised_tick = loom.CORE_HOLD, None
        for th, dth in loom.stimulus_samples(stim, p):
            row = loom.encode_features(th, dth, stim.azimuth_deg, enc) | (1 << nf) | (state << (nf + 1))
            motor = int(outs[row])
            state = int(nxt[row])
            tick += 1
            if motor in (loom.CORE_SHORT, loom.CORE_LONG):
                authorised_tick = tick
                break

        gap = None if last_authorised_tick is None or authorised_tick is None else authorised_tick - last_authorised_tick
        entry = {
            "round": round_no,
            "l_over_v_ms": lv,
            "azimuth_deg": az,
            "reason": reason,
            "ticks_used": tick,
            "motor": motor,
            "action": loom.CORE_ACTION_NAMES[motor],
            "authorised": authorised_tick is not None,
            "ticks_since_previous_authorisation": gap,
        }
        history.append(entry)
        if authorised_tick is not None:
            if gap is not None and gap <= REFRACTORY_TICKS:
                raise SystemExit(f"P1 violated at round {round_no}: {gap} ticks after the previous authorisation")
            last_authorised_tick = authorised_tick
        verdict = "AUTHORISED " + entry["action"] if entry["authorised"] else "refused (" + entry["action"] + ")"
        spacing = f", {gap} ticks after the last one" if gap is not None else ""
        print(f"round {round_no}: l/v {lv:>6.1f} ms az {az:>+5.0f}  {verdict}{spacing}  — {reason}")

    authorised = sum(1 for h in history if h.get("authorised"))
    print(f"\n{authorised} of {len(history)} rounds authorised, over {tick} ticks of circuit time")
    print("every refusal came from the circuit, not from a rule written here")
    if args.json:
        args.json.write_text(json.dumps({"circuit": manifest["name"], "rounds": history}, indent=1) + "\n")
        print(f"transcript written to {args.json}")


if __name__ == "__main__":
    main()
