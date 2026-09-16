// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {NandMachine} from "../src/NandMachine.sol";
import {ReflexCore} from "../src/ReflexCore.sol";
import {ReflexGuard} from "../src/ReflexGuard.sol";

/// The guard must authorise exactly when the circuit commands a takeoff, and the
/// proven refractory property must show up on chain as a limit on calls: no two
/// authorisations within 8 calls by one caller, whatever inputs are supplied. The
/// block-spacing rule is the guard's own code, so it is tested as such.
contract ReflexGuardTest is Test {
    ReflexCore internal core;
    ReflexGuard internal guard;
    string internal json;
    uint256 internal nIn;

    function setUp() public {
        json = vm.readFile("test/fixtures/circuits.json");
        bytes memory netlist = vm.parseJsonBytes(json, ".episodes.netlist");
        nIn = vm.parseJsonUint(json, ".episodes.n_inputs");
        core = new ReflexCore(
            new NandMachine(), netlist, nIn, vm.parseJsonUint(json, ".episodes.n_outputs"), vm.parseJsonUint(json, ".episodes.n_state")
        );
        guard = new ReflexGuard(core, 0);
    }

    function _episode(uint256 e) internal view returns (uint256[] memory inputs, uint256[] memory motor) {
        string memory key = string.concat(".episodes.runs[", vm.toString(e), "]");
        inputs = vm.parseJsonUintArray(json, string.concat(key, ".inputs"));
        motor = vm.parseJsonUintArray(json, string.concat(key, ".motor"));
    }

    function test_deploysWithTheCommittedNetlist() public view {
        assertEq(guard.netlist(), core.netlist());
        assertEq(guard.nIn(), core.nIn());
        assertEq(address(guard.machine()), address(core.machine()));
    }

    function test_authorisesExactlyOnTakeoffTicks() public {
        uint256 count = vm.parseJsonUint(json, ".episodes.count");
        for (uint256 e; e < count; ++e) {
            (uint256[] memory inputs, uint256[] memory motor) = _episode(e);
            address fly = address(uint160(0xE0 + e));
            vm.startPrank(fly);
            for (uint256 t; t < inputs.length; ++t) {
                (uint256 got, bool authorised) = guard.request(inputs[t]);
                assertEq(got, motor[t], "motor");
                assertEq(authorised, motor[t] == 2 || motor[t] == 3, "authorised");
            }
            vm.stopPrank();
        }
    }

    /// The on-chain echo of P1: whatever the caller feeds it, authorisations cannot be
    /// closer together than 8 calls.
    function testFuzz_noTwoAuthorisationsWithinEightCalls(uint256 seed) public {
        address fly = address(0xF11E);
        uint256 mask = (uint256(1) << nIn) - 1;
        uint256 lastAuthorisedCall;
        uint256 authorisations;
        vm.startPrank(fly);
        for (uint256 call = 1; call <= 48; ++call) {
            seed = uint256(keccak256(abi.encode(seed, call)));
            (, bool authorised) = guard.request(seed & mask);
            if (authorised) {
                if (authorisations != 0) {
                    assertGe(call - lastAuthorisedCall, 8, "authorisations closer than 8 calls");
                }
                lastAuthorisedCall = call;
                ++authorisations;
            }
        }
        vm.stopPrank();
        ReflexGuard.Record memory r = guard.recordOf(fly);
        assertEq(r.calls, 48);
        assertEq(r.authorisations, authorisations);
    }

    /// P2 on chain, on the episode that reaches a long-mode takeoff: the four ticks
    /// before it are raising ticks.
    function test_longModeFollowsFourRaisingTicks() public {
        uint256 count = vm.parseJsonUint(json, ".episodes.count");
        uint256 checked;
        for (uint256 e; e < count; ++e) {
            (uint256[] memory inputs, uint256[] memory motor) = _episode(e);
            if (motor[motor.length - 1] != 3) continue;
            address fly = address(uint160(0xD0 + e));
            vm.startPrank(fly);
            for (uint256 t; t < inputs.length; ++t) {
                (uint256 got,) = guard.request(inputs[t]);
                if (got == 3) {
                    assertGe(t, 4, "long mode too early to have four raising ticks");
                    for (uint256 k = 1; k <= 4; ++k) {
                        assertEq(motor[t - k], 1, "tick before a long-mode takeoff is not raising");
                    }
                    ++checked;
                }
            }
            vm.stopPrank();
        }
        assertGt(checked, 0, "no long-mode takeoff in the fixtures");
    }

    function test_callersKeepSeparateStateAndCounters() public {
        (uint256[] memory inputs,) = _episode(0);
        address a = address(0xA11CE);
        address b = address(0xB0B);
        vm.prank(a);
        guard.request(inputs[0]);
        assertEq(guard.recordOf(a).calls, 1);
        assertEq(guard.recordOf(b).calls, 0);
        assertEq(guard.recordOf(b).state, 0);
        vm.prank(a);
        guard.reset();
        assertEq(guard.recordOf(a).calls, 0);
    }

    /// The block-spacing rule is this contract's own code, not something the circuit
    /// proves. Here it meets P1: the circuit refuses a second takeoff for seven ticks,
    /// and then the block rule refuses it again until the blocks have passed.
    function test_minBlocksRejectsASecondAuthorisationTooSoon() public {
        ReflexGuard spaced = new ReflexGuard(core, 100);
        uint256 count = vm.parseJsonUint(json, ".episodes.count");
        address fly = address(0xBEEF);
        for (uint256 e; e < count; ++e) {
            (uint256[] memory inputs, uint256[] memory motor) = _episode(e);
            if (motor[motor.length - 1] < 2) continue; // an episode that reaches a takeoff

            vm.startPrank(fly);
            for (uint256 t; t < inputs.length; ++t) spaced.request(inputs[t]);
            assertEq(spaced.recordOf(fly).authorisations, 1, "the episode should authorise once");

            // Keep asking with the takeoff tick's own inputs. The next seven calls
            // cannot authorise (P1); the first call that would authorise is refused by
            // the block rule, which also says how long to wait.
            uint256 takeoff = inputs[inputs.length - 1];
            bool refused;
            for (uint256 k; k < 24 && !refused; ++k) {
                try spaced.request(takeoff) returns (uint256, bool authorised) {
                    if (k < 7) assertFalse(authorised, "a takeoff within seven ticks of one");
                } catch (bytes memory err) {
                    assertEq(bytes4(err), ReflexGuard.TooSoon.selector, "unexpected revert");
                    refused = true;
                }
            }
            assertTrue(refused, "the block rule never refused a second authorisation");
            vm.roll(block.number + 100);
            (, bool ok) = spaced.request(takeoff);
            assertTrue(ok, "authorised once the blocks have passed");
            vm.stopPrank();
            return;
        }
        revert("no episode in the fixtures reaches a takeoff");
    }

    function test_rejectsWideInput() public {
        vm.expectRevert(abi.encodeWithSelector(ReflexGuard.InputOutOfRange.selector, uint256(1) << nIn));
        guard.request(uint256(1) << nIn);
    }
}
