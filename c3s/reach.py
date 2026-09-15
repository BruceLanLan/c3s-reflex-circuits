"""Reachable states, equivalence from reset, and single stuck-at redundancy.

Every c3s latch starts at 0, so a sequential circuit has a reset state (all latches
0) and a set of states reachable from it. Two circuits with the same ports behave
identically from reset exactly when no reachable state pair of their product
machine gives different outputs for some input; `sequential_equivalence` checks
that by breadth-first search, evaluating every input pattern at every reachable
pair. The two circuits need not share a state encoding or a latch count.

A single stuck-at fault on a NAND output is undetectable on a set of rows when
forcing that signal to the constant changes no primary output and no latch D on
any of those rows. The signal can then be tied to the constant without changing
the step relation on those rows (`remove_redundancy`). When the rows are "every
input at every reachable state", next-state values on reachable rows are kept, so
the reachable set is kept too.

ABC is used as in `c3s.synth`: an untrusted optimiser whose output is parsed back
into the repository's IR. `abc_candidates` returns unverified circuits; callers
check them with the functions above or with `exhaust.step_table`.
"""

from __future__ import annotations

import heapq
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import exhaust, loom, synth
from .netlist import Builder, Circuit, Latch, Nand, Ref

_ONES = np.uint64(0xFFFFFFFFFFFFFFFF)


def latch_indices(circuit: Circuit) -> list[int]:
    return [i for i, c in enumerate(circuit.cells) if isinstance(c, Latch)]


def nand_count(circuit: Circuit) -> int:
    return sum(isinstance(c, Nand) for c in circuit.cells)


def _no_refs(circuit: Circuit) -> None:
    if any(isinstance(c, Ref) for c in circuit.cells):
        raise ValueError("flatten REF cells first")


# ---- domains and evaluation ---------------------------------------------------


@dataclass
class Domain:
    """Rows to evaluate: bit-sliced input and latch-state planes and a mask of the
    bits that are real rows (blocks narrower than a word leave padding bits)."""

    inputs: np.ndarray  # (n_inputs, words)
    state: np.ndarray  # (n_latches, words)
    mask: np.ndarray  # (words,)
    rows: int


def _mask(rows_per_block: int, blocks: int) -> np.ndarray:
    block = np.zeros((rows_per_block + 63) // 64, dtype=np.uint64)
    full, rem = divmod(rows_per_block, 64)
    block[:full] = _ONES
    if rem:
        block[full] = np.uint64((1 << rem) - 1)
    return np.tile(block, blocks)


def full_domain(circuit: Circuit) -> Domain:
    """Every (input, state) row, in `exhaust.step_table` order."""
    n, m = circuit.n_inputs, len(latch_indices(circuit))
    if n + m > exhaust.MAX_EXHAUSTIVE_BITS:
        raise ValueError(f"{n + m} free bits is too many for exhaustive evaluation")
    pat = exhaust._patterns(n + m)
    return Domain(pat[:n], pat[n:], _mask(1 << (n + m), 1), 1 << (n + m))


def states_domain(circuit: Circuit, states: list[int]) -> Domain:
    """Every input pattern at each listed state (bit k = latch k in cell order)."""
    n, m = circuit.n_inputs, len(latch_indices(circuit))
    ip = exhaust._patterns(n)
    w = ip.shape[1]
    st = np.zeros((m, w * len(states)), dtype=np.uint64)
    for j, s in enumerate(states):
        for k in range(m):
            if (s >> k) & 1:
                st[k, j * w : (j + 1) * w] = _ONES
    return Domain(np.tile(ip, (1, len(states))), st, _mask(1 << n, len(states)), len(states) << n)


def evaluate(circuit: Circuit, dom: Domain) -> np.ndarray:
    """All signals over the domain, shape (n_signals, words)."""
    _no_refs(circuit)
    sig = np.zeros((circuit.n_signals, dom.mask.shape[0]), dtype=np.uint64)
    sig[1] = _ONES
    sig[2 : 2 + circuit.n_inputs] = dom.inputs
    first, k = circuit.first_cell_signal, 0
    for i, c in enumerate(circuit.cells):
        if isinstance(c, Nand):
            sig[first + i] = ~(sig[c.a] & sig[c.b])
        else:
            sig[first + i] = dom.state[k]
            k += 1
    return sig


def observed_signals(circuit: Circuit) -> list[int]:
    """Primary outputs, then every latch's D."""
    return circuit.output_signals() + [circuit.cells[i].d for i in latch_indices(circuit)]  # type: ignore[union-attr]


def _values(sig: np.ndarray, signals: list[int], rows: int) -> np.ndarray:
    out = np.zeros(rows, dtype=np.uint64)
    for j, s in enumerate(signals):
        out |= exhaust._unpack(sig[s], rows).astype(np.uint64) << np.uint64(j)
    return out


def block_step(circuit: Circuit, state: int) -> tuple[np.ndarray, np.ndarray]:
    """(outputs, next state) for every input pattern at one state."""
    sig = evaluate(circuit, states_domain(circuit, [state]))
    rows = 1 << circuit.n_inputs
    d = [circuit.cells[i].d for i in latch_indices(circuit)]  # type: ignore[union-attr]
    return _values(sig, circuit.output_signals(), rows), _values(sig, d, rows)


# ---- reachability and equivalence from reset ------------------------------------


def reachable_states(circuit: Circuit, reset: int = 0) -> list[int]:
    seen, frontier = {reset}, [reset]
    while frontier:
        new = []
        for s in frontier:
            for t in np.unique(block_step(circuit, s)[1]).tolist():
                if t not in seen:
                    seen.add(t)
                    new.append(t)
        frontier = new
    return sorted(seen)


def is_closed(circuit: Circuit, states: list[int]) -> bool:
    """No input takes a listed state outside the list."""
    allowed = set(states)
    return all(set(np.unique(block_step(circuit, s)[1]).tolist()) <= allowed for s in states)


def sequential_equivalence(a: Circuit, b: Circuit) -> dict:
    """Product-machine search from the all-zero reset state of both circuits."""
    if (a.n_inputs, a.n_outputs) != (b.n_inputs, b.n_outputs):
        raise ValueError("port mismatch")
    if max(len(latch_indices(a)), len(latch_indices(b))) > 32:
        raise ValueError("more than 32 latches")
    steps: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}

    def step(which: int, c: Circuit, s: int):
        if (which, s) not in steps:
            steps[(which, s)] = block_step(c, s)
        return steps[(which, s)]

    parent: dict[tuple[int, int], tuple[tuple[int, int], int] | None] = {(0, 0): None}
    frontier = [(0, 0)]
    while frontier:
        new = []
        for pair in frontier:
            oa, na = step(0, a, pair[0])
            ob, nb = step(1, b, pair[1])
            bad = np.flatnonzero(oa != ob)
            if bad.size:
                x = int(bad[0])
                trace, at = [x], pair
                while parent[at] is not None:
                    at, prev_x = parent[at]  # type: ignore[misc]
                    trace.append(prev_x)
                return {
                    "equivalent": False,
                    "counterexample": {
                        "inputs_from_reset": trace[::-1],
                        "state_a": pair[0],
                        "state_b": pair[1],
                        "output_a": int(oa[x]),
                        "output_b": int(ob[x]),
                    },
                }
            keys = (na << np.uint64(32)) | nb
            uniq, first_row = np.unique(keys, return_index=True)
            for key, row in zip(uniq.tolist(), first_row.tolist()):
                t = (key >> 32, key & 0xFFFFFFFF)
                if t not in parent:
                    parent[t] = (pair, row)
                    new.append(t)
        frontier = new
    pairs = sorted(parent)
    return {
        "equivalent": True,
        "reachable_state_pairs": len(pairs),
        "rows_checked": len(pairs) << a.n_inputs,
        "reachable_states_a": len({p[0] for p in pairs}),
        "reachable_states_b": len({p[1] for p in pairs}),
        "pairs": pairs,
    }


# ---- single stuck-at redundancy --------------------------------------------------


def undetectable_faults(circuit: Circuit, dom: Domain) -> list[tuple[int, int]]:
    """(signal, stuck value) for every NAND output whose stuck-at fault changes no
    observed signal on the domain's rows. A NAND that is constant on the domain
    appears once, stuck at its constant."""
    sig = evaluate(circuit, dom)
    mask = dom.mask
    zeros = np.zeros_like(mask)
    first = circuit.first_cell_signal
    obs = set(observed_signals(circuit))
    readers: dict[int, list[int]] = {}
    for i, c in enumerate(circuit.cells):
        if isinstance(c, Nand):
            for x in {c.a, c.b}:
                readers.setdefault(x, []).append(first + i)

    found = []
    for i, c in enumerate(circuit.cells):
        if not isinstance(c, Nand):
            continue
        g = first + i
        for v in (0, 1):
            forced = mask if v else zeros
            if not np.any((sig[g] ^ forced) & mask):
                found.append((g, v))
                continue
            if g in obs:
                continue
            changed = {g: forced}
            heap = sorted(set(readers.get(g, [])))
            queued = set(heap)
            detected = False
            while heap:
                s = heapq.heappop(heap)
                cell = circuit.cells[s - first]
                va, vb = changed.get(cell.a), changed.get(cell.b)  # type: ignore[union-attr]
                new = ~((sig[cell.a] if va is None else va) & (sig[cell.b] if vb is None else vb))  # type: ignore[union-attr]
                if not np.any((new ^ sig[s]) & mask):
                    continue
                if s in obs:
                    detected = True
                    break
                changed[s] = new
                for r in readers.get(s, []):
                    if r not in queued:
                        queued.add(r)
                        heapq.heappush(heap, r)
            if not detected:
                found.append((g, v))
    return found


def _not(b: Builder, x: int) -> int:
    idx = x - 2 - b.n_inputs
    if idx >= 0:
        cell = b._cells[idx]
        if isinstance(cell, Nand) and cell.a == cell.b:
            return cell.a
    return b.not_(x)


def _nand(b: Builder, x: int, y: int) -> int:
    if b.ZERO in (x, y):
        return b.ONE
    if x == b.ONE:
        return _not(b, y)
    if y == b.ONE or x == y:
        return _not(b, x)
    return b.nand(x, y)


def rebuild(circuit: Circuit, tie: dict[int, int] | None = None) -> Circuit:
    """Copy through a Builder, tying the listed NAND signals to constants and folding
    constants, double inversions and duplicate gates. Latch order is kept."""
    _no_refs(circuit)
    tie = tie or {}
    b = Builder(circuit.n_inputs)
    first = circuit.first_cell_signal
    remap = {0: 0, 1: 1, **{2 + i: 2 + i for i in range(circuit.n_inputs)}}
    latches = []
    for i in latch_indices(circuit):
        remap[first + i] = b.latch()
        latches.append((remap[first + i], circuit.cells[i].d))  # type: ignore[union-attr]
    for i, c in enumerate(circuit.cells):
        if isinstance(c, Nand):
            s = first + i
            remap[s] = tie[s] if s in tie else _nand(b, remap[c.a], remap[c.b])
    for q, d in latches:
        b.drive(q, remap[d])
    return b.finish([remap[s] for s in circuit.output_signals()])


def remove_redundancy(circuit: Circuit, domain_of) -> tuple[Circuit, list[dict]]:
    """Tie undetectable faults to their constants, one at a time, re-analysing after
    each, until no undetectable fault shortens the circuit. `domain_of(circuit)`
    gives the rows on which detectability is judged."""
    cur = rebuild(circuit)
    log: list[dict] = []
    while True:
        n0 = nand_count(cur)
        for g, v in undetectable_faults(cur, domain_of(cur)):
            cand = rebuild(cur, {g: v})
            if nand_count(cand) < n0:
                log.append({"signal": g, "stuck_at": v, "nand_after": nand_count(cand)})
                cur = cand
                break
        else:
            return cur, log


# ---- latch cut and ABC -----------------------------------------------------------


def cut_latches(circuit: Circuit) -> Circuit:
    """Combinational step relation: inputs, then latch Qs in cell order; outputs,
    then latch Ds. Its truth table is `outs | nxt << n_outputs` of `step_table`."""
    _no_refs(circuit)
    n, li = circuit.n_inputs, latch_indices(circuit)
    b = Builder(n + len(li))
    first = circuit.first_cell_signal
    remap = {0: 0, 1: 1, **{2 + i: 2 + i for i in range(n)}}
    for k, i in enumerate(li):
        remap[first + i] = 2 + n + k
    for i, c in enumerate(circuit.cells):
        if isinstance(c, Nand):
            remap[first + i] = b.nand(remap[c.a], remap[c.b])
    outs = [remap[s] for s in circuit.output_signals()]
    outs += [remap[circuit.cells[i].d] for i in li]  # type: ignore[union-attr]
    return b.finish(outs)


def wrap_latches(comb: Circuit, n_inputs: int, n_outputs: int) -> Circuit:
    """Inverse of `cut_latches`: the trailing inputs become latches fed by the
    trailing outputs, in order."""
    m = comb.n_inputs - n_inputs
    if m < 0 or comb.n_outputs != n_outputs + m:
        raise ValueError("port counts do not describe a latch cut")
    b = Builder(n_inputs)
    qs = [b.latch() for _ in range(m)]
    outs = b.inline(comb, b.inputs() + qs)
    for q, d in zip(qs, outs[n_outputs:]):
        b.drive(q, d)
    return b.finish(outs[:n_outputs])


def circuit_to_seq_blif(circuit: Circuit) -> str:
    """BLIF with `.latch` lines (initial value 0) for NAND and LATCH cells."""
    _no_refs(circuit)
    first = circuit.first_cell_signal
    name = {0: "c0", 1: "c1", **{2 + i: f"x{i}" for i in range(circuit.n_inputs)}}
    for i, c in enumerate(circuit.cells):
        name[first + i] = f"{'q' if isinstance(c, Latch) else 'n'}{first + i}"
    lines = [".model c3s", ".inputs " + " ".join(f"x{i}" for i in range(circuit.n_inputs))]
    lines.append(".outputs " + " ".join(f"y{k}" for k in range(circuit.n_outputs)))
    for i in latch_indices(circuit):
        lines.append(f".latch {name[circuit.cells[i].d]} {name[first + i]} 0")  # type: ignore[union-attr]
    lines += [".names c0", ".names c1", "1"]
    for i, c in enumerate(circuit.cells):
        if isinstance(c, Nand):
            lines += [f".names {name[c.a]} {name[c.b]} {name[first + i]}", "0- 1", "-0 1"]
    for k, sig in enumerate(circuit.output_signals()):
        lines += [f".names {name[sig]} y{k}", "1 1"]
    lines.append(".end")
    return "\n".join(lines) + "\n"


def parse_seq_blif(text: str, n_in: int, n_out: int) -> Circuit:
    """ABC's mapped BLIF (gates from `synth.NAND_GENLIB`, trivial `.names`, and
    `.latch` lines whose initial value must be 0)."""
    text = re.sub(r"\\\n", " ", text)
    inputs: list[str] = []
    outputs: list[str] = []
    latches: list[tuple[str, str]] = []
    pending: list[tuple[str, list[str], str, list[str]]] = []
    lines = [ln.strip() for ln in text.splitlines()]
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith(".inputs"):
            inputs += ln.split()[1:]
        elif ln.startswith(".outputs"):
            outputs += ln.split()[1:]
        elif ln.startswith(".latch"):
            parts = ln.split()
            init = parts[-1] if len(parts) in (4, 6) else "3"
            if init != "0":
                raise ValueError(f"latch {parts[2]} has initial value {init}; c3s latches start at 0")
            latches.append((parts[1], parts[2]))
        elif ln.startswith(".gate"):
            parts = ln.split()
            pins = dict(p.split("=") for p in parts[2:])
            pending.append(("gate", [parts[1]], pins["Y"], [pins.get("A", ""), pins.get("B", "")]))
        elif ln.startswith(".names"):
            sigs = ln.split()[1:]
            cover = []
            while i + 1 < len(lines) and lines[i + 1] and not lines[i + 1].startswith("."):
                i += 1
                cover.append(lines[i])
            pending.append(("names", cover, sigs[-1], sigs[:-1]))
        i += 1
    if len(inputs) != n_in or len(outputs) != n_out:
        raise ValueError(f"BLIF has {len(inputs)}/{len(outputs)} ports, expected {n_in}/{n_out}")

    b = Builder(n_in)
    net = {nm: b.input(k) for k, nm in enumerate(inputs)}
    for _, q in latches:
        net[q] = b.latch()
    remaining = pending
    while remaining:
        progress, nxt = False, []
        for kind, info, out, ins in remaining:
            if any(x and x not in net for x in ins):
                nxt.append((kind, info, out, ins))
                continue
            progress = True
            if kind == "names":
                net[out] = synth._names_to_signal(b, info, [net[x] for x in ins])
            elif info[0] == "ZERO":
                net[out] = b.ZERO
            elif info[0] == "ONE":
                net[out] = b.ONE
            elif info[0] == "buf":
                net[out] = net[ins[0]]
            elif info[0] == "inv":
                net[out] = b.not_(net[ins[0]])
            elif info[0] == "nand2":
                net[out] = b.nand(net[ins[0]], net[ins[1]])
            else:
                raise ValueError(f"unexpected gate {info[0]}")
        if not progress:
            raise ValueError("combinational loop or undefined net in BLIF")
        remaining = nxt
    for d, q in latches:
        b.drive(net[q], net[d])
    return b.finish([net[o] for o in outputs])


def abc_candidates(circuit: Circuit, sequential: bool, recipes=None, timeout: int = 1800) -> tuple[dict[str, Circuit], dict[str, str]]:
    """Unverified ABC results keyed by recipe, and the error of each failed recipe.

    sequential=False: the latch-cut circuit through each `synth.RECIPES` entry,
    rewrapped with the same latch order; the target is the step relation on every row.
    sequential=True: the circuit with its latches, `scorr` (signal correspondence by
    induction from the reset state) before each recipe; the target is behaviour from
    reset only, and ABC may merge or drop latches.
    """
    recipes = recipes or list(synth.RECIPES)
    cands: dict[str, Circuit] = {}
    errors: dict[str, str] = {}
    n, n_out = circuit.n_inputs, circuit.n_outputs
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        lib = work / "nand.genlib"
        lib.write_text(synth.NAND_GENLIB)
        src = work / "in.blif"
        cut = None
        if sequential:
            src.write_text(circuit_to_seq_blif(circuit))
        else:
            cut = cut_latches(circuit)
            src.write_text(synth.circuit_to_blif(cut))
        for r in recipes:
            key = f"{'scorr' if sequential else 'cut'}+{r}"
            out = work / f"out-{r}.blif"
            pre = "strash; scorr; " if sequential else ""
            script = f"read_library {lib}; read_blif {src}; {pre}{synth.RECIPES[r]}; write_blif {out}"
            try:
                res = subprocess.run([synth.abc_binary(), "-q", script], capture_output=True, text=True, timeout=timeout)
                if res.returncode != 0 or not out.exists():
                    raise RuntimeError(f"ABC failed: {res.stdout[-300:]} {res.stderr[-300:]}")
                if cut is None:
                    cands[key] = parse_seq_blif(out.read_text(), n, n_out)
                else:
                    mapped = synth.parse_mapped_blif(out.read_text(), cut.n_inputs, cut.n_outputs)
                    cands[key] = wrap_latches(mapped, n, n_out)
            except (RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as e:
                errors[key] = str(e)[:300]
    return cands, errors


# ---- LoomEscape episodes ------------------------------------------------------------


def motor_trace(step_outs: np.ndarray, step_next: np.ndarray, stim: loom.Stimulus, p: loom.TeacherParams, enc: loom.Encoding) -> list[int]:
    """Motor code at every tick until the first takeoff, driving a core through its
    step tables as `reflex.run_core_episode` does."""
    nf, state, trace = enc.n_inputs, 0, []
    for th, dth in loom.stimulus_samples(stim, p):
        row = loom.encode_features(th, dth, stim.azimuth_deg, enc) | (1 << nf) | (state << (nf + 1))
        trace.append(int(step_outs[row]))
        state = int(step_next[row])
        if trace[-1] in (loom.CORE_SHORT, loom.CORE_LONG):
            break
    return trace
