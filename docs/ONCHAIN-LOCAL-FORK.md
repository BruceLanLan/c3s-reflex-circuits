# The reflex arc on chain, proven on a local fork

**This is a local fork. Nothing was broadcast to any network. (本地分叉、未广播)**

Everything on this page ran inside an [anvil](https://getfoundry.sh) process forked from a
public BNB Smart Chain endpoint: a copy of BSC's state at one block, mined locally, thrown
away when the process exits. No testnet, no mainnet, no deployment, no wallet, no private
key, no mnemonic. The signing accounts are anvil's own development accounts, referred to by
address, and `--unlocked` means Foundry asks anvil to sign rather than holding a key. If you
are looking for a deployed address, there isn't one, and there is not meant to be: the
module is the reader's to deploy ([ONCHAIN-SELF-DEPLOY.md](ONCHAIN-SELF-DEPLOY.md)).

## One command

```sh
scripts/onchain_local_fork.sh
```

It needs Foundry and a `python3`; it needs no key and no account. `PORT=8547` and
`FORK_BLOCK=122240000` are the defaults, `FORK_RPC=…` picks an upstream endpoint, and anvil
writes to `$TMPDIR/c3s-reflex-anvil.log`. The script tries four public BSC endpoints in order and takes
the first that answers, then asks that endpoint whether it still serves state at
`FORK_BLOCK`: an archive does, and the fork is pinned there and so reproducible; a pruned
one does not, and the script says so and forks latest instead rather than dying at the
first state read. anvil is killed on the way out.

The parts, if you would rather run them yourself:

```sh
anvil --fork-url https://bsc-mainnet.public.blastapi.io --fork-block-number 122240000 --port 8547
cd contracts && forge script script/LocalForkProof.s.sol --rpc-url http://127.0.0.1:8547 \
  --broadcast --unlocked --sender 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266   # NETLIST… in the env
FORK_URL=http://127.0.0.1:8547 forge test --match-path 'test/ReflexModuleFork.t.sol'
python scripts/fork_agreement.py --rpc http://127.0.0.1:8547 --module <module>
```

Plain `forge test` does not need any of this: the fork suite skips itself when `FORK_URL` is
unset, and the 32 mock-based tests still run offline.

## Why a fork, when there were already 32 tests

The existing tests put the module in front of a `MockSafe` — a contract in the test file
that behaves the way we believe a Safe behaves. They prove what the module does. They cannot
prove anything about the Safe, and the interesting question is not what the module does with
a grant; it is whether there is a way *around* it into the same Safe.

On a fork of BSC, Safe's v1.4.1 contracts are already there, at Safe's own deterministic
addresses, with Safe's own bytecode:

| what | address | used for |
| --- | --- | --- |
| Safe singleton | `0x41675C099F32341bf84BFc5382aF534df5C7461a` | the Safe every proxy delegatecalls into |
| SafeProxyFactory | `0x4e1DCf7AD4e460CfD30791CCC4F9c8a4f820ec67` | creates the Safe used here |
| CompatibilityFallbackHandler | `0xfd0732Dc9E303f09fCEf3a7388Ad10A83459Ec99` | the Safe's fallback handler |
| MultiSend | `0x38869bf66a61cF6bDB996A6aE40D5853Fd43B526` | delegatecall batching — tried as a way around |
| MultiSendCallOnly | `0x9641d764fc13c8B624c04430C7356C1C7C8102e2` | plain-call batching — tried as a way around |

So the Safe that forwards, and the Safe that refuses, is the real one. The arrangement the
script builds is one 1-of-1 Safe owned by anvil account 0, funded with 10 BNB by an ordinary
transaction, with `NandMachine` and one `ReflexModule` deployed over the compiled policy

```
Policy(min_gap_ticks=4, confirm_per_irreversible=True, forbid_when_blocked=True)
```

and the module enabled by an owner-sent `execTransaction`. The agent is anvil account 1,
named by the supervisor: not an owner, never an owner, holding nothing but the ability to
call `act`.

## What is proven

Each of these is an assertion in `contracts/script/LocalForkProof.s.sol` (as transactions),
in `contracts/test/ReflexModuleFork.t.sol` (14 tests), or in `scripts/onchain_local_fork.sh`
(read back off the chain with `cast` afterwards) — usually in two of the three.

1. **A granted transfer goes through.** A person leaves a confirmation bound to
   `keccak256(abi.encode(safe, agent, to, value, keccak256(data)))`; the agent calls `act`;
   1 BNB leaves the real Safe and arrives. `Forwarded(agent, true)`.
2. **A refused transfer moves nothing.** The same call without a confirmation:
   `granted = false`, the Safe is never asked, both balances unchanged, and the tick is
   spent. See "refusal is not a revert" below — this is the one place this work does
   something other than what was asked for.
3. **No replay.** The grant spent the confirmation, and the agent cannot put it back. The
   script tries the same transfer four more times; the fork tests wait out the whole
   cooldown first, so that the refusal is the missing confirmation and not the cooldown, and
   then try again. The Safe paid exactly once. `scripts/fork_agreement.py` prints the reason
   the policy itself gives for that tick: *irreversible, and no unspent confirm*, with no
   cooldown left to hide behind.
4. **No `delegatecall`, ever.** `act` has no operation parameter; the module passes
   `Operation.Call` as a literal. The proof is not the source line but a target,
   `SlotWriter`, whose only statement writes storage slot 0 — which, reached by
   `delegatecall` from a Safe, would overwrite that Safe's singleton pointer and brick it.
   After a granted tick the Safe's slot 0 is unchanged, the writer's own slot 0 holds the
   value, and the Safe still answers `VERSION()`.
5. **No second path to a Safe-authorised function.** The Safe and the module are not legal
   targets, so the obvious move is an intermediary. MultiSendCallOnly will batch
   `addOwnerWithThreshold(agent, 1)` and `enableModule(stranger)` happily — and makes those
   calls from *its own* address, which to the Safe is neither an owner nor a module — its
   self-authorisation check answers `GS031`, observed on the fork with `cast call
   "addOwnerWithThreshold(address,uint256)" --from <MultiSendCallOnly>`.
   The circuit grants the tick; the Safe refuses the content; the module records
   `lastFailed`. The delegatecall variant of MultiSend refuses the plain call itself
   (*MultiSend should only be called via delegatecall*).
6. **The other doors are shut.** The agent signing its own Safe transaction: `GS026`, it is
   not an owner. The agent or a third party calling `execTransactionFromModule` directly:
   `GS104`, it is not a module.
7. **No two grants out of one.** A target that calls back into `act` while the module is
   forwarding for it gets `Reentrant()`, and the tick counter moves by one, not two.
8. **A guard is not a boundary on this path, and the module is.** Safe v1.4.1's bytecode has
   no `checkModuleTransaction` — the module-guard hook Safe added later — so a guard does
   not see a module's calls. Proven twice: by the absence of that selector in the forked
   code, and by installing a guard that reverts on everything, watching a granted tick
   forward anyway, and watching the owners' own `execTransaction` die.
9. **The owner key is the real boundary.** After `disableModule` the circuit still grants
   and the Safe still says no: `Forwarded(agent, false)`, nothing moves.
10. **The chain and this repository decide the same thing.** No verdict travels. Both sides
    hold the same 299-byte netlist and each evaluates it: `netlist()` and `netlistSha256()`
    read off the deployed module equal the export of `c3s/policy.py` here, and every
    `Decision` the module emitted, replayed tick by tick through `c3s.netlist.tick` and
    through `Policy.reference` (an independent implementation that counts rather than
    simulates gates), reaches the same grant, ending on the same latch state as
    `stateOf(agent)` on chain. Three evaluators, one verdict each.

### Where the boundary's verdict actually comes from

It is worth being exact, because "the module needs to know the boundary's verdict" invites a
design that does not exist here and should not. There is no signed verdict, no oracle, no
message from the console to the chain. The console
(the reflex console's `adapters/bnbagent_boundary.py`) refuses a *signature* in software by
running the circuit itself; the module refuses a *transaction* on chain by running the
same circuit itself, over bits it derives from the call and from storage only the supervisor
can write. The shared artefact is the netlist and its SHA-256, and that is the whole link —
which is why `scripts/fork_agreement.py` checks the hash first and the verdicts second.

This is the same check the console already makes in the other direction: `Chain.evaluate` in
its `console.py` re-runs one decisive row on a public node through an `eth_call` state
override, with nothing deployed, and records whether the chain agrees. That endpoint is the
console's `BSC_RPC`, so `BSC_RPC=http://127.0.0.1:8547 VERIFY_ON_CHAIN=1` makes its second
opinion come from this fork instead of a public node. The difference
is only where the evaluator lives: bytecode installed for the length of one call, or a
deployed module holding the netlist in its own code.

## What is not proven

* **Nothing about a real network.** Not gas prices, not reorgs, not a real Safe UI, not a
  validator. A fork is BSC's state, not BSC's traffic.
* **Nothing new about the circuit.** The properties (`docs/PROPERTIES.md`) were already
  proven exhaustively; this only shows the deployed module reaches the same verdicts. If the
  netlist is wrong, everything here agrees with it perfectly.
* **`lastFailed` on the multisend attempts is recorded, not acted on.** The policy used here
  reads no `failed` input, so a failing forward costs the agent a tick and nothing else.
* **The selector list is still fail-open.** The script lists `transfer` and `approve`; every
  other selector counts as reversible, and a value-moving call through an unlisted selector
  (a router `swap`, for one) is ticked without `irreversible`. That is the supervisor's
  judgement, exactly as `ONCHAIN-SELF-DEPLOY.md` says, and the fork cannot improve it.
* **Ticks are still calls, not time.** Four ticks fit in one block.

## Refusal is not a revert

The brief for this work asked for a refused transfer that "reverts on chain, with the
module's own reason". One of the two refusals here does exactly that, and the other
deliberately does not; the difference is worth the paragraph.

**A structural refusal reverts, with the module's own error.** `act(safe, addOwner(agent))`
reverts `ForbiddenTarget(0x…)`, on chain, in the module's own custom error — a module's call
to its own Safe is an authenticated self-call, and one granted `addOwnerWithThreshold` would
end the arrangement. `act(module, …)` and re-entry (`Reentrant()`) are the same kind. These
spend no tick, because nothing was decided.

**A circuit refusal does not revert.** `act` returns `granted = false`, emits
`Decision(..., granted: false, ...)`, spends the tick, and leaves the Safe untouched. It
would have been easy to add an `actStrict` that reverts instead, and it would have been
wrong twice over:

* A revert refunds the tick. The latches would not advance, so the cooldown would never
  run down and — for a policy compiled with `trip_after_refusals` — the refusal counter
  could never trip. A rule that counts refusals cannot be enforced by a call that costs
  nothing to be refused.
* Given both entry points, an agent would always use the reverting one, because it is the
  strictly cheaper way to probe. The boundary would be enforced only against an agent that
  volunteered to be charged.

So the assertion for a refused transfer is not a revert string. It is four facts about the
chain after the transaction: `granted == false`, the Safe's balance unchanged, the
recipient's balance unchanged, and the tick spent. A caller that wants an error rather than
a `false` should read the return value, or `peek` first — which is free and changes nothing.

## The route around it

Two things were found while looking for the way around this. Both are closed now; the
first was wide open when this fork run began, and closing it is a separate commit.

### Any address was a fresh agent — found here, then closed

As the module stood when this fork run began, `act` recorded everything under `msg.sender`
and never asked who the agent was. There was no allowlist. So on a module that had just been
enabled, an address that appeared nowhere in the deployment — never registered, never
funded, never mentioned — arrived with empty latches: `request` and `intent` high because it
called, `blocked` low, `irreversible` low for a zero-value call whose selector is not on the
supervisor's list, and no earlier grant to cool down from. It was **granted on its first
tick**, and the Safe performed the call. Anvil account 9, in a real transaction, did exactly
that.

The consequence was not that a stranger got one call. It was that *every tick-counting rule
was per address*: "at most one grant in any four ticks" bounded one address, ten addresses
were ten budgets, and addresses are free. [AGENT.md](AGENT.md) calls `min_gap_ticks` and
`max_grants` **boundaries**, "because they count grants rather than inputs" — with no list
of agents that claim was false on chain, and they were costs.

So the module now asks. The supervisor names agents; an unnamed caller gets the module's own
`NotAgent(address)` and spends no tick; a module nobody has been named on grants nothing to
anybody, which is the safe direction to fail in. `setAgent(x, false)` is a kill switch for
one agent, finer than the owners' `disableModule`.

```solidity
mapping(address => bool) public agents;          // written by the supervisor
error NotAgent(address who);

// in act(), after the re-entry check, so a re-entrant call still reads Reentrant:
if (!agents[msg.sender]) revert NotAgent(msg.sender);
```

`test_fork_anUnnamedCallerIsNotAnAgent` asserts the refusal, that no tick is spent, that
naming the same address makes it an agent with its own latches, and that un-naming it stops
that agent alone. `ONCHAIN-SELF-DEPLOY.md` gained the step, before `enableModule` rather
than after it, and `DeployReflexModule.s.sol` an optional `AGENT`. The cost is one cold
`SLOAD` per tick: a refused tick went from ~121k to ~123k gas, a granted one from ~209k to
~211k.

What held even without the list, and is still asserted: value and listed selectors need a
confirmation, and the confirmation key names the agent, so one agent cannot spend
another's. The exposure was the reversible, unlisted calls — which, given a fail-open
selector list, is most calls.

### Delegatecall around the module — closed

The interesting version of this is not MultiSend (covered above) but
`simulateAndRevert`/`simulate`, the one place a Safe will `delegatecall` code it is handed.
Reaching it needs a call *to the Safe*, and `to == safe` reverts `ForbiddenTarget` before a
tick is spent; aimed at the fallback handler instead it runs in the handler's own context,
where there is no Safe to change.
`test_fork_theDelegatecallPathNeedsTheSafeAsATargetAndCannotHaveIt` asserts both halves: the
Safe's slot 0, its owner set and its `VERSION()` all survive a granted tick spent trying.
With `Operation.Call` a literal and assertion 4 above, the module has no parameter, and the
agent no target, through which the Safe can be made to `delegatecall`.

## A fork gotcha worth writing down

Every one of anvil's ten development addresses has code on real BSC: an EIP-7702 delegation,
because their private keys are public and somebody pointed them at a contract. A fork
inherits it. The first run of this proof paid 1 BNB to development account 2 and asserted
that the recipient's balance had gone up by 1 BNB — it had not, because the delegated
contract forwarded the money on. The fixture now sends to two addresses derived from
`keccak256("c3s-reflex fork proof: …")` and asserts they are codeless at the fork block, so
that "the recipient was paid" means what it says. Anyone forking a real chain and using
anvil's accounts as if they were plain EOAs should check `cast code` first.

---

**Local fork only. Nothing was broadcast to any network. (本地分叉、未广播)**
