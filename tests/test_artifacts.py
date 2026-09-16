"""The committed artifacts agree with themselves: each manifest's netlist bytes, hash
and metrics, the component index, the published sealed spec's digest, and the
netlists embedded in the web demo. Nothing here reruns a build (scripts/verify.sh
does that); these checks catch a hand-edited or partially regenerated artifact."""

import hashlib
import json
from pathlib import Path

import pytest

from c3s import loom
from c3s.netlist import from_bytes, to_bytes

ROOT = Path(__file__).resolve().parents[1]
LOOM = ROOT / "circuits" / "loom-escape"
COMPONENTS = ROOT / "circuits" / "components"
MANIFESTS = sorted(p for p in (ROOT / "circuits").rglob("*.json") if "tapeout_netlist_hex" in json.loads(p.read_text()))


def load(path: Path):
    return json.loads(path.read_text())


def test_every_circuit_directory_has_manifests():
    assert {p.parent.name for p in MANIFESTS} == {"components", "loom-escape", "loom-escape-min"}
    assert len(MANIFESTS) >= 20


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.stem)
def test_manifest_hash_and_metrics_match_its_netlist(path):
    m = load(path)
    raw = bytes.fromhex(m["tapeout_netlist_hex"].removeprefix("0x"))
    # learned-policy manifests carry no port lists: 16 feature bits in, 2 pathway bits out
    n_in = len(m["inputs"]) if "inputs" in m else loom.DEFAULT_ENCODING.n_inputs
    n_out = len(m["outputs"]) if "outputs" in m else 2
    circuit = from_bytes(raw, n_in, n_out)
    assert hashlib.sha256(raw).hexdigest() == m["netlist_sha256"]
    assert to_bytes(circuit) == raw
    want = m.get("metrics", m.get("optimized_metrics"))
    assert want is not None
    got = circuit.metrics()
    assert {k: got[k] for k in want} == want


def test_component_index_matches_component_manifests():
    index = load(COMPONENTS / "index.json")
    names = sorted(p.stem for p in COMPONENTS.glob("*.json") if p.stem != "index")
    assert [e["name"] for e in index] == names
    for entry in index:
        m = load(COMPONENTS / f"{entry['name']}.json")
        assert {k: m[k] for k in entry} == entry
        assert m["reference_mismatches"] == 0


def test_published_sealed_spec_matches_the_preregistered_digest():
    spec = (LOOM / "sealed-family.json").read_bytes()
    committed = (LOOM / "sealed-family.sha256").read_text().split()[0]
    assert hashlib.sha256(spec).hexdigest() == committed
    assert len(json.loads(spec)["stimuli"]) == 48


def test_demo_serves_the_pinned_three_js_itself():
    # build/three.min.js of three@0.160.0 from npm, byte for byte the file jsDelivr serves
    vendored = ROOT / "docs" / "demo" / "vendor" / "three-0.160.0.min.js"
    assert hashlib.sha256(vendored.read_bytes()).hexdigest() == "170c6789f43217c96b3170f4b42fafe135de7f7cd48497a4218f9757ee1d49fa"
    page = (ROOT / "docs" / "demo" / "index.html").read_text()
    assert '<script src="vendor/three-0.160.0.min.js"></script>' in page


def test_demo_embeds_the_committed_netlists():
    page = (ROOT / "docs" / "demo" / "index.html").read_text()
    block = page.split("<!-- DATA:BEGIN -->")[1].split("<!-- DATA:END -->")[0]
    data = json.loads(block[block.index("{") : block.rindex("}") + 1])
    core = load(LOOM / f"{data['core']['name']}.json")
    assert data["core"]["name"] == "core-hand-abc"
    assert core["policy"] == data["policy"]["name"]
    for key in ("core", "policy"):
        m = load(LOOM / f"{data[key]['name']}.json")
        assert data[key]["hex"] == m["tapeout_netlist_hex"]
        assert data[key]["sha256"] == m["netlist_sha256"]
        assert data[key]["metrics"] == m["metrics"]
