"""Publish the measured GF input wiring for the simulator page's 3D view.

    python scripts/build_sim_wiring.py      # writes docs/sim/wiring.json

The page cannot read data/, so the per-cell synapse counts already committed in
data/malecns-v1.0-gf-escape-subgraph.json are copied into docs/sim/wiring.json:
for each giant fiber, every presynaptic LC4 and LPLC2 cell with the number of
synapses it makes onto that fiber. These are measured counts from MaleCNS v1.0,
not a model and not anatomy -- there are no positions here, so the page must not
present the layout as morphology.

Deliberately omitted: body ids. They are public in data/ and in the release, but
the page has no use for them and leaving them out keeps the file small.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUBGRAPH = ROOT / "data" / "malecns-v1.0-gf-escape-subgraph.json"
OUT = ROOT / "docs" / "sim" / "wiring.json"
TYPES = ("LC4", "LPLC2")


def main() -> None:
    raw = SUBGRAPH.read_bytes()
    d = json.loads(raw)
    fibers = []
    for gf in d["giant_fibers"]:
        side = gf["instance"].rstrip(")").split("_")[-1]
        entry = {"instance": gf["instance"], "side": side, "visual_projection_input_synapses": gf["visual_projection_input_synapses"]}
        for t in TYPES:
            counts = sorted((int(n) for _, n in gf["inputs"][t]["per_cell_synapses"]), reverse=True)
            entry[t] = {"cells": gf["inputs"][t]["cells"], "synapses_per_cell": counts, "synapses": sum(counts)}
            if entry[t]["cells"] != len(counts):
                raise SystemExit(f"{gf['instance']} {t}: {entry[t]['cells']} cells but {len(counts)} rows")
        fibers.append(entry)
    doc = {
        "format": "c3s.wiring/1",
        "source": {
            "release": d["release"],
            "licence": d["license"],
            "file": SUBGRAPH.name,
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "note": "measured synapse counts per presynaptic cell; no positions, so any layout drawn from this is connectivity, not anatomy",
        "giant_fibers": fibers,
        "lc4_to_lplc2": d["lc4_to_lplc2"],
        "lplc2_to_lc4": d["lplc2_to_lc4"],
    }
    OUT.write_text(json.dumps(doc, indent=1) + "\n")
    cells = sum(f[t]["cells"] for f in fibers for t in TYPES)
    print(f"wrote {OUT.relative_to(ROOT)}: {len(fibers)} giant fibers, {cells} presynaptic cells, {OUT.stat().st_size} bytes")


if __name__ == "__main__":
    main()
