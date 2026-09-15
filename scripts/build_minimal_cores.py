"""Smaller escape cores with the same behaviour as core-hand-abc (v0.3.0, step S1).

    python scripts/build_minimal_cores.py

Writes circuits/loom-escape-min/core-hand-abc-irr.json and core-reach.json from the
committed circuits/loom-escape/core-hand-abc.json.

Question
--------
core-hand-abc is the hand-written motor state machine of `reflex.escape_core` with
the 74-NAND policy-hand-abc inlined; ABC never optimised the core as a whole. From
reset only 12 of its 64 latch states occur. How many of its 173 NAND gates are
needed (a) to reproduce its step relation on all 2^23 (input, state) rows, and
(b) to reproduce its behaviour from reset only?

Method
------
Both circuits come from one loop: remove single stuck-at redundancy (judged by the
repository's own bit-sliced evaluator), then alternate an ABC pass with another
removal while the NAND count keeps falling. ABC output is untrusted; a candidate
that fails the specification's check is discarded.

  irr    specification: the step relation on every row. ABC pass: combinational
         recipes on the latch-cut circuit. Check: step tables equal on 2^23 rows.
         The result has no undetectable single stuck-at fault on those rows.
  reach  specification: behaviour from the all-zero reset state; the other states
         are don't-cares. Starts from irr. Redundancy is judged on rows whose state
         is reachable. ABC pass: the irr recipes plus `scorr` before each recipe on
         the sequential circuit. Check: product-machine search from reset finds no
         output difference. The result has no undetectable single stuck-at fault on
         every input at every reachable state.

Both are verified upper bounds for their specifications, not proven minima. Because
reach starts from irr and differs from it only in the specification, the gap between
them is what the unreachable states contribute under this procedure.

Pre-set judgement (committed before either core was synthesised)
-----------------------------------------------------------------
  primary    R = (NAND(irr) - NAND(reach)) / NAND(irr)
             R >= 10 %  reachable-state don't-cares give a significant saving
             R <=  2 %  the hypothesis that they matter is weakened
             otherwise  a modest saving
  secondary  the same thresholds for NAND(reach) against core-hand-abc (173). This
             was the comparison first written in the project plan; it also counts
             ordinary redundancy that irr removes, so it is reported, not judged.

Pilot disclosure
----------------
Before the thresholds above were committed, an uncommitted pilot counted the
reachable states of core-hand-abc (12 of 64), its undetectable single stuck-at
faults on all 2^23 rows (23 faults on 21 gates; 6 NAND outputs are constant) and
those of policy-hand-abc on 2^16 rows (none). While checking c3s/reach.py against
that pilot, the count on reachable rows only was also seen (23). Mechanics were checked on toy
circuits only: a 2-row PLA showed that ABC's read_pla treats omitted rows as
off-set rather than don't-care, which is why don't-cares are exploited here by the
repository's own fault-based removal, and a 3-latch counter showed that `scorr`
output round-trips through mapped BLIF with latches. Neither core was synthesised
before the commit that added this docstring.

Bytes depend on the ABC build, so the committed artifacts are gated by
equivalence (tests/test_minimal_cores.py) rather than byte reproduction, and
scripts/verify.sh does not rerun this script.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from c3s import calibrate, exhaust, loom, reach, synth
from c3s.netlist import Circuit, Nand, from_bytes, to_bytes

ROOT = Path(__file__).resolve().parents[1]
LOOM = ROOT / "circuits" / "loom-escape"
OUT = ROOT / "circuits" / "loom-escape-min"
SOURCE = "core-hand-abc"
SIGNIFICANT, WEAKENED = 0.10, 0.02
MAX_ROUNDS = 8


def load_manifest(path: Path) -> tuple[dict, Circuit]:
    m = json.loads(path.read_text())
    return m, from_bytes(bytes.fromhex(m["tapeout_netlist_hex"][2:]), len(m["inputs"]), len(m["outputs"]))


def manifest(name: str, circuit: Circuit, inputs, outputs, extra: dict) -> dict:
    raw = to_bytes(circuit)
    m = {
        "format": "c3s.circuit/1",
        "name": name,
        "inputs": list(inputs),
        "outputs": list(outputs),
        "metrics": circuit.metrics(),
        "netlist_sha256": hashlib.sha256(raw).hexdigest(),
        "tapeout_netlist_hex": "0x" + raw.hex(),
    }
    m.update(extra)
    return m


def reachable_domain(c: Circuit) -> reach.Domain:
    return reach.states_domain(c, reach.reachable_states(c))


def redundancy(c: Circuit, dom: reach.Domain) -> dict:
    faults = reach.undetectable_faults(c, dom)
    sig = reach.evaluate(c, dom)
    first = c.first_cell_signal
    constant = sum(
        1
        for i, cell in enumerate(c.cells)
        if isinstance(cell, Nand) and (not np.any(sig[first + i] & dom.mask) or not np.any(~sig[first + i] & dom.mask))
    )
    return {
        "rows": dom.rows,
        "single_stuck_at_faults": 2 * reach.nand_count(c),
        "undetectable": len(faults),
        "gates_with_undetectable_faults": len({g for g, _ in faults}),
        "constant_nand_outputs": constant,
    }


def verdict(r: float) -> str:
    return "significant" if r >= SIGNIFICANT else "weakened" if r <= WEAKENED else "modest"


def shrink(label: str, start: Circuit, domain_of, abc_pass, accept) -> tuple[Circuit, list[dict]]:
    cur, removed = reach.remove_redundancy(start, domain_of)
    if not accept(cur):
        raise RuntimeError(f"{label}: redundancy removal broke the specification")
    history = [{"step": "redundancy removal", "faults_tied": len(removed), "nand": reach.nand_count(cur)}]
    print(f"{label}: redundancy removal {reach.nand_count(start)} -> {reach.nand_count(cur)} NAND", flush=True)
    for rnd in range(1, MAX_ROUNDS + 1):
        cands, errors = abc_pass(cur)
        recipes, ok = {}, {}
        for key, c in sorted(cands.items()):
            good = accept(c)
            recipes[key] = {"nand": reach.nand_count(c), "meets_specification": good}
            if good:
                ok[key] = c
        for key, err in sorted(errors.items()):
            print(f"{label}: {key} failed: {err}", flush=True)
            recipes[key] = {"failed": True}  # the message can carry local temp paths
        entry: dict = {"step": f"ABC round {rnd}", "recipes": recipes}
        history.append(entry)
        if not ok:
            break
        best = min(ok, key=lambda k: (reach.nand_count(ok[k]), k))
        cand, removed = reach.remove_redundancy(ok[best], domain_of)
        if not accept(cand):
            raise RuntimeError(f"{label}: redundancy removal after {best} broke the specification")
        entry.update(selected=best, faults_tied_after=len(removed), nand=reach.nand_count(cand))
        print(f"{label}: round {rnd} {best} {reach.nand_count(ok[best])} -> {reach.nand_count(cand)} NAND", flush=True)
        if reach.nand_count(cand) >= reach.nand_count(cur):
            break
        cur = cand
    return cur, history


def main() -> None:
    tj = json.loads((LOOM / "decision-table.json").read_text())
    p = calibrate.params_from_dict(tj["params"])
    enc = loom.Encoding(**{k: tuple(v) if isinstance(v, list) else v for k, v in tj["encoding"].items()})
    src_m, core = load_manifest(LOOM / f"{SOURCE}.json")
    src_outs, src_nxt = exhaust.step_table(core)
    n_latch = len(reach.latch_indices(core))
    src_states = reach.reachable_states(core)
    source = {
        "circuit": SOURCE,
        "netlist_sha256": src_m["netlist_sha256"],
        "nand": reach.nand_count(core),
        "latch_states": 1 << n_latch,
        "reachable_states": len(src_states),
        "redundancy_all_rows": redundancy(core, reach.full_domain(core)),
        "redundancy_reachable_rows": redundancy(core, reachable_domain(core)),
    }
    print("source:", json.dumps(source), flush=True)

    def same_step_relation(c: Circuit) -> bool:
        if len(reach.latch_indices(c)) != n_latch:
            return False
        o, n = exhaust.step_table(c)
        return bool(np.array_equal(o, src_outs) and np.array_equal(n, src_nxt))

    def same_from_reset(c: Circuit) -> bool:
        return bool(reach.sequential_equivalence(core, c)["equivalent"])

    def full_pass(c: Circuit):
        return reach.abc_candidates(c, sequential=False)

    def reach_pass(c: Circuit):
        comb, e1 = reach.abc_candidates(c, sequential=False)
        seq, e2 = reach.abc_candidates(c, sequential=True)
        return {**comb, **seq}, {**e1, **e2}

    irr, irr_history = shrink("irr", core, reach.full_domain, full_pass, same_step_relation)
    small, reach_history = shrink("reach", irr, reachable_domain, reach_pass, same_from_reset)

    # final checks, recorded in the manifests
    assert same_step_relation(irr)
    irr_red = redundancy(irr, reach.full_domain(irr))
    assert irr_red["undetectable"] == 0
    eq = reach.sequential_equivalence(core, small)
    assert eq["equivalent"]
    states = reach.reachable_states(small)
    closed = reach.is_closed(small, states)
    small_red = redundancy(small, reach.states_domain(small, states))
    assert closed and small_red["undetectable"] == 0
    same_encoding = len(reach.latch_indices(small)) == n_latch and all(a == b for a, b in eq["pairs"])

    sealed = json.loads((LOOM / "sealed-family.json").read_text())["stimuli"]
    families = {
        "train": loom.family("train"),
        "holdout": loom.family("holdout"),
        "sealed (published spec)": [loom.Stimulus(s["l_over_v_ms"], s["azimuth_deg"]) for s in sealed],
    }
    small_outs, small_nxt = exhaust.step_table(small)
    ticks = differing = 0
    for stims in families.values():
        for s in stims:
            want = reach.motor_trace(src_outs, src_nxt, s, p, enc)
            got = reach.motor_trace(small_outs, small_nxt, s, p, enc)
            ticks += len(want)
            differing += want != got
    assert differing == 0

    n_src, n_irr, n_reach = reach.nand_count(core), reach.nand_count(irr), reach.nand_count(small)
    primary = (n_irr - n_reach) / n_irr
    secondary = (n_src - n_reach) / n_src
    common = {k: src_m[k] for k in ("policy", "motor_codes") if k in src_m}
    abc = synth.abc_version()
    bound = "verified upper bound for this specification, not a proven minimum"

    OUT.mkdir(parents=True, exist_ok=True)
    irr_m = manifest(
        "core-hand-abc-irr",
        irr,
        src_m["inputs"],
        src_m["outputs"],
        {
            **common,
            "state_layout": src_m.get("state_layout"),
            "source": source,
            "specification": f"step relation of {SOURCE} on every (input, state) row",
            "equivalence": {"method": "bit-sliced step tables", "rows_checked": len(src_outs), "mismatches": 0},
            "redundancy": irr_red,
            "bound": bound,
            "synthesis": {"abc": abc, "history": irr_history},
            "bytes": "depend on the ABC build; the artifact is gated by equivalence",
        },
    )
    reach_m = manifest(
        "core-reach",
        small,
        src_m["inputs"],
        src_m["outputs"],
        {
            **common,
            "state_layout": src_m.get("state_layout") if same_encoding else "own encoding; see reachable_states",
            "source": {"circuit": SOURCE, "netlist_sha256": src_m["netlist_sha256"], "started_from": "core-hand-abc-irr"},
            "specification": f"behaviour of {SOURCE} from the all-zero reset state; unreachable states are don't-cares",
            "equivalence": {
                "method": "product-machine breadth-first search from reset, every input at every reachable state pair",
                "reachable_state_pairs": eq["reachable_state_pairs"],
                "rows_checked": eq["rows_checked"],
                "mismatches": 0,
            },
            "reachable_states": {"latches": len(reach.latch_indices(small)), "states": states, "closed": closed},
            "state_encoding_matches_source": same_encoding,
            "redundancy": small_red,
            "episodes": {
                "families": {k: len(v) for k, v in families.items()},
                "ticks_compared": ticks,
                "episodes_differing": differing,
            },
            "hypothesis": {
                "preregistered_in": "scripts/build_minimal_cores.py docstring",
                "thresholds": {"significant_min_reduction": SIGNIFICANT, "weakened_max_reduction": WEAKENED},
                "primary": {"irr_nand": n_irr, "reach_nand": n_reach, "reduction": round(primary, 4), "verdict": verdict(primary)},
                "secondary": {"source_nand": n_src, "reach_nand": n_reach, "reduction": round(secondary, 4), "judged": False},
            },
            "bound": bound,
            "synthesis": {"abc": abc, "history": reach_history},
            "bytes": "depend on the ABC build; the artifact is gated by equivalence",
        },
    )
    for m in (irr_m, reach_m):
        (OUT / f"{m['name']}.json").write_text(json.dumps(m, indent=1) + "\n")
        print(f"wrote {m['name']}: {m['metrics']}")
    print(f"primary reduction {primary:.1%} ({verdict(primary)}); secondary {secondary:.1%}")


if __name__ == "__main__":
    main()
