# Putting a compiled policy between an agent and a Safe, yourself

`contracts/src/ReflexModule.sol` is a Safe *module*: a contract the owners of a Safe
enable, after which it may ask the Safe to execute calls. This one asks only on the ticks
where a compiled policy circuit ([AGENT.md](AGENT.md), `c3s/policy.py`) grants. The
project maintainers deploy nothing and hold no wallet; every step below runs on your
machine with your keys, and nothing in this repository ever sees them.

## The shape

```
agent (any key, not an owner) ──act(to,value,data)──▶ ReflexModule ──▶ NandMachine.evaluate
                                                            │  grant
supervisor (your key) ── blocked / confirm / heartbeat ─────┤
                                                            ▼
                                            Safe.execTransactionFromModule (Call only)
```

* **Why a module and not an owner key for the agent.** An owner can sign; a module can
  only ask, and only through this contract. The agent supplies *no input bit*: `request`
  and `intent` are high because it called; `blocked`, `confirm`, `heartbeat` and the
  irreversible-selector list are written by the supervisor; `failed` is recorded by the
  module from the last forwarded call. Nothing the agent sends can move those bits, which
  is what makes a rule resting on them a boundary in AGENT.md's sense.
* **No `reset` for the agent, no operation parameter, no netlist setter.** Each would be a
  way to spend a tick without paying for it: wiping the latches, bundling N calls into one
  `DelegateCall` to MultiSend, swapping the rules. Only the supervisor resets; the
  operation is always `Call`; new rules are a new module.
* Calls targeting the Safe itself or the module revert before any tick is spent: a module's
  call to its Safe is an authenticated self-call, and one granted `addOwnerWithThreshold`
  would end the arrangement.

## What to trust, and what not to

* **The Safe trusts an enabled module completely.** `execTransactionFromModule` checks no
  signature, no nonce and no guard (Safe v1.4.1, the version canonical on 56/97/204/5611).
  A bug in this module's gating is unrestricted access to the Safe. Read it before you
  enable it; the owners can `disableModule` at any time, and that owner key — not the
  circuit — is the real boundary.
* **The supervisor key is the second boundary.** Every input besides the agent's own
  request comes from it. If the agent can sign as the supervisor, every rule is a cost,
  not a limit. Use a separate key, or the Safe itself.
* **Ticks are calls, not time.** "No two grants within 4 ticks" bounds calls; four fit in
  one block. Only `blockAgent` and `heartbeat`, which read `block.number`, bind time, and
  they are the module's own code, not a proven property.
* **A confirmation is bound to one exact call by the module, not by the circuit.** If the
  confirmed call is refused for another reason (cooldown, block), the policy's token latch
  may stay armed and a *different* irreversible call can be granted next. `peek` before you
  `confirm`, and `revoke` a confirmation you no longer mean.
* **The selector list is fail-open.** An unlisted selector counts as reversible. List
  `approve`, `increaseAllowance`, `permit`, `setApprovalForAll` and whatever else hands out
  authority; ETH value always counts as irreversible. The module gates *when*; which
  targets are worth calling at all is your judgement, not the circuit's.
* **`failed` stays high until the agent's next forwarded call.** Under a cooldown longer
  than a breaker's threshold, one failure trips the breaker. `resetAgent` recovers.

## Self-deploy on opBNB testnet (chain 5611)

You need [Foundry](https://getfoundry.sh), a funded testnet key (tBNB from the BNB Chain
faucet, bridged at opbnb-testnet-bridge.bnbchain.org), a Safe v1.4.1 on 5611 whose owner
key you hold (BNB Chain's multisig UI or the Safe app), and a compiled policy.

1. Compile and verify the policy, and export what the module needs:
   ```sh
   python -c "from c3s.policy import Policy; from c3s.netlist import to_bytes; import hashlib
   p = Policy(min_gap_ticks=4, confirm_per_irreversible=True, forbid_when_blocked=True)
   c = p.build(); assert p.verify(c)['outputs_match'] and p.properties(c)['holds']
   b = to_bytes(c); print('NETLIST=0x'+b.hex()); print('NETLIST_SHA256=0x'+hashlib.sha256(b).hexdigest())
   print('N_IN=%d N_STATE=%d' % (len(p.input_names()), p.state_bits)); print(p.input_names())"
   ```
   `OPTIONAL_INPUTS` is the bitmask of optional inputs the policy reads — `1` irreversible,
   `2` failed, `4` heartbeat, `8` confirm_b — and `4 + popcount` must equal `N_IN`.
2. Local check, no network: `cd contracts && forge test`, then the script with the step-1
   variables plus `SAFE` and `SUPERVISOR` and no `--rpc-url`: an in-memory dry run.
3. Deploy, signing on your machine:
   ```sh
   export RPC=https://opbnb-testnet-rpc.bnbchain.org
   forge script script/DeployReflexModule.s.sol --rpc-url $RPC --broadcast --private-key $PK   # or --ledger
   ```
   or with `forge create`, constructor args last (the trailing zero address is `supervisorB`):
   ```sh
   forge create src/NandMachine.sol:NandMachine --rpc-url $RPC --private-key $PK --broadcast
   forge create src/ReflexModule.sol:ReflexModule --rpc-url $RPC --private-key $PK --broadcast \
     --constructor-args $NAND_MACHINE $NETLIST $NETLIST_SHA256 $N_IN $OPTIONAL_INPUTS $N_STATE $SAFE $SUPERVISOR 0x0000000000000000000000000000000000000000
   ```
   Add `--verify --verifier sourcify` (free; 5611 is supported) to publish the source.
4. Check what landed against what you exported, before enabling anything:
   ```sh
   cast call $MODULE "netlistSha256()(bytes32)" --rpc-url $RPC      # must equal NETLIST_SHA256
   cast keccak $(cast call $MODULE "netlist()(bytes)" --rpc-url $RPC)  # must equal: cast keccak $NETLIST
   cast call $MODULE "safe()(address)" --rpc-url $RPC
   ```
5. Enable the module: an owner-signed Safe transaction calling `enableModule(address)` on the
   Safe with `$MODULE` (Transaction Builder, or `cast calldata "enableModule(address)" $MODULE`).
6. Tell the module which selectors are irreversible, as the supervisor:
   ```sh
   cast send $MODULE "setIrreversible(bytes4,bool)" $(cast sig "transfer(address,uint256)") true --rpc-url $RPC --private-key $SUPERVISOR_PK
   cast send $MODULE "setIrreversible(bytes4,bool)" $(cast sig "approve(address,uint256)") true --rpc-url $RPC --private-key $SUPERVISOR_PK
   ```
7. Preview, then act. The agent's key is any key at all; it is not an owner.
   ```sh
   cast call $MODULE "peek(address,address,uint256,bytes)(bool,uint256,uint256,uint256)" $AGENT $TO $VALUE $DATA --rpc-url $RPC
   cast send $MODULE "act(address,uint256,bytes)" $TO $VALUE $DATA --rpc-url $RPC --private-key $AGENT_PK
   ```
   `Decision(agent,to,value,selector,inputs,granted,tick)` is emitted on every call, `Forwarded(agent,success)` on grants.
8. Confirming an irreversible call. The key is `keccak256(abi.encode(safe, agent, to, value,
   keccak256(data)))`; ask the module or derive it yourself, then confirm as the supervisor:
   ```sh
   KEY=$(cast call $MODULE "confirmationKey(address,address,uint256,bytes)(bytes32)" $AGENT $TO $VALUE $DATA --rpc-url $RPC)
   KEY=$(cast keccak $(cast abi-encode "f(address,address,address,uint256,bytes32)" $SAFE $AGENT $TO $VALUE $(cast keccak $DATA)))
   cast send $MODULE "confirm(bytes32)" $KEY --rpc-url $RPC --private-key $SUPERVISOR_PK
   ```
   `blockAgent(address,uint64)` and `heartbeat(address,uint64)` take the block number the
   flag holds until; `resetAgent(address)` clears the agent's latches.
9. Try the kill switch once: `disableModule(prevModule, module)` from the Safe; `act` is
   then still granted by the circuit but `Forwarded` reports `false`.

Gas, measured in `forge test` with the 5-input policy above (`test_gasReport`): a refused
tick ~121k; a granted tick ~209k, of which the mock Safe's call log is ~91k, so the module's
own share of a grant is ~118k plus whatever the real Safe and the target spend.
