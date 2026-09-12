"""Write every catalogue component as a circuit manifest.

    python scripts/export_components.py

Each manifest carries the TapeOut-layout netlist bytes, cost metrics and the
result of the exhaustive check against the component's reference model.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from c3s import exhaust
from c3s.components import CATALOG
from c3s.netlist import to_bytes

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "circuits" / "components"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for name, comp in sorted(CATALOG.items()):
        circuit = comp.build()
        outs, nxt = exhaust.step_table(circuit)
        n_in, rows = circuit.n_inputs, len(outs)
        want_o = np.empty(rows, np.uint64)
        want_s = np.empty(rows, np.uint64)
        for r in range(rows):
            want_o[r], want_s[r] = comp.reference(r & ((1 << n_in) - 1), r >> n_in)
        mismatches = int(np.count_nonzero(outs != want_o))
        if comp.n_state:
            mismatches += int(np.count_nonzero(nxt != want_s))
        raw = to_bytes(circuit)
        m = {
            "format": "c3s.circuit/1",
            "name": name,
            "summary": comp.summary,
            "inputs": list(comp.inputs),
            "outputs": list(comp.outputs),
            "n_state": comp.n_state,
            "metrics": circuit.metrics(),
            "reference_rows_checked": rows,
            "reference_mismatches": mismatches,
            "netlist_sha256": hashlib.sha256(raw).hexdigest(),
            "tapeout_netlist_hex": "0x" + raw.hex(),
        }
        if mismatches:
            raise SystemExit(f"{name}: {mismatches} mismatches against its reference")
        (OUT / f"{name}.json").write_text(json.dumps(m, indent=1) + "\n")
        index.append({k: m[k] for k in ("name", "summary", "n_state", "metrics", "reference_rows_checked", "reference_mismatches")})
        print(name, m["metrics"])
    (OUT / "index.json").write_text(json.dumps(index, indent=1) + "\n")


if __name__ == "__main__":
    main()
