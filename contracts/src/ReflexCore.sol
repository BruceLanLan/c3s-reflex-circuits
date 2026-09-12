// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {NandMachine} from "./NandMachine.sol";

/// @title ReflexCore
/// @notice A fixed sequential netlist with one latch state per caller. Each call to
///         `step` is one tick: the caller supplies the sensory inputs, the machine
///         returns its motor outputs and keeps its latches for the caller's next tick.
/// @dev    The netlist, its port counts and its SHA-256 are fixed at deployment.
///         There is no owner, no upgrade path and no way to change the circuit.
contract ReflexCore {
    NandMachine public immutable machine;
    uint256 public immutable nIn;
    uint256 public immutable nOut;
    uint256 public immutable nState;
    bytes32 public immutable netlistSha256;
    bytes public netlist;

    mapping(address => uint256) public stateOf;

    event Tick(address indexed caller, uint256 inputs, uint256 outputs, uint256 nextState);

    error InputOutOfRange(uint256 inputs);

    constructor(NandMachine machine_, bytes memory netlist_, uint256 nIn_, uint256 nOut_, uint256 nState_) {
        machine = machine_;
        netlist = netlist_;
        nIn = nIn_;
        nOut = nOut_;
        nState = nState_;
        netlistSha256 = sha256(netlist_);
        // Reverts if the netlist is malformed or the port counts do not fit it.
        machine_.evaluate(netlist_, nIn_, nOut_, nState_, 0, 0);
    }

    /// @notice What `step` would return for `who`, without changing state.
    function peek(address who, uint256 inputs) public view returns (uint256 outputs, uint256 nextState) {
        if (inputs >> nIn != 0) revert InputOutOfRange(inputs);
        return machine.evaluate(netlist, nIn, nOut, nState, inputs, stateOf[who]);
    }

    function step(uint256 inputs) external returns (uint256 outputs) {
        uint256 nextState;
        (outputs, nextState) = peek(msg.sender, inputs);
        stateOf[msg.sender] = nextState;
        emit Tick(msg.sender, inputs, outputs, nextState);
    }

    function reset() external {
        delete stateOf[msg.sender];
    }
}
