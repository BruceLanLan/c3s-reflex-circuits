# Changelog

Dates are the day the work landed. Every number below was measured on the machine that
wrote the entry, not estimated.

## Unreleased — the console arrives (2026-09-17)

The repository was the circuits and their proofs. It is now the circuits **and the thing
you run**: a local console that puts those circuits between your own model and the actions
it can take. One clone is the whole product.

### The console (`console/`)

- **Boundaries per class of tool.** Rules for `spend`, `message`, `exec`, `files` and a
  shared `halt` compile to NAND/LATCH circuits, checked on every row of the domain and
  proven over every reachable state before they install. A class set to deny-all compiles
  to no circuit at all, because there is no path to a grant to check.
- **Your own model, through an adapter.** A Claude Code PreToolUse/PostToolUse hook, an
  MCP proxy in front of one server, and a BNB Agent SDK wallet wrapper that signs nothing
  the `spend` circuit did not grant.
- **An approval is spent by the one call a person read.** Each waiting item carries two
  digits; approving requires them, so an approval cannot slide onto a different call —
  including the same tool with a reworded argument, which a recorded run showed a model
  doing.
- **Three places to hold the keys**: the page, a Cardputer on the desk (USB, or Wi-Fi
  after a pairing where the device shows four digits), and a Telegram chat with buttons.
  A device's token is strictly weaker than the cable: it may approve and block, never
  unblock, never fake a heartbeat, never stop or resume everything.
- **`c3s demo`** — a pretend mailbox, calendar and folder, one chore, every call decided,
  nothing leaving the machine. Includes a phishing message the boundary refuses even when
  the model is told plainly to obey it.
- **Tasks as a ledger.** A person files a job and every decision is read along that
  thread. The runner that would execute a job is not built, and the API says so.

### Circuits

- **`trip_after_refusals` ships, and its reset is now proven.** *n* refusals in a row halt
  the agent until a confirm resets it. The rule was found to be a one-way door: while
  tripped every tick is a refusal, so the tick carrying the confirm re-tripped the breaker
  and no confirm ever lifted it. The proof had not caught it, because the monitor asserted
  only the safety half — *nothing is granted while tripped* — which holds forever and
  vacuously. Fixed in one term, and the missing half is now `violations.refusal_reset`,
  checked from every reachable tripped state.
- **A budget bounds an identity, and `docs/AGENT.md` now says whose.** `min_gap_ticks` and
  `max_grants` cannot be satisfied by anything the agent writes, but what they bound is one
  identity's grants. Who may mint an identity is the deployment's question.

### On chain

- **`ReflexModule` proven against a real Safe.** Safe v1.4.1 as it exists in BNB Smart
  Chain's state, on an anvil fork pinned to a block: a granted transfer leaves the Safe, a
  refused one moves nothing and still spends the tick, and four attempts after the cooldown
  expires move nothing — so it is the missing confirmation doing the work, not the clock.
  The two ways around a module on 1.4.1 are closed, with the reverts observed rather than
  assumed. **A local fork; nothing broadcast, and this project holds no key.**
- **A hole the mocks could not show.** `act` keyed its counters by `msg.sender` with no
  allowlist, so every address was a fresh agent granted on its first tick — which makes a
  budget per-address. A real transaction on the fork asserted it open; a supervisor-written
  `agents` map closed it.

### Security

- **The stop button stops what it has never met.** It was a list of the names the console
  knew, and an agent that renamed itself was measured being granted four ticks later; worse,
  a class with no circuit installed ignored it entirely while the page said otherwise. It is
  now a latch checked before any circuit, for every agent and every class, and it survives a
  restart. The cost: a session started while it is down is refused until a person resumes.
- **A phone gets its own token.** The page used to send the operator token on every
  two-second poll, in cleartext, for as long as it was open. `c3s pair` now mints a
  read-scoped viewer token and the operator token never leaves the machine. A phone paired
  before this change has to pair again.
- **An agent's token no longer reads the whole console.** Over the LAN a bound agent's own
  token returned every other agent's pending call, which is the one string
  `console/docs/ISOLATION.md` argues a contained agent cannot obtain.
- Three adversarial passes are written up in `console/SECURITY.md`, including what could
  not be broken, and the findings report in `console/docs/REDTEAM-2026-09-17.md`.

### Also

- Apache-2.0 `LICENSE` added to the console, which declared it and shipped no file.
- A calendar write is a message and cannot be taken back: `create_event`, `update_event`,
  `cancel_event` and `respond_to_event` were classed as ordinary commands and reversible.
  Overwriting a file is irreversible too.
- `GET /api/manifest` answers instead of dying when the manifest builder is missing.

## v0.2.0 — 2026-09-15

Firmware self-test passing on real hardware, and the WebAssembly simulation at
`docs/sim/`. See the [release notes](https://github.com/BruceLanLan/c3s-reflex-circuits/releases/tag/v0.2.0).

## v0.1.0

The connectome-constrained synthesis: MaleCNS v1.0 to a 173-NAND netlist, checked on all
8,388,608 rows, with five temporal properties proven two ways.
