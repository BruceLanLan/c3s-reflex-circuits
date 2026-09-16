"""Publish what the simulator page needs to run the core on a public chain.

    python scripts/build_sim_onchain.py     # writes docs/sim/onchain.json

The page's "on chain" mode sends one read-only `eth_call` whose state override
installs the compiled `NandMachine` runtime at a throwaway address, so a real node
executes the circuit with nothing deployed: no contract address, no wallet, no key,
no gas. That call needs three things the page cannot derive by itself -- the
evaluator's runtime bytecode, the `evaluate` selector and the core's netlist bytes --
so they are published next to the page.

Reads the Foundry artifact (`cd contracts && forge build`) and the committed core
manifest. The bytecode is whatever the pinned solc emitted; the page shows its
digest, and tests/test_firmware.py checks this file against the artifact.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "contracts" / "out" / "NandMachine.sol" / "NandMachine.json"
CORE = ROOT / "circuits" / "loom-escape" / "core-hand-abc.json"
OUT = ROOT / "docs" / "sim" / "onchain.json"
SIGNATURE = "evaluate(bytes,uint256,uint256,uint256,uint256,uint256)"
# Endpoints that answered an eth_call state override and send CORS headers, so a
# browser on another origin can reach them. Anyone can paste a different one.
RPCS = ["https://bsc-rpc.publicnode.com"]


def main() -> None:
    if not ARTIFACT.exists():
        raise SystemExit(f"{ARTIFACT.relative_to(ROOT)} is missing; run: cd contracts && forge build")
    art = json.loads(ARTIFACT.read_text())
    code = art["deployedBytecode"]["object"]
    if not code.startswith("0x"):
        code = "0x" + code
    selector = "0x" + art["methodIdentifiers"][SIGNATURE]
    m = json.loads(CORE.read_text())
    netlist = m["tapeout_netlist_hex"]
    doc = {
        "format": "c3s.onchain/1",
        "note": "read-only eth_call with a state override; nothing is deployed and no wallet is used",
        "evaluator": {
            "contract": "NandMachine",
            "solc": json.loads(art["rawMetadata"])["compiler"]["version"],
            "selector": selector,
            "signature": SIGNATURE,
            "runtime_bytecode": code,
            "runtime_bytes": len(code) // 2 - 1,
            "runtime_sha256": hashlib.sha256(bytes.fromhex(code[2:])).hexdigest(),
        },
        "core": {
            "name": m["name"],
            "netlist": netlist,
            "netlist_sha256": m["netlist_sha256"],
            "n_inputs": len(m["inputs"]),
            "n_outputs": len(m["outputs"]),
            "n_state": m["metrics"]["latch"],
        },
        "rpc": RPCS,
        "chains": {"56": "BSC mainnet", "97": "BSC testnet"},
    }
    OUT.write_text(json.dumps(doc, indent=1) + "\n")
    ev = doc["evaluator"]
    print(f"wrote {OUT.relative_to(ROOT)}: evaluator {ev['runtime_bytes']} B (solc {ev['solc']}), core {m['name']}")


if __name__ == "__main__":
    main()
