"""Gate 0: three zero-parameter questions to the release (v0.4 groundwork).

    python scripts/run_gate0.py --cache ~/.cache/c3s/malecns-v1.0

Writes data/malecns-v1.0-gate0.json. Nothing here fits anything: every number is a
count or a share read straight out of the pinned release files, so the answers do not
depend on the teacher, the encoding or any threshold of the model. What the
pre-registered readings below decide is only which sentence gets written afterwards.

Q1 — how much of the giant fibers' input is inhibitory?
    docs/LIMITATIONS.md records that this teacher treats every input as excitatory,
    while the model it follows (von Reyn et al. 2017) sums two excitatory and two
    inhibitory components. This counts the input synapses onto each giant fiber by the
    presynaptic cell's consensus neurotransmitter.
    Assumption, stated because it is not a measurement: acetylcholine is treated as
    excitatory, GABA as inhibitory, and glutamate as inhibitory (GluCl is the common
    fast glutamate receptor in the fly), with everything else counted as unknown.
    Pre-registered reading, on (GABA + glutamate) as a share of all input synapses:
      >= 20 %  the all-excitatory simplification is material: a teacher without an
               inhibitory term is missing a fifth of the drive, and the next version
               must carry one
      <= 5 %   immaterial at this resolution; the simplification stands
      between  partial: report the number and leave the decision to the next version

Q2 — what sits between the visual projection neurons and the giant fibers?
    von Reyn et al. 2017's size channel is explicitly non-LC4 and its cells are still
    unidentified. This lists every cell type on a two-hop path LC4/LPLC2 -> X -> GF
    where both hops carry at least `--min-synapses` synapses, ranked by how much X
    puts onto the giant fibers. Descriptive: no threshold, no claim that any of them
    is the missing channel.

Q3 — is the "parallel pathway" grouping supported?
    The teacher groups DNp02/DNp04/DNp06/DNp11 as the parallel escape pathway, which
    docs/LIMITATIONS.md already flags as an assumption. This measures, for each of
    them, how much of its input comes from LC4 and LPLC2.
    Pre-registered reading, per candidate, on LC4+LPLC2 as a share of its input synapses:
      >= 25 %  consistent with being driven by the same visual projection neurons
      <  10 %  a poor member of the grouping; say so in LIMITATIONS
      between  weak support; report the number

Needs the release files (about 1 GB, fetched and SHA-256 checked by c3s.connectome),
so scripts/verify.sh does not run it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
from pyarrow import feather

from c3s.connectome import FILES, GF_TYPE, RELEASE, VPN_INPUT_TYPES, fetch, write_json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "malecns-v1.0-gate0.json"
PARALLEL_CANDIDATES = ("DNp02", "DNp04", "DNp06", "DNp11")
INHIBITORY = ("gaba", "glutamate")
EXCITATORY = ("acetylcholine",)
MATERIAL, IMMATERIAL = 0.20, 0.05
GROUPING_SUPPORTED, GROUPING_POOR = 0.25, 0.10


def share(part: float, whole: float) -> float:
    return round(part / whole, 4) if whole else 0.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "c3s" / "malecns-v1.0")
    ap.add_argument("--min-synapses", type=int, default=5, help="Q2: the weight both hops must carry")
    ap.add_argument("--top", type=int, default=15, help="Q2: how many intermediate types to list")
    args = ap.parse_args()

    paths = fetch(args.cache.expanduser())
    ann = feather.read_table(paths["annotations"], columns=["bodyId", "type", "instance", "superclass"]).to_pandas()
    type_of = dict(zip(ann.bodyId, ann.type))
    nt = feather.read_table(paths["neurotransmitters"], columns=["body", "consensus_nt"]).to_pandas()
    nt_of = dict(zip(nt.body, nt.consensus_nt))

    gf_ids = sorted(int(b) for b in ann[ann.type == GF_TYPE].bodyId)
    vpn_ids = sorted(int(b) for b in ann[ann.type.isin(VPN_INPUT_TYPES)].bodyId)
    candidates = {t: sorted(int(b) for b in ann[ann.type == t].bodyId) for t in PARALLEL_CANDIDATES}

    weights = ds.dataset(paths["weights"], format="feather")
    into_gf = weights.to_table(filter=pc.is_in(ds.field("body_post"), value_set=pa.array(gf_ids))).to_pandas()
    from_vpn = weights.to_table(filter=pc.is_in(ds.field("body_pre"), value_set=pa.array(vpn_ids))).to_pandas()
    into_dn = weights.to_table(
        filter=pc.is_in(ds.field("body_post"), value_set=pa.array(sorted(b for ids in candidates.values() for b in ids)))
    ).to_pandas()

    # Q1 -------------------------------------------------------------------------
    q1_fibers, inhib_total, all_total = [], 0, 0
    for gid in gf_ids:
        rows = into_gf[into_gf.body_post == gid].copy()
        rows["nt"] = rows.body_pre.map(lambda b: str(nt_of.get(b, "unknown")).lower())
        by_nt = rows.groupby("nt").weight.sum().sort_values(ascending=False)
        total = int(rows.weight.sum())
        inhib = int(sum(int(v) for k, v in by_nt.items() if k in INHIBITORY))
        exc = int(sum(int(v) for k, v in by_nt.items() if k in EXCITATORY))
        inhib_total += inhib
        all_total += total
        q1_fibers.append(
            {
                "body_id": gid,
                "input_synapses": total,
                "by_neurotransmitter": {str(k): int(v) for k, v in by_nt.items()},
                "excitatory_synapses": exc,
                "inhibitory_synapses": inhib,
                "inhibitory_share": share(inhib, total),
            }
        )
    inhibitory_share = share(inhib_total, all_total)
    q1_verdict = (
        "material" if inhibitory_share >= MATERIAL else "immaterial" if inhibitory_share <= IMMATERIAL else "partial"
    )

    # Q2 -------------------------------------------------------------------------
    hop1 = from_vpn[from_vpn.weight >= args.min_synapses]
    hop2 = into_gf[into_gf.weight >= args.min_synapses]
    middles = set(int(b) for b in hop1.body_post) & set(int(b) for b in hop2.body_pre)
    middles -= set(gf_ids) | set(vpn_ids)
    rows = hop2[hop2.body_pre.isin(middles)].copy()
    rows["type"] = rows.body_pre.map(lambda b: type_of.get(b) or "unannotated")
    onto_gf = rows.groupby("type").agg(synapses_onto_gf=("weight", "sum"), cells=("body_pre", "nunique"))
    from_vpn_rows = hop1[hop1.body_post.isin(middles)].copy()
    from_vpn_rows["type"] = from_vpn_rows.body_post.map(lambda b: type_of.get(b) or "unannotated")
    from_vpn_sum = from_vpn_rows.groupby("type").weight.sum()
    q2 = [
        {
            "type": str(t),
            "cells": int(r.cells),
            "synapses_onto_giant_fibers": int(r.synapses_onto_gf),
            "synapses_from_LC4_LPLC2": int(from_vpn_sum.get(t, 0)),
        }
        for t, r in onto_gf.sort_values("synapses_onto_gf", ascending=False).head(args.top).iterrows()
    ]

    # Q3 -------------------------------------------------------------------------
    q3 = []
    for t, ids in candidates.items():
        rows = into_dn[into_dn.body_post.isin(ids)].copy()
        rows["pre_type"] = rows.body_pre.map(type_of)
        total = int(rows.weight.sum())
        vpn = int(rows[rows.pre_type.isin(VPN_INPUT_TYPES)].weight.sum())
        by_type = rows.groupby(rows.pre_type.fillna("unannotated")).weight.sum().sort_values(ascending=False).head(6)
        s = share(vpn, total)
        q3.append(
            {
                "type": t,
                "cells": len(ids),
                "input_synapses": total,
                "from_LC4_LPLC2": vpn,
                "share_from_LC4_LPLC2": s,
                "verdict": "supported" if s >= GROUPING_SUPPORTED else "poor member" if s < GROUPING_POOR else "weak",
                "top_input_types": {str(k): int(v) for k, v in by_type.items()},
            }
        )

    doc = {
        "format": "c3s.gate0/1",
        "release": RELEASE,
        "license": "CC-BY (MaleCNS dataset); this file is a derived aggregate",
        "source_files": {k: {"name": n, "sha256": d} for k, (n, d) in FILES.items()},
        "preregistered_in": "scripts/run_gate0.py docstring",
        "q1_inhibitory_input_to_the_giant_fibers": {
            "assumption": "acetylcholine excitatory; GABA and glutamate inhibitory; anything else unknown",
            "thresholds": {"material_min_share": MATERIAL, "immaterial_max_share": IMMATERIAL},
            "giant_fibers": q1_fibers,
            "inhibitory_share": inhibitory_share,
            "verdict": q1_verdict,
        },
        "q2_two_hop_between_vpns_and_giant_fibers": {
            "min_synapses_per_hop": args.min_synapses,
            "note": "descriptive: candidates for the non-LC4 channel, not a claim about any of them",
            "types": q2,
        },
        "q3_parallel_pathway_grouping": {
            "thresholds": {"supported_min_share": GROUPING_SUPPORTED, "poor_max_share": GROUPING_POOR},
            "candidates": q3,
        },
    }
    write_json(doc, OUT)
    print(f"wrote {OUT.relative_to(ROOT)}")
    print(f"Q1 inhibitory share of giant-fiber input: {inhibitory_share:.1%} -> {q1_verdict}")
    for e in q2[:5]:
        print(f"Q2 {e['type']:>14s}  {e['cells']:>4d} cells  {e['synapses_onto_giant_fibers']:>6d} onto GF")
    for e in q3:
        print(f"Q3 {e['type']}: {e['share_from_LC4_LPLC2']:.1%} of input from LC4+LPLC2 -> {e['verdict']}")


if __name__ == "__main__":
    main()
