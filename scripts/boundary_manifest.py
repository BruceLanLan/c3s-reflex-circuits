"""Write a boundary manifest for one compiled policy, and print how to publish it through ERC-8004.

    python scripts/boundary_manifest.py \\
        --policy '{"min_gap_ticks":8,"max_grants":7,"confirm_per_irreversible":true,"two_key":true}' \\
        --out manifest.json
    python scripts/boundary_manifest.py --policy '...' --module 0x.. --safe 0x.. --chain-id 97 \\
        --manifest-uri https://example.org/manifest.json --out manifest.json

Compiles the policy, checks it on every row of its domain against the Python reference
and on every reachable state against each rule, and writes the manifest as canonical
JSON (the exact bytes whose keccak256 goes on chain; do not reformat the file before
publishing it). Then it *prints* an ERC-8004 registration file and `cast send`
templates for a user to run on their own machine with their own RPC and key. This
script sends nothing, signs nothing and reads no key.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from c3s import erc8004  # noqa: E402


def commands(manifest: dict, digest: str, manifest_uri: str, chain_id: int, name: str) -> str:
    identity = erc8004.IDENTITY_REGISTRY.get(chain_id, "$IDENTITY_REGISTRY")
    chain = erc8004.CHAIN_NAMES.get(chain_id, f"chain {chain_id}")
    reg = erc8004.registration_file(name, "An agent whose actions pass a compiled, exhaustively checked boundary circuit (" + "; ".join(manifest["rules"]["describe"]) + ").", manifest_uri, digest, chain_id)
    if manifest_uri.startswith("$"):
        # The value commits to the URI, so it cannot be computed before the URI is known.
        meta_arg = '"$METADATA_HEX"'
    else:
        meta_arg = "0x" + erc8004.metadata_value(manifest_uri, digest).hex()
    lines = [
        "",
        f"# --- ERC-8004 on {chain} (printed only; nothing below has been sent) ---",
        "# 1. Publish the manifest bytes unchanged at MANIFEST_URI, and this registration file at AGENT_URI:",
        json.dumps(reg, indent=2),
        "",
        "# 2. Register the agent (returns the agentId in the Registered event):",
        f'cast send {identity} "register(string)" "$AGENT_URI" --rpc-url "$YOUR_RPC" --private-key "$YOUR_KEY"',
        "",
        "# 3. Record the boundary on chain as metadata (value = UTF-8 JSON {keccak256, uri}"
        + ("; rerun with --manifest-uri to have $METADATA_HEX filled in):" if manifest_uri.startswith("$") else "):"),
        f'cast send {identity} "setMetadata(uint256,string,bytes)" "$AGENT_ID" "{erc8004.METADATA_KEY}" {meta_arg} --rpc-url "$YOUR_RPC" --private-key "$YOUR_KEY"',
        "",
        "# 4. Optionally add {\"agentId\": $AGENT_ID, \"agentRegistry\": \"eip155:" + f'{chain_id}:{identity}"}} to the file\'s registrations and:',
        f'cast send {identity} "setAgentURI(uint256,string)" "$AGENT_ID" "$AGENT_URI" --rpc-url "$YOUR_RPC" --private-key "$YOUR_KEY"',
        "",
        "# 5. Ask a validator you name to check it. No Validation Registry deployment on BSC is",
        "#    listed by the ERC-8004 contracts repository or the BNBAgent SDK; supply its address yourself.",
        f'cast send "$VALIDATION_REGISTRY" "validationRequest(address,uint256,string,bytes32)" "$VALIDATOR" "$AGENT_ID" "{manifest_uri}" {digest} --rpc-url "$YOUR_RPC" --private-key "$YOUR_KEY"',
        "",
        f"# The validator runs: python scripts/validate_boundary.py {manifest_uri} --request-hash {digest}",
    ]
    if identity.startswith("$"):
        lines.insert(2, "# No identity registry address is known for this chain here; set $IDENTITY_REGISTRY yourself.")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--policy", required=True, help="policy settings as JSON, e.g. '{\"min_gap_ticks\":8}'")
    ap.add_argument("--out", required=True, type=Path, help="where to write the manifest")
    ap.add_argument("--module", help="address of a deployed ReflexModule for this policy")
    ap.add_argument("--safe", help="the Safe that module is enabled on")
    ap.add_argument("--chain-id", type=int, help="chain of the module (and of the printed commands)")
    ap.add_argument("--manifest-uri", default="$MANIFEST_URI", help="where the manifest will be published")
    ap.add_argument("--name", default="bounded-agent", help="agent name for the printed registration file")
    args = ap.parse_args()

    try:
        policy = erc8004.policy_from_settings(json.loads(args.policy))
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        raise SystemExit(f"--policy: {e}")
    manifest = erc8004.build_manifest(policy, module=args.module, safe=args.safe, chain_id=args.chain_id)
    raw = erc8004.canonical_bytes(manifest)
    args.out.write_bytes(raw)
    digest = erc8004.keccak256(raw)

    c, k = manifest["circuit"], manifest["checked"]
    print(f"wrote {args.out} ({len(raw)} bytes)")
    for rule in manifest["rules"]["describe"]:
        print(f"  rule: {rule}")
    print(f"  circuit: {c['nand']} NAND, {c['latch']} latches, netlist sha256 {c['netlist_sha256']}")
    print(f"  checked: {k['rows']} rows equal the reference; {k['reachable_states']} reachable states, every rule holds: {k['every_rule_holds']}")
    print(f"manifest keccak256 {digest}")
    if args.chain_id is None:
        print("(no --chain-id: the commands below target BSC testnet, 97)")
    print(commands(manifest, digest, args.manifest_uri, args.chain_id if args.chain_id is not None else 97, args.name))


if __name__ == "__main__":
    main()
