# Patterns: a canon of things found in the escape reflex arc

This directory is a registry of *patterns*: a stimulus, the exact per-tick trace the
first circuit answers it with, and who found it, when. Nothing here changes the
circuit. The circuit is frozen; the patterns are what people discover about it.

Conway's Life has lasted since 1970 on four habits — the rules are public, discoveries
are attributed and dated, one file format makes any claim replayable, and checking a
claim is cheap. The verified-dump projects (Redump, No-Intro) add a fifth: a copy is
the thing only if its checksum matches. This registry copies all five.

> The 32-byte whole-domain digest is the reflex arc's name. A copy whose digest differs
> is honestly not this one. That is the only death it can have, and it is why every
> record names the digest it was found under.

## What a record is

One JSON file in `records/`, named after its `id`. The format is `c3s.pattern/1`,
described by [`pattern.schema.json`](pattern.schema.json). A record holds:

| field | what it is |
| --- | --- |
| `id` | `pNNN-short-name`, and the file name |
| `title`, `question` | what was asked, in plain words |
| `how_found` | the search, stated precisely enough to redo: the grid, the criterion, the tie-break |
| `discoverer`, `date` | attribution, ISO date |
| `circuit` | the circuit the record is about: `netlist_sha256` and `domain_chain_sha256` |
| `stimulus` | a looming disc: `l_over_v_ms`, `azimuth_deg` (`start_deg` 10, `end_deg` 170 are fixed by the firmware's geometry) |
| `result` | ticks, how it ended, the takeoff tick, which states it entered |
| `trace.rows` | one row per tick: `tick, sensory, policy, state, motor, next_state` |
| `trace.sha256` | SHA-256 of `rows` as canonical JSON (sorted keys, no whitespace) |
| `geometry` | angular size and speed at the first and last tick — informational, see below |
| `notes` | what the record shows, and anything it does not |

The trace columns are all integers:

- `sensory` — the 17 input bits: the 16-bit sensory word (`size_L`, `speed_L`, `size_R`,
  `speed_R`, four bits each) with bit 16, "standing", always set;
- `policy` — the LoomEscape-16 policy's two pathway bits: 1 = GF crosses, 2 = parallel
  crosses, 3 = both;
- `state`, `next_state` — the core's six latch bits: the raise counter in bits 0–2, the
  refractory timer in bits 3–5;
- `motor` — 0 hold, 1 raising wings, 2 short-mode takeoff, 3 long-mode takeoff.

An episode starts at the loom's onset with the state at reset, and ends either at the
first takeoff or when the disc reaches 170 degrees.

## The rules of the canon

1. **Attributed and dated.** A record names a discoverer and a date. "c3s-reflex
   maintainers" is the attribution for the seeds below; use your own name for yours.
2. **Replayable by anyone.** `scripts/pattern_replay.py` must reproduce the trace on
   both hosts — the Python engine and the WebAssembly build of the firmware's C — with
   no tolerance at all on the integer columns.
3. **Verified against the digest.** A record names the netlist SHA-256 and the
   whole-domain digest of the circuit it is about. If either does not match the
   committed circuit, the record is about something else and the replay says so.
4. **Derived, not typed.** A record is produced by `pattern_replay.py --emit` from a
   stub holding only its metadata and its stimulus, so `--canonical` can re-derive the
   whole file and compare it byte for byte. Hand-edited traces are not accepted.
5. **The search is stated.** `how_found` gives the grid and the criterion. "The smallest
   l/v that does X" means nothing without the grid it was smallest on.
6. **No invented facts.** Every number in a record comes out of this repository. The
   circuit is a model of one reflex arc, not a claim about a fly; see
   [`docs/LIMITATIONS.md`](../docs/LIMITATIONS.md).

### Why the geometry is not in the checksum

Each host computes the looming geometry with its own `atan` and `tan`: the device's
newlib, the browser's `Math`, CPython's libm. They agree to about 1e-12 relative, not
to the last bit, so the angular columns are recorded for the first and last tick only,
marked informational, and compared with a tolerance. The integer columns — everything
the circuit actually sees and does — are exact on every host, and only those are
hashed.

## Checking a record

```sh
scripts/pattern_replay.py --all                  # every record, both hosts
scripts/pattern_replay.py --all --canonical      # also re-derive each record
scripts/pattern_replay.py --all --verify-digest  # also digest all 2**23 rows on wasm
scripts/pattern_replay.py patterns/records/p001-smallest-long-mode.json
```

Exit status is 0 only if every record agreed on every tick on both hosts.
`tests/test_patterns.py` runs the same checks under pytest.

## Adding one

1. Find something. The hunt is the point: a stimulus class nobody has recorded, an edge
   in the encoding, a state nothing reaches, a boundary that is not where it should be.
2. Write a stub — `id`, `title`, `question`, `how_found`, `discoverer`, `date`, optional
   `notes`, and `stimulus` with `l_over_v_ms` and `azimuth_deg`.
3. `scripts/pattern_replay.py --emit stub.json --out patterns/records/pNNN-name.json`
4. `scripts/pattern_replay.py --all --canonical` and paste the output in your pull
   request. There is no index to update: the file is the entry.

Reproducing someone else's record on your own hardware is as welcome as a new one. The
Cardputer prints its own whole-domain digest over serial
(`digest <hex> over <rows> rows in <ms> ms`); the simulator's "One fingerprint, many
hosts" panel compares it with the browser's and with the committed EVM fixture.

## The five seeds

All five were found on 2026-09-16 by the c3s-reflex maintainers, under core digest
`0x477aee38…`, and all five replay identically on Python and on WebAssembly.

| id | stimulus | ticks | ends | what it shows |
| --- | --- | --- | --- | --- |
| [`p001-smallest-long-mode`](records/p001-smallest-long-mode.json) | l/v 8.8 ms, az 31° | 16 | long-mode takeoff | The smallest l/v on a 0.1 ms grid that still takes the slow escape. The boundary is not monotonic: 8.9–9.2 ms take the fast one. |
| [`p002-one-ulp-past-the-overlap`](records/p002-one-ulp-past-the-overlap.json) | l/v 20 ms, az 30.000000000000004° | 38 | long-mode takeoff | One double past the 30° edge of the binocular overlap the right eye is alone: every sensory word changes, and the takeoff moves four ticks later. |
| [`p003-twice-to-full-raise`](records/p003-twice-to-full-raise.json) | l/v 122.5 ms, az 31° | 271 | long-mode takeoff | The raise counter saturates, falls back, and saturates again — 1,931 of 108,419 episodes do that, and none does it three times. |
| [`p004-takeoff-on-the-first-tick`](records/p004-takeoff-on-the-first-tick.json) | l/v 0.8 ms, az 0° | 1 | short-mode takeoff | The earliest takeoff there can be, on the loom's onset tick; 0.8 ms is the slowest approach that manages it. |
| [`p005-raises-but-never-takes-off`](records/p005-raises-but-never-takes-off.json) | l/v 156 ms, az 31° | 354 | no takeoff | Four raising commands, the counter reaching three, and no escape. The mirror stimulus at −31° does escape. |

Two things the seeds taught us, both open to being overturned by the next record:

- **Four of the five sit at or just past 31 degrees.** That is the first degree outside
  the modelled 30-degree binocular overlap, where only the right eye sees. In MaleCNS
  v1.0 the right giant fiber receives fewer LC4 and LPLC2 synapses than the left, so
  the right eye alone is the weakest drive the model has — and the weakest drive is
  where the interesting boundaries are: it is slow enough to spend ticks raising wings
  instead of taking off at once. The mirror azimuth, seen by the stronger left eye,
  behaves differently in `p001`, `p003` and `p005`.
- **Seven of the twelve reachable states never appear in any record.**
  `c3s.reach.reachable_states` finds 12 of the 64 states reachable from reset: 0, 1, 2,
  3, 4, 8, 16, 24, 32, 40, 48, 56. An episode stops at its first takeoff, so it can only
  ever enter the first five; the other seven are the refractory timer counting down, and
  they appear only as the `next_state` of the very last row. A record format that keeps
  running after a takeoff would reach them, and the firmware's own `run` command stops,
  so that would need a new command on every host — an open question, not a defect.

## Open questions

Unclaimed, in the spirit of Conway's $50 for a growing pattern. Anyone may take one;
say so in your record's `question`.

1. Is there a stimulus whose episode enters the saturated raise counter three times? The
   grid in `p003` says no, on that grid.
2. What is the largest l/v that escapes at all, and does the boundary have holes in it
   the way `p001`'s does?
3. Over the whole 2-D stimulus space, how many distinct traces are there? The sensory
   encoding has 65,536 words and an episode is a path through them; the number of
   reachable *paths* is not known here.
4. Is there a pair of stimuli, differing by one ulp in azimuth or in l/v, whose takeoff
   ticks differ by more than the four of `p002`?
5. Which single NAND gate of the 173, held at a constant value, changes the fewest
   records? `c3s.reach.undetectable_faults` answers a related question on the domain;
   nobody has asked it of the canon.
