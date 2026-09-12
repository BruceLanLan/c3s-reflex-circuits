# Evaluation

Three different questions are kept apart here, because answering one says
nothing about the others:

1. **Is each circuit exactly what the stage before it specified?** (compiler
   evidence — see [CIRCUITS](CIRCUITS.md); every answer there is "yes, over the
   complete domain")
2. **How faithfully does a circuit reproduce the continuous teacher's behaviour?**
   (behavioural fidelity — this document)
3. **Does the measured wiring constrain that behaviour at all?** (controls — this
   document)

None of them asks whether the teacher is a correct model of a fly. That question
is outside what this repository can answer; see [LIMITATIONS](LIMITATIONS.md).

## Episode fidelity of the exact circuits

An exact circuit behaves exactly like the teacher seen through the sensory
encoding (the *quantised teacher*); the build checks that equality tick by tick.
All remaining disagreement with the continuous teacher is therefore caused by the
encoding.

`core-hand-abc` (173 NAND + 6 LATCH), 4-bit log encoding:

| Family | Episodes | Escape agreement | Mode agreement (short vs long) | Mean \|Δ takeoff tick\| |
| --- | ---: | ---: | ---: | ---: |
| `train` | 35 | 1.000 | 0.829 | 0.80 |
| `holdout` | 42 | 1.000 | 0.905 | 1.00 |

All mode disagreements occur at `l/v` values where the continuous teacher switches
between short and long mode, i.e. where the GF crosses threshold within about one
wing-raise period of the parallel pathway. Takeoff timing agrees to about one tick
(5 ms).

## Encoding choice

`scripts/compare_encodings.py` scores each encoding by quantised-teacher episode
fidelity. Selection rule, train family only: among encodings with at most 16 input
bits, highest mode agreement, then lowest mean tick error, then fewer bits.

| Encoding | Inputs | Train mode | Train \|Δtick\| | Holdout mode | Holdout \|Δtick\| |
| --- | ---: | ---: | ---: | ---: | ---: |
| `3bit-hand` | 12 | 0.600 | 0.86 | 0.571 | 3.24 |
| **`4bit-log`** (selected) | 16 | 0.829 | 0.80 | 0.905 | 1.00 |
| `4bit-log-speed50` | 16 | 0.829 | 0.86 | 1.000 | 1.24 |
| `5bit-log` (reference, too wide) | 20 | 0.771 | 0.26 | 0.952 | 1.10 |

Escape agreement is 1.000 for every encoding in both families.

> **Disclosure** (verbatim from `compare_encodings.py`): this comparison was
> written after the first 3-bit build showed low mode agreement, and holdout
> numbers were printed during that exploration. The holdout family therefore is
> not blind with respect to the encoding choice; the sealed family is.

Two further observations: `4bit-log-speed50` scores higher on holdout but loses on
the train tie-break, and it was not selected; finer bins (5 bits) do not improve
mode agreement on train. The short/long boundary is a race decided within a few
ticks, so quantisation error there is noisy rather than monotone in resolution.

## Learned circuits (DLGN)

Differentiable logic gate networks (Petersen et al. 2022, re-implemented) were
trained on a random half of the 65,536 table rows and tested on the other half.
For every run the compiled circuit equals the hardened network on all 65,536 rows,
and ABC's optimised circuit equals the compiled one.

| Policy | Row accuracy (held-out rows) | NAND after ABC | Depth | Core mode agreement train / holdout | Core escape agreement train / holdout |
| --- | ---: | ---: | ---: | ---: | ---: |
| exact synthesis (`policy-hand-abc`) | 1.000 | **74** | 11 | 0.829 / 0.905 | 1.000 / 1.000 |
| DLGN 64-64-32 | 0.674 | 216 | 17 | 0.600 / 0.667 | 0.800 / 0.762 |
| DLGN 128-128-64 | 0.828 | 500 | 45 | 0.800 / 0.762 | 1.000 / 1.000 |
| DLGN 256-256-128 | 0.899 | 1,340 | 53 | 0.743 / 0.762 | 1.000 / 1.000 |

(200 epochs, Adam, learning rate 0.02, GroupSum temperature 4, class-balanced
loss, seed 0; one run per size.)

**Reading.** For a function that can be tabulated completely — 16 inputs, 65,536
rows — exact logic synthesis dominates: it is smaller, shallower and exact. The
learned networks are larger by a factor of 3 to 18 and still wrong on 10–33 % of
rows. The place for DLGN in this pipeline is a teacher that *cannot* be tabulated
(more input bits, or behaviour only available as sampled trajectories), where the
comparison with exact synthesis is no longer available. That regime is not
demonstrated here.

## Controls: does the wiring matter?

The connectome enters the teacher through four synapse counts per side. The
controls (`scripts/run_controls.py`, results in
`circuits/loom-escape/controls.json`) move those measured counts into the wrong
slots — all 23 non-identity permutations, plus left/right symmetrisation — and
evaluate each variant in two ways: re-running the identical calibration
procedure, and holding the measured thresholds fixed. The interpretation was
written into the script before the results were computed.

<!-- RESULT:controls -->

## Sealed family

Before the controls were run and before any evaluation on it, a family of 48
looming stimuli (`l/v` log-uniform in 8–160 ms, azimuth uniform in −90° to 90°)
was generated with a random salt and committed only by its SHA-256:

    cd71f34b36f20d90cb3f552d683f4bfd03e3211d5e1a63b4649ca64429f77764

`scripts/evaluate_sealed.py` refuses any spec whose digest differs, rebuilds the
decision table and checks it against the committed one, and records the hashes of
every artifact the result depends on. It was run once, after the teacher,
encoding, calibration and all circuits were frozen.

<!-- RESULT:sealed -->

The spec itself is not in the repository. Publishing it later lets anyone verify
the digest and re-run the evaluation.
