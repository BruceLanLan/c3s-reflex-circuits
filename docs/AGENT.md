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

Compiled rules inherit the same limits as everything else here. They bound **what the
circuit grants**: how often, in what order, under which flag. They say nothing about
what an agent does with a grant, and they measure time in ticks that advance only when
something drives them.

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

A short way to say it: the circuit can bound *how often* and *in what order* an agent
acts, and can be checked to do so. It cannot make an agent honest, and this
repository does not say it can.

## Running it

```sh
python scripts/run_agent_demo.py                     # offline, built-in model
python scripts/run_agent_demo.py --model-url <url>   # your model proposes
cd contracts && forge test --match-contract ReflexGuardTest
```
