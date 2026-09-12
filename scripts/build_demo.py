"""Embed the verified escape core and its reference episodes into the web demo.

    python scripts/build_demo.py

The demo (docs/demo/index.html) evaluates the committed core netlist gate by gate
in the browser. This script writes everything the page needs into the JSON block
between the DATA markers of that file: the netlists exactly as committed, the
encoding and teacher parameters, and reference episodes computed here in Python.
On load the page replays those episodes through its own evaluator and reports any
mismatch, so a stale or broken build is visible in the page itself.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

from c3s import calibrate, exhaust, loom, reflex
from c3s.netlist import Latch, from_bytes

ROOT = Path(__file__).resolve().parents[1]
LOOM = ROOT / "circuits" / "loom-escape"
PAGE = ROOT / "docs" / "demo" / "index.html"
BEGIN, END = "<!-- DATA:BEGIN -->", "<!-- DATA:END -->"
EPISODES = [(10.0, 0.0), (40.0, -60.0), (80.0, 20.0)]


def circuit_entry(name: str) -> tuple[dict, object]:
    m = json.loads((LOOM / f"{name}.json").read_text())
    raw = bytes.fromhex(m["tapeout_netlist_hex"][2:])
    c = from_bytes(raw, len(m["inputs"]), len(m["outputs"]))
    return {
        "name": name,
        "hex": m["tapeout_netlist_hex"],
        "sha256": hashlib.sha256(raw).hexdigest(),
        "inputs": m["inputs"],
        "outputs": m["outputs"],
        "nState": sum(isinstance(x, Latch) for x in c.cells),
        "metrics": m["metrics"],
    }, c


def main() -> None:
    tj = json.loads((LOOM / "decision-table.json").read_text())
    p = calibrate.params_from_dict(tj["params"])
    enc = loom.Encoding(**{k: tuple(v) if isinstance(v, list) else v for k, v in tj["encoding"].items()})
    weights = loom.load_weights(ROOT / "data" / "malecns-v1.0-gf-escape-subgraph.json", p)

    core_meta, core = circuit_entry("core-hand-abc")
    policy_meta, _ = circuit_entry("policy-hand-abc")
    core_meta["stateLayout"] = reflex.core_state_layout(p)
    outs, nxt = exhaust.step_table(core)
    nf = enc.n_inputs

    episodes = []
    for lv, az in EPISODES:
        stim = loom.Stimulus(lv, az)
        state, ticks = 0, []
        for th, dth in loom.stimulus_samples(stim, p):
            x = loom.encode_features(th, dth, az, enc) | (1 << nf)
            row = x | (state << (nf + 1))
            motor, state = int(outs[row]), int(nxt[row])
            ticks.append([x, motor, state])
            if motor in (loom.CORE_SHORT, loom.CORE_LONG):
                break
        teacher = loom.run_teacher_episode(stim, weights, p)
        episodes.append({"lv": lv, "azimuth": az, "ticks": ticks, "teacher": [teacher.action, teacher.tick]})

    data = {
        "format": "c3s.demo/1",
        "core": core_meta,
        "policy": policy_meta,
        "encoding": {"bits": enc.bits, "sizeEdges": enc.size_edges_deg, "speedEdges": enc.speed_edges_dps},
        "teacher": {
            "sizeMu": p.size_mu_deg,
            "sizeSigma": p.size_sigma_deg,
            "speedScale": p.speed_scale_dps,
            "gfThreshold": p.gf_threshold,
            "parallelThreshold": p.parallel_threshold,
            "wingRaiseTicks": p.wing_raise_ticks,
            "refractoryTicks": p.refractory_ticks,
            "tickMs": p.tick_ms,
            "weights": {s: asdict(w) for s, w in weights.items()},
        },
        "stimulus": {"startDeg": 10.0, "endDeg": 170.0, "binocularOverlapDeg": 30.0},
        "motorNames": {str(k): v for k, v in loom.CORE_ACTION_NAMES.items()},
        "episodes": episodes,
    }
    html = PAGE.read_text()
    if BEGIN not in html or END not in html:
        raise SystemExit(f"{PAGE.relative_to(ROOT)} has no DATA markers")
    block = f'{BEGIN}\n<script type="application/json" id="c3s-data">{json.dumps(data, separators=(",", ":"))}</script>\n{END}'
    PAGE.write_text(re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), lambda _: block, html, flags=re.S))
    print(f"embedded {core_meta['name']} ({core_meta['metrics']['nand']} NAND + {core_meta['nState']} LATCH) and {len(episodes)} reference episodes into {PAGE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
