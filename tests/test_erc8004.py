"""Boundary manifests for ERC-8004 (c3s/erc8004.py, scripts/boundary_manifest.py,
scripts/validate_boundary.py): the same policy always publishes the same bytes, a
manifest whose recorded numbers or netlist bytes were changed is caught by name, the
channel each input is published under is the one c3s.policy assigns, and nothing the
scripts print could carry a key."""

import json
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from c3s import erc8004
from c3s.policy import AGENT_WRITABLE, INPUT_NAMES, MUST_COME_FROM_THE_TOOL_LAYER, OPTIONAL_INPUTS, Policy

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

HAS_CAST = shutil.which("cast") is not None or Path("~/.foundry/bin/cast").expanduser().exists()
needs_cast = pytest.mark.skipif(not HAS_CAST, reason="keccak256 needs Foundry's cast")

POLICY = Policy(min_gap_ticks=8, max_grants=7, confirm_per_irreversible=True, two_key=True, forbid_when_blocked=True)
SETTINGS = '{"min_gap_ticks":8,"max_grants":7,"confirm_per_irreversible":true,"two_key":true,"forbid_when_blocked":true}'
TOOL = {"package": "c3s", "version": "test", "git_commit": "test"}


@pytest.fixture(scope="module")
def manifest() -> dict:
    return erc8004.build_manifest(POLICY, tool=TOOL)


@needs_cast
def test_keccak_is_keccak256_not_sha3():
    # The empty-string and "hello" digests every Ethereum tool agrees on.
    assert erc8004.keccak256(b"") == "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert erc8004.keccak256(b"hello") == "0x1c8aff950685c2ed4bc3174f3472287b56d9517b9c948127319a09a7a36deac8"
    assert erc8004.selector("netlistSha256()") == "9427f324"


@needs_cast
def test_manifest_is_deterministic(manifest):
    again = erc8004.build_manifest(POLICY, tool=TOOL)
    a, b = erc8004.canonical_bytes(manifest), erc8004.canonical_bytes(again)
    assert a == b
    assert erc8004.keccak256(a) == erc8004.keccak256(b)
    # canonical: re-serialising what was parsed gives the same bytes
    assert erc8004.canonical_bytes(json.loads(a)) == a
    assert b"\n" not in a and b'": ' not in a and b'", "' not in a


def test_manifest_records_the_checks(manifest):
    c, k = manifest["circuit"], manifest["checked"]
    assert manifest["format"] == erc8004.FORMAT
    assert manifest["rules"]["describe"] == POLICY.describe()
    assert erc8004.policy_from_settings(manifest["rules"]["settings"]) == POLICY
    assert k["rows"] == 1 << POLICY.domain_bits and k["domain_bits"] == POLICY.domain_bits
    assert k["matches_reference"] and k["every_rule_holds"]
    assert all(v == 0 for v in k["violations"].values())
    assert c["latch"] == POLICY.state_bits
    assert len(bytes.fromhex(c["netlist_hex"][2:])) == c["bytes"]
    assert manifest["limits"] == erc8004.LIMITS
    assert "onchain" not in manifest


def test_channel_map_matches_policy(manifest):
    names = [i["name"] for i in manifest["inputs"]]
    assert names == list(POLICY.input_names())
    assert [i["bit"] for i in manifest["inputs"]] == list(range(len(names)))
    for entry in manifest["inputs"]:
        want = "agent" if entry["name"] in AGENT_WRITABLE else "tool_layer"
        assert entry["channel"] == want
    # every input any policy can read has exactly one channel
    for name in INPUT_NAMES + OPTIONAL_INPUTS:
        assert (name in AGENT_WRITABLE) != (name in MUST_COME_FROM_THE_TOOL_LAYER)
        assert erc8004.channel(name) in ("agent", "tool_layer")
    assert erc8004.optional_inputs_mask(POLICY) == 1 | 8  # irreversible, confirm_b


def test_settings_are_strict():
    with pytest.raises(ValueError):
        erc8004.policy_from_settings({"min_gap_ticks": 8, "made_up": 1})
    with pytest.raises(ValueError):
        erc8004.policy_from_settings({"two_key": 1})
    with pytest.raises(ValueError):
        erc8004.policy_from_settings({"min_gap_ticks": True})
    with pytest.raises(ValueError):
        erc8004.build_manifest(POLICY, safe="0x0000000000000000000000000000000000000001", tool=TOOL)


@needs_cast
def test_untouched_manifest_scores_100(manifest):
    raw = erc8004.canonical_bytes(manifest)
    report, ctx = erc8004.validate(raw, expected_hash=erc8004.keccak256(raw))
    assert report.score == 100, report.failing()
    assert ctx is not None and ctx["netlist"].hex() == manifest["circuit"]["netlist_hex"][2:]


def _tampered(manifest: dict, change) -> bytes:
    m = json.loads(json.dumps(manifest))
    change(m)
    return erc8004.canonical_bytes(m)


def _flip_netlist_byte(m: dict) -> None:
    raw = bytearray(bytes.fromhex(m["circuit"]["netlist_hex"][2:]))
    raw[len(raw) // 2] ^= 0x01
    m["circuit"]["netlist_hex"] = "0x" + raw.hex()


TAMPERS = {
    "rows": (lambda m: m["checked"].__setitem__("rows", m["checked"]["rows"] + 1), "checked.rows"),
    "reachable": (lambda m: m["checked"].__setitem__("reachable_states", m["checked"]["reachable_states"] - 1), "checked.reachable_states"),
    "violation": (lambda m: m["checked"]["violations"].__setitem__("budget", 1), "checked.violations"),
    "holds": (lambda m: m["checked"].__setitem__("every_rule_holds", False), "checked.every_rule_holds"),
    "nand": (lambda m: m["circuit"].__setitem__("nand", m["circuit"]["nand"] - 1), "circuit.nand"),
    "latch": (lambda m: m["circuit"].__setitem__("latch", m["circuit"]["latch"] + 1), "circuit.latch"),
    "sha": (lambda m: m["circuit"].__setitem__("netlist_sha256", "0x" + "00" * 32), "netlist_sha256"),
    "netlist_byte": (_flip_netlist_byte, "netlist_sha256"),
    "setting": (lambda m: m["rules"]["settings"].__setitem__("max_grants", 6), "rules_describe"),
    "channel": (lambda m: m["inputs"][2].__setitem__("channel", "agent"), "input_channels"),
    "limits_dropped": (lambda m: m.__setitem__("limits", []), "limits_stated"),
}


@needs_cast
@pytest.mark.parametrize("which", sorted(TAMPERS))
def test_tampering_is_caught_by_name(manifest, which):
    change, check = TAMPERS[which]
    raw = _tampered(manifest, change)
    report, _ = erc8004.validate(raw)
    assert report.score < 100
    assert check in report.failing(), report.failing()


@needs_cast
def test_flipped_netlist_byte_also_differs_from_recompile(manifest):
    report, _ = erc8004.validate(_tampered(manifest, _flip_netlist_byte))
    assert "netlist_equals_recompiled" in report.failing()


@needs_cast
def test_reformatted_file_and_wrong_request_hash_are_caught(manifest):
    pretty = json.dumps(manifest, indent=2, sort_keys=True).encode()
    report, _ = erc8004.validate(pretty)
    assert "canonical_bytes" in report.failing()
    raw = erc8004.canonical_bytes(manifest)
    report, _ = erc8004.validate(raw, expected_hash="0x" + "11" * 32)
    assert report.failing() == ["hash_matches_request"]


KEYLIKE = re.compile(r"(?<![0-9a-fA-Fx])(0x)?[0-9a-fA-F]{64}(?![0-9a-fA-F])")


def _no_key_in(text: str, allowed: set[str]) -> None:
    assert "$YOUR_KEY" in text and "$YOUR_RPC" in text
    for line in text.splitlines():
        if "cast send" not in line:
            continue
        assert "--private-key \"$YOUR_KEY\"" in line
        for m in KEYLIKE.finditer(line):
            assert m.group(0).lower().removeprefix("0x") in allowed, f"unexpected 32-byte value in: {line}"


@needs_cast
def test_printed_commands_hold_placeholders_not_keys(tmp_path):
    out = tmp_path / "m.json"
    res = subprocess.run([PY, "scripts/boundary_manifest.py", "--policy", SETTINGS, "--out", str(out)], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stderr
    digest = erc8004.keccak256(out.read_bytes())
    assert f"manifest keccak256 {digest}" in res.stdout
    assert "$METADATA_HEX" in res.stdout and erc8004.IDENTITY_REGISTRY[97] in res.stdout
    _no_key_in(res.stdout, {digest[2:]})

    report = tmp_path / "r.json"
    res = subprocess.run([PY, "scripts/validate_boundary.py", str(out), "--offline", "--out", str(report)], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stdout + res.stderr
    body = report.read_bytes()
    assert json.loads(body)["score"] == 100
    response = erc8004.keccak256(body)
    assert f'{digest} 100 "$RESPONSE_URI" {response} "c3s-boundary"' in res.stdout
    _no_key_in(res.stdout, {digest[2:], response[2:]})


@needs_cast
def test_manifest_naming_a_module_needs_the_chain_to_score_100(tmp_path):
    m = erc8004.build_manifest(POLICY, module="0x000000000000000000000000000000000000dEaD", chain_id=97, tool=TOOL)
    assert m["onchain"]["optional_inputs"] == 9
    path = tmp_path / "m.json"
    path.write_bytes(erc8004.canonical_bytes(m))
    res = subprocess.run([PY, "scripts/validate_boundary.py", str(path), "--offline"], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert res.returncode == 1
    assert "skip module" in res.stdout and "score 0" in res.stdout


@needs_cast
def test_module_return_values_decode(manifest):
    # What a ReflexModule's netlist() and safe() return, ABI-encoded by cast rather than
    # by this code, so the decoder is checked against an independent encoder.
    sys.path.insert(0, str(ROOT / "scripts"))
    import validate_boundary as vb

    cast = shutil.which("cast") or str(Path("~/.foundry/bin/cast").expanduser())
    netlist = manifest["circuit"]["netlist_hex"]
    blob = subprocess.run([cast, "abi-encode", "f(bytes)", netlist], capture_output=True, text=True, check=True).stdout.strip()
    assert "0x" + vb.decode_bytes_return(blob[2:]).hex() == netlist
    with pytest.raises(ValueError):
        vb.decode_bytes_return(blob[2:-64])
    safe = "0x00000000000000000000000000000000000000a5"
    blob = subprocess.run([cast, "abi-encode", "f(address)", safe], capture_output=True, text=True, check=True).stdout.strip()
    assert vb.address_return(blob[2:]) == safe


def _online(url: str) -> bool:
    try:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}).encode()
        req = urllib.request.Request(url, data=body, headers={"content-type": "application/json", "user-agent": "c3s-test"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return "result" in json.loads(resp.read())
    except Exception:
        return False


@needs_cast
def test_rows_agree_on_bsc_and_a_non_module_is_refused(tmp_path):
    url = "https://bsc-testnet-rpc.publicnode.com"
    if not _online(url):
        pytest.skip("no network, or the public BSC testnet node did not answer")
    # A contract that exists but is not a ReflexModule: the identity registry.
    m = erc8004.build_manifest(POLICY, module=erc8004.IDENTITY_REGISTRY[97], chain_id=97, tool=TOOL)
    path = tmp_path / "m.json"
    path.write_bytes(erc8004.canonical_bytes(m))
    res = subprocess.run([PY, "scripts/validate_boundary.py", str(path), "--rpc", url, "--rows", "4"], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert "ok   eth_call_rows" in res.stdout, res.stdout
    assert "ok   module.chain_id" in res.stdout and "ok   module.has_code" in res.stdout
    assert res.returncode == 1 and "score 0" in res.stdout
