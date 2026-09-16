"""Fetch the real shapes of the cells the model names, for the simulator page.

    python scripts/build_sim_skeletons.py          # writes docs/sim/skeletons.{bin,json}

The committed subgraph says which cells synapse onto the giant fibers and how often;
it says nothing about where they are. MaleCNS v1.0 publishes skeletons (SWC, already
downsampled at source) in a public bucket, so the page can draw the cells themselves
instead of a made-up brain shape:

    https://storage.googleapis.com/flyem-male-cns/v1.0/segmentation/skeletons-malecns/skeletons-swc/<bodyId>.swc

Which cells: both giant fibers in full, and per fiber the `--per-type` strongest LC4
and LPLC2 inputs by measured synapse count — a rule anyone can re-run, not a hand
picked set. Long chains are decimated (`--decimate`), branch points and endpoints
always kept, so the shape survives while the file stays small enough for a page.

Every downloaded file's SHA-256 goes into docs/sim/skeletons.json, together with the
URL it came from, so the geometry on the page can be traced back to the release. The
raw SWC files are not committed; the decimated geometry is, as derived data, the same
arrangement as the aggregate in data/. MaleCNS is CC-BY: see NOTICE.

Needs the network, so scripts/verify.sh does not rerun it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUBGRAPH = ROOT / "data" / "malecns-v1.0-gf-escape-subgraph.json"
OUT_BIN = ROOT / "docs" / "sim" / "skeletons.bin"
OUT_JSON = ROOT / "docs" / "sim" / "skeletons.json"
URL = "https://storage.googleapis.com/flyem-male-cns/v1.0/segmentation/skeletons-malecns/skeletons-swc/{body}.swc"
TYPES = ("LC4", "LPLC2")


def fetch(body: int, cache: Path) -> tuple[str, str]:
    """The SWC text and its SHA-256, from the cache when it is already there."""
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{body}.swc"
    if not path.exists():
        req = urllib.request.Request(URL.format(body=body), headers={"user-agent": "c3s-reflex-circuits/0.3"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            path.write_bytes(resp.read())
    raw = path.read_bytes()
    return raw.decode(), hashlib.sha256(raw).hexdigest()


def parse_swc(text: str) -> tuple[list[tuple[float, float, float]], list[int]]:
    """(positions, parent index per node). SWC columns: id type x y z radius parent."""
    pos: list[tuple[float, float, float]] = []
    parent: list[int] = []
    index: dict[int, int] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        f = line.split()
        node = int(f[0])
        index[node] = len(pos)
        pos.append((float(f[2]), float(f[3]), float(f[4])))
        parent.append(int(f[6]))
    return pos, [index.get(p, -1) for p in parent]


def decimate(pos, parent, step: int) -> list[tuple[int, int]]:
    """Segments after dropping every node that is merely in the middle of a chain,
    except every `step`-th one. Branch points and endpoints always survive."""
    children = [0] * len(pos)
    for p in parent:
        if p >= 0:
            children[p] += 1
    keep = [False] * len(pos)
    for i in range(len(pos)):
        interior = parent[i] >= 0 and children[i] == 1
        keep[i] = (not interior) or (i % step == 0)
    segments = []
    for i in range(len(pos)):
        if not keep[i]:
            continue
        p = parent[i]
        while p >= 0 and not keep[p]:
            p = parent[p]
        if p >= 0:
            segments.append((p, i))
    return segments


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per-type", type=int, default=12, help="strongest inputs of each type per fiber")
    ap.add_argument("--decimate", type=int, default=6, help="keep every Nth node inside a chain")
    ap.add_argument("--gf-decimate", type=int, default=2, help="the giant fibers deserve more detail")
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "c3s" / "malecns-skeletons")
    args = ap.parse_args()

    sub = json.loads(SUBGRAPH.read_text())
    wanted: list[dict] = []
    for gf in sub["giant_fibers"]:
        side = gf["instance"].rstrip(")").split("_")[-1]
        wanted.append({"body": int(gf["body_id"]), "type": "DNp01", "side": side, "synapses": gf["visual_projection_input_synapses"], "step": args.gf_decimate})
        for t in TYPES:
            rows = sorted(gf["inputs"][t]["per_cell_synapses"], key=lambda r: (-int(r[1]), int(r[0])))
            for body, syn in rows[: args.per_type]:
                wanted.append({"body": int(body), "type": t, "side": side, "synapses": int(syn), "step": args.decimate})

    cells, points, segments, sources = [], [], [], []
    for w in wanted:
        text, digest = fetch(w["body"], args.cache)
        pos, parent = parse_swc(text)
        segs = decimate(pos, parent, w["step"])
        used = sorted({i for s in segs for i in s})
        remap = {old: len(points) + k for k, old in enumerate(used)}
        points.extend(pos[i] for i in used)
        cells.append(
            {
                "body_id": w["body"],
                "type": w["type"],
                "side": w["side"],
                "synapses": w["synapses"],
                "first_segment": len(segments),
                "segments": len(segs),
                "nodes_in_release": len(pos),
            }
        )
        segments.extend((remap[a], remap[b]) for a, b in segs)
        sources.append({"body_id": w["body"], "sha256": digest, "bytes": len(text.encode())})
        print(f"{w['type']:5s} {w['side']} {w['body']:>7d}  {len(pos):>5d} nodes -> {len(segs):>5d} segments", flush=True)

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    centre = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2)
    scale = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) / 2 or 1.0

    blob = bytearray()
    for x, y, z in points:
        blob += struct.pack("<3f", (x - centre[0]) / scale, (y - centre[1]) / scale, (z - centre[2]) / scale)
    seg_offset = len(blob)
    for a, b in segments:
        blob += struct.pack("<2I", a, b)
    OUT_BIN.write_bytes(blob)

    doc = {
        "format": "c3s.skeletons/1",
        "note": "real skeletons from MaleCNS v1.0 (CC-BY, see NOTICE), decimated; positions are the release's, normalised into a unit box",
        "source": {
            "release": sub["release"],
            "licence": sub["license"],
            "url_pattern": URL,
            "subgraph_sha256": hashlib.sha256(SUBGRAPH.read_bytes()).hexdigest(),
            "selection": f"both giant fibers, and per fiber the {args.per_type} strongest LC4 and LPLC2 inputs by synapse count",
            "decimation": {"chain_step": args.decimate, "giant_fiber_chain_step": args.gf_decimate},
            "files": sources,
        },
        "layout": {"points": len(points), "segments": len(segments), "segments_byte_offset": seg_offset,
                   "point_format": "float32 xyz", "segment_format": "uint32 pair of point indices"},
        "cells": cells,
    }
    OUT_JSON.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"wrote {OUT_BIN.relative_to(ROOT)} ({len(blob)} bytes) and {OUT_JSON.relative_to(ROOT)}: "
          f"{len(cells)} cells, {len(points)} points, {len(segments)} segments")


if __name__ == "__main__":
    main()
