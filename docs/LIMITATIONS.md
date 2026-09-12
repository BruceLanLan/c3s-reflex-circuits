# Limitations and falsification criteria

## What this repository does not claim

* **It does not simulate a fly brain.** It uses four synapse counts per side from
  one connectome, inside a hand-specified model of one behaviour.
* **Behavioural fidelity is not biological fidelity.** A circuit that matches the
  teacher says nothing about whether the teacher matches the fly. The teacher's
  structure follows the abstracts of Ache et al. (2019) and von Reyn et al.
  (2014, 2017); its thresholds and timing are calibrated against qualitative
  targets, not fitted to recordings.
* **It makes no new neuroscience claim.** No result here should be cited as
  evidence about the *Drosophila* escape circuit.
* **Nothing is deployed.** No contract or circuit from this repository has been
  deployed to any chain.

## Known simplifications in the teacher

| Simplification | Why it matters |
| --- | --- |
| Synapse count used as weight; all inputs excitatory | Real synaptic efficacy varies per connection and is not in the connectome |
| Instantaneous drive, no membrane or synaptic dynamics | The GF's actual response has time course; timing-sensitive decisions are the most affected |
| LC4 and LPLC2 treated as independent channels | MaleCNS shows 2,902 LPLC2→LC4 synapses (1,180 edges) that the model ignores |
| `DNp02`/`DNp04`/`DNp06`/`DNp11` as the "parallel pathway" | A modelling hypothesis; the dataset does not label these as the parallel escape circuit |
| Size tuning (μ = 60°, σ = 30°), speed scale, binocular overlap, tick length, refractory period | Chosen, not measured |
| Three free parameters calibrated by grid search | 89 of 1,105 points satisfy the constraints; see the controls for how much the wiring restricts this |
| One animal, one release | Left and right giant fibers already differ by 34 % in input synapses |
| No neuromodulation, plasticity, habituation or internal state beyond the motor latches | — |
| Constant-velocity, single-object looms only | The (θ, θ') snapshot is sufficient only for this stimulus class |

## Known limitations of the circuits and evaluation

* **The encoding sets the ceiling.** Exact circuits reproduce the quantised teacher
  perfectly; all remaining error is quantisation, concentrated at the short/long
  mode boundary.
* **The holdout family is not blind to the encoding choice** (see the disclosure
  in [EVALUATION](EVALUATION.md#encoding-choice)). The sealed family is.
* **DLGN results are single runs** per size with one seed and no tuning.
* **On-chain cost** is measured only for the EVM evaluator in this repository
  (about 370k gas per tick). TapeOut protocol fees and limits were not checked.
* **REF composition** is decoded and simulated but not exercised by any circuit.
* **Byte identity depends on tool versions.** Artifacts reproduce byte for byte with
  Yosys 0.68 and torch 2.14 (CPU). Another ABC release may synthesise a different
  but equivalent netlist: the exhaustive equivalence checks still gate the build,
  but the byte comparison in `scripts/verify.sh` would then fail.

## Falsification criteria

Each criterion below would show that a claim of this project fails. The status
column records what this repository found.

| # | The approach fails if… | Status |
| --- | --- | --- |
| F1 | a hand-written baseline cannot reproduce the teacher's decision table | Not triggered: `policy-hand` matches all 65,536 rows |
| F2 | a compiled or optimised circuit differs from its specification anywhere | Not triggered: 0 mismatches over every complete domain, in Python and on the EVM |
| F3 | the quantised policy's behaviour diverges badly from the continuous teacher | Partially triggered: escape agreement 1.0, but mode agreement 0.83 (train) / 0.90 (holdout) |
| F4 | wrongly assigned wiring constrains behaviour as well as the measured wiring | Partially triggered: 18 of 23 permutations admit no parameters, but the 5 that keep the parallel pathway's LC4 count in place calibrate as well as the measured wiring; the constraint reduces to one ordinal fact |
| F5 | the circuit is too large for an on-chain evaluator | Not triggered: 173 NAND + 6 LATCH, 1,235 bytes, about 370k gas per tick on the reference EVM evaluator |
| F6 | results on the sealed family contradict the reported fidelity | Not triggered: 48 sealed episodes, escape agreement 1.000, mode agreement 0.875 (between train 0.829 and holdout 0.905) |
| F7 | no one other than the authors can reproduce the artifacts | Partially addressed: CI on a clean Linux machine reproduces the EVM fixtures and the demo data byte for byte and passes the EVM suite; its full rebuild with a different Yosys 0.68 build matches everything except the sizes of two ABC recipes that are not selected (873 → 874 and 823 → 824 NAND). No independent reproduction by a third party has been reported yet |
