// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {Script} from "forge-std/Script.sol";
import {console2} from "forge-std/console2.sol";
import {NandMachine} from "../src/NandMachine.sol";
import {ISafe} from "../src/ISafe.sol";
import {ReflexModule} from "../src/ReflexModule.sol";
import {RealSafe, ISafeFull, ISafeProxyFactory, IMultiSend} from "./RealSafe.sol";

/// The whole arrangement, as real transactions, on a local anvil fork of BNB Smart Chain.
///
/// **Local fork only. Nothing here is broadcast to any network.** The `--broadcast` flag
/// points at `http://127.0.0.1:<port>`, an anvil process forked from a public BSC endpoint;
/// the transactions land in anvil's own memory and are gone when it exits. The senders are
/// anvil's unlocked development accounts, addressed by address: this script contains no
/// private key, no mnemonic and no seed phrase, and `--unlocked` means Foundry asks anvil
/// to sign rather than signing here.
///
///     scripts/onchain_local_fork.sh          # starts anvil, runs this, then the fork tests
///
/// What it does, in order, each step a transaction:
///
///  1. creates a real Safe v1.4.1 — the singleton and factory already deployed on BSC,
///     present in the forked state — 1-of-1, owner = account 0;
///  2. funds it with 10 BNB from account 0;
///  3. deploys `NandMachine` and one `ReflexModule` over the compiled policy whose bytes
///     arrive in `NETLIST` (the export of `c3s/policy.py`, hash-checked by the constructor);
///  4. enables the module on the Safe with an owner-sent `execTransaction`;
///  5. runs the decisive cases as the agent, which is not an owner and never becomes one.
///
/// Every claim is a `require`, and `scripts/onchain_local_fork.sh` re-reads the balances
/// and the emitted `Decision` rows off anvil afterwards, so the assertions are checked
/// twice: once in the simulation Foundry broadcasts from, once against the chain state
/// that the broadcast actually produced.
contract LocalForkProof is Script {
    // anvil's default development accounts, by address. No key appears here.
    address internal constant A0 = 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266; // owner + supervisor
    address internal constant A1 = 0x70997970C51812dc3A010C7d01b50e0d17dc79C8; // the agent
    address internal constant A9 = 0xa0Ee7A142d267C1f36714E4a8F75612F20a79720; // a stranger

    /// Where value is sent, and where a stranger sends. Not anvil accounts: on real BSC
    /// every one of anvil's development addresses carries an EIP-7702 delegation (their
    /// private keys are public, so somebody pointed them at a contract), and a fork
    /// inherits it — paying one of them runs that contract's `receive`, which in the run
    /// that found this forwarded the BNB straight on. Two addresses nobody has touched,
    /// asserted to be codeless at the fork block, keep "the recipient was paid" honest.
    address internal constant RECIPIENT = address(uint160(uint256(keccak256("c3s-reflex fork proof: recipient"))));
    address internal constant ELSEWHERE = address(uint160(uint256(keccak256("c3s-reflex fork proof: elsewhere"))));

    uint256 internal constant FUNDING = 10 ether;
    uint256 internal constant MOVED = 1 ether;

    function run() external {
        require(block.chainid == 56, "this proof forks BNB Smart Chain (56)");
        require(RealSafe.SINGLETON.code.length > 0, "no Safe singleton in the forked state");
        require(RealSafe.PROXY_FACTORY.code.length > 0, "no Safe proxy factory in the forked state");

        bytes memory netlist = vm.envBytes("NETLIST");
        bytes32 sha = vm.envBytes32("NETLIST_SHA256");
        uint256 nIn = vm.envUint("N_IN");
        uint8 optional = uint8(vm.envUint("OPTIONAL_INPUTS"));
        uint256 nState = vm.envUint("N_STATE");

        // -- 1. a real Safe, from the factory already on chain ------------------
        vm.startBroadcast(A0);
        address safeAddr = ISafeProxyFactory(RealSafe.PROXY_FACTORY).createProxyWithNonce(
            RealSafe.SINGLETON, RealSafe.setupCalldata(A0), uint256(keccak256(abi.encode(A0, block.timestamp, block.number)))
        );
        vm.stopBroadcast();
        ISafeFull safe = ISafeFull(safeAddr);
        require(keccak256(bytes(safe.VERSION())) == keccak256("1.4.1"), "not Safe 1.4.1");
        require(safe.isOwner(A0) && safe.getThreshold() == 1, "the Safe is not 1-of-1 owned by account 0");

        // -- 2. money in it, by transaction, not by cheat code -------------------
        vm.startBroadcast(A0);
        (bool funded,) = safeAddr.call{value: FUNDING}("");
        vm.stopBroadcast();
        require(funded && safeAddr.balance == FUNDING, "the Safe was not funded");

        // -- 3. the reflex arc: evaluator and module ------------------------------
        vm.startBroadcast(A0);
        NandMachine machine = new NandMachine();
        ReflexModule module = new ReflexModule(machine, netlist, sha, nIn, optional, nState, ISafe(safeAddr), A0, address(0));
        module.setIrreversible(bytes4(keccak256("transfer(address,uint256)")), true);
        module.setIrreversible(bytes4(keccak256("approve(address,uint256)")), true);
        module.setAgent(A1, true);
        vm.stopBroadcast();
        require(module.agents(A1), "the agent was not named");
        require(!module.agents(A9), "a stranger was named");
        require(module.netlistSha256() == sha, "the deployed netlist is not the exported one");
        require(keccak256(module.netlist()) == keccak256(netlist), "the netlist does not read back from code");

        // -- 4. the owner enables it ---------------------------------------------
        vm.startBroadcast(A0);
        safe.execTransaction(
            safeAddr,
            0,
            abi.encodeCall(ISafeFull.enableModule, (address(module))),
            0,
            0,
            0,
            0,
            address(0),
            payable(address(0)),
            RealSafe.preValidatedSignature(A0)
        );
        vm.stopBroadcast();
        require(safe.isModuleEnabled(address(module)), "the module is not enabled");
        require(!safe.isOwner(A1), "the agent must not be an owner");

        console2.log(string.concat("FORK_SAFE=", vm.toString(safeAddr)));
        console2.log(string.concat("FORK_MODULE=", vm.toString(address(module))));
        console2.log(string.concat("FORK_MACHINE=", vm.toString(address(machine))));
        console2.log(string.concat("FORK_AGENT=", vm.toString(A1)));
        console2.log(string.concat("FORK_SUPERVISOR=", vm.toString(A0)));
        console2.log(string.concat("FORK_RECIPIENT=", vm.toString(RECIPIENT)));
        console2.log(string.concat("FORK_STRANGER=", vm.toString(A9)));

        _decisiveCases(safe, module);
    }

    /// The five cases, each one an assertion about chain state after a transaction.
    function _decisiveCases(ISafeFull safe, ReflexModule module) internal {
        address safeAddr = address(safe);
        require(RECIPIENT.code.length == 0 && ELSEWHERE.code.length == 0, "the test addresses are not codeless at this fork block");
        bytes32 key = module.confirmationKey(A1, RECIPIENT, MOVED, "");
        uint256 recipientBefore = RECIPIENT.balance;

        // (a) refused. The transfer moves value, so `irreversible` is high, and the policy
        //     is `confirm_per_irreversible`: with no confirmation it is refused. The tick is
        //     spent — a refusal that cost nothing would let an agent retry for ever.
        vm.startBroadcast(A1);
        (bool granted,) = module.act(RECIPIENT, MOVED, "");
        vm.stopBroadcast();
        require(!granted, "(a) an unconfirmed transfer was granted");
        require(safeAddr.balance == FUNDING, "(a) the Safe paid out on a refusal");
        require(RECIPIENT.balance == recipientBefore, "(a) the recipient was paid on a refusal");
        require(module.recordOf(A1).ticks == 1, "(a) the refused tick was not spent");
        console2.log("(a) refused, unconfirmed transfer: nothing moved, tick 1 spent");

        // (b) granted. A person leaves a confirmation bound to this exact call, and only
        //     then does the Safe pay.
        vm.startBroadcast(A0);
        module.confirm(key);
        vm.stopBroadcast();
        require(module.confirmations(key), "(b) the confirmation was not recorded");

        vm.startBroadcast(A1);
        (granted,) = module.act(RECIPIENT, MOVED, "");
        vm.stopBroadcast();
        require(granted, "(b) the confirmed transfer was refused");
        require(safeAddr.balance == FUNDING - MOVED, "(b) the Safe did not pay");
        require(RECIPIENT.balance == recipientBefore + MOVED, "(b) the recipient was not paid");
        require(!module.confirmations(key), "(b) the confirmation was not spent");
        console2.log("(b) granted, confirmed transfer: 1 BNB left the real Safe, confirmation spent");

        // (c) no replay. Ticks 3..5 are the cooldown; on tick 6 the cooldown is over and the
        //     same call is refused again, because the confirmation is gone. Nothing the agent
        //     sends restores it.
        for (uint256 t = 3; t <= 6; ++t) {
            vm.startBroadcast(A1);
            (granted,) = module.act(RECIPIENT, MOVED, "");
            vm.stopBroadcast();
            require(!granted, "(c) the transfer was replayed");
            require(safeAddr.balance == FUNDING - MOVED, "(c) the Safe paid twice");
        }
        require(module.recordOf(A1).ticks == 6, "(c) ticks were not spent");
        console2.log("(c) no replay: four more attempts, the Safe paid once, six ticks spent");

        // (d) no second path. The module never asks for `DelegateCall`, so the only way to a
        //     Safe-authorised function would be an intermediary that calls the Safe back —
        //     and to the Safe that intermediary is not an owner and not a module.
        //     MultiSendCallOnly batches calls from its own address, so `addOwnerWithThreshold`
        //     arrives from MultiSendCallOnly and the Safe rejects it (GS031). The circuit
        //     grants the tick; the Safe refuses the content.
        bytes memory batch = RealSafe.packedCall(
            safeAddr, 0, abi.encodeCall(ISafeFull.addOwnerWithThreshold, (A1, 1))
        );
        vm.startBroadcast(A1);
        (granted,) = module.act(RealSafe.MULTI_SEND_CALL_ONLY, 0, abi.encodeCall(IMultiSend.multiSend, (batch)));
        vm.stopBroadcast();
        require(granted, "(d) expected the circuit to grant this tick");
        require(!safe.isOwner(A1), "(d) the agent made itself an owner through MultiSendCallOnly");
        require(safe.getOwners().length == 1, "(d) the owner set changed");
        require(module.lastFailed(A1), "(d) the forwarded call should be recorded as failed");
        console2.log("(d) no second path: a granted tick through MultiSendCallOnly cannot add an owner");

        // (e) a stranger. `act` records state per `msg.sender`, so before it asked who the
        //     agent was, an address that appears nowhere in the deployment was a fresh agent
        //     with empty latches and was granted on its first tick — see
        //     docs/ONCHAIN-LOCAL-FORK.md, "The route around it". Now it is refused at the
        //     door, with the module's own reason, and spends nothing.
        //     This one is checked in simulation, not broadcast: a reverting transaction
        //     aborts a broadcast run. `scripts/onchain_local_fork.sh` re-checks it against
        //     the chain with `cast call`, and the fork tests assert the typed error.
        vm.prank(A9);
        (bool reached, bytes memory err) = address(module).call(abi.encodeCall(ReflexModule.act, (ELSEWHERE, 0, "")));
        require(!reached, "(e) an address nobody named was allowed to act");
        require(bytes4(err) == ReflexModule.NotAgent.selector, "(e) refused for some other reason");
        require(module.recordOf(A9).ticks == 0, "(e) the stranger spent a tick");
        console2.log("(e) an address nobody named is refused with NotAgent and spends nothing");

        console2.log("all assertions held, on a local fork, nothing broadcast");
    }
}
