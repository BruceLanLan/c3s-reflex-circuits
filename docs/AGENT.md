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
