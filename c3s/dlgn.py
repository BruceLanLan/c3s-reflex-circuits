"""Differentiable logic gate networks, re-implemented from the paper.

Method: F. Petersen, C. Borgelt, H. Kuehne, O. Deussen, "Deep Differentiable
Logic Gate Networks", NeurIPS 2022 (arXiv:2210.08277). The authors' reference
implementation (difflogic) is MIT-licensed and states "Patent pending"; this
module is an independent implementation written from the paper's description and
does not change that notice's relevance. Check it before commercial use.

Each neuron reads two fixed, randomly chosen signals of the previous layer and
holds a learnable distribution over the 16 two-input Boolean functions. During
training a neuron outputs the expectation of its gate under that distribution,
where each gate is evaluated through the multilinear extension of its truth
table (the probabilistic relaxation used in the paper). After training every
neuron keeps its most probable gate, which yields a plain Boolean circuit.

The classification head follows the paper's GroupSum: the last layer is split
into one group per class, the class score is the (soft) popcount of its group,
and prediction is the argmax. The compiled circuit implements that head with the
component library's popcount and argmax, so hardware and model agree exactly.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import components
from .netlist import Builder, Circuit

# TRUTH[g] = outputs of gate g at inputs (a, b) = (0,0), (0,1), (1,0), (1,1).
# Gate g's four output bits, read with (0,0) as the most significant, spell g.
TRUTH = np.array([[(g >> (3 - k)) & 1 for k in range(4)] for g in range(16)], dtype=np.float32)
GATE_NAMES = (
    "false", "and", "a_and_not_b", "a", "not_a_and_b", "b", "xor", "or",
    "nor", "xnor", "not_b", "a_or_not_b", "not_a", "not_a_or_b", "nand", "true",
)
PASS_A = 3


def wire_gate(b: Builder, g: int, x: int, y: int) -> int:
    """Gate g as NAND logic (constants fold away)."""
    if g == 0:
        return b.ZERO
    if g == 1:
        return b.and_(x, y)
    if g == 2:
        return b.and_(x, b.not_(y))
    if g == 3:
        return x
    if g == 4:
        return b.and_(b.not_(x), y)
    if g == 5:
        return y
    if g == 6:
        return b.xor(x, y)
    if g == 7:
        return b.or_(x, y)
    if g == 8:
        return b.not_(b.or_(x, y))
    if g == 9:
        return b.not_(b.xor(x, y))
    if g == 10:
        return b.not_(y)
    if g == 11:
        return b.nand(b.not_(x), y)
    if g == 12:
        return b.not_(x)
    if g == 13:
        return b.nand(x, b.not_(y))
    if g == 14:
        return b.nand(x, y)
    if g == 15:
        return b.ONE
    raise ValueError(g)


@dataclass
class HardNetwork:
    """A trained network after gate selection. Pure numpy; no torch needed."""

    n_inputs: int
    layers: list[tuple[np.ndarray, np.ndarray, np.ndarray]]  # (a_idx, b_idx, gate)
    n_classes: int

    def forward_bits(self, x: np.ndarray) -> np.ndarray:
        """x: (rows, n_inputs) of {0,1}. Returns last-layer bits."""
        h = x.astype(np.uint8)
        for a, b_, g in self.layers:
            av, bv = h[:, a], h[:, b_]
            idx = (av << 1) | bv  # 0..3 in the (a,b) order of TRUTH
            h = TRUTH.astype(np.uint8)[g[None, :], idx]
        return h

    def predict(self, x: np.ndarray) -> np.ndarray:
        h = self.forward_bits(x)
        k = h.shape[1] // self.n_classes
        scores = h[:, : k * self.n_classes].reshape(len(h), self.n_classes, k).sum(axis=2)
        return scores.argmax(axis=1)  # numpy argmax: first maximum wins, as in the circuit

    def gate_histogram(self) -> dict[str, int]:
        counts = np.zeros(16, int)
        for _, _, g in self.layers:
            counts += np.bincount(g, minlength=16)
        return {GATE_NAMES[i]: int(c) for i, c in enumerate(counts) if c}

    def to_circuit(self) -> Circuit:
        b = Builder(self.n_inputs)
        sig = b.inputs()
        for a, b_idx, g in self.layers:
            sig = [wire_gate(b, int(gg), sig[int(aa)], sig[int(bb)]) for aa, bb, gg in zip(a, b_idx, g)]
        k = len(sig) // self.n_classes
        scores = [components.popcount(b, sig[c * k : (c + 1) * k]) for c in range(self.n_classes)]
        idx = components.argmax(b, scores)
        return b.finish(idx)

    def to_json(self) -> dict:
        return {
            "format": "c3s.dlgn-hard/1",
            "n_inputs": self.n_inputs,
            "n_classes": self.n_classes,
            "layers": [{"a": a.tolist(), "b": b.tolist(), "gate": g.tolist()} for a, b, g in self.layers],
        }


def train(
    x: np.ndarray,
    y: np.ndarray,
    widths: list[int],
    n_classes: int,
    *,
    epochs: int = 400,
    lr: float = 0.02,
    tau: float = 1.0,
    batch: int = 1024,
    seed: int = 0,
    residual_init: float = 0.0,
    class_weights: np.ndarray | None = None,
    log_every: int = 0,
) -> tuple[HardNetwork, list[dict]]:
    """Train a DLGN on binary inputs x (rows, n_in) and integer labels y.

    `widths` are the neuron counts per layer; the last width must be divisible by
    `n_classes`. `residual_init` > 0 biases initial logits towards the pass-through
    gate, a trick introduced for deeper logic networks by Petersen et al. 2024.
    """
    import torch

    if widths[-1] % n_classes:
        raise ValueError("last layer width must be divisible by n_classes")
    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    truth = torch.tensor(TRUTH)  # (16, 4)

    conns = []
    params = []
    prev = x.shape[1]
    for w in widths:
        # Each previous signal is used at least once when possible, then random fill.
        perm = np.concatenate([rng.permutation(prev) for _ in range((2 * w) // prev + 1)])[: 2 * w]
        rng.shuffle(perm)
        a, b_ = perm[:w].copy(), perm[w:].copy()
        same = a == b_
        b_[same] = (b_[same] + 1 + rng.integers(0, max(prev - 1, 1), same.sum())) % prev if prev > 1 else b_[same]
        conns.append((a, b_))
        logits = torch.randn(w, 16) * 1.0
        if residual_init:
            logits[:, PASS_A] += residual_init
        params.append(torch.nn.Parameter(logits))
        prev = w

    opt = torch.optim.Adam(params, lr=lr)
    xt = torch.tensor(x, dtype=torch.float32)
    yt = torch.tensor(y, dtype=torch.long)
    cw = torch.tensor(class_weights, dtype=torch.float32) if class_weights is not None else None
    k = widths[-1] // n_classes
    history = []

    def soft_forward(inp: torch.Tensor) -> torch.Tensor:
        h = inp
        for (a, b_), p in zip(conns, params):
            av, bv = h[:, a], h[:, b_]
            basis = torch.stack([(1 - av) * (1 - bv), (1 - av) * bv, av * (1 - bv), av * bv], dim=-1)
            mix = torch.softmax(p, dim=-1) @ truth  # (w, 4): expected truth table per neuron
            h = (basis * mix).sum(-1)
        return h[:, : k * n_classes].reshape(len(h), n_classes, k).sum(-1) / tau

    n = len(xt)
    for ep in range(epochs):
        order = torch.randperm(n)
        tot = 0.0
        for i in range(0, n, batch):
            idx = order[i : i + batch]
            loss = torch.nn.functional.cross_entropy(soft_forward(xt[idx]), yt[idx], weight=cw)
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += loss.item() * len(idx)
        if log_every and (ep % log_every == 0 or ep == epochs - 1):
            hard = _harden(conns, params, x.shape[1], n_classes)
            acc = float((hard.predict(x) == y).mean())
            history.append({"epoch": ep, "loss": tot / n, "hard_train_acc": acc})
    return _harden(conns, params, x.shape[1], n_classes), history


def _harden(conns, params, n_inputs: int, n_classes: int) -> HardNetwork:
    layers = []
    for (a, b_), p in zip(conns, params):
        layers.append((a.astype(np.int64), b_.astype(np.int64), p.detach().argmax(-1).numpy().astype(np.int64)))
    return HardNetwork(n_inputs, layers, n_classes)
