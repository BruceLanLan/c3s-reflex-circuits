# Circuit layers: netlists, proofs and the on-chain evaluator

This document covers L2 (hard circuits), L3 (equivalence evidence) and L4 (the
public on-chain form).

## Netlist format

One intermediate representation is used from synthesis through to chain
(`c3s/netlist.py`).

| Signal | Meaning |
| --- | --- |
| 0 | constant 0 |
| 1 | constant 1 |
| 2 … 2 + n − 1 | primary inputs |
| then one per cell | cell outputs, in cell order |

| Cell | Bytes | Semantics |
| --- | --- | --- |
| `NAND a b` | `0x00 ‖ u24 a ‖ u24 b` | `¬(a ∧ b)`; `a`, `b` must be earlier signals |
| `LATCH d` | `0x01 ‖ u24 d` | outputs the bit stored at the end of the previous tick (0 initially), then stores signal `d`; `d` may point forwards |
| `REF` | `0x02 ‖ address ‖ u64 id ‖ u8 nIn ‖ u8 nOut ‖ u24 × nIn` | calls another deployed circuit; decoded and evaluated, not used by the circuits here |

Primary outputs are the **last** `nOut` signals. `Builder.finish` removes dead
cells and places outputs at the tail, duplicating a NAND or adding a double
inversion only when the layout forces it.

### Compatibility with the TapeOut netlist layout

This layout and its tick semantics were cross-checked against the web
application publicly served at tapeout.net on 2026-09-12:

* the public decoder module (SHA-256 `3f857e4a…bb89d9dd`) and tick evaluator
  module (SHA-256 `db9bb3ce…0c3c5c23`) were run under Node.js outside this
  repository;
* 400 random sequential circuits (up to 8 inputs, up to 119 cells, about 20 % latches),
  4,800 ticks with carried state: **0 mismatches** against `c3s.netlist`;
* the final `policy-hand-abc` over all 65,536 inputs and three `core-hand-abc`
  episodes (345 ticks with state): **0 mismatches**.

Observed in the same bundle but **not** verified against the deployed protocol:
the canvas import limits (256 pins, 20,000 cells, 2 MB) and the factory
signature `tapeout(bytes nl, uint32 nIn, uint32 nOut) payable`. Fees, on-chain
size limits and deployment behaviour were **not** checked, and nothing from this
repository has been deployed to any chain.

### In-browser evaluator (`docs/demo/`)

The web demo carries a third evaluator, written in JavaScript from the tables above
(`<script id="c3s-evaluator">` in `docs/demo/index.html`). On 2026-09-12 it was run
under Node.js over the complete domain of `core-hand-abc` (8,388,608 rows) and
`policy-hand-abc` (65,536 rows): its output and next-state bytes were identical to
`exhaust.step_table` and to the public tapeout.net evaluator. On every page load it
also checks the SHA-256 of the embedded netlist and replays the reference episodes
written by `scripts/build_demo.py`, and shows any mismatch on screen.

## Circuit inventory

### LoomEscape-16 (`circuits/loom-escape/`)

| Circuit | Inputs → outputs | NAND | LATCH | Depth | Bytes | Relation to teacher table |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `policy-hand` | 16 → 2 | 427 | 0 | 51 | 2,989 | 0 of 65,536 rows differ |
| `policy-hand-abc` | 16 → 2 | 74 | 0 | 11 | 518 | 0 of 65,536 rows differ |
| `policy-table-abc` | 16 → 2 | 74 | 0 | 11 | 518 | 0 of 65,536 rows differ (same netlist as above) |
| `core-hand` | 17 → 2 | 526 | 6 | 65 | 3,706 | step relation: 0 of 8,388,608 rows differ |
| `core-hand-abc` | 17 → 2 | 173 | 6 | 25 | 1,235 | step relation: 0 of 8,388,608 rows differ |
| `core-table-abc` | 17 → 2 | 173 | 6 | 25 | 1,235 | step relation: 0 of 8,388,608 rows differ |

* `policy-hand` is written by hand: per eye and pathway, a lookup from size bin to
  the smallest speed bin that crosses threshold, then a comparator. Its staircase
  reproduces the teacher's cells with zero exceptions.
* ABC re-synthesis of `policy-hand` and ABC synthesis of the raw 65,536-row table
  converge on the **identical** 74-NAND netlist.
* A core adds the motor state machine: inputs are the 16 sensory bits and
  `standing`; outputs `motor` = 0 hold, 1 raising wings, 2 short-mode takeoff,
  3 long-mode takeoff; six latches hold a 3-bit wing-raise counter and a 3-bit
  refractory timer (LSB first).

### Minimal cores (`circuits/loom-escape-min/`)

How much of `core-hand-abc` is needed? It is the hand-written motor state machine
with the ABC-synthesised policy inlined, so ABC never saw the core as one circuit.
Of its 346 single stuck-at faults, 23 are undetectable on the full domain (on 21
gates), and 6 NAND outputs are constant there.

| Circuit | Inputs → outputs | NAND | LATCH | Depth | Bytes | Specification |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `core-hand-abc-irr` | 17 → 2 | 113 | 6 | 18 | 815 | the step relation of `core-hand-abc` on every one of 8,388,608 rows |
| `core-reach` | 17 → 2 | 113 | 6 | 18 | 815 | its behaviour from reset only; the 52 unreachable states are don't-cares |

* Both come from one loop (`scripts/build_minimal_cores.py`): tie undetectable
  stuck-at faults to their constants, then alternate an ABC pass with another
  removal while the NAND count falls. Removal alone takes the core from 173 to 131
  NAND; ABC on the latch cut then reaches 113, i.e. **34.7 % smaller** than
  `core-hand-abc` with an identical step relation on all rows. Neither is a proven
  minimum; each is a verified upper bound for its specification.
* From reset only 12 of the 64 latch states occur: `(raise, refr)` ∈
  {(0, 0..7), (1..4, 0)}. Relaxing the specification to those rows — and adding
  ABC's `scorr` to the recipes — removed **nothing further**, so `core-reach` is
  byte-identical to `core-hand-abc-irr`. Against the thresholds registered before
  either core was synthesised (≥ 10 % smaller significant, ≤ 2 % weakened), the
  reduction of 0 % **weakens** the hypothesis that reachable-state don't-cares
  matter here.
* That result is about this procedure. Don't-cares are exploited only by tying
  undetectable faults to constants and by ABC's sequential signal correspondence;
  a stronger don't-care synthesis could still find a smaller core. What is
  established is that 35 % of the source core is redundant on its full domain, and
  that the remaining 113 gates have no single stuck-at redundancy on either domain.
* Checks: `core-hand-abc-irr` against all 8,388,608 rows of the step relation;
  `core-reach` by product-machine search from reset (12 reachable state pairs ×
  131,072 inputs = 1,572,864 rows), plus closure of its reachable set and 125
  episodes (35 train, 42 holdout, 48 published sealed) identical tick by tick over
  12,197 ticks. Both are in the EVM fixtures.
* Bytes depend on the ABC build, so these artifacts are gated by equivalence
  (`tests/test_minimal_cores.py`), not by byte reproduction, and
  `scripts/verify.sh` does not rerun the build. The pre-registration is commit
  `0aa2d94`, which precedes the synthesis.

### Components (`circuits/components/`)

Reusable pure computations, each exhaustively checked against a Python reference
model over its whole (input, state) domain.

| Component | NAND | LATCH | Depth |
| --- | ---: | ---: | ---: |
| `threshold_ge5_w4` | 16 | 0 | 12 |
| `threshold_ge100_w8` | 35 | 0 | 25 |
| `laterality_max2_w3` | 49 | 0 | 18 |
| `laterality_max2_w4` | 65 | 0 | 22 |
| `popcount_8` | 87 | 0 | 19 |
| `popcount_15` | 200 | 0 | 28 |
| `argmax_3x3` | 96 | 0 | 36 |
| `argmax_4x2` | 104 | 0 | 42 |
| `recent_pulse_4` | 9 | 3 | 6 |
| `recent_pulse_8` | 21 | 7 | 14 |
| `saturating_counter_w3` | 39 | 3 | 11 |
| `saturating_counter_w4` | 52 | 4 | 13 |
| `refractory_hold3_w2` | 24 | 2 | 8 |
| `refractory_hold5_w3` | 37 | 3 | 10 |

These are hand-built and not optimised; they exist to be composed (the escape core
uses the comparator, counter and refractory logic) and to be improved upon.

## How equivalence is established

Every arrow below is an exhaustive check over the complete domain, not sampling.

```
teacher decision table ──(2^16 rows)──► policy-hand ──(2^16)──► policy-hand-abc
          │                                                        ▲
          └────────────(2^16 rows)──► policy-table-abc ─────(identical netlist)
policy + motor spec ──(2^23 (input, state) rows)──► core
core step tables ──(every tick)──► quantised-teacher episodes (must be identical)
Python bit-sliced evaluator ──(SHA-256 chain over 2^n rows)──► EVM NandMachine
Python codec + tick evaluator ──(random + final circuits)──► public tapeout.net evaluator
Python step tables ──(2^16 and 2^23 rows)──► in-browser JavaScript evaluator
```

* **Two independent evaluators in Python.** `netlist.tick` is scalar;
  `exhaust.step_table` is bit-sliced. Tests cross-check them on random sequential
  circuits.
* **ABC is untrusted.** Its mapped BLIF is parsed back into the repository's IR
  and re-verified; a build stops if any recipe's result is not equivalent.
* **Deterministic.** Two from-scratch builds produced byte-identical artifacts.
* **Equivalence from reset** (the minimal cores) needs no shared state encoding: a
  product-machine search visits every reachable pair of states and compares the
  outputs for every input pattern there, and reports a counterexample trace from
  reset when they differ (`c3s/reach.py`).

## EVM contracts (`contracts/`)

### `NandMachine`

A stateless evaluator for the layout above. It is bit-sliced: every signal is a
`uint256` whose bit *j* is the signal's value in pattern *j*, so
`evaluatePlanes` evaluates up to 256 input patterns per call; `evaluate` is the
same code for a single pattern. It rejects reads of undefined signals, unknown
opcodes, truncated cells, state-size mismatches and `REF` cells.

### `ReflexCore`

One fixed sequential netlist with independent latch state per caller. `step(inputs)`
performs one tick for `msg.sender` and emits `Tick`; `peek` is the read-only form;
`reset` clears the caller's state. The netlist, port counts and SHA-256 are fixed
at deployment. There is no owner and no upgrade path.

### Tests

`forge test` runs:

* **full-domain differential test**: for 18 circuits (all 14 components, both
  74-NAND policies and both 173-NAND cores) the EVM evaluator's output and
  next-state planes over the complete (input, state) domain are chained with
  SHA-256 and compared with the chain computed in Python — up to 8,388,608 rows
  per circuit;
* **episode replay**: three stimulus episodes through a deployed `ReflexCore`,
  checking the motor command at every tick;
* malformed-netlist and per-caller-isolation tests.

All 10 tests pass (Foundry 1.8.1, solc 0.8.28, via-IR, optimiser 10,000 runs).

### Gas

Measured with `forge test --gas-report` on `core-hand-abc` (173 NAND + 6 LATCH):

| Call | Median gas |
| --- | ---: |
| `ReflexCore.step` (one tick, state stored) | 370,058 |
| `NandMachine.evaluate` (the evaluation inside it) | 253,616 |

The difference is mostly reading the 1,235-byte netlist from storage on every tick
and writing the caller's state. Storing the netlist as contract code instead of in
storage would remove most of the read cost; that optimisation is not implemented.

## Reproducing

With Yosys 0.68 (`yosys-abc`) and Foundry 1.8.1:

```sh
python scripts/build_loom_escape.py     # teacher, table, policies, cores, manifests
python scripts/export_components.py
python scripts/export_evm_fixtures.py
cd contracts && forge install foundry-rs/forge-std --no-git && forge test
```

or run `scripts/verify.sh`, which also asserts that every committed artifact is
reproduced byte for byte.
