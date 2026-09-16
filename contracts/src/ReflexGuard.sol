// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {NandMachine} from "./NandMachine.sol";
import {ReflexCore} from "./ReflexCore.sol";

/// @title ReflexGuard
/// @notice Uses the verified escape core as an authoriser: a caller gets one
///         authorisation only on a tick where the circuit commands a takeoff. The
///         guard says *when* an action may happen; it never says what the action is,
///         and it moves no value.
///
/// @dev Evaluates through the same `NandMachine` and the same netlist bytes as the
///      `ReflexCore` it is constructed from — the constructor checks the SHA-256 — but
///      keeps its own latch state per caller, because `ReflexCore` keys state by
///      `msg.sender` and every caller of a shared guard would otherwise share one
///      state. The circuit is therefore exactly the verified one.
///
/// Inherited from the proofs in docs/PROPERTIES.md, for any inputs whatsoever:
///  * P1 (proven on every reachable state, two independent engines): no takeoff occurs
///    within 7 ticks of a takeoff. One call is one tick, so **at most one
///    authorisation in any 8 consecutive calls** by the same caller.
///  * P2 (proven): a long-mode authorisation (motor 3) is immediately preceded by four
///    consecutive raising ticks, so it costs the caller four earlier calls.
///
/// NOT inherited. These are ordinary code, covered by tests only, and must not be
/// described as verified:
///  * any binding to time. Eight calls fit inside one transaction, so the proven limit
///    bounds calls, not wall-clock. `minBlocks` adds a block-spacing rule of this
///    contract's own making.
///  * any claim that the 17 input bits are real sensor readings. The caller builds
///    them, so a caller who wants a long-mode authorisation can simply supply four
///    raising ticks: P2 is a commitment cost, not a security property.
///  * whatever consumes `Authorised`. The actuator is outside this contract, and no
///    proof here says anything about it.
contract ReflexGuard {
    uint256 internal constant MOTOR_RAISING = 1;
    uint256 internal constant MOTOR_SHORT = 2;
    uint256 internal constant MOTOR_LONG = 3;

    struct Record {
        uint256 state; // the caller's latch state in the circuit
        uint64 calls;
        uint64 authorisations;
        uint64 lastAuthorisedBlock;
    }

    NandMachine public immutable machine;
    ReflexCore public immutable core;
    uint256 public immutable nIn;
    uint256 public immutable nOut;
    uint256 public immutable nState;
    /// @notice Blocks that must pass between two authorisations for one caller; 0 disables.
    uint256 public immutable minBlocks;
    bytes public netlist;

    mapping(address => Record) internal records;

    event Authorised(address indexed caller, uint256 motor, uint256 call, uint256 authorisations);
    event Tick(address indexed caller, uint256 inputs, uint256 motor, uint256 nextState);

    error InputOutOfRange(uint256 inputs);
    error NetlistMismatch();
    error TooSoon(uint256 blocksToWait);

    constructor(ReflexCore core_, uint256 minBlocks_) {
        core = core_;
        machine = core_.machine();
        netlist = core_.netlist();
        if (sha256(netlist) != core_.netlistSha256()) revert NetlistMismatch();
        nIn = core_.nIn();
        nOut = core_.nOut();
        nState = core_.nState();
        minBlocks = minBlocks_;
    }

    function recordOf(address who) external view returns (Record memory) {
        return records[who];
    }

    /// @notice One tick for the caller. `authorised` is true exactly when the circuit
    ///         commands a takeoff on this tick.
    function request(uint256 inputs) external returns (uint256 motor, bool authorised) {
        if (inputs >> nIn != 0) revert InputOutOfRange(inputs);
        Record memory r = records[msg.sender];
        uint256 nextState;
        (motor, nextState) = machine.evaluate(netlist, nIn, nOut, nState, inputs, r.state);
        r.state = nextState;
        r.calls += 1;
        authorised = motor == MOTOR_SHORT || motor == MOTOR_LONG;
        if (authorised) {
            if (minBlocks != 0 && r.authorisations != 0 && block.number < r.lastAuthorisedBlock + minBlocks) {
                revert TooSoon(r.lastAuthorisedBlock + minBlocks - block.number);
            }
            r.authorisations += 1;
            r.lastAuthorisedBlock = uint64(block.number);
            emit Authorised(msg.sender, motor, r.calls, r.authorisations);
        }
        records[msg.sender] = r;
        emit Tick(msg.sender, inputs, motor, nextState);
    }

    /// @notice What `request` would return, without changing anything.
    function peek(address who, uint256 inputs) external view returns (uint256 motor, bool authorised) {
        if (inputs >> nIn != 0) revert InputOutOfRange(inputs);
        (motor,) = machine.evaluate(netlist, nIn, nOut, nState, inputs, records[who].state);
        authorised = motor == MOTOR_SHORT || motor == MOTOR_LONG;
    }

    /// @notice Clears the caller's own circuit state and counters.
    function reset() external {
        delete records[msg.sender];
    }
}
