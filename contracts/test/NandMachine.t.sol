// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {NandMachine} from "../src/NandMachine.sol";

/// Differential test: the EVM evaluator must reproduce, over the complete
/// (input, state) domain of every exported circuit, the step relation computed by
/// the Python bit-sliced evaluator. Fixtures carry a SHA-256 chain over all
/// output and next-state planes, so the full domain is checked without shipping
/// the tables themselves.
contract NandMachineTest is Test {
    NandMachine internal m;
    uint256[8] internal lowPatterns;

    function setUp() public {
        m = new NandMachine();
        for (uint256 i; i < 8; ++i) {
            uint256 pat;
            for (uint256 j; j < 256; ++j) {
                if ((j >> i) & 1 == 1) pat |= uint256(1) << j;
            }
            lowPatterns[i] = pat;
        }
    }

    function _plane(uint256 bit, uint256 base, uint256 blockMask) internal view returns (uint256) {
        if (bit < 8) return lowPatterns[bit] & blockMask;
        return ((base >> bit) & 1 == 1) ? blockMask : 0;
    }

    function _checkCircuit(string memory json, string memory key) internal view {
        bytes memory netlist = vm.parseJsonBytes(json, string.concat(key, ".netlist"));
        uint256 nIn = vm.parseJsonUint(json, string.concat(key, ".n_inputs"));
        uint256 nOut = vm.parseJsonUint(json, string.concat(key, ".n_outputs"));
        uint256 nState = vm.parseJsonUint(json, string.concat(key, ".n_state"));
        bytes32 expected = vm.parseJsonBytes32(json, string.concat(key, ".domain_chain_sha256"));
        uint256 bits = nIn + nState;
        uint256 rows = uint256(1) << bits;
        uint256 blockRows = rows < 256 ? rows : 256;
        uint256 blockMask = blockRows == 256 ? type(uint256).max : (uint256(1) << blockRows) - 1;
        bytes32 h;
        uint256[] memory ins = new uint256[](nIn);
        uint256[] memory st = new uint256[](nState);
        for (uint256 base; base < rows; base += 256) {
            for (uint256 i; i < nIn; ++i) {
                ins[i] = _plane(i, base, blockMask);
            }
            for (uint256 i; i < nState; ++i) {
                st[i] = _plane(nIn + i, base, blockMask);
            }
            (uint256[] memory o, uint256[] memory n) = m.evaluatePlanes(netlist, nIn, nOut, ins, st);
            for (uint256 k; k < o.length; ++k) {
                o[k] &= blockMask;
            }
            for (uint256 k; k < n.length; ++k) {
                n[k] &= blockMask;
            }
            h = sha256(abi.encodePacked(h, o, n));
        }
        assertEq(h, expected, key);
    }

    function test_fullDomainMatchesPythonReference() public view {
        string memory json = vm.readFile("test/fixtures/circuits.json");
        uint256 count = vm.parseJsonUint(json, ".count");
        for (uint256 c; c < count; ++c) {
            _checkCircuit(json, string.concat(".circuits[", vm.toString(c), "]"));
        }
    }

    function test_scalarMatchesPlanes() public view {
        // XOR from four NANDs: signals 2,3 inputs; 4 = nand(2,3); 5 = nand(2,4); 6 = nand(3,4); 7 = nand(5,6)
        bytes memory xorNet = hex"00000002000003" hex"00000002000004" hex"00000003000004" hex"00000005000006";
        for (uint256 r; r < 4; ++r) {
            (uint256 out,) = m.evaluate(xorNet, 2, 1, 0, r, 0);
            assertEq(out, (r & 1) ^ (r >> 1));
        }
    }

    function test_latchReadsPreviousTick() public view {
        // toggle: q = latch(d = x xor q)
        // 3 = latch(d=7); 4 = nand(2,3); 5 = nand(2,4); 6 = nand(3,4); 7 = nand(5,6)
        bytes memory t = hex"01000007" hex"00000002000003" hex"00000002000004" hex"00000003000004" hex"00000005000006";
        uint256 state;
        uint256 out;
        uint8[5] memory pulses = [1, 0, 1, 1, 0];
        uint8[5] memory expectQ = [0, 1, 1, 0, 1];
        for (uint256 i; i < 5; ++i) {
            // output is the last cell (xor), expose q through state instead
            assertEq(state, expectQ[i]);
            (out, state) = m.evaluate(t, 1, 1, 1, pulses[i], state);
        }
    }

    function test_rejectsForwardRead() public {
        vm.expectRevert(abi.encodeWithSelector(NandMachine.ReadsUndefinedSignal.selector, 3, 5));
        m.evaluate(hex"00000005000002", 1, 1, 0, 0, 0);
    }

    function test_rejectsUnknownOpcode() public {
        vm.expectRevert(abi.encodeWithSelector(NandMachine.UnknownOpcode.selector, 0, 7));
        m.evaluate(hex"07000002000002", 1, 1, 0, 0, 0);
    }

    function test_rejectsRef() public {
        vm.expectRevert(abi.encodeWithSelector(NandMachine.ReferenceNotSupported.selector, 0));
        m.evaluate(hex"02", 1, 1, 0, 0, 0);
    }

    function test_rejectsTruncated() public {
        vm.expectRevert(abi.encodeWithSelector(NandMachine.Truncated.selector, 0));
        m.evaluate(hex"000000020000", 1, 1, 0, 0, 0);
    }
}
