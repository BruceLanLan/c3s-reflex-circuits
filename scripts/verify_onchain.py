"""Evaluate the published core on BNB Smart Chain without deploying anything.

    python scripts/verify_onchain.py                     # one episode, 12 ticks
    python scripts/verify_onchain.py --ticks 24 --rpc https://bsc-dataseed.bnbchain.org

`eth_call` with a state override installs the compiled `NandMachine` bytecode at a
throwaway address for the duration of one read-only call, so a real BSC node runs the
circuit: no deployment, no transaction, no wallet, no private key, no gas, and nothing
left on chain afterwards. Each tick the node returns is compared with this
repository's own evaluator, which makes the chain a fourth independent evaluator
beside Python, the firmware's C and the browser's WebAssembly.

What leaves this machine: the netlist bytes and input values -- both already public in
this repository -- and your IP address, to whichever RPC provider you point at.
Nothing is signed, and no key is read or needed.

Needs the Foundry artifact contracts/out/NandMachine.sol/NandMachine.json, which
`cd contracts && forge build` produces.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

from c3s import calibrate, exhaust, loom
from c3s.netlist import from_bytes

ROOT = Path(__file__).resolve().parents[1]
LOOM = ROOT / "circuits" / "loom-escape"
ARTIFACT = ROOT / "contracts" / "out" / "NandMachine.sol" / "NandMachine.json"
DEFAULT_RPC = "https://bsc-rpc.publicnode.com"
# Any address will do: the override gives it code for one call only, and nothing is stored.
ADDRESS = "0x000000000000000000000000000000000000c3f5"
EVALUATE = "6b758dac"  # evaluate(bytes,uint256,uint256,uint256,uint256,uint256)
CHAINS = {56: "BSC mainnet", 97: "BSC testnet"}


def _word(v: int) -> str:
    return f"{v:064x}"


def encode_evaluate(netlist: bytes, n_in: int, n_out: int, n_state: int, inputs: int, state: int) -> str:
    head = _word(6 * 32) + "".join(_word(v) for v in (n_in, n_out, n_state, inputs, state))
    pad = (32 - len(netlist) % 32) % 32
    return "0x" + EVALUATE + head + _word(len(netlist)) + netlist.hex() + "00" * pad


def rpc(url: str, method: str, params: list) -> str:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    # Some public endpoints reject urllib's default User-Agent outright.
    headers = {"content-type": "application/json", "user-agent": "c3s-reflex-circuits/0.3 (+scripts/verify_onchain.py)"}
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        out = json.loads(resp.read())
    if "error" in out:
        raise SystemExit(f"RPC error from {url}: {out['error']}")
    return out["result"]


def evaluate_on_chain(url: str, code: str, netlist: bytes, ports: tuple[int, int, int], inputs: int, state: int) -> tuple[int, int]:
    data = encode_evaluate(netlist, *ports, inputs, state)
    raw = rpc(url, "eth_call", [{"to": ADDRESS, "data": data}, "latest", {ADDRESS: {"code": code}}])[2:]
    if len(raw) < 128:
        raise SystemExit(f"short return from eth_call: 0x{raw}")
    return int(raw[:64], 16), int(raw[64:128], 16)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rpc", default=DEFAULT_RPC, help=f"JSON-RPC endpoint (default {DEFAULT_RPC})")
    ap.add_argument("--ticks", type=int, default=12, help="how many ticks to ask the node for")
    ap.add_argument("--l-over-v", type=float, default=40.0)
    ap.add_argument("--azimuth", type=float, default=-60.0)
    args = ap.parse_args()

    if not ARTIFACT.exists():
        raise SystemExit(f"{ARTIFACT.relative_to(ROOT)} is missing; run: cd contracts && forge build")
    code = json.loads(ARTIFACT.read_text())["deployedBytecode"]["object"]
    m = json.loads((LOOM / "core-hand-abc.json").read_text())
    netlist = bytes.fromhex(m["tapeout_netlist_hex"][2:])
    core = from_bytes(netlist, len(m["inputs"]), len(m["outputs"]))
    n_state = m["metrics"]["latch"]
    ports = (core.n_inputs, core.n_outputs, n_state)
    outs, nxt = exhaust.step_table(core)

    tj = json.loads((LOOM / "decision-table.json").read_text())
    p = calibrate.params_from_dict(tj["params"])
    enc = loom.Encoding(**{k: tuple(v) if isinstance(v, list) else v for k, v in tj["encoding"].items()})

    chain_id = int(rpc(args.rpc, "eth_chainId", []), 16)
    print(f"{args.rpc}: chain {chain_id} ({CHAINS.get(chain_id, 'unknown')}), nothing deployed, read-only calls")
    print(f"netlist {m['netlist_sha256'][:16]}... {len(netlist)} bytes, evaluator bytecode {len(code) // 2 - 1} bytes")

    nf, state = enc.n_inputs, 0
    stim = loom.Stimulus(args.l_over_v, args.azimuth)
    trace = []
    for tick, (th, dth) in enumerate(loom.stimulus_samples(stim, p)):
        x = loom.encode_features(th, dth, stim.azimuth_deg, enc) | (1 << nf)
        row = x | (state << (nf + 1))
        trace.append((tick, x, state, int(outs[row]), int(nxt[row])))
        state = int(nxt[row])
        if trace[-1][3] in (loom.CORE_SHORT, loom.CORE_LONG):
            break

    # Ask the node about every tick where something happens first, then spread the rest
    # over the episode: a handful of identical "hold" rows would prove very little.
    acting = [i for i, t in enumerate(trace) if t[3] != loom.CORE_HOLD]
    spread = [round(i * (len(trace) - 1) / max(1, args.ticks - 1)) for i in range(args.ticks)]
    picked: list[int] = []
    for i in acting + spread:
        if i not in picked and len(picked) < args.ticks:
            picked.append(i)
    picked.sort()

    mismatches = 0
    for i in picked:
        tick, x, st, want_out, want_state = trace[i]
        got_out, got_state = evaluate_on_chain(args.rpc, code, netlist, ports, x, st)
        ok = (got_out, got_state) == (want_out, want_state)
        mismatches += not ok
        verdict = "agrees" if ok else f"DIFFERS from local motor {want_out} state {want_state}"
        print(
            f"tick {tick:3d} inputs 0x{x:05x} state {st:2d} -> motor {got_out} state {got_state:2d}"
            f"  {verdict}  ({loom.CORE_ACTION_NAMES[got_out]})"
        )

    print(f"{len(picked)} of {len(trace)} ticks checked on chain, {mismatches} differing")
    if mismatches:
        raise SystemExit("the node and the local evaluator disagree")


if __name__ == "__main__":
    main()
