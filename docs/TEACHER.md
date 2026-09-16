# Teacher layer: an explicit, falsifiable model of the escape decision

The teacher (L1) is the thing every circuit is ultimately compared against. It is
deliberately small, fully deterministic, and every parameter is labelled by
where it came from. Code: `c3s/loom.py`, `c3s/calibrate.py`.

## Stimulus

A dark disc of half-size *l* approaches at constant speed *v*. With time to
contact *τ* its full angular size and expansion speed are

    θ(τ)  = 2 · atan(l / (v τ))
    θ'(τ) = 2 (l/v) / (τ² + (l/v)²)

so each stimulus is characterised by `l/v` (ms) and an azimuth. Stimuli start at
θ = 10° and end at θ = 170°, sampled every tick (5 ms). The first sample is exactly
10° by definition rather than recomputed through `tan` and `atan`: 10° is also the
first size-bin edge, and the recomputed value lands one unit in the last place
either side of it depending on the platform's maths library.

For a constant-velocity approach a single snapshot (θ, θ') determines `l/v`
exactly: `l/v = 2 sin²(θ/2) / θ'`. This is why a per-tick circuit that only sees
the current size and speed is not missing hidden history about the stimulus.

Two stimulus families are fixed in code and never overlap in `l/v` or azimuth:

| Family | l/v (ms) | Azimuth (deg) | Episodes |
| --- | --- | --- | ---: |
| `train` | 10, 15, 20, 30, 40, 60, 80 | −60, −20, 0, 20, 60 | 35 |
| `holdout` | 12, 25, 35, 50, 70, 100, 140 | −75, −40, −10, 10, 40, 75 | 42 |

A third, **sealed** family is committed only by its SHA-256; see
[EVALUATION](EVALUATION.md#sealed-family).

## Model

For each eye that sees the stimulus (each eye covers its own hemifield plus 30°
across the midline):

    r_s  = exp(−(θ − μ)² / (2σ²))                  size drive   (LPLC2 channel)
    r_v  = max(θ', 0) / v_scale                    speed drive  (LC4 channel)
    V_GF = (n[LC4→GF] · r_v + n[LPLC2→GF] · r_s) / 1000
    V_P  = (n[LC4→P]  · r_v + n[LPLC2→P]  · r_s) / 1000

The giant-fiber pathway *crosses* when `V_GF ≥ gf_threshold`; the parallel
pathway crosses when `V_P ≥ parallel_threshold`. The two eyes are OR-ed.

**This is not the published GF model.** von Reyn et al. 2017 sum *four* components:
two excitatory (non-LC4 angular size, LC4 angular velocity) and **two inhibitory**
ones ([ModelDB 230400](https://modeldb.science/230400)); Ache et al. 2019 report
inhibitory input to the giant fibers producing tonic hyperpolarisation. The teacher
above has excitatory drive only. That is a deliberate simplification, and the
circuits in this repository are verified against *this* teacher, not against the
published model — a distinction the evidence ladder depends on. It is also one
plausible reason the identifiability analysis finds a third of the decision table
decided by the free parameters rather than by the data
([EVALUATION](EVALUATION.md#parameter-identifiability)). Adding inhibition would
change the teacher, the table and every circuit downstream of them, so it belongs
in a new version with its own pre-registration rather than an edit here.

Motor selection, per tick, for a standing fly outside its refractory period:

* GF crosses and the wings are **not yet** raised → **short-mode takeoff**
* only the parallel pathway crosses and the wings are not yet raised → continue
  the long-mode program (the wings count as raised after `wing_raise_ticks`
  consecutive ticks of it)
* the wings **are** raised and either pathway still crosses → **long-mode
  takeoff**; a GF crossing is not required, so a long-mode takeoff can be driven
  by the parallel pathway alone
* otherwise hold, and the wing program resets

Because the wing program needs `wing_raise_ticks` (4 ticks, 20 ms) of sustained
parallel-pathway drive, slow looms take off late or not at all: with the selected
parameters the core takes off at about 47° (l/v 40 ms), 62° (80 ms) and 85°
(150 ms), and holds for l/v of 300 ms and 1,000 ms.

After a takeoff the machine is refractory for `refractory_ticks` ticks.

## Where every ingredient comes from

| Ingredient | Value | Provenance |
| --- | --- | --- |
| GF response = linear velocity term + Gaussian size term | form of `V_GF` | Ache et al. 2019 (abstract): "a model summing a linear function of angular velocity (provided by LC4) and a Gaussian function of angular size (provided by LPLC2)" |
| Linear integration of the two features in the GF | sum, no interaction term | von Reyn et al. 2017 (abstract) |
| GF threshold above the parallel circuits; relative timing selects short vs long mode | motor rule above | von Reyn et al. 2014 (abstract) |
| LC4/LPLC2 synapse counts onto each GF, per side | 2,580 / 2,220 (R), 3,782 / 2,642 (L) | **measured here**, MaleCNS v1.0 |
| LC4/LPLC2 counts onto the parallel candidates | 8,807 / 2,294 (R), 11,817 / 2,899 (L) | **measured here**; the pathway grouping is an **assumption** |
| Synapse count as weight; excitatory sign | — | **assumption** (sign consistent with ACh predictions) |
| Instantaneous drive, no synaptic delay | — | **assumption** |
| Size tuning centre μ, width σ | 60°, 30° | **assumption** |
| Speed scale | 1000 °/s | **assumption** (sets units only) |
| Binocular overlap | ±30° | **assumption** |
| Tick | 5 ms | **assumption** |
| `gf_threshold`, `parallel_threshold`, `wing_raise_ticks` | 4.25, 6.0, 4 | **calibrated** (below) |
| `refractory_ticks` | 7 | **assumption** |

The model is linear in the two features, as the cited abstracts describe. No
superlinear or multiplicative integration is modelled.

## Calibration

The connectome fixes the *ratio* of speed to size drive in each pathway; it does
not fix the thresholds or the motor timing. Those three free parameters are
chosen by a published grid search (`c3s/calibrate.py`) on the `train` family only.

Grid: `gf_threshold` 2.0–6.0 step 0.25, `parallel_threshold` 2.0–8.0 step 0.5,
`wing_raise_ticks` 2–6 (1,105 points).

Constraints:

| | Constraint | Basis |
| --- | --- | --- |
| C1 | escape rate ≥ 0.9 | design target (not a literature value) |
| C2 | short-mode fraction non-increasing in `l/v`, ≥ 0.6 at 10 ms, ≤ 0.2 at 80 ms | fast looms bias towards GF-mediated escapes (von Reyn et al. 2017) |
| C3 | silencing LPLC2 lowers the mean short-mode fraction to ≤ 0.7 × intact | LPLC2 is necessary for GF-mediated escape (Ache et al. 2019) |

**89 of 1,105** grid points satisfy all three. Among those, the selection
heuristic picks the one whose mean short-mode fraction is closest to 0.4, then
fewer wing-raise ticks, then lower thresholds. The heuristic has no biological
meaning; it is stated so that it can be challenged.

Selected teacher on the `train` family:

| l/v (ms) | 10 | 15 | 20 | 30 | 40 | 60 | 80 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Short-mode fraction, intact | 1.0 | 1.0 | 0.8 | 0 | 0 | 0 | 0 |
| Short-mode fraction, LPLC2 silenced | 1.0 | 0 | 0 | 0 | 0 | 0 | 0 |

Every episode ends in a takeoff in both conditions (escape rate 1.0).

Whether 89 satisfying points is a strong or weak constraint is exactly what the
[controls](EVALUATION.md#controls-does-the-wiring-matter) test.

## Sensory encoding: LoomEscape-16

Each eye's size and speed are quantised to 4 bits on logarithmic scales:

* size: bin 0 is below 10°; edges `10 · 18^(k/15)` degrees for k = 0…15 (10° to 180°)
* speed: bin 0 is below 25 °/s; edges `25 · 512^(k/15)` °/s for k = 0…15 (25 to 12,800 °/s)

Input bits, LSB first: `size_L[0..3]`, `speed_L[0..3]`, `size_R[0..3]`,
`speed_R[0..3]`. An eye that does not see the stimulus reports zeros.

The decision table (`circuits/loom-escape/decision-table.json`, 65,536 rows) gives
the two pathway bits for every input. Each (size bin, speed bin) cell is labelled
by majority over 64 samples drawn log-uniformly inside the bin. Mean label purity
is 98.5 %; the least pure cell is 53 %, which is where quantisation hides the
decision boundary.

**Observation about this teacher:** the table contains no input for which the GF
pathway crosses and the parallel pathway does not (rows: neither 18,447; parallel
only 7,563; both 39,526). With the calibrated thresholds, every sensory state that
drives the GF past threshold also drives the velocity-heavy parallel pathway past
its threshold. This is a consequence of the model, not a constraint imposed on the
circuit, and it is why the GF-only output code never appears.

### Why 4 bits

The first build used 3-bit bins. The circuits were exact, but mode agreement with
the continuous teacher was only 0.60 on `train`: short versus long mode is decided
by whether the GF crosses within a few ticks of the parallel pathway, and 3-bit
bins cannot resolve that race. Encodings are now compared by the episode fidelity
of the *quantised teacher* (what any exact circuit will do). See
[EVALUATION](EVALUATION.md#encoding-choice) for the numbers and the disclosure
about how the holdout family was exposed during that comparison.
