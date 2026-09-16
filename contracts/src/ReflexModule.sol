// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {NandMachine} from "./NandMachine.sol";
import {ISafe} from "./ISafe.sol";

/// @title ReflexModule
/// @notice A Safe module that lets an agent act through a human-owned Safe only on the
///         ticks where a compiled, verified policy circuit (c3s/policy.py) grants.
///
/// The arrangement:
///
///     agent ──act(to,value,data)──▶ ReflexModule ──inputs──▶ NandMachine ──grant──▶ Safe.execTransactionFromModule
///                                        │
///                             supervisor writes blocked / confirm / heartbeat / irreversible
///
/// The agent is *not* an owner of the Safe. It reaches the Safe only through `act`, and
/// it supplies no input bit: every bit the circuit reads is derived here from the call
/// itself and from storage that only the supervisor can write. `request` and `intent` are
/// simply high on every call — the agent calling *is* the request — so any rule that rests
/// on them alone is a cost the agent pays by calling, exactly as docs/AGENT.md says.
///
/// Input word, LSB first, in the order c3s/policy.py fixes:
///
///     bit 0  request       always 1
///     bit 1  intent        always 1
///     bit 2  blocked       block.number < blockedUntil[agent]                 (supervisor)
///     bit 3  confirm       confirmations[key], spent by this tick               (supervisor)
///     then, only for the optional inputs the policy was compiled with, in this order:
///            irreversible  irreversibleSelectors[bytes4(data)] || value > 0    (supervisor keeps the list)
///            failed        the agent's most recent forwarded call reverted     (recorded here)
///            heartbeat     block.number < heartbeatUntil[agent]                (supervisor)
///            confirm_b     confirmationsB[key], spent by this tick              (supervisorB)
///
///     key = keccak256(abi.encode(safe, agent, to, value, keccak256(data)))
///
/// The next latch state is stored *before* the Safe is called, the whole module is locked
/// against re-entry while it forwards, and the operation is always `Call`: there is no
/// parameter through which a `DelegateCall` (and so a MultiSend batch counted as one
/// tick) could be requested. Calls whose target is the Safe itself or this module revert
/// outright — a module call to the Safe is an authenticated self-call, and one granted
/// tick of `addOwnerWithThreshold(agent)` would end the boundary.
///
/// A refused call does not revert: `act` returns `granted = false`, emits `Decision`, and
/// the tick has been spent. Only hard errors revert (forbidden target, re-entry, a
/// malformed constructor), and those spend no tick.
///
/// What is verified and what is not. The circuit's rules hold for every input sequence
/// (docs/PROPERTIES.md, `Policy.properties`), so whatever this module feeds it, no two
/// grants come closer than the policy's gap, nothing is granted while `blocked` is high,
/// and so on. Everything that turns a call into bits is ordinary code with ordinary
/// tests: the selector list is the supervisor's judgement (an unlisted selector counts as
/// reversible — list `approve`, `permit`, `setApprovalForAll` and their kin); a
/// confirmation is bound to one exact call by this module, but the circuit's own `token`
/// latch is bound to nothing, so if the confirmed call is refused for another reason
/// (cooldown, block) the token stays armed and a different irreversible call may be
/// granted next — `peek` before `confirm`; `failed` stays high until the agent's next
/// call is forwarded, so under a cooldown a single failure can trip a breaker whose
/// threshold is below the gap, and `resetAgent` is the recovery. Ticks are calls, not
/// time. None of this is a proof; the Safe trusts an enabled module completely, and the
/// owner key that can `disableModule` is the real boundary.
///
/// The netlist is fixed at construction and hash-checked. There is no setter: new rules
/// mean a new module that the Safe owner enables and an old one they disable.
contract ReflexModule {
    // Positions in the input word.
    uint256 internal constant BIT_REQUEST = 0;
    uint256 internal constant BIT_INTENT = 1;
    uint256 internal constant BIT_BLOCKED = 2;
    uint256 internal constant BIT_CONFIRM = 3;
    // Optional inputs, as bits of `optionalInputs`, in the order the policy appends them.
    uint8 public constant OPT_IRREVERSIBLE = 1 << 0;
    uint8 public constant OPT_FAILED = 1 << 1;
    uint8 public constant OPT_HEARTBEAT = 1 << 2;
    uint8 public constant OPT_CONFIRM_B = 1 << 3;

    uint256 internal constant N_OUT = 1;
    // A runtime-code contract cannot exceed EIP-170's 24,576 bytes; one byte is the STOP prefix.
    uint256 internal constant MAX_NETLIST = 24_575;

    struct Record {
        uint256 state; // the agent's latch state in the circuit
        uint64 ticks; // calls to `act` so far
        uint64 blockedUntil; // blocked while block.number < blockedUntil
        uint64 heartbeatUntil; // heartbeat high while block.number < heartbeatUntil
        bool lastFailed; // the most recent forwarded call reverted
    }

    NandMachine public immutable machine;
    ISafe public immutable safe;
    address public immutable supervisor;
    /// @notice Writes `confirm_b`. Fixed at construction so that one key cannot appoint the other.
    address public immutable supervisorB;
    uint256 public immutable nIn;
    uint256 public immutable nState;
    uint8 public immutable optionalInputs;
    bytes32 public immutable netlistSha256;
    /// @dev The netlist lives in the runtime code of a contract deployed by the constructor
    ///      (SSTORE2 shape: `0x00 ‖ netlist`, read with EXTCODECOPY from offset 1), not in
    ///      `bytes` storage. Reading n bytes of code costs 2600 (cold) + 3 per word; reading
    ///      the same bytes from storage costs 2100 per 32-byte word. For the 299-byte policy
    ///      in the tests, reading back through an external call measures 9,216 gas from code
    ///      against 25,416 from `bytes` storage (test_gasReport); the 1,235-byte escape core
    ///      would pay ~88k from storage, so the saving grows with the circuit. The cost is
    ///      a one-time contract creation at construction.
    address public immutable netlistPointer;
    uint256 public immutable netlistLength;

    mapping(address => Record) internal records;
    mapping(bytes32 => bool) public confirmations;
    mapping(bytes32 => bool) public confirmationsB;
    mapping(bytes4 => bool) public irreversibleSelectors;

    uint256 private _entered = 1;

    event Decision(
        address indexed agent, address indexed to, uint256 value, bytes4 selector, uint256 inputs, bool granted, uint64 tick
    );
    event Forwarded(address indexed agent, bool success);
    event Blocked(address indexed agent, uint64 untilBlock);
    event Heartbeat(address indexed agent, uint64 untilBlock);
    event Confirmed(bytes32 indexed key, bool second, bool on);
    event IrreversibleSelector(bytes4 indexed selector, bool on);
    event AgentReset(address indexed agent);

    error NetlistMismatch();
    error NetlistTooLong(uint256 length);
    error PortCountsOutOfRange(uint256 nIn, uint256 nState);
    error InputLayoutMismatch(uint256 nIn, uint8 optionalInputs);
    error PointerDeploymentFailed();
    error ZeroAddress();
    error NotSupervisor();
    error ForbiddenTarget(address to);
    error Reentrant();

    modifier onlySupervisor() {
        if (msg.sender != supervisor) revert NotSupervisor();
        _;
    }

    /// @param machine_        the stateless evaluator
    /// @param netlist_        the compiled policy, TapeOut byte layout (c3s.netlist.to_bytes)
    /// @param expectedSha256  sha256 of `netlist_`, from the same export
    /// @param nIn_            the policy's input count, 4..8
    /// @param optionalInputs_ which optional inputs the policy reads (OPT_* bits); the
    ///                        policy appends them in fixed order, so this fixes the layout
    ///                        and must agree with nIn_. AGENT.md's spend policy, for one,
    ///                        has six inputs whose sixth is confirm_b, not failed.
    /// @param nState_         the policy's latch count, ≤ 32
    /// @param safe_           the Safe this module will be enabled on
    /// @param supervisor_     writes blocked, confirm, heartbeat, the selector list, resets
    /// @param supervisorB_    writes confirm_b; address(0) unless the policy reads it
    constructor(
        NandMachine machine_,
        bytes memory netlist_,
        bytes32 expectedSha256,
        uint256 nIn_,
        uint8 optionalInputs_,
        uint256 nState_,
        ISafe safe_,
        address supervisor_,
        address supervisorB_
    ) {
        if (sha256(netlist_) != expectedSha256) revert NetlistMismatch();
        if (netlist_.length > MAX_NETLIST) revert NetlistTooLong(netlist_.length);
        if (nIn_ < 4 || nIn_ > 8 || nState_ > 32) revert PortCountsOutOfRange(nIn_, nState_);
        if (optionalInputs_ > 0x0F || 4 + _popcount(optionalInputs_) != nIn_) {
            revert InputLayoutMismatch(nIn_, optionalInputs_);
        }
        if (address(machine_) == address(0) || address(safe_) == address(0) || supervisor_ == address(0)) {
            revert ZeroAddress();
        }
        if ((optionalInputs_ & OPT_CONFIRM_B) != 0 && supervisorB_ == address(0)) revert ZeroAddress();

        machine = machine_;
        safe = safe_;
        supervisor = supervisor_;
        supervisorB = supervisorB_;
        nIn = nIn_;
        nState = nState_;
        optionalInputs = optionalInputs_;
        netlistSha256 = expectedSha256;
        netlistLength = netlist_.length;
        netlistPointer = _store(netlist_);

        // The stored copy must read back as what was checked, and the netlist must be
        // well formed for these port counts (this reverts inside NandMachine otherwise).
        bytes memory back = _netlist();
        if (keccak256(back) != keccak256(netlist_)) revert NetlistMismatch();
        machine_.evaluate(back, nIn_, N_OUT, nState_, 0, 0);
    }

    // -- the gated path -------------------------------------------------------

    /// @notice One tick for `msg.sender`. Forwards `(to, value, data)` to the Safe as a
    ///         plain call exactly when the circuit grants; otherwise returns `false`
    ///         without reverting. Either way the tick is spent.
    function act(address to, uint256 value, bytes calldata data) external returns (bool granted, bytes memory result) {
        if (_entered != 1) revert Reentrant();
        if (to == address(safe) || to == address(this)) revert ForbiddenTarget(to);
        _entered = 2;

        address agent = msg.sender;
        Record memory r = records[agent];
        bytes32 key = confirmationKey(agent, to, value, data);
        uint256 inputs = _inputs(r, key, value, data);
        uint256 nextState;
        uint256 out;
        (out, nextState) = machine.evaluate(_netlist(), nIn, N_OUT, nState, inputs, r.state);
        granted = out & 1 == 1;

        // Commit the tick before anything external runs: state, counter, spent confirmations.
        r.state = nextState;
        r.ticks += 1;
        if ((inputs >> BIT_CONFIRM) & 1 == 1) delete confirmations[key];
        if (_has(OPT_CONFIRM_B) && (inputs >> _position(OPT_CONFIRM_B)) & 1 == 1) delete confirmationsB[key];
        records[agent] = r;
        emit Decision(agent, to, value, bytes4(data), inputs, granted, r.ticks);

        if (granted) {
            // A Safe returns `false` when the inner call reverts and reverts itself only when
            // this module is not enabled on it. Both are the agent's failure to record, and
            // neither may undo the tick committed above.
            bool success;
            try safe.execTransactionFromModuleReturnData(to, value, data, ISafe.Operation.Call) returns (
                bool ok, bytes memory returned
            ) {
                success = ok;
                result = returned;
            } catch (bytes memory err) {
                result = err;
            }
            records[agent].lastFailed = !success;
            emit Forwarded(agent, success);
        }
        _entered = 1;
    }

    /// @notice What `act` would decide for `agent` right now, without spending anything.
    ///         `inputs` is the word the circuit would read, `state` the agent's latches
    ///         before the tick and `nextState` after it.
    function peek(address agent, address to, uint256 value, bytes calldata data)
        external
        view
        returns (bool wouldGrant, uint256 inputs, uint256 state, uint256 nextState)
    {
        if (to == address(safe) || to == address(this)) revert ForbiddenTarget(to);
        Record memory r = records[agent];
        inputs = _inputs(r, confirmationKey(agent, to, value, data), value, data);
        uint256 out;
        (out, nextState) = machine.evaluate(_netlist(), nIn, N_OUT, nState, inputs, r.state);
        wouldGrant = out & 1 == 1;
        state = r.state;
    }

    /// @notice The key a supervisor confirms to raise `confirm` on exactly this call.
    function confirmationKey(address agent, address to, uint256 value, bytes calldata data) public view returns (bytes32) {
        return keccak256(abi.encode(address(safe), agent, to, value, keccak256(data)));
    }

    // -- the supervisor's side ------------------------------------------------

    /// @notice `blocked` is high for `agent` while `block.number < untilBlock`.
    function blockAgent(address agent, uint64 untilBlock) external onlySupervisor {
        records[agent].blockedUntil = untilBlock;
        emit Blocked(agent, untilBlock);
    }

    /// @notice `heartbeat` is high for `agent` while `block.number < untilBlock`.
    function heartbeat(address agent, uint64 untilBlock) external onlySupervisor {
        records[agent].heartbeatUntil = untilBlock;
        emit Heartbeat(agent, untilBlock);
    }

    /// @notice Raise `confirm` on the one call whose key this is. Spent by that call's tick.
    function confirm(bytes32 key) external onlySupervisor {
        confirmations[key] = true;
        emit Confirmed(key, false, true);
    }

    /// @notice Withdraw a confirmation the agent has not spent yet.
    function revoke(bytes32 key) external onlySupervisor {
        delete confirmations[key];
        emit Confirmed(key, false, false);
    }

    /// @notice The second key, for policies compiled with `two_key`.
    function confirmB(bytes32 key) external {
        if (msg.sender != supervisorB) revert NotSupervisor();
        confirmationsB[key] = true;
        emit Confirmed(key, true, true);
    }

    function revokeB(bytes32 key) external {
        if (msg.sender != supervisorB) revert NotSupervisor();
        delete confirmationsB[key];
        emit Confirmed(key, true, false);
    }

    /// @notice Selectors that count as irreversible. ETH value always counts, listed or not.
    function setIrreversible(bytes4 selector, bool on) external onlySupervisor {
        irreversibleSelectors[selector] = on;
        emit IrreversibleSelector(selector, on);
    }

    /// @notice Clears everything recorded about `agent`: latches, tick count, block,
    ///         heartbeat and the failure flag. The agent has no way to do this itself.
    function resetAgent(address agent) external onlySupervisor {
        delete records[agent];
        emit AgentReset(agent);
    }

    // -- reading ----------------------------------------------------------------

    function recordOf(address agent) external view returns (Record memory) {
        return records[agent];
    }

    function stateOf(address agent) external view returns (uint256) {
        return records[agent].state;
    }

    function blockedUntil(address agent) external view returns (uint64) {
        return records[agent].blockedUntil;
    }

    function heartbeatUntil(address agent) external view returns (uint64) {
        return records[agent].heartbeatUntil;
    }

    function lastFailed(address agent) external view returns (bool) {
        return records[agent].lastFailed;
    }

    /// @notice The netlist bytes, read back from code.
    function netlist() external view returns (bytes memory) {
        return _netlist();
    }

    /// @notice Which input position an optional input occupies, or revert if the policy
    ///         does not read it.
    function inputPosition(uint8 optional) external view returns (uint256) {
        if (!_has(optional)) revert InputLayoutMismatch(nIn, optionalInputs);
        return _position(optional);
    }

    // -- internals --------------------------------------------------------------

    function _inputs(Record memory r, bytes32 key, uint256 value, bytes calldata data)
        internal
        view
        returns (uint256 inputs)
    {
        inputs = (1 << BIT_REQUEST) | (1 << BIT_INTENT);
        if (block.number < r.blockedUntil) inputs |= 1 << BIT_BLOCKED;
        if (confirmations[key]) inputs |= 1 << BIT_CONFIRM;
        if (_has(OPT_IRREVERSIBLE) && (value > 0 || irreversibleSelectors[bytes4(data)])) {
            inputs |= 1 << _position(OPT_IRREVERSIBLE);
        }
        if (_has(OPT_FAILED) && r.lastFailed) inputs |= 1 << _position(OPT_FAILED);
        if (_has(OPT_HEARTBEAT) && block.number < r.heartbeatUntil) inputs |= 1 << _position(OPT_HEARTBEAT);
        if (_has(OPT_CONFIRM_B) && confirmationsB[key]) inputs |= 1 << _position(OPT_CONFIRM_B);
    }

    function _has(uint8 optional) internal view returns (bool) {
        return (optionalInputs & optional) != 0;
    }

    /// @dev Optional inputs sit after the four fixed ones, in OPT_* order, skipping absent ones.
    function _position(uint8 optional) internal view returns (uint256) {
        return 4 + _popcount(optionalInputs & (optional - 1));
    }

    function _popcount(uint8 x) internal pure returns (uint256 n) {
        while (x != 0) {
            n += x & 1;
            x >>= 1;
        }
    }

    /// @dev Deploys `0x00 ‖ data` as the runtime code of a fresh contract. The init code is
    ///      PUSH4 len DUP1 PUSH1 0x0e PUSH1 0 CODECOPY PUSH1 0 RETURN, then the code itself;
    ///      the leading STOP keeps the pointer from being callable as anything.
    function _store(bytes memory data) internal returns (address pointer) {
        bytes memory code = abi.encodePacked(hex"00", data);
        // casting to 'uint32' is safe because the constructor bounds data.length by MAX_NETLIST
        // forge-lint: disable-next-line(unsafe-typecast)
        bytes memory initcode = abi.encodePacked(hex"63", uint32(code.length), hex"80600e6000396000f3", code);
        assembly ("memory-safe") {
            pointer := create(0, add(initcode, 0x20), mload(initcode))
        }
        if (pointer == address(0)) revert PointerDeploymentFailed();
    }

    function _netlist() internal view returns (bytes memory out) {
        address p = netlistPointer;
        uint256 len = netlistLength;
        out = new bytes(len);
        assembly ("memory-safe") {
            extcodecopy(p, add(out, 0x20), 1, len)
        }
    }
}
