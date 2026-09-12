"""Extraction of the giant-fiber looming-escape subgraph from MaleCNS v1.0.

Source: the MaleCNS v1.0 flat-connectome release (FlyEM / HHMI Janelia,
University of Cambridge, MRC LMB and Google Research), licensed CC-BY. The
release files are pinned by SHA-256; extraction refuses to run on anything else.

Only aggregate counts and per-cell synapse totals are written out. The raw
release files are never committed.
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path

RELEASE = "male-cns:v1.0"
BASE_URL = "https://storage.googleapis.com/flyem-male-cns/v1.0/connectome-data/flat-connectome"
FILES = {
    "annotations": (
        "body-annotations-male-cns-v1.0-minconf-0.5.feather",
        "2177e246113e4cfbf1e7772ec37c6da1955ff22e8063d0b1f833101f99a9a3b2",
    ),
    "neurotransmitters": (
        "body-neurotransmitters-male-cns-v1.0.feather",
        "95c9289220663abeb3409f3ad9e5a7f8a53f8093f5139d15502cd08da8879621",
    ),
    "weights": (
        "connectome-weights-male-cns-v1.0-minconf-0.5.feather",
        "e35da783d1c686b2b58b3b87cd6a403ae43bfcfba8bff28e08ef752c1a56afc1",
    ),
}

GF_TYPE = "DNp01"  # giant fiber
VPN_INPUT_TYPES = ("LC4", "LPLC2")
PARALLEL_DN_CANDIDATES = ("DNp02", "DNp04", "DNp06", "DNp11")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(cache_dir: Path, verify: bool = True) -> dict[str, Path]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for key, (name, digest) in FILES.items():
        path = cache_dir / name
        if not path.exists():
            tmp = path.with_suffix(".part")
            urllib.request.urlretrieve(f"{BASE_URL}/{name}", tmp)
            tmp.rename(path)
        if verify and sha256_file(path) != digest:
            raise RuntimeError(f"{name}: SHA-256 does not match the pinned release")
        paths[key] = path
    return paths


def _side(instance: object) -> str | None:
    if not isinstance(instance, str) or not instance:
        return None
    tail = instance.rsplit("_", 1)[-1]
    return tail if tail in ("L", "R") else None


def extract_escape_subgraph(paths: dict[str, Path]) -> dict:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.dataset as ds
    import pyarrow.feather as feather

    ann = feather.read_table(
        paths["annotations"], columns=["bodyId", "type", "instance", "superclass", "status"]
    ).to_pandas()
    type_of = dict(zip(ann.bodyId, ann.type))
    inst_of = dict(zip(ann.bodyId, ann.instance))
    super_of = dict(zip(ann.bodyId, ann.superclass))

    gf = ann[ann.type == GF_TYPE].sort_values("bodyId")
    vpn = ann[ann.type.isin(VPN_INPUT_TYPES)]
    gf_ids = [int(x) for x in gf.bodyId]
    vpn_ids = [int(x) for x in vpn.bodyId]

    weights = ds.dataset(paths["weights"], format="feather")
    into_gf = weights.to_table(filter=pc.is_in(ds.field("body_post"), value_set=pa.array(gf_ids))).to_pandas()
    from_gf = weights.to_table(filter=pc.is_in(ds.field("body_pre"), value_set=pa.array(gf_ids))).to_pandas()
    from_vpn = weights.to_table(filter=pc.is_in(ds.field("body_pre"), value_set=pa.array(vpn_ids))).to_pandas()

    nt = feather.read_table(paths["neurotransmitters"], columns=["body", "cell_type", "consensus_nt"]).to_pandas()

    out: dict = {
        "release": RELEASE,
        "source_files": {k: {"name": n, "sha256": d} for k, (n, d) in FILES.items()},
        "license": "CC-BY (MaleCNS dataset); this file is a derived aggregate",
        "cell_type_counts": {
            t: {
                "cells": int((ann.type == t).sum()),
                "by_side": {
                    s: int(((ann.type == t) & (ann.instance.map(_side) == s)).sum()) for s in ("L", "R")
                },
            }
            for t in (GF_TYPE,) + VPN_INPUT_TYPES
        },
        "giant_fibers": [],
    }

    for gid in gf_ids:
        rows = into_gf[into_gf.body_post == gid].copy()
        rows["pre_type"] = rows.body_pre.map(type_of)
        rows["pre_super"] = rows.body_pre.map(super_of)
        rows["pre_side"] = rows.body_pre.map(lambda b: _side(inst_of.get(b)))
        total = int(rows.weight.sum())
        vp = rows[rows.pre_super == "visual_projection"]
        vp_total = int(vp.weight.sum())
        per_type = {}
        for t in VPN_INPUT_TYPES:
            sub = rows[rows.pre_type == t].sort_values(["weight", "body_pre"], ascending=[False, True])
            per_type[t] = {
                "cells": int(sub.body_pre.nunique()),
                "synapses": int(sub.weight.sum()),
                "presynaptic_side_counts": {
                    str(k): int(v) for k, v in sub.pre_side.fillna("unknown").value_counts().sort_index().items()
                },
                "per_cell_synapses": [[int(b), int(w)] for b, w in zip(sub.body_pre, sub.weight)],
            }
        lc_lplc = sum(per_type[t]["synapses"] for t in VPN_INPUT_TYPES)
        superclass = (
            rows.groupby(rows.pre_super.fillna("unannotated")).weight.sum().sort_values(ascending=False)
        )
        outs = from_gf[from_gf.body_pre == gid].copy()
        outs["post_type"] = outs.body_post.map(type_of)
        top_out = (
            outs.groupby(outs.post_type.fillna("unannotated")).weight.sum().sort_values(ascending=False).head(10)
        )
        out["giant_fibers"].append(
            {
                "body_id": gid,
                "instance": inst_of[gid],
                "input_synapses_total": total,
                "input_synapses_by_presynaptic_superclass": {str(k): int(v) for k, v in superclass.items()},
                "visual_projection_input_synapses": vp_total,
                "inputs": per_type,
                "share_of_total_input": round(lc_lplc / total, 4),
                "share_of_visual_projection_input": round(lc_lplc / vp_total, 4),
                "output_synapses_total": int(outs.weight.sum()),
                "top_output_types": {str(k): int(v) for k, v in top_out.items()},
            }
        )

    fv = from_vpn.copy()
    fv["pre_type"] = fv.body_pre.map(type_of)
    fv["post_type"] = fv.body_post.map(type_of)
    lp_lc = fv[(fv.pre_type == "LPLC2") & (fv.post_type == "LC4")]
    lc_lp = fv[(fv.pre_type == "LC4") & (fv.post_type == "LPLC2")]
    out["lplc2_to_lc4"] = {
        "edges": int(len(lp_lc)),
        "synapses": int(lp_lc.weight.sum()),
        "edges_with_at_least_5_synapses": int((lp_lc.weight >= 5).sum()),
    }
    out["lc4_to_lplc2"] = {"edges": int(len(lc_lp)), "synapses": int(lc_lp.weight.sum())}
    dn = fv[fv.post_type.fillna("").str.startswith("DN")]
    top_dn = dn.groupby(["pre_type", "post_type"]).weight.sum().sort_values(ascending=False).head(12)
    out["top_descending_targets"] = [[str(a), str(b), int(w)] for (a, b), w in top_dn.items()]

    # Other descending neurons that receive LC4/LPLC2 input. The teacher model uses
    # them as a *candidate* parallel (non-GF) escape pathway; that role is a
    # modelling hypothesis, not an annotation of the dataset.
    fv["pre_side"] = fv.body_pre.map(lambda b: _side(inst_of.get(b)))
    fv["post_inst"] = fv.body_post.map(inst_of)
    fv["post_side"] = fv.post_inst.map(_side)
    par = fv[fv.post_type.isin(PARALLEL_DN_CANDIDATES)]
    cand = {}
    for side in ("L", "R"):
        rows = par[par.post_side == side]
        entry = {}
        for t in VPN_INPUT_TYPES:
            sub = rows[rows.pre_type == t]
            entry[t] = {
                "ipsilateral": int(sub[sub.pre_side == side].weight.sum()),
                "contralateral": int(sub[(sub.pre_side != side) & sub.pre_side.notna()].weight.sum()),
            }
        entry["by_type"] = {
            str(k): int(v) for k, v in rows.groupby("post_type").weight.sum().sort_index().items()
        }
        cand[side] = entry
    out["parallel_dn_candidates"] = {"types": list(PARALLEL_DN_CANDIDATES), "by_postsynaptic_side": cand}
    ntc = nt[nt.cell_type.isin((GF_TYPE,) + VPN_INPUT_TYPES)]
    out["consensus_neurotransmitter"] = {
        t: {str(k): int(v) for k, v in g.consensus_nt.value_counts().sort_index().items()}
        for t, g in ntc.groupby("cell_type")
    }
    return out


def write_json(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n")
