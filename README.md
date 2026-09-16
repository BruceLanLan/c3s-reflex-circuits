# C3S Reflex Circuits

**Connectome-constrained circuit synthesis: from the fruit-fly giant-fiber escape
pathway to exhaustively verified NAND/LATCH netlists that run on chain.**

[中文说明](README.zh-CN.md) · [Live demo](https://brucelanlan.github.io/c3s-reflex-circuits/demo/) ·
[Handheld simulator](https://brucelanlan.github.io/c3s-reflex-circuits/sim/) ·
[Release v0.2.0](https://github.com/BruceLanLan/c3s-reflex-circuits/releases/tag/v0.2.0)

This repository asks a narrow question: *if a behaviour is shaped by measured
wiring, how small a deterministic machine reproduces it, and how much of that
machine can be proven rather than trusted?* It does not put a brain on chain. It
takes one reflex — looming-evoked escape, gated by the giant fiber — and carries it
through five layers of evidence, each checked against the one before it.

```
MaleCNS v1.0 connectome ─► explicit teacher model ─► 16-bit decision table
      (measured)              (cited + assumed)          (65,536 rows)
                                                              │
             EVM / TapeOut-layout netlist ◄─ NAND+LATCH core ◄─┘
                (public, replayable)          (exhaustively equivalent)
```

## Web demo

The [live demo](https://brucelanlan.github.io/c3s-reflex-circuits/demo/)
([`docs/demo/index.html`](docs/demo/index.html)) is a voxel-world bench for the
escape core: launch a looming block at a fly, and the page quantises what each eye
sees into the 16 input bits and evaluates the 173-NAND + 6-LATCH netlist
([`core-hand-abc`](circuits/loom-escape/core-hand-abc.json)) cell by cell in the
browser, showing the pathway lamps, the latches and the motor command at every
5 ms tick. On load it checks the netlist's SHA-256 and replays reference episodes
from the Python build.

To run it locally:

```sh
python -m http.server -d docs 8000     # then open http://localhost:8000/demo/
```

GitHub Pages serves the page from `main` → `/docs`.
`python scripts/build_demo.py` re-embeds the circuits after a rebuild.

## On a Cardputer ADV

[`firmware/cardputer`](firmware/cardputer) runs the same escape core on an M5Stack
Cardputer ADV (ESP32-S3). The netlist bytes are embedded unchanged and evaluated cell
by cell in C. At boot the device checks both netlists' SHA-256, replays the reference
episodes tick by tick and checks the core against the policy on all 131,072 rows at
rest. It then launches looming discs from the l/v and azimuth you pick on the keyboard
and shows the sensory bits, pathway lamps, latches and motor command at every tick,
slowed down to be watchable, with a tick log on USB serial.

```sh
pio run -d firmware/cardputer -t upload
```

Without PlatformIO, write the image attached to the
[v0.2.0 release](https://github.com/BruceLanLan/c3s-reflex-circuits/releases/tag/v0.2.0):
`pip install esptool`, then
`python -m esptool --chip esp32s3 write_flash 0x0 c3s-escape-core-cardputer-adv-v0.2.0.bin`.
On 2026-09-15 the self-test passed on a Cardputer ADV in 4.5 s.

Without the device, the
[handheld simulator](https://brucelanlan.github.io/c3s-reflex-circuits/sim/) runs the
same C code compiled to WebAssembly in the browser. `tests/test_firmware.py` checks
both builds, native and WebAssembly, against the Python engine on all 8,388,608
(input, state) rows of the core. Details, keys and
limitations: [docs/FIRMWARE.md](docs/FIRMWARE.md).

## Results at a glance

| | |
| --- | --- |
| **Measured wiring** | LC4 + LPLC2 provide 99.61 % (right) and 99.86 % (left) of the giant fibers' visual-projection input synapses in MaleCNS v1.0 — but only 25.8 % of all their input synapses |
| **Policy circuit** | 16 sensory bits → 2 pathway bits in **74 NAND**, depth 11, exactly equal to the teacher on all 65,536 inputs |
| **Stateful escape core** | **173 NAND + 6 LATCH** (1,235 bytes); step relation equal to its specification on all 8,388,608 (input, state) rows |
| **Minimal cores** | the same step relation in **113 NAND + 6 LATCH**, 35 % smaller, equal on all 8,388,608 rows; relaxing the specification to the 12 states reachable from reset removed nothing further, against thresholds registered beforehand |
| **Temporal properties** | five properties — refractory window, raising prerequisite, quiet-tick hold, short-mode guard, state invariant — each proven twice, by this repository's evaluator (all 8,388,608 rows, or a trace search from reset) and by Yosys temporal induction with no assumptions, each with a control that must and does fail |
| **Behaviour vs continuous teacher** | escape agreement 1.00; short/long-mode agreement 0.83 (train) and 0.90 (holdout); takeoff within ~1 tick (5 ms) |
| **Firmware** | the Cardputer ADV firmware's C evaluator, compiled for the host and to WebAssembly (the online simulator), equals the Python engine on all 8,388,608 (input, state) rows of the core; the device itself checks the hashes, the reference episodes and 131,072 rows at rest on every boot |
| **On a public chain** | a read-only `eth_call` with a state override makes a BSC mainnet node execute the core with **nothing deployed** — no contract address, no wallet, no key, no gas — and it reproduced the sampled ticks of an episode, takeoff included, with no difference |
| **EVM** | full-domain differential test of 20 circuits passes; one tick of the core costs ~370k gas on the reference evaluator |
| **TapeOut byte layout** | cross-checked against the public tapeout.net decoder and evaluator: 0 mismatches over 4,800 random ticks and the final circuits |
| **Learned circuits (DLGN)** | 67–90 % row accuracy at 216–1,340 NAND: on a fully tabulable function, exact synthesis wins |
| **Parameter identifiability** | of 1,105 grid points, 89 satisfy the constraints and give 48 distinct decision tables; 67 % of the 65,536 rows are identical across all of them, policy size ranges 67–149 NAND (median 85), and the published circuit (74 NAND, 22nd percentile) is a typical feasible one by thresholds registered before the run |
| **Controls** | of 23 wrong assignments of the measured synapse counts, 18 admit no teacher parameters at all; the 5 that do all keep the parallel pathway's dominant LC4 count in place — the wiring constrains behaviour, but through one ordinal fact |
| **Sealed family** | pre-registered by SHA-256, evaluated once: escape agreement 1.00, mode agreement 0.875 over 48 unseen stimuli |
| **Reproducibility** | two from-scratch builds are byte-identical; `scripts/verify.sh` rebuilds the teacher, table, circuits, components, web-demo data and EVM fixtures and checks them byte for byte (DLGN runs, controls and the sealed evaluation are run separately; a DLGN re-run reproduced its netlist exactly); CI on a clean Linux machine reproduces the EVM fixtures and demo data byte for byte and passes the EVM suite |

## Evidence ladder

Each layer answers a different question, and none can stand in for another.

| Layer | Question | Where |
| --- | --- | --- |
| **L0** Biological provenance | Which cells, synapses and release files? | [docs/CONNECTOME.md](docs/CONNECTOME.md), `data/` |
| **L1** Executable teacher | Which equations, which parameters, where does each come from? | [docs/TEACHER.md](docs/TEACHER.md), `c3s/loom.py` |
| **L2** Hard circuit | Which gates, how many, how deep? | [docs/CIRCUITS.md](docs/CIRCUITS.md), `circuits/` |
| **L3** Equivalence | Is every circuit exactly its specification? | [docs/CIRCUITS.md](docs/CIRCUITS.md#how-equivalence-is-established) |
| **L4** Public machine | Can anyone replay it on an EVM? | `contracts/`, [docs/CIRCUITS.md](docs/CIRCUITS.md#evm-contracts-contracts) |

Behavioural fidelity, controls and the sealed test are in
[docs/EVALUATION.md](docs/EVALUATION.md). What is simplified, assumed or not
claimed is in [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

## How it works

1. **Connectome (L0).** From the pinned MaleCNS v1.0 release, extract the LC4 and
   LPLC2 inputs to both giant fibers (`DNp01`) and to a candidate parallel set of
   descending neurons. The connectome contributes the *ratio* of looming-speed to
   looming-size drive in each pathway: about 1.2–1.4 : 1 onto the giant fiber and
   3.8–4.1 : 1 onto the parallel candidates.
2. **Teacher (L1).** Each pathway's drive is a linear speed term plus a Gaussian
   size term (the form reported by Ache et al. 2019), weighted by those synapse
   counts. The giant fiber crossing threshold before the wings are raised selects a
   short-mode takeoff; otherwise the parallel pathway drives the long-mode program
   (the timing mechanism of von Reyn et al. 2014). Three free parameters are
   calibrated against literature-derived constraints on a training stimulus family.
3. **Encoding.** Each eye reports angular size and expansion speed as 4-bit
   logarithmic bins: 16 input bits (**LoomEscape-16**).
4. **Circuits (L2).** A readable hand-written policy (per-eye threshold staircases),
   ABC-optimised versions, circuits learned with differentiable logic gate networks,
   and a stateful core that adds the motor state machine in six latches.
5. **Proof (L3).** Every circuit is checked over its complete domain by a
   bit-sliced evaluator; ABC is treated as untrusted; two independent Python
   evaluators and the EVM evaluator must agree.
6. **Chain (L4).** `NandMachine` evaluates any netlist in the TapeOut byte layout;
   `ReflexCore` holds one escape core with independent latch state per caller and
   no admin.

## Repository layout

```
c3s/            netlist IR and codec, exhaustive evaluator, components, connectome
                extraction, teacher, calibration, reflex circuits, ABC bridge, DLGN
scripts/        extraction, build, encoding comparison, DLGN training, controls,
                sealed evaluation, fixture export, verify.sh
data/           derived connectome aggregate (CC-BY source)
circuits/       every circuit as a manifest: metrics, SHA-256, netlist bytes, evidence
contracts/      NandMachine and ReflexCore (Solidity) with Foundry tests
firmware/       Cardputer ADV firmware (PlatformIO) and the host driver for its test
formal/         SystemVerilog property spec proven by Yosys induction
docs/           connectome, teacher, circuits, properties, evaluation, firmware,
                limitations, references; docs/demo/ is the web demo
tests/          pytest suite
```

## Reproduce

Requirements: Python ≥ 3.10 with `numpy`, `torch`, `pytest` (plus `pyarrow` and
`pandas` to re-extract the connectome), Yosys 0.68 (for `yosys-abc`), Foundry
1.8.1 (solc 0.8.28) and a C compiler (for `tests/test_firmware.py`, which is skipped
without one). Building the firmware needs PlatformIO. The committed artifacts were built with Yosys 0.68 and torch
2.14 on CPU; byte-for-byte identity is asserted for those versions (see
[LIMITATIONS](docs/LIMITATIONS.md#known-limitations-of-the-circuits-and-evaluation)).

```sh
pip install -e ".[dev,learn,connectome]"
scripts/verify.sh          # tests, full rebuild, byte-for-byte artifact check, EVM suite
scripts/verify.sh --full   # also re-download and re-extract the MaleCNS subgraph (~1.1 GB)
```

Individual stages:

```sh
python scripts/extract_connectome.py      # L0
python scripts/build_loom_escape.py       # L1–L3: calibration, table, circuits, cores
python scripts/build_minimal_cores.py     # smaller cores with the same behaviour
python scripts/check_properties.py --controls   # temporal properties, two methods
python scripts/train_dlgn.py --widths 128,128,64
python scripts/run_controls.py
python scripts/export_evm_fixtures.py && (cd contracts && forge test)
```

`.github/workflows/verify.yml` repeats this on a clean machine for every push: the
test suite, a byte-for-byte rebuild of the EVM fixtures and the demo data, and the
Foundry suite including the full-domain differential test. A third job proves the
temporal properties by Yosys induction, then runs the whole of `scripts/verify.sh`
with Yosys 0.68 and reports, without gating, whether the ABC-produced netlists also
reproduce byte for byte there.

## Common tasks

| Task | Where to start |
| --- | --- |
| Add a reusable component | Write a builder and a Python reference model in `c3s/components.py` and register it in `CATALOG`. `tests/test_components.py` checks it over its whole (input, state) domain; `scripts/export_evm_fixtures.py` adds it to the EVM differential test. |
| Change the encoding | Add an `Encoding` in `c3s/loom.py` and to `CANDIDATES` in `scripts/compare_encodings.py`. The script refuses a `DEFAULT_ENCODING` that its train-only selection rule does not pick. |
| Change the teacher | Equations are `drives`, `select_action` and `core_step` in `c3s/loom.py`; calibration is `c3s/calibrate.py`; the source of every parameter is in [docs/TEACHER.md](docs/TEACHER.md). |
| Train a learned circuit | `python scripts/train_dlgn.py --widths 128,128,64` writes `circuits/loom-escape/dlgn-128x128x64-s0.json`. |
| Use a circuit on chain | Every manifest carries `tapeout_netlist_hex`; `contracts/src/NandMachine.sol` evaluates any such netlist and `contracts/src/ReflexCore.sol` holds one core with per-caller state. Usage is in `contracts/test/`. |
| Update the web demo | `python scripts/build_demo.py` after a rebuild. |
| Run the core on a device | `pio run -d firmware/cardputer -t upload` ([docs/FIRMWARE.md](docs/FIRMWARE.md)). After a rebuild, run `python scripts/build_firmware.py` after `build_demo.py`. Only `src/main.cpp` and `platformio.ini` are specific to the Cardputer ADV. |

Any change to the teacher, the encoding or the calibration voids the one-time sealed
evaluation ([docs/EVALUATION.md](docs/EVALUATION.md)).

## Research basis

* **Connectome.** MaleCNS v1.0 (Berg et al., *Cell* 2026), produced by the FlyEM
  team at HHMI Janelia with the University of Cambridge, the MRC Laboratory of
  Molecular Biology and **Google Research**, whose flood-filling networks performed
  the automated segmentation.
* **Escape circuit.** Ache et al. 2019 (LC4 velocity and LPLC2 size inputs to the
  giant fiber); von Reyn et al. 2014 (spike timing selects takeoff mode) and 2017
  (linear feature integration).
* **Logic learning.** Deep differentiable logic gate networks (Petersen, Borgelt,
  Kuehne and Deussen, NeurIPS 2022, arXiv:2210.08277), re-implemented here; and
  **Google Research**'s differentiable logic cellular automata (Miotti, Niklasson,
  Randazzo and Mordvintsev, 2025), which first trained such gates in recurrent,
  stateful circuits.
* **Limits.** Scheffer & Meinertzhagen 2021 (*A connectome is not enough*); Pospisil
  et al. 2024 (connectome as a prior for causal models).

Full list: [docs/REFERENCES.md](docs/REFERENCES.md).

## Author and contact

Author: [BruceBlue](https://github.com/BruceLanLan) · Contact: X
[@BruceBlue](https://x.com/BruceBlue)

## Licence and notices

Code: Apache-2.0 ([LICENSE](LICENSE)). The connectome aggregate derives from
CC-BY data and must be attributed. The DLGN method's reference implementation
carries a "Patent pending" notice. See [NOTICE](NOTICE).
