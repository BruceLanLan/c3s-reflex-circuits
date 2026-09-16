# A verified circuit as an agent's boundary

An agent that can act needs something to say *no*. This repository has an unusual
candidate: a 173-NAND reflex whose behaviour is checked on every one of its
8,388,608 rows and five of whose temporal properties are proven twice over. Two
pieces here put that circuit in front of an agent's actions — one off chain, one on.

Nothing in either is deployed, and neither holds a key.

## The arrangement

```
your model  ──proposes──▶  the encoder  ──17 bits──▶  the escape core  ──▶  authorised or not
 (untrusted)                (this repo)                (verified)
```

The model may only propose a looming stimulus and say why. It cannot authorise
anything, cannot reach the latches, and cannot widen the input: the proposal is
validated as numbers in range, encoded by the repository's own encoder, and fed to
the circuit tick by tick. An action counts as authorised only on a tick where the
circuit itself commands a takeoff.

* `scripts/run_agent_demo.py` — the loop above, offline, with a built-in stand-in
  model so it runs with no network and no keys. Point `--model-url` at your own model
  to put it in the loop.
* `contracts/src/ReflexGuard.sol` — the same verdict on chain, one call per tick,
  with the caller's latch state kept per address. It evaluates through the same
  `NandMachine` and the same netlist bytes, and the constructor checks their SHA-256.
* `contracts/src/ReflexModule.sol` — a compiled policy as a Safe module: the agent acts
  through a human-owned Safe only on ticks the circuit grants, and writes none of the
  bits it reads. [ONCHAIN-SELF-DEPLOY.md](ONCHAIN-SELF-DEPLOY.md) is the self-deploy flow.
* `scripts/boundary_manifest.py` / `scripts/validate_boundary.py` — a compiled policy as
  an ERC-8004 boundary manifest anyone can re-check and score; see [ERC8004.md](ERC8004.md).

## Rules you write, compiled and proven

The three properties above are not only facts about a fly. They are the shapes of the
boundaries someone actually wants around an agent — a rate limit, a commitment cost, a
forbidding condition — which is why `c3s/policy.py` makes them writable:

```python
from c3s.policy import Policy

policy = Policy(min_gap_ticks=8, commit_ticks=4, forbid_when_blocked=True,
                max_grants=3, confirm_window_ticks=8)
circuit = policy.build()        # 4 inputs, 1 output; this one is 241 NAND + 18 LATCH
policy.verify(circuit)          # every row of its domain equals the Python reference
policy.properties(circuit)      # every rule, in every state reachable from reset
```

### Which input a rule rests on decides what it is worth

This matters more than the gate count, and it is the thing most easily got wrong.
`request` and `intent` are written by the agent. A rule that rests on them alone —
`commit_ticks` — is a **cost the agent can always choose to pay**: four ticks of
"intent" is four ticks it simply spends. `blocked` and `confirm` must come from a
layer the agent cannot write (the tool runner, a human, a signed feed); rules resting
on those are **boundaries**. `min_gap_ticks` and `max_grants` bind whatever the agent
does, because they count grants rather than inputs.

Nothing in the module can enforce that separation — it is a property of how the thing
is wired up. So it is named in the code (`AGENT_WRITABLE`,
`MUST_COME_FROM_THE_TOOL_LAYER`), asserted in the tests, and written here: a
deployment that lets the model produce its own `confirm` has a commitment cost
dressed up as a boundary.

A `Policy` compiles to a NAND/LATCH circuit with no hidden state. Two things are then
checked, and they are different things:

* **Equality**: the circuit and a plain-Python statement of the same rules agree on
  every row of the domain — 128 rows for a confirmation window alone (36 NAND +
  3 LATCH), 4,194,304 for the five-rule policy above with a 60-tick cooldown and a
  15-grant budget (241 NAND + 18 LATCH). The domain stays small on purpose: exhaustive
  checking needs `2^(inputs + state)` rows, and measured here a 200-gate circuit at 24
  bits costs about a second and a gigabyte, four times that at 26.
* **The rules themselves**: a search from reset visits every state the circuit can
  reach under every input, with monitors that count independently of the circuit's own
  latches, and no rule is ever broken. Claiming a rule the circuit does not enforce —
  a stricter gap, a longer commitment, a smaller budget — is reported as violations, so
  the check cannot pass by being vacuous; `tests/test_policy.py` includes those four
  controls.

### Four more rules, each resting on the tool layer

The five above generalise a measured circuit. The next four are the shapes that recur
in the tools people actually put around agents — kill switches, approval inboxes,
retry limits, two-person rules — and each reads an input the agent cannot write, so
each is a boundary rather than a cost. Measured alone, with `forbid_when_blocked` off:

* **A halt that stays down.** `sticky_block=True`: a tick with `blocked` high halts the
  agent, and the halt holds until a later tick with `confirm` high; that tick grants
  nothing either, the next one may. `heartbeat_ticks=H` halts the same way after `H`
  consecutive ticks without `heartbeat`. Today's `forbid_when_blocked` lifts the
  instant the flag drops; this one needs a person. 11 NAND + 1 LATCH over 32 rows for
  the sticky block; 51 NAND + 4 LATCH over 512 rows for an 8-tick heartbeat.
* **A confirm that is spent.** `confirm_per_irreversible=True`: a tick with
  `irreversible` high is granted only if a `confirm` has arrived since the last
  irreversible grant (or arrives now), and the grant spends it. Reversible grants
  neither need nor spend it. A `confirm_window` authorises many grants per confirm,
  which is the wrong shape for money or deletion. 15 NAND + 1 LATCH over 64 rows.
* **A breaker.** `trip_after_failures=k`: `k` consecutive ticks with `failed` high
  trip it, the tripping tick included; nothing is granted until a `confirm` resets it.
  A success in between resets the count. 41 NAND + 3 LATCH over 256 rows for `k=3`.
* **Two keys.** `two_key=True`: every grant needs both `confirm` and `confirm_b` to
  have arrived since the last grant (or to arrive now), and the grant spends both.
  16 NAND + 2 LATCH over 128 rows.
* **A halt after refusals.** `trip_after_refusals=R`: `R` refused requests in a row halt
  the agent until a `confirm` resets it. This is the one rule that reads the circuit's own
  verdict rather than an input — an agent that keeps asking for what it cannot have is
  stopped rather than left hammering the boundary — and it needs no new input at all: the
  refusal is `request` with no `grant`. A grant or a confirm clears the run; a tick with no
  request is not a refusal and leaves the count alone. `grant` reaches only the next state,
  never a condition of the same tick, so nothing here is circular. 51 NAND + 3 LATCH over
  128 rows for `R=3`.

  Its control needed care, and the care is the lesson: claiming a stricter `R` than the
  circuit enforces is only caught where the circuit would still *grant* — so the control
  runs with `forbid_when_blocked` on, where a tick when `blocked` drops is grantable. State
  a stricter claim against a policy that refuses for ever (a spent budget) and it passes
  vacuously, which is exactly the shape of a proof that proves nothing.

The four inputs these read — `irreversible`, `failed`, `heartbeat`, `confirm_b` — are
appended to the circuit only when the rule that reads them is on, in that order, so
every policy written with the original five rules compiles to exactly the bytes it did
before (`tests/test_policy.py` checks the published 81 NAND + 6 LATCH figure by hash).
Each rule has its own independent monitor in `properties`, and its own control: a
sticky block claimed of a stateless one, a shorter heartbeat, a spent confirm claimed of
a window, a breaker at two claimed of one built at three, two keys claimed of none —
all five are reported as violations.

### One policy per tool class

Rules for different kinds of action should be compiled as separate policies, not one:
a *spend* policy (`min_gap_ticks=8, max_grants=7, confirm_per_irreversible=True,
two_key=True` — 108 NAND + 9 LATCH, 32,768 rows), an *exec* policy (`min_gap_ticks=4,
commit_ticks=4, confirm_per_irreversible=True, trip_after_failures=3` — 123 NAND +
9 LATCH, 32,768 rows), and a shared *halt* (`sticky_block=True, heartbeat_ticks=8` —
56 NAND + 4 LATCH, 512 rows) whose verdict feeds each class's `blocked`. Rows then add
rather than multiply — 2^15 + 2^15 + 2^9 instead of 2^28 — and every check stays
exhaustive. The honest cost: an invariant that spans classes ("no message within three
ticks of a payment", "at most twenty grants across everything") is not verified by
anything here unless the product of the machines is built and checked as one.

Compiled rules inherit the same limits as everything else here. They bound **what the
circuit grants**: how often, in what order, under which flag. They say nothing about
what an agent does with a grant, and they measure time in ticks that advance only when
something drives them — an agent that decides when a tick happens has defeated every
temporal rule above, so ticks, like `blocked`, must be the tool layer's to drive.

## What the proofs give it

For **any** proposals whatsoever, because the properties hold on every reachable
state ([PROPERTIES](PROPERTIES.md)):

| | Property | What it means for the agent |
| --- | --- | --- |
| **P1** | no takeoff within 7 ticks of a takeoff | at most one authorisation in any 8 consecutive ticks — a rate limit no argument can talk past |
| **P2** | a long-mode takeoff follows 4 consecutive raising ticks | the costly action costs four ticks of commitment first |
| **P3** | not standing, or refractory ⇒ hold | there are states in which nothing is authorised at all |

`forge test` re-checks P1's consequence on chain with a 256-run fuzz over arbitrary
input sequences: no two authorisations within 8 calls by one caller.

## What the proofs do not give it

This is the part that matters, because borrowed credibility is the obvious failure
mode of putting a verified circuit anywhere near an agent.

* **No binding to time.** The limit is in ticks, and eight calls fit inside one
  transaction. `ReflexGuard`'s optional `minBlocks` spacing is ordinary code with
  ordinary tests, not a proven property.
* **No meaning for the inputs.** The caller builds the 17 bits. A caller that wants a
  long-mode authorisation can simply supply four raising ticks, so P2 is a commitment
  *cost*, not a security property. The circuit constrains the *shape* of a sequence,
  never the honesty of whoever writes it.
* **No control of the actuator.** Authorisation is a verdict. What happens next is
  outside the circuit, outside the guard, and outside every proof here.
* **No claim about the model.** Whatever proposes is untrusted, and the demo treats a
  malformed proposal as a lost turn rather than an error to recover from.

### What no rule of this kind can express

The compiled policies are finite-state monitors that can only withhold a grant. That
class is well understood, and its limits are not ours to negotiate away:

* **Only safety.** A monitor that observes and withholds enforces exactly the
  properties whose violation shows up in a finite prefix — "never two grants within
  eight ticks", "never a grant while halted". It cannot enforce that anything *does*
  happen: "the task eventually completes", "every request is eventually granted" are
  not even decidable from a finite prefix, let alone enforceable by refusing.
* **Suppression, not repair.** The circuit withholds `grant`. It cannot undo a granted
  action, insert a compensating one, or roll anything back; "if X happened, Y must
  also happen" is out of reach.
* **Flags, not values.** Amounts, timestamps, addresses and strings do not fit in a
  few bits. Whatever reduces "this transfer exceeds the limit" to `blocked = 1` is
  outside the circuit and has to be trusted on its own account.
* **Logic, not judgement.** Every proof here is of the form *given these bits, this
  verdict*. Whether `irreversible` was set on the right calls, whether `failed` was
  reported honestly, whether `heartbeat` came from a live process — those are promises
  by the tool layer, and the circuit cannot tell a kept one from a broken one.
* **Per class, not across.** With one policy per tool class, nothing checks a rule
  that mentions two classes at once.

A short way to say it: the circuit can bound *how often* and *in what order* an agent
acts, and can be checked to do so. It cannot make an agent honest, and this
repository does not say it can.

## Running it

```sh
python scripts/run_agent_demo.py                     # offline, built-in model
python scripts/run_agent_demo.py --model-url <url>   # your model proposes
cd contracts && forge test --match-contract ReflexGuardTest
```
