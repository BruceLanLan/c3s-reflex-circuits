"""Check that the module deployed on a local fork decided what this repository decides.

    python scripts/fork_agreement.py --rpc http://127.0.0.1:8547 --module 0x...

`scripts/onchain_local_fork.sh` runs this last. It reads nothing but the chain and this
repository, and it answers one question: are the verdicts on that fork the verdicts of the
compiled policy here?

There is no signed verdict travelling from the console to the chain, and there is no oracle.
Both sides hold the same artefact -- the netlist, 299 bytes of NAND and LATCH cells -- and
both evaluate it themselves. So the check has two halves:

1. **The same rules.** The netlist bytes and their SHA-256, exported from `c3s/policy.py`
   here, must equal `netlist()` and `netlistSha256()` read off the deployed module. The
   module's constructor already refuses a netlist whose hash it was not given; this confirms
   the hash it was given is the one this repository produces today.
2. **The same verdicts.** Every `Decision` the module emitted on the fork carries the input
   word it read and the grant it reached. Replayed tick by tick, per agent, through
   `c3s.netlist.tick` (the Python evaluator) and through `Policy.reference` (an independent
   reference that reads counters, not gates), the grants must agree, and the latch state the
   replay ends on must equal `stateOf(agent)` on chain.

That is the same comparison the console's own second opinion makes when it re-evaluates a
decisive row on a public node (`Chain.evaluate` in the reflex console's `console.py`),
except that here the evaluator is a deployed contract holding the netlist rather than
bytecode installed for the length of one `eth_call`. Point the console's own second opinion
at this fork with `BSC_RPC=http://127.0.0.1:8547 VERIFY_ON_CHAIN=1` and it cross-checks
against the same chain.

Nothing is signed and no transaction is sent: every call here is `eth_call` or
`eth_getLogs`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from c3s import erc8004  # noqa: E402
from c3s.netlist import tick, to_bytes  # noqa: E402
from c3s.policy import Policy  # noqa: E402

# The policy the fork proof deploys: docs/ONCHAIN-LOCAL-FORK.md keeps these in one place.
POLICY = Policy(min_gap_ticks=4, confirm_per_irreversible=True, forbid_when_blocked=True)

DECISION = "Decision(address,address,uint256,bytes4,uint256,bool,uint64)"


def selector(signature: str) -> str:
    return erc8004.keccak256(signature.encode())[:10]


def rpc(url: str, method: str, params: list):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        out = json.loads(resp.read())
    if "error" in out:
        raise RuntimeError(f"{method}: {out['error'].get('message', out['error'])}")
    return out["result"]


def call(url: str, to: str, data: str) -> str:
    return rpc(url, "eth_call", [{"to": to, "data": data}, "latest"])


def read_bytes(word_hex: str) -> bytes:
    """Decode an ABI `bytes` return value (offset, length, payload)."""
    raw = bytes.fromhex(word_hex[2:])
    offset = int.from_bytes(raw[:32], "big")
    length = int.from_bytes(raw[offset : offset + 32], "big")
    return raw[offset + 32 : offset + 32 + length]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rpc", default="http://127.0.0.1:8547", help="the local anvil fork")
    ap.add_argument("--module", required=True, help="the ReflexModule deployed on that fork")
    args = ap.parse_args()

    chain_id = int(rpc(args.rpc, "eth_chainId", []), 16)
    circuit = POLICY.build()
    verify, properties = POLICY.verify(circuit), POLICY.properties(circuit)
    netlist = to_bytes(circuit)
    names = POLICY.input_names()
    print(f"fork          chain {chain_id} at {args.rpc}")
    print(f"policy        {', '.join(POLICY.describe())}")
    print(f"              outputs_match={verify['outputs_match']} properties_hold={properties['holds']}")

    # -- 1. the same rules. The module commits to SHA-256 of the netlist, not keccak.
    sha = "0x" + hashlib.sha256(netlist).hexdigest()
    on_chain_sha = call(args.rpc, args.module, selector("netlistSha256()"))
    on_chain_netlist = read_bytes(call(args.rpc, args.module, selector("netlist()")))
    if on_chain_sha.lower() != sha:
        print(f"FAIL  netlistSha256() is {on_chain_sha}, this repository exports {sha}")
        return 1
    if on_chain_netlist != netlist:
        print(f"FAIL  netlist() is {len(on_chain_netlist)} bytes, this repository exports {len(netlist)}")
        return 1
    print(f"netlist       {len(netlist)} bytes, sha256 {sha[:18]}… agrees with the deployed module")

    # -- 2. the same verdicts -------------------------------------------------
    topic = erc8004.keccak256(DECISION.encode())
    # Only blocks the fork itself mined: asking from block 0 makes anvil forward the range
    # to the upstream endpoint, which answers 429 rather than search all of BSC's history.
    try:
        info = rpc(args.rpc, "anvil_nodeInfo", [])
        first = int(info["forkConfig"]["forkBlockNumber"]) + 1
    except Exception:
        first = max(0, int(rpc(args.rpc, "eth_blockNumber", []), 16) - 256)
    logs = rpc(
        args.rpc,
        "eth_getLogs",
        [{"fromBlock": hex(first), "toBlock": "latest", "address": args.module, "topics": [topic]}],
    )
    if not logs:
        print("FAIL  the module emitted no Decision: nothing to replay")
        return 1

    rows: list[dict] = []
    for log in logs:
        data = bytes.fromhex(log["data"][2:])
        rows.append(
            {
                "agent": "0x" + log["topics"][1][-40:],
                "to": "0x" + log["topics"][2][-40:],
                "value": int.from_bytes(data[0:32], "big"),
                "selector": "0x" + data[32:36].hex(),
                "inputs": int.from_bytes(data[64:96], "big"),
                "granted": bool(int.from_bytes(data[96:128], "big")),
                "tick": int.from_bytes(data[128:160], "big"),
                "block": int(log["blockNumber"], 16),
                "index": int(log["logIndex"], 16),
            }
        )
    rows.sort(key=lambda r: (r["block"], r["index"]))

    state: dict[str, list[int]] = {}
    disagreements = 0
    print(f"decisions     {len(rows)} on chain, replaying per agent")
    for r in rows:
        agent = r["agent"]
        before = state.setdefault(agent, [0] * POLICY.state_bits)
        bits = [(r["inputs"] >> i) & 1 for i in range(len(names))]
        outs, after = tick(circuit, bits, before)
        gates = bool(outs[0])
        counters, _ = POLICY.reference(dict(zip(names, bits)), POLICY.fields(sum(b << i for i, b in enumerate(before))))
        ok = gates == r["granted"] == bool(counters)
        state[agent] = after
        why = POLICY.reasons(dict(zip(names, bits)), sum(b << i for i, b in enumerate(before)))
        mark = "  " if ok else "!!"
        print(
            f"{mark} {agent[:10]}… tick {r['tick']:>2}  inputs 0b{r['inputs']:0{len(names)}b}"
            f"  chain={'grant ' if r['granted'] else 'refuse'}"
            f"  python={'grant ' if gates else 'refuse'}"
            f"  reference={'grant ' if counters else 'refuse'}"
            + (f"  — {'; '.join(why)}" if why else "")
        )
        if not ok:
            disagreements += 1

    for agent, bits in state.items():
        want = sum(b << i for i, b in enumerate(bits))
        got = int(call(args.rpc, args.module, selector("stateOf(address)") + agent[2:].rjust(64, "0")), 16)
        if got != want:
            print(f"FAIL  stateOf({agent}) is {got:#x}, the replay ends on {want:#x}")
            disagreements += 1
        else:
            print(f"state         {agent[:10]}… ends on {want:#0{POLICY.state_bits + 2}b}, the same on chain")

    if disagreements:
        print(f"FAIL  {disagreements} disagreement(s) between the fork and this repository")
        return 1
    print(f"OK            {len(rows)} decisions, three evaluators, one verdict each")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
