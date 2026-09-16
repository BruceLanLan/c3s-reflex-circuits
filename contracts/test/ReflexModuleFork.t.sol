// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {NandMachine} from "../src/NandMachine.sol";
import {ISafe} from "../src/ISafe.sol";
import {ReflexModule} from "../src/ReflexModule.sol";
import {RealSafe, ISafeFull, ISafeProxyFactory, IMultiSend, ISimulateTxAccessor} from "../script/RealSafe.sol";

/// Writes to its own storage slot 0. Reached by `call` it clobbers nothing that matters;
/// reached by `delegatecall` from a Safe it would overwrite the Safe's slot 0 — the
/// singleton address — and brick it. Which is how `test_fork_everyForwardedCallIsACall`
/// can tell the two apart from the outside.
contract SlotWriter {
    event Wrote(address by);

    function write(address value) external {
        assembly {
            sstore(0, value)
        }
        emit Wrote(msg.sender);
    }
}

/// Calls back into the module while the module is forwarding on its behalf.
contract Reenterer {
    ReflexModule public module;
    bytes4 public caught;

    function arm(ReflexModule m) external {
        module = m;
    }

    function poke() external {
        try module.act(address(this), 0, abi.encodeCall(Reenterer.poke, ())) {
            caught = bytes4(0);
        } catch (bytes memory err) {
            caught = bytes4(err);
        }
    }
}

/// The same module, the same compiled policy and the same rules as `ReflexModule.t.sol` —
/// but against the Safe that is really deployed on BNB Smart Chain, in forked state, rather
/// than a mock that behaves the way we believe a Safe behaves.
///
/// Skipped unless `FORK_URL` is set, so `forge test` stays offline by default:
///
///     scripts/onchain_local_fork.sh                       # starts anvil and sets it
///     FORK_URL=http://127.0.0.1:8547 forge test --match-path 'test/ReflexModuleFork.t.sol'
///
/// The mock proved what the module does with a grant. These tests are about the two things
/// a mock cannot answer: whether a real Safe forwards and refuses where we said it would,
/// and whether there is a way around the module into the same Safe — another entry point,
/// a replay, a `delegatecall`, an intermediary, a guard, a second identity.
contract ReflexModuleForkTest is Test {
    bytes internal constant NETLIST =
        hex"0100002a0100002c01000031000000040000040000000200000a0000000b00000b00000007000007000000080000080000000d00000e0000000f00000f0000000c0000100000001100001100000006000006000000090000090000000500000500000014000015000000130000130000001600001600000017000018000000120000190000001a00001a0000000800000d0000000800001c0000000d00001c0000001d00001e000000100000100000000d00002000000007000010000000210000220000001f00002000000008000010000000240000250000001b00001b000000230000270000000100001b00000028000029000000260000270000002900002b0000000600001b0000002d00002d0000002e00002e0000001600002f000000300000300000001a00001a";
    bytes32 internal constant SHA = 0xc95d6f6170d140d475e91fd12564c080d9f5f950416f5d84c2bb387c7606724b;
    uint256 internal constant N_IN = 5;
    uint256 internal constant N_STATE = 3;
    uint8 internal constant OPTIONAL = 1; // OPT_IRREVERSIBLE
    uint256 internal constant GAP = 4;

    uint256 internal constant FUNDING = 10 ether;
    uint256 internal constant MOVED = 1 ether;

    address internal owner = makeAddr("safe owner");
    address internal supervisor = makeAddr("supervisor");
    address internal agent = makeAddr("agent");
    address internal stranger = makeAddr("stranger");
    address internal recipient = makeAddr("recipient");

    ISafeFull internal safe;
    NandMachine internal machine;
    ReflexModule internal module;

    function setUp() public {
        string memory url = vm.envOr("FORK_URL", string(""));
        if (bytes(url).length == 0) {
            vm.skip(true);
            return;
        }
        vm.createSelectFork(url);
        require(block.chainid == 56, "FORK_URL is not a fork of BNB Smart Chain (56)");
        require(RealSafe.SINGLETON.code.length > 0, "no Safe v1.4.1 singleton in the forked state");

        safe = ISafeFull(
            ISafeProxyFactory(RealSafe.PROXY_FACTORY).createProxyWithNonce(
                RealSafe.SINGLETON, RealSafe.setupCalldata(owner), uint256(keccak256("c3s-reflex fork test"))
            )
        );
        vm.deal(address(safe), FUNDING);

        machine = new NandMachine();
        module = new ReflexModule(machine, NETLIST, SHA, N_IN, OPTIONAL, N_STATE, ISafe(address(safe)), supervisor, address(0));
        vm.prank(supervisor);
        module.setAgent(agent, true);
        _asOwner(address(safe), abi.encodeCall(ISafeFull.enableModule, (address(module))));
    }

    // -- helpers ----------------------------------------------------------------

    /// An owner-sent Safe transaction, signed with Safe's pre-validated signature form
    /// (v = 1, valid because the owner is the caller): no key, no EIP-712 hashing.
    function _asOwner(address to, bytes memory data) internal {
        vm.prank(owner);
        safe.execTransaction(
            to, 0, data, 0, 0, 0, 0, address(0), payable(address(0)), RealSafe.preValidatedSignature(owner)
        );
    }

    function _act(address who, address to, uint256 value, bytes memory data) internal returns (bool granted) {
        vm.prank(who);
        (granted,) = module.act(to, value, data);
    }

    function _confirm(address to, uint256 value, bytes memory data) internal {
        bytes32 key = module.confirmationKey(agent, to, value, data);
        vm.prank(supervisor);
        module.confirm(key);
    }

    /// Spend ticks until the cooldown after the last grant has passed.
    function _coolDown() internal {
        for (uint256 i = 0; i < GAP - 1; ++i) {
            assertFalse(_act(agent, recipient, 0, ""), "expected a cooldown tick to be refused");
        }
    }

    // -- the wiring ---------------------------------------------------------------

    function test_fork_theForkHasTheRealSafeAndTheModuleIsWiredToIt() public view {
        assertEq(safe.VERSION(), "1.4.1", "not the canonical Safe version");
        assertEq(RealSafe.SINGLETON, address(uint160(uint256(vm.load(address(safe), bytes32(0))))), "not that singleton");
        assertTrue(safe.isModuleEnabled(address(module)), "module not enabled");
        assertTrue(safe.isOwner(owner));
        assertFalse(safe.isOwner(agent), "the agent is an owner");
        assertEq(safe.getThreshold(), 1);
        assertEq(module.netlistSha256(), SHA);
        assertEq(module.netlist(), NETLIST, "the netlist did not read back from code");
        assertEq(address(module.safe()), address(safe));
    }

    // -- the two decisive cases ----------------------------------------------------

    /// Value moves, so `irreversible` is high; the policy is `confirm_per_irreversible`;
    /// nobody confirmed. The circuit refuses, the Safe is never asked, and the tick is
    /// spent — a refusal that cost the agent nothing would be a free retry.
    function test_fork_unconfirmedTransferIsRefusedAndMovesNothing() public {
        (bool wouldGrant, uint256 inputs,,) = module.peek(agent, recipient, MOVED, "");
        assertEq(inputs, 0x13, "request|intent|irreversible");
        assertFalse(wouldGrant, "peek disagrees");

        assertFalse(_act(agent, recipient, MOVED, ""), "the unconfirmed transfer was granted");
        assertEq(address(safe).balance, FUNDING, "the Safe paid out on a refusal");
        assertEq(recipient.balance, 0, "the recipient was paid on a refusal");
        assertEq(module.recordOf(agent).ticks, 1, "the refused tick was not spent");
    }

    /// A person confirms this exact call, and only then does the real Safe pay.
    function test_fork_confirmedTransferLeavesTheRealSafeAndSpendsTheConfirmation() public {
        _confirm(recipient, MOVED, "");
        vm.expectEmit(true, false, false, true, address(module));
        emit ReflexModule.Forwarded(agent, true);
        assertTrue(_act(agent, recipient, MOVED, ""), "the confirmed transfer was refused");
        assertEq(address(safe).balance, FUNDING - MOVED, "the Safe did not pay");
        assertEq(recipient.balance, MOVED, "the recipient was not paid");
        assertFalse(module.confirmations(module.confirmationKey(agent, recipient, MOVED, "")), "confirmation not spent");
    }

    /// The confirmation is gone, and the agent cannot put it back. After the cooldown has
    /// passed — so that the cooldown is not what is doing the work — the same transfer is
    /// refused again, and the Safe has paid exactly once.
    function test_fork_noReplayOnceTheCooldownHasPassed() public {
        _confirm(recipient, MOVED, "");
        assertTrue(_act(agent, recipient, MOVED, ""));
        _coolDown();
        (bool wouldGrant,,,) = module.peek(agent, recipient, MOVED, "");
        assertFalse(wouldGrant, "the cooldown has passed and the replay would be granted");
        assertFalse(_act(agent, recipient, MOVED, ""), "the transfer was replayed");
        assertEq(address(safe).balance, FUNDING - MOVED, "the Safe paid twice");
        assertEq(recipient.balance, MOVED, "the recipient was paid twice");
    }

    // -- the ways around it --------------------------------------------------------

    /// The one on-chain refusal that *is* a revert, and it carries the module's own reason:
    /// the Safe and the module are never legal targets, because a module's call to its Safe
    /// is an authenticated self-call and one granted `addOwnerWithThreshold` would end the
    /// arrangement. No tick is spent, because nothing was decided.
    function test_fork_actOnTheSafeRevertsWithTheModulesOwnReason() public {
        bytes memory addOwner = abi.encodeCall(ISafeFull.addOwnerWithThreshold, (agent, 1));
        vm.expectRevert(abi.encodeWithSelector(ReflexModule.ForbiddenTarget.selector, address(safe)));
        this.forbidden(address(safe), addOwner);
        vm.expectRevert(abi.encodeWithSelector(ReflexModule.ForbiddenTarget.selector, address(module)));
        this.forbidden(address(module), "");
        assertEq(module.recordOf(agent).ticks, 0, "a reverted call spent a tick");
        assertFalse(safe.isOwner(agent));
    }

    function forbidden(address to, bytes memory data) external {
        vm.prank(agent);
        module.act(to, 0, data);
    }

    /// The obvious way around "the Safe is not a target": have something else call the Safe.
    /// MultiSendCallOnly will batch it, but it makes those calls from its own address, and to
    /// the Safe that address is neither an owner nor a module. The circuit grants the tick;
    /// the Safe refuses the content, and the module records the failure.
    function test_fork_multiSendCallOnlyCannotAddAnOwner() public {
        bytes memory batch = RealSafe.packedCall(
            address(safe), 0, abi.encodeCall(ISafeFull.addOwnerWithThreshold, (agent, 1))
        );
        assertTrue(
            _act(agent, RealSafe.MULTI_SEND_CALL_ONLY, 0, abi.encodeCall(IMultiSend.multiSend, (batch))),
            "expected the circuit to grant this tick"
        );
        assertFalse(safe.isOwner(agent), "the agent became an owner through MultiSendCallOnly");
        assertEq(safe.getOwners().length, 1, "the owner set changed");
        assertTrue(module.lastFailed(agent), "the failed forward was not recorded");

        // And the same batch aimed at enabling a second module of the agent's own.
        _coolDown();
        batch = RealSafe.packedCall(address(safe), 0, abi.encodeCall(ISafeFull.enableModule, (stranger)));
        assertTrue(_act(agent, RealSafe.MULTI_SEND_CALL_ONLY, 0, abi.encodeCall(IMultiSend.multiSend, (batch))));
        assertFalse(safe.isModuleEnabled(stranger), "a second module was enabled");
    }

    /// The other MultiSend — the one Safe transactions delegatecall into, which is how a
    /// batch normally becomes one Safe transaction. `act` has no operation parameter, so it
    /// can only be reached by a plain call, and 1.4.1 refuses that itself.
    function test_fork_multiSendDelegateVariantRefusesAPlainCall() public {
        bytes memory batch = RealSafe.packedCall(
            address(safe), 0, abi.encodeCall(ISafeFull.addOwnerWithThreshold, (agent, 1))
        );
        assertTrue(_act(agent, RealSafe.MULTI_SEND, 0, abi.encodeCall(IMultiSend.multiSend, (batch))));
        assertTrue(module.lastFailed(agent), "the plain call to MultiSend was reported as a success");
        assertFalse(safe.isOwner(agent));
    }

    /// Nothing the agent sends can make the Safe `delegatecall`: the module passes
    /// `Operation.Call` as a literal. `SlotWriter.write` would overwrite the Safe's slot 0,
    /// its singleton pointer, if it ran in the Safe's context — the Safe would stop being a
    /// Safe. It runs in its own instead, and the Safe is untouched.
    function test_fork_everyForwardedCallIsACallNotADelegateCall() public {
        SlotWriter writer = new SlotWriter();
        bytes32 slot0Before = vm.load(address(safe), bytes32(0));
        assertTrue(_act(agent, address(writer), 0, abi.encodeCall(SlotWriter.write, (agent))), "not granted");
        assertFalse(module.lastFailed(agent), "the write failed for some other reason");
        assertEq(vm.load(address(safe), bytes32(0)), slot0Before, "the Safe's singleton pointer moved");
        assertEq(vm.load(address(writer), bytes32(0)), bytes32(uint256(uint160(agent))), "it ran somewhere else");
        assertEq(safe.VERSION(), "1.4.1", "the Safe stopped answering");
    }

    /// The one place a Safe will `delegatecall` code it is handed is `simulateAndRevert`,
    /// which lives on the Safe itself and is also reachable through the fallback handler's
    /// `simulate`. Both need a call whose target is the Safe, and `to == safe` reverts before
    /// a tick is spent; aimed straight at the handler instead, `simulate` runs in the
    /// handler's own context, where there is no Safe to change.
    function test_fork_theDelegatecallPathNeedsTheSafeAsATargetAndCannotHaveIt() public {
        SlotWriter writer = new SlotWriter();
        bytes memory payload = abi.encodeCall(SlotWriter.write, (agent));
        bytes32 slot0Before = vm.load(address(safe), bytes32(0));

        vm.expectRevert(abi.encodeWithSelector(ReflexModule.ForbiddenTarget.selector, address(safe)));
        this.forbidden(address(safe), abi.encodeCall(ISafeFull.simulateAndRevert, (address(writer), payload)));

        assertTrue(
            _act(agent, RealSafe.FALLBACK_HANDLER, 0, abi.encodeCall(ISimulateTxAccessor.simulate, (address(writer), 0, payload, 1))),
            "expected the circuit to grant this tick"
        );
        assertEq(vm.load(address(safe), bytes32(0)), slot0Before, "the Safe's singleton pointer moved");
        assertEq(safe.getOwners().length, 1);
        assertEq(safe.VERSION(), "1.4.1", "the Safe stopped answering");
    }

    /// A guard is the natural place to put a second opinion, and on Safe v1.4.1 it is not
    /// one for a module: the forked bytecode has no `checkModuleTransaction`, and a guard
    /// that reverts on everything stops the owners' own transactions while leaving the
    /// module's path open. The module *is* the boundary on this path, not a guard beside it.
    function test_fork_aRevertingGuardDoesNotSeeTheModulePath() public {
        address guard = address(new RevertingGuard());
        _asOwner(address(safe), abi.encodeCall(ISafeFull.setGuard, (guard)));

        _confirm(recipient, MOVED, "");
        assertTrue(_act(agent, recipient, MOVED, ""), "the guard stopped a granted tick");
        assertEq(recipient.balance, MOVED);

        // The owners' own path is now dead, kill switch included, which is why this is a
        // test and not a step of the deployment script.
        vm.expectRevert();
        this.ownerTransaction(address(safe), abi.encodeCall(ISafeFull.setGuard, (address(0))));
    }

    function ownerTransaction(address to, bytes memory data) external {
        _asOwner(to, data);
    }

    /// A target that calls back into the module while it is forwarding gets `Reentrant`,
    /// so a grant cannot be turned into two.
    function test_fork_reentryDuringForwardingIsRejected() public {
        Reenterer r = new Reenterer();
        r.arm(module);
        assertTrue(_act(agent, address(r), 0, abi.encodeCall(Reenterer.poke, ())), "not granted");
        assertEq(r.caught(), ReflexModule.Reentrant.selector, "re-entry was not refused");
        assertEq(module.recordOf(agent).ticks, 1, "the re-entrant call spent a second tick");
    }

    /// The two doors that are not the module. The agent is not an owner, so its signature
    /// buys nothing (GS026); and the Safe's module entry point is only for enabled modules,
    /// so a third party — or the agent itself — cannot use it directly (GS104).
    function test_fork_theOtherDoorsIntoTheSafeAreShut() public {
        vm.prank(agent);
        vm.expectRevert(bytes("GS026"));
        safe.execTransaction(
            recipient, MOVED, "", 0, 0, 0, 0, address(0), payable(address(0)), RealSafe.preValidatedSignature(agent)
        );

        vm.prank(agent);
        vm.expectRevert(bytes("GS104"));
        safe.execTransactionFromModule(recipient, MOVED, "", 0);

        vm.prank(stranger);
        vm.expectRevert(bytes("GS104"));
        safe.execTransactionFromModule(recipient, MOVED, "", 0);

        assertEq(address(safe).balance, FUNDING, "something got out");
    }

    /// The real boundary: the owner key. After `disableModule` the circuit still decides,
    /// and the Safe still says no.
    function test_fork_disableModuleIsTheKillSwitch() public {
        _asOwner(address(safe), abi.encodeCall(ISafeFull.disableModule, (address(0x1), address(module))));
        assertFalse(safe.isModuleEnabled(address(module)));

        _confirm(recipient, MOVED, "");
        vm.expectEmit(true, false, false, true, address(module));
        emit ReflexModule.Forwarded(agent, false);
        assertTrue(_act(agent, recipient, MOVED, ""), "the circuit should still grant");
        assertEq(address(safe).balance, FUNDING, "a disabled module still moved value");
        assertEq(recipient.balance, 0);
    }

    /// The route this fork run found, and the fix for it. `act` keys everything by
    /// `msg.sender`, so before the module knew who its agents were, an address that appeared
    /// nowhere in the deployment arrived with empty latches — `request` and `intent` high,
    /// `blocked` low, `irreversible` low for a zero-value call whose selector is not on the
    /// list, no prior grant to cool down from — and was granted on its first tick. Every
    /// tick-counting rule was therefore per address, and addresses are free, which made
    /// `min_gap_ticks` a cost rather than the boundary AGENT.md calls it.
    ///
    /// Now `act` asks. An unnamed caller gets `NotAgent` and spends nothing, while the agent
    /// the supervisor named is unaffected — and a stranger still cannot spend a confirmation
    /// left for that agent, because the confirmation key names the agent.
    /// docs/ONCHAIN-LOCAL-FORK.md, "The route around it".
    function test_fork_anUnnamedCallerIsNotAnAgent() public {
        SlotWriter writer = new SlotWriter();
        bytes memory write = abi.encodeCall(SlotWriter.write, (stranger));

        assertTrue(_act(agent, address(writer), 0, write), "the named agent's first tick");
        assertFalse(_act(agent, address(writer), 0, write), "the named agent is now cooling down");

        vm.expectRevert(abi.encodeWithSelector(ReflexModule.NotAgent.selector, stranger));
        this.strangerActs(address(writer), write);
        assertEq(module.recordOf(stranger).ticks, 0, "an unnamed caller spent a tick");
        assertEq(module.recordOf(agent).ticks, 2, "the agent's counter, untouched");

        // Named, the same address is an agent with its own empty latches — which is the
        // point of naming rather than of a second contract.
        vm.prank(supervisor);
        module.setAgent(stranger, true);
        assertTrue(_act(stranger, address(writer), 0, write), "a named second agent was refused");

        // Even so, a confirmation belongs to one agent: the key includes it.
        _confirm(recipient, MOVED, "");
        assertFalse(_act(stranger, recipient, MOVED, ""), "a second agent spent the first one's confirmation");
        assertEq(address(safe).balance, FUNDING, "value left the Safe on the wrong agent's tick");

        // And taking the name away is a kill switch for that agent alone.
        vm.prank(supervisor);
        module.setAgent(stranger, false);
        vm.expectRevert(abi.encodeWithSelector(ReflexModule.NotAgent.selector, stranger));
        this.strangerActs(address(writer), write);
        assertTrue(safe.isModuleEnabled(address(module)), "the module is still enabled for the others");
    }

    function strangerActs(address to, bytes memory data) external {
        vm.prank(stranger);
        module.act(to, 0, data);
    }
}

/// Refuses every Safe transaction it is asked about. On v1.4.1 it is never asked about a
/// module's call, which is the point.
contract RevertingGuard {
    error GuardSaysNo();

    function checkTransaction(
        address,
        uint256,
        bytes memory,
        uint8,
        uint256,
        uint256,
        uint256,
        address,
        address payable,
        bytes memory,
        address
    ) external pure {
        revert GuardSaysNo();
    }

    function checkAfterExecution(bytes32, bool) external pure {
        revert GuardSaysNo();
    }

    function supportsInterface(bytes4) external pure returns (bool) {
        return true;
    }
}
