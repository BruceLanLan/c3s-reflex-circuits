// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

/// @title NandMachine
/// @notice Stateless evaluator for gate-level netlists in the TapeOut byte layout:
///         NAND  = 0x00 ‖ u24 a ‖ u24 b
///         LATCH = 0x01 ‖ u24 d
///         REF   = 0x02 ‖ ...   (not supported here; flatten first)
///         Signal 0 is constant 0, signal 1 is constant 1, signals 2..2+nIn-1 are the
///         inputs, and every cell defines the next signal. The outputs are the last
///         nOut signals. A LATCH reads the state bit given for it and yields signal d
///         as its next state.
/// @dev    Evaluation is bit-sliced: every signal is a uint256 whose bit j is the
///         signal's value in input pattern j, so one call evaluates up to 256
///         patterns. Scalar evaluation is the same code with patterns in bit 0.
contract NandMachine {
    error UnknownOpcode(uint256 offset, uint256 opcode);
    error ReferenceNotSupported(uint256 offset);
    error Truncated(uint256 offset);
    error ReadsUndefinedSignal(uint256 cellSignal, uint256 readSignal);
    error StateSizeMismatch(uint256 latches, uint256 statePlanes);
    error TooFewCells(uint256 cells, uint256 nOut);
    error SignalOutOfRange(uint256 signal);

    /// @notice Evaluate one tick over up to 256 patterns at once.
    function evaluatePlanes(
        bytes calldata netlist,
        uint256 nIn,
        uint256 nOut,
        uint256[] calldata inputPlanes,
        uint256[] calldata statePlanes
    ) external pure returns (uint256[] memory outputPlanes, uint256[] memory nextStatePlanes) {
        return _run(netlist, nIn, nOut, inputPlanes, statePlanes);
    }

    /// @notice Evaluate one tick for a single pattern. Inputs and state are packed LSB first.
    function evaluate(bytes calldata netlist, uint256 nIn, uint256 nOut, uint256 nState, uint256 inputs, uint256 state)
        external
        pure
        returns (uint256 outputs, uint256 nextState)
    {
        return evaluateScalar(netlist, nIn, nOut, nState, inputs, state);
    }

    function evaluateScalar(bytes calldata netlist, uint256 nIn, uint256 nOut, uint256 nState, uint256 inputs, uint256 state)
        public
        pure
        returns (uint256 outputs, uint256 nextState)
    {
        uint256[] memory ins = new uint256[](nIn);
        for (uint256 i; i < nIn; ++i) {
            ins[i] = (inputs >> i) & 1;
        }
        uint256[] memory st = new uint256[](nState);
        for (uint256 i; i < nState; ++i) {
            st[i] = (state >> i) & 1;
        }
        (uint256[] memory o, uint256[] memory n) = _run(netlist, nIn, nOut, ins, st);
        for (uint256 k; k < o.length; ++k) {
            outputs |= (o[k] & 1) << k;
        }
        for (uint256 k; k < n.length; ++k) {
            nextState |= (n[k] & 1) << k;
        }
    }

    function _run(
        bytes calldata netlist,
        uint256 nIn,
        uint256 nOut,
        uint256[] memory inputPlanes,
        uint256[] memory statePlanes
    ) private pure returns (uint256[] memory outputPlanes, uint256[] memory nextStatePlanes) {
        uint256 len = netlist.length;
        // Every cell is at least 4 bytes, which bounds the signal count.
        uint256[] memory sig = new uint256[](2 + nIn + len / 4);
        sig[1] = type(uint256).max;
        for (uint256 i; i < nIn; ++i) {
            sig[2 + i] = inputPlanes[i];
        }
        uint256[] memory latchD = new uint256[](statePlanes.length);
        uint256 defined = 2 + nIn;
        uint256 latches;
        uint256 p;
        while (p < len) {
            uint256 op = uint8(netlist[p]);
            if (op == 0x00) {
                if (p + 7 > len) revert Truncated(p);
                uint256 a = _u24(netlist, p + 1);
                uint256 b = _u24(netlist, p + 4);
                if (a >= defined) revert ReadsUndefinedSignal(defined, a);
                if (b >= defined) revert ReadsUndefinedSignal(defined, b);
                sig[defined] = ~(sig[a] & sig[b]);
                p += 7;
            } else if (op == 0x01) {
                if (p + 4 > len) revert Truncated(p);
                if (latches >= statePlanes.length) revert StateSizeMismatch(latches + 1, statePlanes.length);
                sig[defined] = statePlanes[latches];
                latchD[latches] = _u24(netlist, p + 1);
                ++latches;
                p += 4;
            } else if (op == 0x02) {
                revert ReferenceNotSupported(p);
            } else {
                revert UnknownOpcode(p, op);
            }
            ++defined;
        }
        if (latches != statePlanes.length) revert StateSizeMismatch(latches, statePlanes.length);
        if (defined - 2 - nIn < nOut) revert TooFewCells(defined - 2 - nIn, nOut);
        outputPlanes = new uint256[](nOut);
        for (uint256 k; k < nOut; ++k) {
            outputPlanes[k] = sig[defined - nOut + k];
        }
        nextStatePlanes = new uint256[](latches);
        for (uint256 k; k < latches; ++k) {
            uint256 d = latchD[k];
            if (d >= defined) revert SignalOutOfRange(d);
            nextStatePlanes[k] = sig[d];
        }
    }

    function _u24(bytes calldata data, uint256 offset) private pure returns (uint256 v) {
        v = (uint256(uint8(data[offset])) << 16) | (uint256(uint8(data[offset + 1])) << 8) | uint256(uint8(data[offset + 2]));
    }
}
