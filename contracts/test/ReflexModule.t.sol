// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {console2} from "forge-std/console2.sol";
import {NandMachine} from "../src/NandMachine.sol";
import {ISafe} from "../src/ISafe.sol";
import {ReflexModule} from "../src/ReflexModule.sol";

/// Stands in for a Safe with this module enabled: forwards the call the way
/// `execTransactionFromModuleReturnData` does (inner failure is a `false`, not a revert),
/// records every call, and can be told to revert outright or to re-enter the module first.
contract MockSafe is ISafe {
    struct Call {
        address to;
        uint256 value;
        bytes data;
        Operation operation;
    }

    Call[] public calls;
    bool public revertOutright;
    ReflexModule public reenterInto;
    bytes4 public reentryError;

    receive() external payable {}

    function count() external view returns (uint256) {
        return calls.length;
    }

    function setRevertOutright(bool on) external {
        revertOutright = on;
    }

    function setReenter(ReflexModule module) external {
        reenterInto = module;
    }

    function execTransactionFromModuleReturnData(address to, uint256 value, bytes memory data, Operation operation)
        external
        returns (bool success, bytes memory returnData)
    {
        if (revertOutright) revert("GS104");
        if (address(reenterInto) != address(0)) {
            try reenterInto.act(to, 0, data) {
                reentryError = bytes4(0);
            } catch (bytes memory err) {
                reentryError = bytes4(err);
            }
        }
        calls.push(Call(to, value, data, operation));
        (success, returnData) = to.call{value: value}(data);
    }
}

contract Target {
    uint256 public pings;
    uint256 public burns;
    uint256 public received;

    receive() external payable {
        received += msg.value;
    }

    function ping() external returns (uint256) {
        return ++pings;
    }

    function burn(uint256 amount) external returns (uint256) {
        burns += amount;
        return burns;
    }

    function fail() external pure {
        revert("no");
    }
}

/// The same bytes kept in `bytes` storage, to measure what the code pointer saves.
contract BytesHolder {
    bytes public netlist;

    constructor(bytes memory n) {
        netlist = n;
    }
}

/// The module must grant exactly when the compiled policy grants, forward only then, and
/// turn every call into input bits the agent did not write. The policy under test is
///
///     Policy(min_gap_ticks=4, confirm_per_irreversible=True, forbid_when_blocked=True)
///
/// inputs (request, intent, blocked, confirm, irreversible), 3 latches, 299 bytes. The bytes
/// and the hash below are the export of
///
///     .venv/bin/python -c "from c3s.policy import Policy; from c3s.netlist import to_bytes; import hashlib;
///       p=Policy(min_gap_ticks=4, confirm_per_irreversible=True, forbid_when_blocked=True); b=to_bytes(p.build());
///       print(b.hex()); print(hashlib.sha256(b).hexdigest()); print(p.input_names(), p.state_bits)"
///
/// and `p.properties()` holds on all 8 reachable states (640 rows). The escape-core fixture
/// is not used here: it has two outputs, and a policy module reads one.
contract ReflexModuleTest is Test {
    bytes internal constant NETLIST =
        hex"0100002a0100002c01000031000000040000040000000200000a0000000b00000b00000007000007000000080000080000000d00000e0000000f00000f0000000c0000100000001100001100000006000006000000090000090000000500000500000014000015000000130000130000001600001600000017000018000000120000190000001a00001a0000000800000d0000000800001c0000000d00001c0000001d00001e000000100000100000000d00002000000007000010000000210000220000001f00002000000008000010000000240000250000001b00001b000000230000270000000100001b00000028000029000000260000270000002900002b0000000600001b0000002d00002d0000002e00002e0000001600002f000000300000300000001a00001a";
    bytes32 internal constant SHA = 0xc95d6f6170d140d475e91fd12564c080d9f5f950416f5d84c2bb387c7606724b;
    uint256 internal constant N_IN = 5;
    uint256 internal constant N_STATE = 3;
    uint8 internal constant OPTIONAL = 1; // OPT_IRREVERSIBLE
    uint256 internal constant GAP = 4;

    NandMachine internal machine;
    MockSafe internal safe;
    Target internal target;
    ReflexModule internal module;
    address internal supervisor = address(0x5AFE);
    address internal agent = address(0xA6E7);

    function setUp() public {
        machine = new NandMachine();
        safe = new MockSafe();
        target = new Target();
        vm.deal(address(safe), 1000 ether);
        module = new ReflexModule(machine, NETLIST, SHA, N_IN, OPTIONAL, N_STATE, safe, supervisor, address(0));
        vm.startPrank(supervisor);
        module.setIrreversible(Target.burn.selector, true);
        module.setAgent(agent, true);
        vm.stopPrank();
    }

    function _act(bytes memory data) internal returns (bool granted) {
        return _act(address(target), 0, data);
    }

    function _act(address to, uint256 value, bytes memory data) internal returns (bool granted) {
        vm.prank(agent);
        (granted,) = module.act(to, value, data);
    }

    function _key(address to, uint256 value, bytes memory data) internal view returns (bytes32) {
        return keccak256(abi.encode(address(safe), agent, to, value, keccak256(data)));
    }

    function _confirm(address to, uint256 value, bytes memory data) internal {
        vm.prank(supervisor);
        module.confirm(_key(to, value, data));
    }

    // -- construction -----------------------------------------------------------

    function test_constructorRejectsAWrongHash() public {
        vm.expectRevert(ReflexModule.NetlistMismatch.selector);
        new ReflexModule(machine, NETLIST, bytes32(uint256(SHA) ^ 1), N_IN, OPTIONAL, N_STATE, safe, supervisor, address(0));
    }

    function test_constructorRejectsALayoutThatDoesNotFitTheInputCount() public {
        vm.expectRevert(abi.encodeWithSelector(ReflexModule.InputLayoutMismatch.selector, N_IN, uint8(0)));
        new ReflexModule(machine, NETLIST, SHA, N_IN, 0, N_STATE, safe, supervisor, address(0));
        // Two keys without a second supervisor cannot ever grant, so it is refused at construction.
        vm.expectRevert(ReflexModule.ZeroAddress.selector);
        new ReflexModule(machine, NETLIST, SHA, N_IN, 8, N_STATE, safe, supervisor, address(0));
        // And the wrong latch count is caught by the evaluator.
        vm.expectRevert(abi.encodeWithSelector(NandMachine.StateSizeMismatch.selector, 3, 2));
        new ReflexModule(machine, NETLIST, SHA, N_IN, OPTIONAL, 2, safe, supervisor, address(0));
    }

    function test_storesTheNetlistInCodeAndReadsItBack() public view {
        assertEq(module.netlist(), NETLIST);
        assertEq(module.netlistSha256(), SHA);
        assertEq(module.netlistPointer().code.length, NETLIST.length + 1);
        assertEq(module.inputPosition(module.OPT_IRREVERSIBLE()), 4);
    }

    // -- the rules, on chain ------------------------------------------------------

    /// Tick 1 grants and forwards; the cooldown then refuses three ticks without forwarding;
    /// tick 5 grants again.
    function test_reversibleCallGrantedThenCooledDownThenGrantedAgain() public {
        bytes memory ping = abi.encodeCall(Target.ping, ());
        assertTrue(_act(ping), "tick 1");
        assertEq(safe.count(), 1);
        assertEq(target.pings(), 1);
        for (uint256 t = 2; t < 1 + GAP; ++t) {
            assertFalse(_act(ping), "cooldown tick");
            assertEq(safe.count(), 1, "a refused call was forwarded");
        }
        assertTrue(_act(ping), "tick 5");
        assertEq(safe.count(), 2);
        assertEq(target.pings(), 2);
        assertEq(module.recordOf(agent).ticks, 5);
        (,,, ISafe.Operation op) = safe.calls(0);
        assertEq(uint256(op), uint256(ISafe.Operation.Call));
    }

    /// An irreversible call needs its own confirmation, on exactly this call, and spends it.
    function test_irreversibleCallNeedsAConfirmationOnItsExactKeyAndSpendsIt() public {
        bytes memory burn = abi.encodeCall(Target.burn, (7));
        bytes memory ping = abi.encodeCall(Target.ping, ());
        (, uint256 inputs,,) = module.peek(agent, address(target), 0, burn);
        assertEq(inputs, 0x13, "request|intent|irreversible");

        assertFalse(_act(burn), "tick 1: no confirmation");
        assertEq(safe.count(), 0);

        _confirm(address(target), 0, burn);
        assertTrue(module.confirmations(_key(address(target), 0, burn)));
        assertTrue(_act(burn), "tick 2: confirmed");
        assertEq(target.burns(), 7);
        assertFalse(module.confirmations(_key(address(target), 0, burn)), "confirmation spent");

        // Ticks 3..5 are refused by the cooldown whatever the call is.
        for (uint256 t = 3; t <= 5; ++t) assertFalse(_act(ping), "cooldown");
        // Tick 6: the cooldown is over, so this refusal is the spent token's.
        assertFalse(_act(burn), "tick 6: token spent");
        assertEq(target.burns(), 7);

        // A confirmation for different data does not help, and stays pending.
        bytes memory otherBurn = abi.encodeCall(Target.burn, (8));
        _confirm(address(target), 0, otherBurn);
        assertFalse(_act(burn), "tick 7: wrong key");
        assertTrue(module.confirmations(_key(address(target), 0, otherBurn)), "unrelated confirmation untouched");
        vm.prank(supervisor);
        module.revoke(_key(address(target), 0, otherBurn));
        assertFalse(module.confirmations(_key(address(target), 0, otherBurn)));

        _confirm(address(target), 0, burn);
        assertTrue(_act(burn), "tick 8: confirmed again");
        assertEq(target.burns(), 14);
    }

    /// ETH value counts as irreversible whether or not the selector is listed.
    function test_valueCountsAsIrreversible() public {
        (, uint256 inputs,,) = module.peek(agent, address(target), 1 wei, "");
        assertEq(inputs & 0x10, 0x10);
        assertFalse(_act(address(target), 1 wei, ""), "unconfirmed value");
        assertEq(target.received(), 0);
        _confirm(address(target), 1 wei, "");
        assertTrue(_act(address(target), 1 wei, ""), "confirmed value");
        assertEq(target.received(), 1 wei);
        assertEq(address(safe).balance, 1000 ether - 1 wei);
    }

    function test_blockRefusesUntilTheBlockPasses() public {
        bytes memory ping = abi.encodeCall(Target.ping, ());
        uint64 until = uint64(vm.getBlockNumber() + 10);
        vm.prank(supervisor);
        module.blockAgent(agent, until);
        assertEq(module.blockedUntil(agent), until);
        (, uint256 inputs,,) = module.peek(agent, address(target), 0, ping);
        assertEq(inputs & 0x4, 0x4, "blocked bit");
        assertFalse(_act(ping), "blocked");
        vm.roll(until - 1);
        assertFalse(_act(ping), "still blocked on the last block");
        vm.roll(until);
        assertTrue(_act(ping), "block passed");
        assertEq(safe.count(), 1);
    }

    function test_onlyTheSupervisorWritesInputs() public {
        address stranger = address(0xBAD);
        bytes32 key = _key(address(target), 0, "");
        vm.startPrank(stranger);
        vm.expectRevert(ReflexModule.NotSupervisor.selector);
        module.confirm(key);
        vm.expectRevert(ReflexModule.NotSupervisor.selector);
        module.revoke(key);
        vm.expectRevert(ReflexModule.NotSupervisor.selector);
        module.blockAgent(agent, type(uint64).max);
        vm.expectRevert(ReflexModule.NotSupervisor.selector);
        module.heartbeat(agent, type(uint64).max);
        vm.expectRevert(ReflexModule.NotSupervisor.selector);
        module.setIrreversible(Target.ping.selector, true);
        vm.expectRevert(ReflexModule.NotSupervisor.selector);
        module.resetAgent(agent);
        vm.expectRevert(ReflexModule.NotSupervisor.selector);
        module.confirmB(key);
        vm.expectRevert(ReflexModule.NotSupervisor.selector);
        module.setAgent(stranger, true);
        vm.stopPrank();
        // And an address the supervisor has not named cannot spend a tick at all.
        vm.prank(stranger);
        vm.expectRevert(abi.encodeWithSelector(ReflexModule.NotAgent.selector, stranger));
        module.act(address(target), 0, abi.encodeCall(Target.ping, ()));
        assertEq(module.recordOf(stranger).ticks, 0, "an unnamed caller spent a tick");
        // The agent cannot reset its own latch either.
        vm.prank(agent);
        vm.expectRevert(ReflexModule.NotSupervisor.selector);
        module.resetAgent(agent);
        // The supervisor can.
        _act(abi.encodeCall(Target.ping, ()));
        assertGt(module.recordOf(agent).state, 0);
        vm.prank(supervisor);
        module.resetAgent(agent);
        assertEq(module.recordOf(agent).state, 0);
        assertEq(module.recordOf(agent).ticks, 0);
    }

    /// There is no operation parameter, so a DelegateCall cannot be asked for. What can go
    /// wrong downstream is a failure, and a failure is recorded, not propagated: the inner
    /// call reverting (a `false` from the Safe) and the Safe itself reverting (the module
    /// disabled) both leave the latch state exactly where the circuit put it.
    function test_aFailedForwardIsRecordedAndTheLatchStateIsIntact() public {
        bytes memory failing = abi.encodeCall(Target.fail, ());
        (bool would,,, uint256 predicted) = module.peek(agent, address(target), 0, failing);
        assertTrue(would);
        assertTrue(_act(failing), "granted");
        assertTrue(module.lastFailed(agent), "inner revert recorded");
        assertEq(module.stateOf(agent), predicted, "latch state");
        assertEq(safe.count(), 1);

        for (uint256 t; t < GAP - 1; ++t) _act(failing);
        safe.setRevertOutright(true);
        (would,,, predicted) = module.peek(agent, address(target), 0, abi.encodeCall(Target.ping, ()));
        assertTrue(would);
        assertTrue(_act(abi.encodeCall(Target.ping, ())), "granted, Safe reverted");
        assertTrue(module.lastFailed(agent), "outright revert recorded");
        assertEq(module.stateOf(agent), predicted, "latch state after a Safe revert");
        assertEq(target.pings(), 0);

        safe.setRevertOutright(false);
        for (uint256 t; t < GAP - 1; ++t) _act(failing);
        assertTrue(_act(abi.encodeCall(Target.ping, ())));
        assertFalse(module.lastFailed(agent), "a success clears it");
    }

    /// A policy whose fifth input is not `irreversible`: Policy(two_key=True,
    /// forbid_when_blocked=True) reads (request, intent, blocked, confirm, confirm_b), so
    /// the layout mask — not the input count — decides what bit 4 means. Exported the same
    /// way as NETLIST above; 16 NAND + 2 LATCH, properties hold on 4 reachable states.
    function test_twoKeyPolicyReadsConfirmBAtBitFourAndSpendsBothKeys() public {
        bytes memory twoKey =
            hex"010000180100001a00000004000004000000020000090000000a00000a00000007000007000000050000050000000c00000d00000008000008000000060000060000000f0000100000000e000011000000120000120000000b00001300000014000014000000150000150000000e00001600000017000017000000110000160000001900001900000014000014";
        bytes32 sha = 0x8f4bcbd8990fc5a47a76c116d860b879329e473d9628bcf4f3fc5d6612e2407d;
        address second = address(0xB0B);
        ReflexModule pair = new ReflexModule(machine, twoKey, sha, 5, 8, 2, safe, supervisor, second);
        vm.prank(supervisor);
        pair.setAgent(agent, true);
        assertEq(pair.inputPosition(pair.OPT_CONFIRM_B()), 4);
        uint8 irreversible = pair.OPT_IRREVERSIBLE();
        vm.expectRevert(abi.encodeWithSelector(ReflexModule.InputLayoutMismatch.selector, 5, uint8(8)));
        pair.inputPosition(irreversible);

        bytes memory ping = abi.encodeCall(Target.ping, ());
        bytes32 key = keccak256(abi.encode(address(safe), agent, address(target), uint256(0), keccak256(ping)));
        // Value is not irreversible here: the policy has no such input, so the bit is never set.
        (bool would, uint256 inputs,,) = pair.peek(agent, address(target), 1 wei, ping);
        assertEq(inputs, 0x3);
        assertFalse(would, "no keys");

        vm.prank(agent);
        (bool granted,) = pair.act(address(target), 0, ping);
        assertFalse(granted, "tick 1: no keys");

        vm.prank(supervisor);
        pair.confirm(key);
        (would, inputs,,) = pair.peek(agent, address(target), 0, ping);
        assertEq(inputs, 0xB, "confirm at bit 3");
        vm.prank(agent);
        (granted,) = pair.act(address(target), 0, ping);
        assertFalse(granted, "tick 2: one key");
        assertFalse(pair.confirmations(key), "first key spent into the circuit's key_a latch");

        vm.prank(second);
        pair.confirmB(key);
        (would, inputs,,) = pair.peek(agent, address(target), 0, ping);
        assertEq(inputs, 0x13, "confirm_b at bit 4");
        assertTrue(would);
        vm.prank(agent);
        (granted,) = pair.act(address(target), 0, ping);
        assertTrue(granted, "tick 3: both keys");
        assertFalse(pair.confirmationsB(key), "second key spent");
        assertEq(pair.stateOf(agent), 0, "both key latches spent by the grant");
        assertEq(target.pings(), 1);

        vm.prank(agent);
        (granted,) = pair.act(address(target), 0, ping);
        assertFalse(granted, "tick 4: keys are spent");
        vm.prank(supervisor);
        vm.expectRevert(ReflexModule.NotSupervisor.selector);
        pair.confirmB(key);
    }

    function test_theSafeAndTheModuleAreNeverTargets() public {
        vm.expectRevert(abi.encodeWithSelector(ReflexModule.ForbiddenTarget.selector, address(safe)));
        module.peek(agent, address(safe), 0, hex"");
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(ReflexModule.ForbiddenTarget.selector, address(safe)));
        module.act(address(safe), 0, hex"");
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(ReflexModule.ForbiddenTarget.selector, address(module)));
        module.act(address(module), 0, abi.encodeCall(ReflexModule.resetAgent, (agent)));
        assertEq(module.recordOf(agent).ticks, 0, "no tick spent");
    }

    /// The on-chain echo of the policy's rate limit: whatever the agent asks, and whatever
    /// the supervisor confirms, no two grants within GAP calls.
    function testFuzz_noTwoGrantsWithinFourCalls(uint256 seed) public {
        uint256 lastGrant;
        uint256 grants;
        for (uint256 call = 1; call <= 64; ++call) {
            seed = uint256(keccak256(abi.encode(seed, call)));
            address to = seed % 4 == 0 ? address(uint160(seed >> 96)) : address(target);
            if (to == address(safe) || to == address(module)) to = address(target);
            uint256 value = (seed >> 8) % 3;
            bytes memory data;
            uint256 pick = (seed >> 16) % 4;
            if (pick == 0) data = abi.encodeCall(Target.ping, ());
            else if (pick == 1) data = abi.encodeCall(Target.burn, (seed >> 24));
            else if (pick == 2) data = abi.encodeCall(Target.fail, ());
            else data = abi.encodePacked(bytes4(uint32(seed >> 32)), seed);
            if ((seed >> 64) & 1 == 1) _confirm(to, value, data);

            (bool would,,,) = module.peek(agent, to, value, data);
            bool granted = _act(to, value, data);
            assertEq(granted, would, "peek disagrees with act");
            if (granted) {
                if (grants != 0) assertGe(call - lastGrant, GAP, "grants closer than the gap");
                lastGrant = call;
                ++grants;
            }
        }
        assertEq(module.recordOf(agent).ticks, 64);
        assertEq(safe.count(), grants, "forwarded exactly the grants");
    }

    function test_reentryDuringForwardingIsRejected() public {
        safe.setReenter(module);
        assertTrue(_act(abi.encodeCall(Target.ping, ())), "outer call granted");
        assertEq(safe.reentryError(), ReflexModule.Reentrant.selector, "inner act rejected by the lock");
        assertEq(target.pings(), 1, "the outer call still ran");
        assertEq(module.recordOf(agent).ticks, 1, "no tick spent by the rejected call");
        assertEq(module.recordOf(address(safe)).ticks, 0);
    }

    function test_peekAgreesWithActAndSpendsNothing() public {
        bytes memory ping = abi.encodeCall(Target.ping, ());
        (bool would, uint256 inputs, uint256 before, uint256 next) = module.peek(agent, address(target), 0, ping);
        assertTrue(would);
        assertEq(inputs, 0x3);
        assertEq(before, 0);
        assertEq(module.recordOf(agent).ticks, 0);
        assertTrue(_act(ping));
        assertEq(module.stateOf(agent), next);
        (would, inputs, before, next) = module.peek(agent, address(target), 0, ping);
        assertFalse(would, "cooldown");
        assertEq(before, module.stateOf(agent));
        assertFalse(_act(ping));
        assertEq(module.stateOf(agent), next);
    }

    /// Gas for the 5-input policy, granted and refused, plus what the code pointer saves
    /// over `bytes` storage for these 299 bytes. Numbers are logged, not asserted.
    function test_gasReport() public {
        bytes memory ping = abi.encodeCall(Target.ping, ());
        vm.startPrank(agent);
        uint256 g = gasleft();
        module.act(address(target), 0, ping);
        uint256 grantedCold = g - gasleft();
        g = gasleft();
        module.act(address(target), 0, ping);
        uint256 refused = g - gasleft();
        module.act(address(target), 0, ping);
        module.act(address(target), 0, ping);
        g = gasleft();
        module.act(address(target), 0, ping);
        uint256 grantedWarm = g - gasleft();
        vm.stopPrank();
        // What the mock itself costs for the same forwarded call (its call log is storage a
        // real Safe does not write), so the module's own share can be read off.
        vm.prank(address(module));
        g = gasleft();
        safe.execTransactionFromModuleReturnData(address(target), 0, ping, ISafe.Operation.Call);
        uint256 mockSafe = g - gasleft();
        console2.log("act granted, tick 1 (cold storage, MockSafe included)", grantedCold);
        console2.log("act refused, tick 2 (cooldown, warm)", refused);
        console2.log("act granted, tick 5 (warm, MockSafe included)", grantedWarm);
        console2.log("MockSafe forward alone, warm", mockSafe);

        BytesHolder holder = new BytesHolder(NETLIST);
        g = gasleft();
        holder.netlist();
        uint256 fromStorage = g - gasleft();
        g = gasleft();
        module.netlist();
        uint256 fromCode = g - gasleft();
        console2.log("netlist read from bytes storage", fromStorage);
        console2.log("netlist read from code pointer", fromCode);
        assertLt(fromCode, fromStorage);
    }
}
