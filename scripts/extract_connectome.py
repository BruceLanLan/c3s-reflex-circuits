"""Download (if needed), verify and extract the GF looming-escape subgraph.

    python scripts/extract_connectome.py --cache ~/.cache/c3s/malecns-v1.0

Writes data/malecns-v1.0-gf-escape-subgraph.json. The release files are about
1.1 GB in total and are verified against pinned SHA-256 digests.
"""

import argparse
from pathlib import Path

from c3s.connectome import extract_escape_subgraph, fetch, write_json

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "c3s" / "malecns-v1.0")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "malecns-v1.0-gf-escape-subgraph.json")
    args = ap.parse_args()
    paths = fetch(args.cache.expanduser())
    write_json(extract_escape_subgraph(paths), args.out)
    print(f"wrote {args.out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
