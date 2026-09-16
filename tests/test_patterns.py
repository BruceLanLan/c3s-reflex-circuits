"""The pattern registry (patterns/) and its replay tool (scripts/pattern_replay.py).

A pattern record claims that one stimulus produces one exact per-tick trace. These
tests establish, for the five seed records:

- every record matches patterns/pattern.schema.json, checked by the small validator
  below rather than by an optional dependency;
- the Python engine and the WebAssembly build of the firmware's C both reproduce every
  recorded tick exactly, and the recorded checksum is the checksum of those rows;
- every record can be re-derived from its own metadata and stimulus, byte for byte, so
  no trace in the registry was typed or edited by hand;
- a record with one integer changed, or one naming a different circuit, fails and says
  which check failed;
- the README's table lists exactly the records that exist.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pattern_replay as pr  # noqa: E402

PATTERNS = ROOT / "patterns"
RECORDS = sorted((PATTERNS / "records").glob("*.json"))
SCHEMA = json.loads((PATTERNS / "pattern.schema.json").read_text())
needs_node = pytest.mark.skipif(pr.wasm_available() is None, reason="node not installed")


# -- a validator for the subset of JSON Schema the pattern schema uses --------


def validate(value, schema: dict, path: str = "", root: dict | None = None) -> list[str]:
    """Returns a list of violations. Supports the keywords pattern.schema.json uses:
    $ref, const, enum, type, required, additionalProperties, properties, items (tuple
    and single), minItems, maxItems, uniqueItems, minLength, minimum, maximum,
    exclusiveMinimum, pattern."""
    root = root if root is not None else schema
    bad: list[str] = []
    if "$ref" in schema:
        target = root
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        return validate(value, target, path, root)
    if "const" in schema and value != schema["const"]:
        bad.append(f"{path}: {value!r} is not {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        bad.append(f"{path}: {value!r} is not one of {schema['enum']}")
    types = schema.get("type")
    if types is not None:
        names = [types] if isinstance(types, str) else types
        classes = {
            "object": dict,
            "array": list,
            "string": str,
            "number": (int, float),
            "integer": int,
            "null": type(None),
        }
        ok = any(
            isinstance(value, classes[n]) and not (n in ("number", "integer") and isinstance(value, bool)) for n in names
        )
        if not ok:
            bad.append(f"{path}: {type(value).__name__} is not {names}")
            return bad
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            bad.append(f"{path}: shorter than {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            bad.append(f"{path}: {value!r} does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            bad.append(f"{path}: {value} < {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            bad.append(f"{path}: {value} > {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            bad.append(f"{path}: {value} <= {schema['exclusiveMinimum']}")
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                bad.append(f"{path}: missing {name!r}")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in props:
                    bad.append(f"{path}: unexpected {name!r}")
        for name, sub in props.items():
            if name in value:
                bad += validate(value[name], sub, f"{path}.{name}", root)
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            bad.append(f"{path}: {len(value)} items < {schema['minItems']}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            bad.append(f"{path}: {len(value)} items > {schema['maxItems']}")
        if schema.get("uniqueItems") and len(value) != len({json.dumps(v) for v in value}):
            bad.append(f"{path}: items are not unique")
        items = schema.get("items")
        if isinstance(items, list):
            for i, sub in enumerate(items):
                if i < len(value):
                    bad += validate(value[i], sub, f"{path}[{i}]", root)
        elif isinstance(items, dict):
            for i, item in enumerate(value):
                bad += validate(item, items, f"{path}[{i}]", root)
    return bad


def test_the_validator_catches_what_it_is_meant_to():
    schema = {"type": "object", "additionalProperties": False, "required": ["a"],
              "properties": {"a": {"type": "integer", "minimum": 1}, "b": {"const": 2}}}
    assert validate({"a": 1}, schema) == []
    assert validate({}, schema)  # missing a
    assert validate({"a": 0}, schema)  # below minimum
    assert validate({"a": 1, "c": 3}, schema)  # unexpected key
    assert validate({"a": 1, "b": 3}, schema)  # wrong const
    assert validate({"a": True}, schema)  # a bool is not an integer here


# -- the registry -------------------------------------------------------------


def test_the_registry_still_holds_the_five_seeds():
    """A sixth record is welcome and needs no bookkeeping — the canon has no index — but
    the five seeds must not quietly leave."""
    assert {
        "p001-smallest-long-mode",
        "p002-one-ulp-past-the-overlap",
        "p003-twice-to-full-raise",
        "p004-takeoff-on-the-first-tick",
        "p005-raises-but-never-takes-off",
    } <= {p.stem for p in RECORDS}


@pytest.mark.parametrize("path", RECORDS, ids=lambda p: p.stem)
def test_record_matches_the_schema(path: Path):
    record = json.loads(path.read_text())
    assert validate(record, SCHEMA, path.stem) == []
    assert record["id"] == path.stem
    assert record["trace"]["rows"][0][0] == 0
    assert [row[0] for row in record["trace"]["rows"]] == list(range(record["result"]["ticks"]))
    # each row's state is the previous row's next_state, starting from reset
    states = [0] + [row[5] for row in record["trace"]["rows"][:-1]]
    assert [row[3] for row in record["trace"]["rows"]] == states


@pytest.mark.parametrize("path", RECORDS, ids=lambda p: p.stem)
def test_record_replays_identically_on_python(path: Path):
    r = pr.check_record(path, use_wasm=False)
    assert r.failures == []


@needs_node
@pytest.mark.parametrize("path", RECORDS, ids=lambda p: p.stem)
def test_record_replays_identically_on_python_and_wasm(path: Path):
    r = pr.check_record(path, use_wasm=True, canonical=True)
    assert r.failures == []


def test_the_readme_lists_every_record_and_nothing_else():
    text = (PATTERNS / "README.md").read_text()
    listed = set(re.findall(r"records/(p[0-9]{3}-[a-z0-9-]+)\.json", text))
    assert listed == {p.stem for p in RECORDS}


# -- what a bad record looks like ---------------------------------------------


def _write(tmp_path: Path, record: dict) -> Path:
    path = tmp_path / f"{record['id']}.json"
    path.write_bytes(pr.pretty_bytes(record))
    return path


def test_one_changed_integer_is_caught(tmp_path: Path):
    record = json.loads(RECORDS[0].read_text())
    record["trace"]["rows"][len(record["trace"]["rows"]) // 2][4] ^= 1
    r = pr.check_record(_write(tmp_path, record), use_wasm=False)
    assert "checksum" in r.failures and "python" in r.failures


def test_a_recomputed_checksum_does_not_rescue_a_changed_trace(tmp_path: Path):
    """Someone who edits a trace and re-hashes it still has to face the two hosts."""
    record = json.loads(RECORDS[0].read_text())
    record["trace"]["rows"][0][1] += 1
    record["trace"]["sha256"] = pr.trace_checksum(record["trace"]["rows"])
    r = pr.check_record(_write(tmp_path, record), use_wasm=False)
    assert r.failures == ["python"]


def test_a_record_naming_a_different_circuit_is_caught(tmp_path: Path):
    record = json.loads(RECORDS[0].read_text())
    record["circuit"]["domain_chain_sha256"] = "0x" + "11" * 32
    record["circuit"]["netlist_sha256"] = "22" * 32
    r = pr.check_record(_write(tmp_path, record), use_wasm=False)
    assert r.failures == ["circuit", "fingerprint"]


def test_a_hand_written_record_fails_the_canonical_check(tmp_path: Path):
    """Rule 4 of the canon: records are derived, not typed. Changing the summary a
    record carries about itself, without changing the trace, is still caught."""
    record = json.loads(RECORDS[0].read_text())
    record["result"]["ended"] = "short-mode takeoff"
    r = pr.check_record(_write(tmp_path, record), use_wasm=False, canonical=True)
    assert r.failures == ["canonical"]


def test_the_trace_checksum_covers_the_rows_and_nothing_else():
    record = json.loads(RECORDS[1].read_text())
    rows = record["trace"]["rows"]
    assert pr.trace_checksum(rows) == record["trace"]["sha256"]
    # the geometry is outside the checksum on purpose
    record["geometry"]["first_tick"][1] += 1.0
    assert pr.trace_checksum(record["trace"]["rows"]) == record["trace"]["sha256"]
    assert pr.trace_checksum(rows[:-1]) != record["trace"]["sha256"]
