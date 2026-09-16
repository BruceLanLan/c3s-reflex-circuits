# Connectome layer: the giant-fiber looming subgraph in MaleCNS v1.0

This layer (L0 in the [evidence ladder](../README.md#evidence-ladder)) answers one
question: which measured wiring does the teacher model use, and where exactly did
each number come from?

## Source

| Item | Value |
| --- | --- |
| Dataset | MaleCNS v1.0 (`male-cns:v1.0`), flat-connectome release |
| Producers | FlyEM project team (HHMI Janelia), University of Cambridge (Zoology), MRC Laboratory of Molecular Biology, Google Research |
| Licence | CC-BY |
| Paper | Berg et al., *Cell* 2026, doi:10.1016/j.cell.2026.08.015 |
| Segmentation | Automated with flood-filling networks (Google Research), then proofread |

Three release files are used, each pinned by SHA-256 in `c3s/connectome.py`.
Extraction refuses to run if any digest differs.

| File | SHA-256 |
| --- | --- |
| `body-annotations-male-cns-v1.0-minconf-0.5.feather` | `2177e246113e4cfbf1e7772ec37c6da1955ff22e8063d0b1f833101f99a9a3b2` |
| `body-neurotransmitters-male-cns-v1.0.feather` | `95c9289220663abeb3409f3ad9e5a7f8a53f8093f5139d15502cd08da8879621` |
| `connectome-weights-male-cns-v1.0-minconf-0.5.feather` | `e35da783d1c686b2b58b3b87cd6a403ae43bfcfba8bff28e08ef752c1a56afc1` |

`scripts/extract_connectome.py` downloads the files (about 1.1 GB), verifies
them, and writes `data/malecns-v1.0-gf-escape-subgraph.json`. Two independent runs
produced byte-identical output (SHA-256 `8dff72df…6bc2de`). Only aggregate counts
and per-cell synapse totals are written; the release files are never committed.

## Measured here (MaleCNS v1.0, one male animal)

All numbers in this section were computed by this repository from the pinned
files. A "synapse" is one row weight in the connection table (confidence ≥ 0.5).

### Inputs to the two giant fibers (cell type `DNp01`)

| | GF right (`DNp01(GF)_R`, body 10001) | GF left (`DNp01(GF)_L`, body 10010) |
| --- | ---: | ---: |
| All input synapses | 18,582 | 24,896 |
| Input synapses from visual projection neurons | 4,819 | 6,433 |
| LC4 cells / synapses | 55 / 2,580 | 71 / 3,782 |
| LPLC2 cells / synapses | 91 / 2,220 | 94 / 2,642 |
| LC4 + LPLC2 share of **visual projection** input | **99.61 %** | **99.86 %** |
| LC4 + LPLC2 share of **all** input | 25.83 % | 25.80 % |
| Side of the presynaptic LC4 / LPLC2 cells | all right | all left |

The denominators matter. LC4 and LPLC2 together provide essentially all of the
giant fiber's *visual projection* input, but only about a quarter of its total
input synapses; the rest comes from central-brain, descending, ascending and
unannotated partners.

Every LC4 and every LPLC2 cell in the dataset that contacts a giant fiber contacts
the fiber on its own side.

### Cell-type totals

| Type | Cells | Left | Right |
| --- | ---: | ---: | ---: |
| LC4 | 126 | 71 | 55 |
| LPLC2 | 185 | 94 | 91 |
| DNp01 (giant fiber) | 2 | 1 | 1 |

### Interactions between the two feature channels

| Direction | Edges | Synapses | Edges with ≥ 5 synapses |
| --- | ---: | ---: | ---: |
| LPLC2 → LC4 | 1,180 | 2,902 | 135 |
| LC4 → LPLC2 | 306 | 361 | — |

The teacher model does **not** use these edges. They are reported because they
show that the two "independent" feature channels are not wired independently,
which is one of the simplifications listed in [LIMITATIONS](LIMITATIONS.md).

### Other descending neurons that receive LC4/LPLC2 input

LC4's strongest descending-neuron target is not the giant fiber but `DNp04`
(11,597 synapses from LC4 against 6,362 onto `DNp01`). The teacher model groups
`DNp02`, `DNp04`, `DNp06` and `DNp11` into a *candidate parallel pathway*. **That
grouping is a modelling hypothesis of this repository, not an annotation of the
dataset.**

| Postsynaptic side | LC4 → candidates (ipsilateral) | LPLC2 → candidates (ipsilateral) | Contralateral |
| --- | ---: | ---: | ---: |
| Left | 11,817 | 2,899 | 0 |
| Right | 8,807 | 2,294 | 0 |

The ratio of velocity-channel (LC4) to size-channel (LPLC2) input differs sharply
between the two pathways, and that ratio is exactly what the teacher takes from
the connectome:

| Pathway | LC4 : LPLC2, left | LC4 : LPLC2, right |
| --- | ---: | ---: |
| Giant fiber | 1.43 : 1 | 1.16 : 1 |
| Parallel candidates | 4.08 : 1 | 3.84 : 1 |

### Neurotransmitter predictions

The release's consensus neurotransmitter *prediction* is acetylcholine for all
126 LC4 cells, all 185 LPLC2 cells and both giant fibers. These are predictions
from EM image features, not measurements; the teacher treats all four inputs as
excitatory.

## Zero-parameter queries (Gate 0)

`scripts/run_gate0.py` asks the release three questions whose answers depend on no
parameter of the model — each is a count or a share — with the readings registered in
its docstring before it ran (commit `efdaf6c`). Results:
`data/malecns-v1.0-gate0.json`.

**How much of the giant fibers' input is inhibitory?** **30.2 %** of their input
synapses come from cells whose consensus neurotransmitter is GABA or glutamate,
counting glutamate as inhibitory — an assumption, not a measurement, since GluCl is
the common fast glutamate receptor in the fly. The registered reading calls anything
at or above 20 % material: the teacher's all-excitatory simplification is missing
about a third of the drive ([LIMITATIONS](LIMITATIONS.md#known-simplifications-in-the-teacher)).

**What sits between the visual projection neurons and the giant fibers?** Cell types
on two-hop paths LC4/LPLC2 → X → giant fiber, both hops carrying at least 5 synapses,
ranked by what X puts onto the fibers:

| Type | Cells | Synapses onto the giant fibers |
| --- | ---: | ---: |
| `DNp70` | 2 | 1,416 |
| `SAD064` | 6 | 1,215 |
| `PVLP122` | 5 | 1,134 |
| `PVLP010` | 2 | 711 |
| `PVLP151` | 4 | 591 |

These are candidates for the non-LC4 size channel that von Reyn et al. 2017 left
unidentified — a list worth looking at, not a claim about any of them.

**Is the "parallel pathway" grouping supported?** Of each candidate's input synapses,
LC4 and LPLC2 supply **68.4 % of `DNp04`**, 23.0 % of `DNp02`, 15.4 % of `DNp11` and
**6.6 % of `DNp06`**. Only `DNp04` clears the registered bar of 25 %, and `DNp06`
falls below the 10 % that marks a poor member. The grouping this teacher assumes is
not uniform in the data.

## What this layer does not provide

* **Synaptic strength.** Synapse counts are used as a proxy for weight. The
  connectome does not state how strong a connection is in vivo.
* **Dynamics.** Time constants, receptor kinetics, neuromodulation and plasticity
  are absent.
* **Electrical coupling.** The connection table counts chemical synapses only.
* **Biological variability.** This is one animal. The left and right giant fibers
  already differ by 34 % in total input synapses; a second animal could differ
  more.
* **Comparison with earlier reconstructions.** Ache et al. (2019) reconstructed
  the LC4/LPLC2 inputs to the giant fiber in a female EM volume (FAFB). This
  repository consulted only that paper's abstract and therefore does not restate
  its counts.
