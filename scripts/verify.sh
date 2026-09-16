#!/usr/bin/env bash
# Re-run every check in the repository from committed inputs.
#
#   scripts/verify.sh            # everything except the connectome download
#   scripts/verify.sh --full     # also re-extract the subgraph from the MaleCNS release (~1.1 GB)
#
# Requires: Python >= 3.10 with numpy, torch, pytest (and pyarrow, pandas for --full),
# yosys-abc (or abc) on PATH, Foundry (forge) on PATH; a C compiler and Node for the
# firmware tests (each build's tests are skipped without its tool).
#
# Regenerated and compared byte for byte: teacher calibration, decision table,
# encoding comparison, policy and core manifests, component manifests, the data block
# of the web demo, the firmware's data files, EVM fixtures (and, with --full, the
# connectome subgraph).
# NOT regenerated here: DLGN runs (scripts/train_dlgn.py; a re-run of the 128-128-64
# run reproduced its netlist byte for byte on torch 2.14 CPU), controls.json
# (scripts/run_controls.py, about 25 minutes), sealed-evaluation.json (needs the
# unpublished sealed spec), docs/sim/c3s_core.wasm (scripts/build_sim.py; another
# clang may emit different bytes, so tests/test_firmware.py checks it by behaviour)
# and the minimal cores (scripts/build_minimal_cores.py; the bytes depend on the ABC
# build, so tests/test_minimal_cores.py checks them by equivalence). The simulator's
# skeleton geometry (scripts/build_sim_skeletons.py) needs the network, so it is not
# rebuilt either; tests/test_firmware.py checks it against the committed subgraph.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
PY="${PYTHON:-python3}"

step() { printf '\n== %s\n' "$*"; }

if [[ "${1:-}" == "--full" ]]; then
  step "extract MaleCNS v1.0 subgraph and compare with the committed file"
  cp data/malecns-v1.0-gf-escape-subgraph.json /tmp/c3s-subgraph-committed.json
  "$PY" scripts/extract_connectome.py
  cmp data/malecns-v1.0-gf-escape-subgraph.json /tmp/c3s-subgraph-committed.json
fi

step "unit and exhaustive tests"
"$PY" -m pytest -q

step "rebuild LoomEscape-16 from scratch (calibration, table, circuits, cores)"
"$PY" scripts/build_loom_escape.py
"$PY" scripts/compare_encodings.py
"$PY" scripts/export_components.py
"$PY" scripts/build_demo.py
"$PY" scripts/build_firmware.py

step "committed artifacts must be reproduced byte for byte"
git diff --exit-code -- circuits/ data/ docs/demo/ firmware/cardputer/lib/c3s_core/

step "EVM evaluator over the full domain of every exported circuit"
"$PY" scripts/export_evm_fixtures.py
git diff --exit-code -- contracts/test/fixtures/
(cd contracts && { [[ -d lib/forge-std ]] || forge install foundry-rs/forge-std --no-git; } && forge test)

printf '\nall checks passed\n'
