// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {NandMachine} from "../src/NandMachine.sol";
import {ReflexCore} from "../src/ReflexCore.sol";

/// Replays stimulus episodes exported from the Python pipeline through the
/// deployed escape core, tick by tick, and checks every motor output.
contract ReflexCoreTest is Test {
    ReflexCore internal core;
    string internal json;

    function setUp() public {
        json = vm.readFile("test/fixtures/circuits.json");
        bytes memory netlist = vm.parseJsonBytes(json, ".episodes.netlist");
        uint256 nIn = vm.parseJsonUint(json, ".episodes.n_inputs");
        uint256 nOut = vm.parseJsonUint(json, ".episodes.n_outputs");
        uint256 nState = vm.parseJsonUint(json, ".episodes.n_state");
        core = new ReflexCore(new NandMachine(), netlist, nIn, nOut, nState);
        assertEq(core.netlistSha256(), vm.parseJsonBytes32(json, ".episodes.netlist_sha256"));
    }

    function test_episodesReplayExactly() public {
        uint256 count = vm.parseJsonUint(json, ".episodes.count");
        for (uint256 e; e < count; ++e) {
            string memory key = string.concat(".episodes.runs[", vm.toString(e), "]");
            uint256[] memory inputs = vm.parseJsonUintArray(json, string.concat(key, ".inputs"));
            uint256[] memory motor = vm.parseJsonUintArray(json, string.concat(key, ".motor"));
            address fly = address(uint160(0xF1 + e));
            vm.startPrank(fly);
            for (uint256 t; t < inputs.length; ++t) {
                assertEq(core.step(inputs[t]), motor[t], string.concat(key, " tick ", vm.toString(t)));
            }
            vm.stopPrank();
        }
    }

    function test_callersHaveIndependentState() public {
        uint256[] memory inputs = vm.parseJsonUintArray(json, ".episodes.runs[0].inputs");
        address a = address(0xA11CE);
        address b = address(0xB0B);
        vm.prank(a);
        core.step(inputs[inputs.length - 1]);
        uint256 afterA = core.stateOf(a);
        assertEq(core.stateOf(b), 0);
        vm.prank(b);
        core.reset();
        assertEq(core.stateOf(a), afterA);
    }

    function test_rejectsWideInput() public {
        uint256 nIn = core.nIn();
        vm.expectRevert(abi.encodeWithSelector(ReflexCore.InputOutOfRange.selector, uint256(1) << nIn));
        core.step(uint256(1) << nIn);
    }
}
