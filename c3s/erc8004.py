"""A compiled boundary as something an ERC-8004 registry can point at and anyone can check.

ERC-8004 (Trustless Agents, draft) gives an agent an ERC-721 identity whose `agentURI`
resolves to a registration file, free-form on-chain metadata, and a Validation Registry
in which a validator the agent's owner names answers a request with a score from 0 to
100. Every off-chain document is committed to by its keccak256.

This module writes the document: a *boundary manifest* for one compiled `Policy` — the
rules in their own words and settings, which channel each input bit must come from, the
netlist bytes and their SHA-256, every number the exhaustive checks produced, and the
lineage the circuit was compiled in (the fly escape core's netlist digest as parent) — as
canonical JSON whose keccak256 is what goes on chain. `validate` is the other half:
given those bytes it recompiles the policy, decodes the published netlist, re-runs
`verify()` and `properties()` on it and compares every recorded value, one named check
at a time. It needs nothing but this repository; `scripts/validate_boundary.py` adds the
read-only chain checks on top.

A score of 100 says the published circuit and the published checks agree with each
other and with the rules. It does not say an agent routes its actions through the
circuit, nor that the tool layer sets `blocked`/`confirm`/... honestly — the `limits`
list in every manifest says so, and `validate` refuses a manifest that drops it.

Nothing here signs, deploys or sends anything. keccak256 is computed by Foundry's
`cast keccak` in a subprocess, because the project environment has no keccak library;
`cast` must be on PATH or in ~/.foundry/bin.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from . import __version__
from .netlist import from_bytes, to_bytes
from .policy import AGENT_WRITABLE, MUST_COME_FROM_THE_TOOL_LAYER, OUTPUT_NAMES, Policy

ROOT = Path(__file__).resolve().parents[1]

FORMAT = "c3s.boundary/1"
REPORT_FORMAT = "c3s.boundary-validation/1"

# Every boundary circuit is descended from the first one by method, not by data: the fly
# escape core was compiled, checked on every row of its domain and proven over every
# reachable state by the same code that compiles a policy. A manifest says so by naming
# that core's netlist digest as its parent, and `validate` refuses a manifest that
# claims a parent which is not the committed core.
PARENT_CIRCUIT = "core-hand-abc"
PARENT_RELATION = "compiled-by-the-same-method"
METADATA_KEY = "c3s.boundary"
SERVICE_NAME = "c3s-boundary"
VALIDATION_TAG = "c3s-boundary"
REGISTRATION_TYPE = "https://eips.ethereum.org/EIPS/eip-8004#registration-v1"

# ERC-8004 registries as listed by github.com/erc-8004/erc-8004-contracts (README, commit
# b9e466c) and, for the identity registry, by the BNBAgent SDK's network presets
# (python/bnbagent/config.py, commit 0a16d57). Checked read-only on 2026-09-16: each
# address has code on its chain, the identity registry answers name() "AgentIdentity"
# and getVersion() "2.0.0", the reputation registry's getIdentityRegistry() returns the
# identity address beside it. Neither source lists a Validation Registry deployment on
# BSC, so its address is always an argument the user supplies.
IDENTITY_REGISTRY = {
    56: "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432",
    97: "0x8004A818BFB912233c491871b3d84c89A494BD9e",
}
REPUTATION_REGISTRY = {
    56: "0x8004BAa17C55a88189AE136b182e5fdA19dE9b63",
    97: "0x8004B663056A597Dffe9eCcC1965A193B7388713",
}
CHAIN_NAMES = {56: "BSC mainnet", 97: "BSC testnet", 204: "opBNB mainnet", 5611: "opBNB testnet"}

# ReflexModule's OPT_* bits (contracts/src/ReflexModule.sol).
OPTIONAL_INPUT_BITS = {"irreversible": 1, "failed": 2, "heartbeat": 4, "confirm_b": 8}

LIMITS = [
    "Ticks are not time: a rule over ticks bounds calls or decisions, and any number of ticks can fit in one block or one second.",
    "Every input except request and intent is a promise by the tool layer: the circuit cannot tell whether blocked, confirm, irreversible, failed, heartbeat or confirm_b mean what they are named.",
    "A local gate (hook, proxy, wallet provider) bounds only the channels routed through it; an action taken by any other path is never seen.",
    "The Safe-module form is the structural one: an agent that is not an owner and can act only through the module's act() cannot bypass the circuit, while the Safe owners and the supervisor key remain outside it.",
    "A validation score of 100 means the published circuit and checks match this manifest; it does not show that any agent is actually wired through the circuit.",
]


# -- bytes and hashes ---------------------------------------------------------


def canonical_bytes(obj) -> bytes:
    """The one serialisation that is hashed: sorted keys, no whitespace, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _cast() -> str:
    found = shutil.which("cast") or os.path.expanduser("~/.foundry/bin/cast")
    if not os.path.exists(found):
        raise RuntimeError("keccak256 needs Foundry's `cast` (https://getfoundry.sh) on PATH or in ~/.foundry/bin")
    return found


def keccak256(data: bytes) -> str:
    """keccak256 as 0x-prefixed hex. Always hands `cast` hex, never a raw string, so
    bytes that happen to look like hex are not reinterpreted."""
    out = subprocess.run([_cast(), "keccak", "0x" + data.hex()], capture_output=True, text=True, check=True, timeout=30)
    digest = out.stdout.strip()
    if len(digest) != 66 or not digest.startswith("0x"):
        raise RuntimeError(f"unexpected output from cast keccak: {digest!r}")
    return digest


def manifest_hash(manifest: dict) -> str:
    return keccak256(canonical_bytes(manifest))


def selector(signature: str) -> str:
    """4-byte function selector, 8 hex digits, no prefix."""
    return keccak256(signature.encode())[2:10]


# -- the policy, as published -------------------------------------------------


def policy_settings(policy: Policy) -> dict:
    return dataclasses.asdict(policy)


def policy_from_settings(settings: dict) -> Policy:
    """Rebuild a Policy from recorded settings, refusing unknown keys and wrong types
    (a bool where a count belongs would compile to a different circuit)."""
    fields = {f.name: f for f in dataclasses.fields(Policy)}
    unknown = sorted(set(settings) - set(fields))
    if unknown:
        raise ValueError(f"unknown policy settings: {unknown}")
    for name, value in settings.items():
        want = bool if isinstance(fields[name].default, bool) else int
        if type(value) is not want:
            raise ValueError(f"{name} must be {want.__name__}, got {value!r}")
    return Policy(**settings)


def channel(name: str) -> str:
    if name in AGENT_WRITABLE:
        return "agent"
    if name in MUST_COME_FROM_THE_TOOL_LAYER:
        return "tool_layer"
    raise ValueError(f"unknown input {name!r}")


def channel_map(policy: Policy) -> list[dict]:
    return [{"bit": i, "name": n, "channel": channel(n)} for i, n in enumerate(policy.input_names())]


def optional_inputs_mask(policy: Policy) -> int:
    return sum(OPTIONAL_INPUT_BITS[n] for n in policy.input_names() if n in OPTIONAL_INPUT_BITS)


def parent_netlist_sha256(root: Path = ROOT) -> str:
    """The committed netlist SHA-256 of the fly escape core, 0x-prefixed."""
    raw = json.loads((root / "circuits" / "loom-escape" / f"{PARENT_CIRCUIT}.json").read_text())
    digest = str(raw["netlist_sha256"]).lower().removeprefix("0x")
    if len(digest) != 64:
        raise ValueError(f"{PARENT_CIRCUIT} has no usable netlist_sha256")
    return "0x" + digest


def lineage(root: Path = ROOT) -> dict:
    return {
        "parent": parent_netlist_sha256(root),
        "parent_name": PARENT_CIRCUIT,
        "relation": PARENT_RELATION,
    }


def git_commit(root: Path = ROOT) -> str:
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def tool_info() -> dict:
    return {"package": "c3s", "version": __version__, "git_commit": git_commit()}


def _checked(verify: dict, props: dict) -> dict:
    return {
        "rows": verify["rows"],
        "domain_bits": verify["domain_bits"],
        "matches_reference": bool(verify["outputs_match"] and verify["next_state_matches"]),
        "reachable_states": props["reachable_states"],
        "states_possible": props["states_possible"],
        "configurations_visited": props["configurations_visited"],
        "rows_checked": props["rows_checked"],
        "every_rule_holds": bool(props["holds"]),
        "violations": dict(props["violations"]),
    }


def build_manifest(
    policy: Policy,
    *,
    module: str | None = None,
    safe: str | None = None,
    chain_id: int | None = None,
    tool: dict | None = None,
) -> dict:
    """Compile, check exhaustively, and describe one policy. Raises if the checks fail:
    a manifest is only written for a circuit that passed them."""
    circuit = policy.build()
    verify = policy.verify(circuit)
    props = policy.properties(circuit)
    checked = _checked(verify, props)
    if not (checked["matches_reference"] and checked["every_rule_holds"]):
        raise ValueError(f"policy failed its own checks: {checked}")
    raw = to_bytes(circuit)
    metrics = circuit.metrics()
    manifest = {
        "format": FORMAT,
        "rules": {"describe": policy.describe(), "settings": policy_settings(policy)},
        "inputs": channel_map(policy),
        "outputs": list(OUTPUT_NAMES),
        "circuit": {
            "nand": metrics["nand"],
            "latch": metrics["latch"],
            "bytes": len(raw),
            "state_bits": policy.state_bits,
            "netlist_sha256": "0x" + hashlib.sha256(raw).hexdigest(),
            "netlist_hex": "0x" + raw.hex(),
        },
        "checked": checked,
        "lineage": lineage(),
        "limits": list(LIMITS),
        "tool": tool if tool is not None else tool_info(),
    }
    if module is not None:
        if chain_id is None:
            raise ValueError("an on-chain module needs its chain id")
        onchain = {"chain_id": int(chain_id), "module": module, "optional_inputs": optional_inputs_mask(policy)}
        if safe is not None:
            onchain["safe"] = safe
        manifest["onchain"] = onchain
    elif safe is not None or chain_id is not None:
        raise ValueError("--safe/--chain-id describe a deployed module; give --module too")
    return manifest


# -- checking a manifest ------------------------------------------------------


class Report:
    """Named checks, each recorded with what was expected and what was found."""

    def __init__(self) -> None:
        self.checks: list[dict] = []

    def add(self, name: str, ok: bool, expected=None, got=None, note: str | None = None) -> bool:
        entry = {"name": name, "ok": bool(ok), "expected": expected, "got": got}
        if note:
            entry["note"] = note
        self.checks.append(entry)
        return bool(ok)

    def skip(self, name: str, why: str) -> None:
        self.checks.append({"name": name, "ok": False, "skipped": True, "note": why})

    @property
    def score(self) -> int:
        # Binary on purpose: a boundary that matches its manifest in most respects is not
        # a partially trustworthy boundary.
        return 100 if self.checks and all(c["ok"] for c in self.checks) else 0

    def failing(self) -> list[str]:
        return [c["name"] for c in self.checks if not c["ok"]]


def validate(raw: bytes, *, expected_hash: str | None = None, report: Report | None = None) -> tuple[Report, dict | None]:
    """Everything that can be checked from the manifest bytes alone. Returns the report
    and the decoded circuit context (for chain checks), or None if decoding failed."""
    r = report if report is not None else Report()
    digest = keccak256(raw)
    if expected_hash is not None:
        r.add("hash_matches_request", digest.lower() == expected_hash.lower(), expected_hash, digest)
    try:
        m = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        r.add("json_parses", False, "UTF-8 JSON", str(e))
        return r, None
    if not isinstance(m, dict):
        r.add("json_parses", False, "object", type(m).__name__)
        return r, None
    r.add("canonical_bytes", canonical_bytes(m) == raw, "sorted keys, no whitespace, UTF-8", None if canonical_bytes(m) == raw else "differs")
    r.add("format", m.get("format") == FORMAT, FORMAT, m.get("format"))
    r.add("limits_stated", m.get("limits") == LIMITS, LIMITS, m.get("limits"))
    # A manifest may claim descent only from the circuit this repository publishes: a
    # parent digest that is not the committed core's is a claim about another lineage.
    want_lineage = lineage()
    got_lineage = m.get("lineage")
    if isinstance(got_lineage, dict):
        got_lineage = dict(got_lineage)
        got_lineage["parent"] = str(got_lineage.get("parent", "")).lower()
        if not got_lineage["parent"].startswith("0x"):
            got_lineage["parent"] = "0x" + got_lineage["parent"]
    r.add("lineage", got_lineage == want_lineage, want_lineage, m.get("lineage"))

    rules = m.get("rules") or {}
    try:
        policy = policy_from_settings(rules.get("settings") or {})
    except (TypeError, ValueError) as e:
        r.add("settings_compile", False, "valid Policy settings", str(e))
        return r, None
    r.add("settings_compile", True)
    r.add("rules_describe", rules.get("describe") == policy.describe(), policy.describe(), rules.get("describe"))
    r.add("input_channels", m.get("inputs") == channel_map(policy), channel_map(policy), m.get("inputs"))
    r.add("outputs", m.get("outputs") == list(OUTPUT_NAMES), list(OUTPUT_NAMES), m.get("outputs"))

    c = m.get("circuit") or {}
    try:
        published = bytes.fromhex(str(c.get("netlist_hex", ""))[2:])
    except ValueError as e:
        r.add("netlist_hex_decodes", False, "hex", str(e))
        return r, None
    recorded_sha = str(c.get("netlist_sha256", "")).lower()
    actual_sha = "0x" + hashlib.sha256(published).hexdigest()
    r.add("netlist_sha256", actual_sha == recorded_sha, recorded_sha, actual_sha)

    compiled = policy.build()
    compiled_raw = to_bytes(compiled)
    r.add("netlist_equals_recompiled", published == compiled_raw, "0x" + hashlib.sha256(compiled_raw).hexdigest(), actual_sha)

    try:
        circuit = from_bytes(published, len(policy.input_names()), len(OUTPUT_NAMES))
        circuit.validate()
    except Exception as e:  # a corrupted netlist is a failed check, not a crash
        r.add("netlist_decodes", False, "a well-formed netlist", f"{type(e).__name__}: {e}")
        return r, None
    r.add("netlist_decodes", True)
    metrics = circuit.metrics()
    for key, got in (("nand", metrics["nand"]), ("latch", metrics["latch"]), ("bytes", len(published)), ("state_bits", policy.state_bits)):
        r.add(f"circuit.{key}", c.get(key) == got, c.get(key), got)

    # The checks run on the circuit decoded from the published bytes: that is the thing
    # being vouched for, whether or not it happens to equal the recompile.
    recorded = m.get("checked") or {}
    try:
        fresh = _checked(policy.verify(circuit), policy.properties(circuit))
    except Exception as e:
        r.add("checks_rerun", False, "verify() and properties() complete", f"{type(e).__name__}: {e}")
        return r, None
    for key, got in fresh.items():
        r.add(f"checked.{key}", recorded.get(key) == got, recorded.get(key), got)
    r.add("checked.no_extra_fields", set(recorded) == set(fresh), sorted(fresh), sorted(recorded))
    r.add("fresh_checks_pass", fresh["matches_reference"] and fresh["every_rule_holds"], True, fresh["matches_reference"] and fresh["every_rule_holds"])

    onchain = m.get("onchain")
    if onchain is not None:
        want_mask = optional_inputs_mask(policy)
        r.add("onchain.optional_inputs", onchain.get("optional_inputs") == want_mask, want_mask, onchain.get("optional_inputs"))
    return r, {"manifest": m, "policy": policy, "circuit": circuit, "netlist": published, "hash": digest}


def build_report(r: Report, manifest_digest: str, extra: dict | None = None) -> dict:
    out = {
        "format": REPORT_FORMAT,
        "manifest_hash": manifest_digest,
        "score": r.score,
        "tag": VALIDATION_TAG,
        "failing": r.failing(),
        "checks": r.checks,
        "validator_tool": tool_info(),
    }
    if extra:
        out.update(extra)
    return out


# -- what a user would send (printed, never sent) -----------------------------


def registration_file(name: str, description: str, manifest_uri: str, digest: str, chain_id: int | None, image: str = "") -> dict:
    """An ERC-8004 registration file whose services list carries the boundary. The spec
    lets services be any number and type; it lists three `supportedTrust` values and says
    nothing about others, so the boundary is advertised as a service plus on-chain
    metadata rather than as a new trust value. `registrations` is left empty: the
    agentId exists only after `register`, and the file is updated with setAgentURI."""
    return {
        "type": REGISTRATION_TYPE,
        "name": name,
        "description": description,
        "image": image,
        "services": [{"name": SERVICE_NAME, "endpoint": manifest_uri, "version": FORMAT, "keccak256": digest}],
        "registrations": [],
        "x402Support": False,
        "active": True,
    }


def metadata_value(manifest_uri: str, digest: str) -> bytes:
    return canonical_bytes({"keccak256": digest, "uri": manifest_uri})
