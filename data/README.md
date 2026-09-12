# Data

## `malecns-v1.0-gf-escape-subgraph.json`

Aggregate counts derived from the **MaleCNS v1.0** connectome (FlyEM project team
at HHMI Janelia, University of Cambridge, MRC Laboratory of Molecular Biology and
Google Research), which is licensed **CC-BY**. If you use this file, cite the
dataset paper:

> Berg S, Beckett IR, Costa M, Schlegel P, Januszewski M, et al. Sexual dimorphism
> in the complete *Drosophila* male central nervous system connectome. *Cell*
> (2026). doi:10.1016/j.cell.2026.08.015

Produced by `scripts/extract_connectome.py` from three release files pinned by
SHA-256 (listed inside the file under `source_files`). Re-running the script on
the same files reproduces this file byte for byte.

### Fields

| Field | Content |
| --- | --- |
| `release`, `source_files`, `license` | provenance |
| `cell_type_counts` | cells per type (`DNp01`, `LC4`, `LPLC2`), by side |
| `giant_fibers[]` | per giant fiber: total input synapses; input by presynaptic superclass; visual-projection input; for LC4 and LPLC2 the cell count, synapse total, side counts and per-cell `[bodyId, synapses]`; LC4+LPLC2 shares of total and of visual-projection input; output total and ten largest output types |
| `lplc2_to_lc4`, `lc4_to_lplc2` | edges and synapses between the two feature channels |
| `top_descending_targets` | the twelve largest `[presynaptic type, descending type, synapses]` entries from LC4/LPLC2 |
| `parallel_dn_candidates` | LC4/LPLC2 synapses onto `DNp02`/`DNp04`/`DNp06`/`DNp11` per postsynaptic side, split ipsi/contralateral (the grouping is a modelling hypothesis) |
| `consensus_neurotransmitter` | the release's consensus neurotransmitter predictions for the three types |

Counts are row weights of `connectome-weights-male-cns-v1.0-minconf-0.5.feather`
(synapse confidence ≥ 0.5).
