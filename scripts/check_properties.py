"""Temporal properties of the escape core, proven two independent ways (v0.3.0, S2).

    python scripts/check_properties.py                  # both methods
    python scripts/check_properties.py --method python  # or yosys
    python scripts/check_properties.py --controls       # also run the negative controls

The properties of `core-hand-abc` (motor codes 0 hold, 1 raising wings, 2 short-mode
takeoff, 3 long-mode takeoff; state = a 3-bit wing-raise counter then a 3-bit
refractory timer, LSB first; WING = wing_raise_ticks = 4, REFR = refractory_ticks = 7):

  P1  a takeoff tick is followed by no takeoff for the next REFR ticks
  P2  a long-mode takeoff is immediately preceded by WING consecutive raising ticks
  P3  when not standing, or while the refractory timer runs, the core holds, and the
      wing-raise counter is cleared for the next tick
  P4  a short-mode takeoff happens only with the giant-fiber pathway asserted and
      fewer than WING raising ticks accumulated
  P5  raise = 0, or refr = 0 and raise <= WING: the invariant that carves the 12
      reachable states out of 64

Method A (Python, always available). P3, P4 and P5 are checked on every one of the
8,388,608 (input, state) rows of the step relation, so they hold from any state,
reachable or not; for P5 that row-wise check is a one-step induction, and the reset
state satisfies the invariant, so it holds forever. P1 and P2 are trace properties:
a breadth-first search from reset over (core state, monitor state) configurations,
evaluating every input pattern at each one.

Method B (Yosys, when installed). formal/loom-escape.sv states the same five
properties over the committed netlist bytes, read as BLIF, and each is proven by
temporal induction (`sat -tempinduct -prove-asserts -set-init-zero -verify`) with no
assumptions -- no property is allowed to lean on another.

Negative controls (--controls). Each property is mutated into a neighbouring claim
that must fail, in both methods, so that "proven" cannot be confused with vacuous:
P1 with REFR + 1, P2 with WING + 1, P3 turned into "an active tick never holds",
P4 with "no raises" instead of "fewer than WING", P5 with raise < WING.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from c3s import calibrate, exhaust, reach
from c3s.netlist import Circuit, from_bytes

ROOT = Path(__file__).resolve().parents[1]
LOOM = ROOT / "circuits" / "loom-escape"
SPEC = ROOT / "formal" / "loom-escape.sv"
PROPERTIES = ("p1", "p2", "p3", "p4", "p5")

# Each control rewrites the spec into a claim that must fail.
CONTROLS = {
    "p1": ("`define REFR 7", "`define REFR 8"),
    "p2": ("`define WING 4", "`define WING 5"),
    "p3": ("!quiet || motor == 2'd0", "quiet || motor != 2'd0"),
    "p4": ("raise_c < `WING", "raise_c == 3'd0"),
    "p5": ("raise_c <= `WING", "raise_c < `WING"),
}


def load(name: str) -> Circuit:
    m = json.loads((LOOM / f"{name}.json").read_text())
    return from_bytes(bytes.fromhex(m["tapeout_netlist_hex"][2:]), len(m["inputs"]), len(m["outputs"]))


def teacher_timing() -> tuple[int, int]:
    tj = json.loads((LOOM / "decision-table.json").read_text())
    p = calibrate.params_from_dict(tj["params"])
    return p.wing_raise_ticks, p.refractory_ticks


def spec_timing() -> tuple[int, int]:
    text = SPEC.read_text()
    return tuple(int(re.search(rf"`define {k} (\d+)", text).group(1)) for k in ("WING", "REFR"))  # type: ignore[return-value]


def python_method(core: Circuit, policy: Circuit, wing: int, refr_ticks: int, mutate: str | None = None) -> dict:
    """Counterexample counts for each property; zero everywhere means proven."""
    nf = policy.n_inputs
    outs, nxt = exhaust.step_table(core)
    rows = np.arange(len(outs), dtype=np.int64)
    standing = (rows >> nf) & 1
    raise_c = (rows >> (nf + 1)) & 7
    refr = (rows >> (nf + 4)) & 7
    gf = exhaust.truth_table(policy).astype(np.int64)[rows & ((1 << nf) - 1)] & 1
    motor = outs.astype(np.int64)
    next_raise = nxt.astype(np.int64) & 7
    next_refr = (nxt.astype(np.int64) >> 3) & 7

    quiet = (standing == 0) | (refr != 0)
    short_limit = 0 if mutate == "p4" else wing
    inv_wing = wing - 1 if mutate == "p5" else wing

    def invariant(r, f):
        return (r == 0) | ((f == 0) & (r <= inv_wing))

    p3_bad = (~quiet & (motor == 0)) if mutate == "p3" else (quiet & ((motor != 0) | (next_raise != 0)))
    res = {
        "p3": {"rows": len(outs), "counterexamples": int(np.count_nonzero(p3_bad))},
        "p4": {"rows": len(outs), "counterexamples": int(np.count_nonzero((motor == 2) & ((gf == 0) | (raise_c >= short_limit))))},
        "p5": {
            "rows": len(outs),
            "counterexamples": int(np.count_nonzero(invariant(raise_c, refr) & ~invariant(next_raise, next_refr))),
            "reset_satisfies": bool(invariant(np.int64(0), np.int64(0))),
        },
    }
    res.update(_trace_method(core, wing, refr_ticks, mutate))
    return res


def _trace_method(core: Circuit, wing: int, refr_ticks: int, mutate: str | None = None) -> dict:
    """P1 and P2 over (core state, monitor) configurations reachable from reset."""
    refr_claim = refr_ticks + 1 if mutate == "p1" else refr_ticks
    wing_claim = wing + 1 if mutate == "p2" else wing
    cap = refr_claim + 1
    start = (0, cap, 0)  # (core state, ticks since takeoff, consecutive raising ticks)
    seen, frontier = {start}, [start]
    bad1 = bad2 = rows = 0
    while frontier:
        new = []
        for state, since, run in frontier:
            motor, nxt = reach.block_step(core, state)
            motor, nxt = motor.astype(np.int64), nxt.astype(np.int64)
            rows += motor.size
            takeoff = (motor == 2) | (motor == 3)
            bad1 += int(np.count_nonzero(takeoff & (since <= refr_claim)))
            bad2 += int(np.count_nonzero((motor == 3) & (run < wing_claim)))
            nsince = np.where(takeoff, 1, min(since + 1, cap))  # the tick after a takeoff is 1 tick later
            nrun = np.where(motor == 1, min(run + 1, wing_claim), 0)
            for key in np.unique((nxt << 8) | (nsince << 4) | nrun).tolist():
                nxt_state = (key >> 8, (key >> 4) & 15, key & 15)
                if nxt_state not in seen:
                    seen.add(nxt_state)
                    new.append(nxt_state)
        frontier = new
    return {
        "p1": {"configurations": len(seen), "rows": rows, "counterexamples": bad1},
        "p2": {"configurations": len(seen), "rows": rows, "counterexamples": bad2},
    }


def yosys_available() -> bool:
    return shutil.which("yosys") is not None


def yosys_method(core: Circuit, policy: Circuit, properties=PROPERTIES, mutate: str | None = None, maxsteps: int = 24) -> dict:
    """Temporal induction per property. `proved` is Yosys's own exit status."""
    spec = SPEC.read_text()
    if mutate:
        old, new = CONTROLS[mutate]
        if old not in spec:
            raise RuntimeError(f"control for {mutate} does not match the spec: {old!r}")
        spec = spec.replace(old, new, 1)
    out: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "core.blif").write_text(reach.circuit_to_seq_blif(core, model="c3s_core", expose_state=True))
        (work / "policy.blif").write_text(reach.circuit_to_seq_blif(policy, model="c3s_policy"))
        (work / "spec.sv").write_text(spec)
        for prop in properties:
            script = (
                f"read_blif {work}/core.blif; read_blif {work}/policy.blif; "
                f"read_verilog -formal {work}/spec.sv; hierarchy -top {prop}; proc; flatten; "
                f"chformal -lower; opt_clean; "
                f"sat -tempinduct -prove-asserts -set-init-zero -maxsteps {maxsteps} -verify"
            )
            res = subprocess.run(["yosys", "-p", script], capture_output=True, text=True, timeout=1800)
            log = (res.stdout + res.stderr).replace(str(work), "<tmp>")
            depths = re.findall(r"induction step (\d+)", log)
            out[prop] = {"proved": res.returncode == 0, "induction_depth": int(depths[-1]) if depths else None}
            if res.returncode != 0:
                out[prop]["log_tail"] = log[-400:]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--method", choices=("both", "python", "yosys"), default="both")
    ap.add_argument("--controls", action="store_true", help="also check that mutated claims fail")
    ap.add_argument("--maxsteps", type=int, default=24)
    args = ap.parse_args()

    wing, refr_ticks = teacher_timing()
    if (wing, refr_ticks) != spec_timing():
        raise SystemExit(f"formal/loom-escape.sv has WING/REFR {spec_timing()}, teacher has {(wing, refr_ticks)}")
    core, policy = load("core-hand-abc"), load("policy-hand-abc")
    failures = []

    if args.method in ("both", "python"):
        res = python_method(core, policy, wing, refr_ticks)
        for prop in PROPERTIES:
            r = res[prop]
            ok = r["counterexamples"] == 0 and r.get("reset_satisfies", True)
            print(f"python {prop}: {'proved' if ok else 'FAILED'} {json.dumps(r)}")
            failures += [] if ok else [f"python {prop}"]
        if args.controls:
            for prop in PROPERTIES:
                r = python_method(core, policy, wing, refr_ticks, mutate=prop)[prop]
                ok = r["counterexamples"] > 0
                print(f"python {prop} control: {'fails as it must' if ok else 'DID NOT FAIL'} {json.dumps(r)}")
                failures += [] if ok else [f"python {prop} control"]

    if args.method in ("both", "yosys"):
        if not yosys_available():
            print("yosys not installed; method B skipped")
        else:
            for prop, r in yosys_method(core, policy, maxsteps=args.maxsteps).items():
                print(f"yosys {prop}: {'proved' if r['proved'] else 'FAILED'} {json.dumps(r)}")
                failures += [] if r["proved"] else [f"yosys {prop}"]
            if args.controls:
                for prop in PROPERTIES:
                    r = yosys_method(core, policy, properties=(prop,), mutate=prop, maxsteps=args.maxsteps)[prop]
                    ok = not r["proved"]
                    print(f"yosys {prop} control: {'fails as it must' if ok else 'DID NOT FAIL'}")
                    failures += [] if ok else [f"yosys {prop} control"]

    if failures:
        raise SystemExit("failed: " + ", ".join(failures))
    print("all checks passed")


if __name__ == "__main__":
    main()
