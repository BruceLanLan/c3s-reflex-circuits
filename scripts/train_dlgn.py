"""Train a differentiable logic gate network on the LoomEscape-16 teacher table.

    python scripts/train_dlgn.py --widths 128,128,64 --epochs 200 --seed 0

Protocol: the 2^16 table rows are split at random into a training half and a
held-out half (fixed split seed); the network never sees held-out rows. The two
pathway bits are learned as one 4-way class (gf | parallel << 1), so the
GroupSum head's argmax index *is* the 2-bit output. After hardening, the network
is compiled to NAND logic, checked exhaustively against the hardened network
(must be 0 rows different), re-optimised by ABC (verified again) and wrapped in
the escape core for episode tests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np

from c3s import calibrate, dlgn, exhaust, loom, reflex, synth
from c3s.netlist import to_bytes

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "circuits" / "loom-escape"
N_CLASSES = 4


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--widths", default="128,128,64")
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--lr", type=float, default=0.02)
    ap.add_argument("--tau", type=float, default=4.0)
    ap.add_argument("--batch", type=int, default=1024)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--split-seed", type=int, default=2026)
    ap.add_argument("--residual-init", type=float, default=0.0)
    ap.add_argument("--tag", default=None)
    args = ap.parse_args()
    widths = [int(w) for w in args.widths.split(",")]
    tag = args.tag or f"dlgn-{'x'.join(map(str, widths))}-s{args.seed}"

    tj = json.loads((OUT / "decision-table.json").read_text())
    enc_d = tj["encoding"]
    enc = loom.Encoding(**{k: tuple(v) if isinstance(v, list) else v for k, v in enc_d.items()})
    table = np.frombuffer(bytes.fromhex(tj["table_hex"]), dtype=np.uint8).astype(np.int64)
    p = calibrate.params_from_dict(tj["params"])
    n_in = enc.n_inputs
    rows = np.arange(len(table))
    x = ((rows[:, None] >> np.arange(n_in)) & 1).astype(np.uint8)
    rng = np.random.default_rng(args.split_seed)
    train_mask = np.zeros(len(rows), bool)
    train_mask[rng.permutation(len(rows))[: len(rows) // 2]] = True

    counts = np.bincount(table[train_mask], minlength=N_CLASSES).astype(float)
    class_weights = counts.sum() / (N_CLASSES * np.maximum(counts, 1))
    t0 = time.time()
    net, history = dlgn.train(
        x[train_mask], table[train_mask], widths, N_CLASSES,
        epochs=args.epochs, lr=args.lr, tau=args.tau, batch=args.batch, seed=args.seed,
        residual_init=args.residual_init, class_weights=class_weights, log_every=25,
    )
    seconds = time.time() - t0
    pred = net.predict(x)

    def acc(mask):
        return round(float((pred[mask] == table[mask]).mean()), 5)

    def recall(mask):
        return {f"gf={c & 1},parallel={c >> 1}": round(float((pred[mask & (table == c)] == c).mean()), 5) for c in range(N_CLASSES) if (mask & (table == c)).any()}

    compiled = net.to_circuit()
    compiled_tt = exhaust.truth_table(compiled)
    compile_diff = int(np.count_nonzero(compiled_tt != pred.astype(np.uint64)))
    opt, opt_report = synth.optimize_circuit(compiled)
    opt_diff = int(np.count_nonzero(exhaust.truth_table(opt) != compiled_tt))

    core = reflex.escape_core(opt, p)
    outs, nxt = exhaust.step_table(core)
    weights = loom.load_weights(ROOT / "data" / "malecns-v1.0-gf-escape-subgraph.json", p)
    fid = {}
    for fam in ("train", "holdout"):
        mode, escape = [], []
        for s in loom.family(fam):
            t = loom.run_teacher_episode(s, weights, p)
            c = reflex.run_core_episode(outs, nxt, s, p, enc)
            mode.append(t.action == c.action)
            escape.append((t.action == loom.CORE_HOLD) == (c.action == loom.CORE_HOLD))
        fid[fam] = {"mode_agreement": round(float(np.mean(mode)), 4), "escape_agreement": round(float(np.mean(escape)), 4)}

    raw = to_bytes(opt)
    report = {
        "format": "c3s.dlgn-run/2",
        "tag": tag,
        "method": "Petersen et al. 2022 (arXiv:2210.08277), independent re-implementation",
        "config": vars(args) | {"widths": widths},
        "encoding": enc.name,
        "table_sha256": tj["table_sha256"],
        "train_seconds": round(seconds, 1),
        "history": history,
        "gate_histogram": net.gate_histogram(),
        "accuracy": {"train_rows": acc(train_mask), "heldout_rows": acc(~train_mask), "all_rows": acc(np.ones(len(rows), bool))},
        "heldout_per_class_recall": recall(~train_mask),
        "rows_differing_from_teacher": int(np.count_nonzero(pred != table)),
        "compiled_vs_hardened_rows_differing": compile_diff,
        "abc_vs_compiled_rows_differing": opt_diff,
        "compiled_metrics": compiled.metrics(),
        "optimized_metrics": opt.metrics(),
        "synthesis": opt_report,
        "core_metrics": core.metrics(),
        "core_episodes": fid,
        "netlist_sha256": hashlib.sha256(raw).hexdigest(),
        "tapeout_netlist_hex": "0x" + raw.hex(),
    }
    (OUT / f"{tag}.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: report[k] for k in ("tag", "accuracy", "heldout_per_class_recall", "compiled_vs_hardened_rows_differing", "abc_vs_compiled_rows_differing", "compiled_metrics", "optimized_metrics", "core_episodes", "train_seconds")}))


if __name__ == "__main__":
    main()
