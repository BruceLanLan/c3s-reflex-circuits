"""Logic synthesis bridge to ABC (Berkeley Logic Synthesis and Verification Group).

Two entry points, both returning a NAND-only `Circuit` that has been checked for
exhaustive equivalence against its source before it is returned:

* `synthesize_table(table, n_in, n_out)` — a complete truth table;
* `optimize_circuit(circuit)` — an existing combinational circuit.

ABC is used as an untrusted optimiser: its output is parsed back into the
repository's own IR and re-verified with the bit-sliced evaluator, so a bug in
ABC, in the BLIF parser or in the mapping cannot silently produce a wrong
circuit.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from . import exhaust
from .netlist import Builder, Circuit, Nand

NAND_GENLIB = """\
GATE ZERO  0 Y=CONST0;
GATE ONE   0 Y=CONST1;
GATE buf   0 Y=A;        PIN * NONINV 1 999 1 0 1 0
GATE inv   1 Y=!A;       PIN * INV 1 999 1 0 1 0
GATE nand2 1 Y=!(A*B);   PIN * INV 1 999 1 0 1 0
"""

# ABC's standard scripts, spelled out with full command names (the short forms
# b/rw/rf/rs/resyn2/... are aliases defined in abc.rc, which a bare ABC binary
# does not load).
_ALIASES = {
    "b": "balance",
    "rw": "rewrite",
    "rwz": "rewrite -z",
    "rf": "refactor",
    "rfz": "refactor -z",
    "rs": "resub",
}


def _expand(script: str) -> str:
    out = []
    for cmd in script.split(";"):
        words = cmd.split()
        if words and words[0] in _ALIASES:
            words = _ALIASES[words[0]].split() + words[1:]
        out.append(" ".join(words))
    return "; ".join(out)


_RESYN2 = _expand("b; rw; rf; b; rw; rwz; b; rfz; rwz; b")
_RESYN2RS = _expand(
    "b; rs -K 6; rw; rs -K 6 -N 2; rf; rs -K 8; b; rs -K 8 -N 2; rw; rs -K 10; rwz; "
    "rs -K 10 -N 2; b; rs -K 12; rfz; rs -K 12 -N 2; rwz; b"
)
_COMPRESS2RS = _expand(
    "b -l; rs -K 6 -l; rw -l; rs -K 6 -N 2 -l; rf -l; rs -K 8 -l; b -l; rs -K 8 -N 2 -l; "
    "rw -l; rs -K 10 -l; rwz -l; rs -K 10 -N 2 -l; b -l; rs -K 12 -l; rfz -l; "
    "rs -K 12 -N 2 -l; rwz -l; b -l"
)

# Optimisation recipes; the smallest verified result wins.
RECIPES = {
    "resyn2": f"strash; {_RESYN2}; {_RESYN2}; map -a",
    "compress2rs": f"strash; dc2; {_RESYN2RS}; {_COMPRESS2RS}; map -a",
    "collapse": f"collapse; strash; dch -f; {_RESYN2}; map -a",
    "dch": "strash; dch -f; map -a; mfs2; strash; dch -f; map -a",
    "deep": f"strash; {_COMPRESS2RS}; dch -f; {_COMPRESS2RS}; dch -f; map -a; mfs2; strash; dch -f; map -a",
}


def abc_binary() -> str:
    exe = os.environ.get("C3S_ABC") or shutil.which("yosys-abc") or shutil.which("abc")
    if not exe:
        raise RuntimeError("ABC not found; install yosys (provides yosys-abc) or set C3S_ABC")
    return exe


def abc_version() -> str:
    res = subprocess.run([abc_binary(), "-q", "version"], capture_output=True, text=True)
    return (res.stdout.strip().splitlines() or ["unknown"])[0]


def table_to_pla(table: np.ndarray, n_in: int, n_out: int) -> str:
    lines = [f".i {n_in}", f".o {n_out}", ".type fr"]
    for r, y in enumerate(table.tolist()):
        ins = "".join(str((r >> i) & 1) for i in range(n_in))
        outs = "".join(str((int(y) >> k) & 1) for k in range(n_out))
        lines.append(f"{ins} {outs}")
    lines.append(".e")
    return "\n".join(lines) + "\n"


def circuit_to_blif(circuit: Circuit) -> str:
    if any(not isinstance(c, Nand) for c in circuit.cells):
        raise ValueError("only NAND-only combinational circuits can be exported")
    name = {0: "c0", 1: "c1"}
    for i in range(circuit.n_inputs):
        name[2 + i] = f"x{i}"
    lines = [".model c3s", ".inputs " + " ".join(f"x{i}" for i in range(circuit.n_inputs))]
    lines.append(".outputs " + " ".join(f"y{k}" for k in range(circuit.n_outputs)))
    lines += [".names c0", ".names c1", "1"]
    s = circuit.first_cell_signal
    for c in circuit.cells:
        name[s] = f"n{s}"
        lines += [f".names {name[c.a]} {name[c.b]} n{s}", "0- 1", "-0 1"]
        s += 1
    for k, sig in enumerate(circuit.output_signals()):
        lines += [f".names {name[sig]} y{k}", "1 1"]
    lines.append(".end")
    return "\n".join(lines) + "\n"


def parse_mapped_blif(text: str, n_in: int, n_out: int) -> Circuit:
    """Parse ABC's mapped BLIF (gates from NAND_GENLIB plus trivial .names)."""
    text = re.sub(r"\\\n", " ", text)
    b = Builder(n_in)
    net: dict[str, int] = {f"x{i}": b.input(i) for i in range(n_in)}
    # ABC may name primary inputs as in the PLA (x0..) or by position; map by .inputs order.
    pending: list[tuple[str, list[str], str, list[str]]] = []
    inputs: list[str] = []
    outputs: list[str] = []
    lines = [ln.strip() for ln in text.splitlines()]
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith(".inputs"):
            inputs += ln.split()[1:]
        elif ln.startswith(".outputs"):
            outputs += ln.split()[1:]
        elif ln.startswith(".gate"):
            parts = ln.split()
            gate = parts[1]
            pins = dict(p.split("=") for p in parts[2:])
            pending.append(("gate", [gate], pins["Y"], [pins.get("A", ""), pins.get("B", "")]))
        elif ln.startswith(".names"):
            sigs = ln.split()[1:]
            cover = []
            while i + 1 < len(lines) and lines[i + 1] and not lines[i + 1].startswith("."):
                i += 1
                cover.append(lines[i])
            pending.append(("names", cover, sigs[-1], sigs[:-1]))
        i += 1
    net = {name: b.input(k) for k, name in enumerate(inputs)}

    remaining = pending
    while remaining:
        progress, nxt = False, []
        for kind, info, out, ins in remaining:
            if any(x and x not in net for x in ins):
                nxt.append((kind, info, out, ins))
                continue
            progress = True
            if kind == "gate":
                g = info[0]
                if g == "ZERO":
                    net[out] = b.ZERO
                elif g == "ONE":
                    net[out] = b.ONE
                elif g == "buf":
                    net[out] = net[ins[0]]
                elif g == "inv":
                    net[out] = b.not_(net[ins[0]])
                elif g == "nand2":
                    net[out] = b.nand(net[ins[0]], net[ins[1]])
                else:
                    raise ValueError(f"unexpected gate {g}")
            else:
                net[out] = _names_to_signal(b, info, [net[x] for x in ins])
        if not progress:
            raise ValueError("combinational loop or undefined net in BLIF")
        remaining = nxt
    if len(outputs) != n_out:
        raise ValueError(f"BLIF has {len(outputs)} outputs, expected {n_out}")
    return b.finish([net[o] for o in outputs])


def _names_to_signal(b: Builder, cover: list[str], ins: list[int]) -> int:
    """Only the trivial covers ABC emits after mapping: constants and buffers."""
    if not ins:
        return b.ONE if cover and cover[0].strip() == "1" else b.ZERO
    if len(ins) == 1 and len(cover) == 1:
        pattern, val = cover[0].split()
        if val != "1":
            raise ValueError(f"unsupported cover {cover}")
        return ins[0] if pattern == "1" else b.not_(ins[0])
    raise ValueError(f"unsupported .names cover with {len(ins)} inputs: {cover}")


def _run_abc(source_cmd: str, source_path: Path, n_in: int, n_out: int, recipe: str, work: Path) -> Circuit:
    lib = work / "nand.genlib"
    lib.write_text(NAND_GENLIB)
    out = work / f"out-{recipe}.blif"
    script = f"read_library {lib}; {source_cmd} {source_path}; {RECIPES[recipe]}; write_blif {out}"
    res = subprocess.run([abc_binary(), "-q", script], capture_output=True, text=True, timeout=1800)
    if res.returncode != 0 or not out.exists():
        raise RuntimeError(f"ABC failed ({recipe}): {res.stdout[-500:]} {res.stderr[-500:]}")
    return parse_mapped_blif(out.read_text(), n_in, n_out)


def _best(candidates: dict[str, Circuit], reference: np.ndarray) -> tuple[Circuit, dict]:
    report = {}
    best_name = None
    for name, c in candidates.items():
        got = exhaust.truth_table(c)
        bad = exhaust.first_mismatch(got, reference)
        report[name] = {"nand": c.metrics()["nand"], "depth": c.metrics()["depth"], "equivalent": bad is None}
        if bad is None and (best_name is None or c.metrics()["nand"] < candidates[best_name].metrics()["nand"]):
            best_name = name
    if best_name is None:
        raise RuntimeError(f"no ABC recipe produced an equivalent circuit: {report}")
    return candidates[best_name], {"selected": best_name, "recipes": report, "abc": abc_version()}


def synthesize_table(table: np.ndarray, n_in: int, n_out: int, recipes=None) -> tuple[Circuit, dict]:
    recipes = recipes or list(RECIPES)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        pla = work / "table.pla"
        pla.write_text(table_to_pla(table, n_in, n_out))
        cands = {}
        for r in recipes:
            try:
                cands[r] = _run_abc("read_pla", pla, n_in, n_out, r, work)
            except (RuntimeError, subprocess.TimeoutExpired) as e:  # recorded, not fatal
                cands_err = str(e)[:200]
                cands.setdefault("_errors", []).append(cands_err)  # type: ignore[arg-type]
        errors = cands.pop("_errors", [])
        circuit, report = _best(cands, table.astype(np.uint64))
        report["errors"] = errors
        return circuit, report


def optimize_circuit(circuit: Circuit, recipes=None) -> tuple[Circuit, dict]:
    recipes = recipes or list(RECIPES)
    reference = exhaust.truth_table(circuit)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        blif = work / "in.blif"
        blif.write_text(circuit_to_blif(circuit))
        cands = {"input": circuit}
        for r in recipes:
            cands[r] = _run_abc("read_blif", blif, circuit.n_inputs, circuit.n_outputs, r, work)
        return _best(cands, reference)
