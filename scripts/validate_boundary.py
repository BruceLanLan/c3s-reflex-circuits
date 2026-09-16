"""Check a published boundary manifest yourself, and print the ERC-8004 response for it.

    python scripts/validate_boundary.py manifest.json
    python scripts/validate_boundary.py https://example.org/manifest.json --request-hash 0x.. \\
        --rpc https://bsc-rpc.publicnode.com --rows 8 --out report.json

Anyone can run this; nothing in it needs the manifest's author. From the manifest bytes
alone it recompiles the policy from `rules.settings`, checks the netlist bytes and their
SHA-256, decodes the published netlist and re-runs the exhaustive checks on it, and
compares every recorded number (c3s/erc8004.py `validate`). With `--rpc` it also:

  * evaluates a handful of rows of the published netlist on a real node through the
    zero-deployment `eth_call` state override scripts/verify_onchain.py uses (the
    evaluator bytecode comes from docs/sim/onchain.json), and compares each with the
    local step table;
  * if the manifest names a deployed ReflexModule, reads its netlistSha256(), netlist(),
    nIn(), nState(), optionalInputs() and safe() and compares them.

A manifest that names a module is not scored 100 without that module check. The report
is canonical JSON; its keccak256 is the responseHash. The `validationResponse` command is
printed, never sent: it must come from the validator address the agent's owner named in
`validationRequest`, signed on the validator's own machine.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from c3s import erc8004, exhaust  # noqa: E402
from verify_onchain import encode_evaluate, rpc  # noqa: E402

ONCHAIN = ROOT / "docs" / "sim" / "onchain.json"
DEFAULT_RPC = {56: "https://bsc-rpc.publicnode.com", 97: "https://bsc-testnet-rpc.publicnode.com"}
# Any address will do: the override gives it code for one call only.
THROWAWAY = "0x000000000000000000000000000000000000c3f5"


def fetch(source: str) -> bytes:
    if source.startswith(("https://", "http://")):
        req = urllib.request.Request(source, headers={"user-agent": "c3s-boundary-validator"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    return Path(source).read_bytes()


def _call(url: str, to: str, data: str, overrides: dict | None = None) -> str:
    params: list = [{"to": to, "data": data}, "latest"]
    if overrides:
        params.append(overrides)
    return rpc(url, "eth_call", params)[2:]


def pick_rows(circuit, n_in: int, count: int) -> list[tuple[int, int, int, int]]:
    """(inputs, state, grant, next_state) for `count` rows from reachable states, half
    granted and half refused where possible, spread evenly rather than clustered."""
    outs, nxt = exhaust.step_table(circuit)
    reachable, frontier = {0}, [0]
    while frontier:
        new = []
        for s in frontier:
            for i in range(1 << n_in):
                t = int(nxt[i | (s << n_in)])
                if t not in reachable:
                    reachable.add(t)
                    new.append(t)
        frontier = new
    rows = [i | (s << n_in) for s in sorted(reachable) for i in range(1 << n_in)]
    granted = [r for r in rows if int(outs[r]) & 1]
    refused = [r for r in rows if not int(outs[r]) & 1]

    def spread(xs: list[int], k: int) -> list[int]:
        if k <= 0 or not xs:
            return []
        if k >= len(xs):
            return xs
        return [xs[round(j * (len(xs) - 1) / max(1, k - 1))] for j in range(k)]

    half = count // 2
    chosen = spread(granted, half) + spread(refused, count - half)
    mask = (1 << n_in) - 1
    return [(r & mask, r >> n_in, int(outs[r]), int(nxt[r])) for r in chosen]


def chain_rows(r: erc8004.Report, url: str, ctx: dict, count: int) -> None:
    ev = json.loads(ONCHAIN.read_text())["evaluator"]
    code = ev["runtime_bytecode"]
    policy, circuit, netlist = ctx["policy"], ctx["circuit"], ctx["netlist"]
    n_in = len(policy.input_names())
    ports = (n_in, 1, policy.state_bits)
    rows = pick_rows(circuit, n_in, count)
    got_rows, bad = [], 0
    for inputs, state, want_out, want_next in rows:
        raw = _call(url, THROWAWAY, encode_evaluate(netlist, *ports, inputs, state), {THROWAWAY: {"code": code}})
        got = (int(raw[:64], 16), int(raw[64:128], 16)) if len(raw) >= 128 else None
        ok = got == (want_out, want_next)
        bad += not ok
        got_rows.append({"inputs": inputs, "state": state, "grant": want_out, "next_state": want_next, "node": list(got) if got else raw})
    r.add("eth_call_rows", bad == 0 and len(rows) > 0, f"{len(rows)} rows equal to the local step table", f"{len(rows) - bad} equal", note=f"evaluator runtime sha256 {ev['runtime_sha256']}")
    r.checks[-1]["rows"] = got_rows


def _word(raw: str, i: int = 0) -> int:
    return int(raw[64 * i : 64 * (i + 1)], 16)


def decode_bytes_return(raw: str) -> bytes:
    """ABI-decode a single `bytes` return value (hex without 0x): offset, length, data."""
    offset = _word(raw) * 2
    length = int(raw[offset : offset + 64], 16)
    data = raw[offset + 64 : offset + 64 + 2 * length]
    if len(data) != 2 * length:
        raise ValueError("short bytes return")
    return bytes.fromhex(data)


def address_return(raw: str) -> str:
    return "0x" + raw[24:64]


def module_checks(r: erc8004.Report, url: str, ctx: dict) -> None:
    m, policy = ctx["manifest"], ctx["policy"]
    onchain = m["onchain"]
    module = str(onchain.get("module", ""))
    want_chain = onchain.get("chain_id")
    chain_id = int(rpc(url, "eth_chainId", []), 16)
    if not r.add("module.chain_id", chain_id == want_chain, want_chain, chain_id):
        return
    code = rpc(url, "eth_getCode", [module, "latest"])
    if not r.add("module.has_code", len(code) > 2, "contract code", f"{max(0, len(code) - 2) // 2} bytes"):
        return

    def read(sig: str) -> str:
        return _call(url, module, "0x" + erc8004.selector(sig))

    sha = "0x" + read("netlistSha256()")[:64]
    r.add("module.netlistSha256", sha.lower() == str(m["circuit"]["netlist_sha256"]).lower(), m["circuit"]["netlist_sha256"], sha)
    stored = decode_bytes_return(read("netlist()"))
    r.add("module.netlist_bytes", stored == ctx["netlist"], f"{len(ctx['netlist'])} bytes equal to the manifest", f"{len(stored)} bytes, {'equal' if stored == ctx['netlist'] else 'different'}")
    for sig, want in (("nIn()", len(policy.input_names())), ("nState()", policy.state_bits), ("optionalInputs()", erc8004.optional_inputs_mask(policy))):
        got = _word(read(sig))
        r.add(f"module.{sig[:-2]}", got == want, want, got)
    if "safe" in onchain:
        got = address_return(read("safe()"))
        r.add("module.safe", got.lower() == str(onchain["safe"]).lower(), onchain["safe"], got)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", help="manifest file path or http(s) URL")
    ap.add_argument("--request-hash", help="the requestHash recorded on chain; the manifest bytes must hash to it")
    ap.add_argument("--rpc", help="JSON-RPC endpoint for read-only checks (default for a module on 56/97: a public node)")
    ap.add_argument("--offline", action="store_true", help="no network at all; a manifest naming a module then cannot score 100")
    ap.add_argument("--rows", type=int, default=6, help="rows to evaluate on chain through eth_call (0: none)")
    ap.add_argument("--out", type=Path, help="write the validation report here")
    ap.add_argument("--response-uri", default="$RESPONSE_URI", help="where the report will be published")
    args = ap.parse_args()

    raw = fetch(args.manifest)
    report, ctx = erc8004.validate(raw, expected_hash=args.request_hash)
    digest = erc8004.keccak256(raw)

    url = None
    if not args.offline:
        chain = ((ctx or {}).get("manifest") or {}).get("onchain", {}).get("chain_id") if ctx else None
        url = args.rpc or DEFAULT_RPC.get(chain)
    extra = {}
    if ctx is not None:
        if url and args.rows > 0:
            try:
                chain_rows(report, url, ctx, args.rows)
            except (SystemExit, Exception) as e:
                report.add("eth_call_rows", False, "node answers", f"{type(e).__name__}: {e}")
        if "onchain" in ctx["manifest"]:
            if url:
                try:
                    module_checks(report, url, ctx)
                except (SystemExit, Exception) as e:
                    report.add("module.read", False, "node answers", f"{type(e).__name__}: {e}")
            else:
                report.skip("module", "the manifest names a deployed module and no RPC was used; pass --rpc")
        if url:
            extra["rpc"] = url

    out = erc8004.build_report(report, digest, extra)
    body = erc8004.canonical_bytes(out)
    response_hash = erc8004.keccak256(body)
    if args.out:
        args.out.write_bytes(body)

    for c in report.checks:
        mark = "skip" if c.get("skipped") else ("ok  " if c["ok"] else "FAIL")
        detail = "" if c["ok"] else f"  expected {json.dumps(c.get('expected'))[:120]} got {json.dumps(c.get('got'))[:120]}"
        print(f"{mark} {c['name']}{detail}" + (f"  ({c['note']})" if c.get("note") and not c["ok"] else ""))
    print(f"manifest keccak256 {digest}")
    print(f"score {report.score}" + (f"; failing: {', '.join(report.failing())}" if report.failing() else ""))
    print(f"report keccak256 {response_hash}" + (f" written to {args.out}" if args.out else " (pass --out to keep the report)"))
    request = args.request_hash or digest
    print()
    print("# ERC-8004 response (printed only; nothing has been sent). Must be sent from the validator address")
    print("# named in validationRequest; the Validation Registry address is yours to supply.")
    print(
        f'cast send "$VALIDATION_REGISTRY" "validationResponse(bytes32,uint8,string,bytes32,string)" '
        f'{request} {report.score} "{args.response_uri}" {response_hash} "{erc8004.VALIDATION_TAG}" '
        f'--rpc-url "$YOUR_RPC" --private-key "$YOUR_KEY"'
    )
    if report.score != 100:
        sys.exit(1)


if __name__ == "__main__":
    main()
