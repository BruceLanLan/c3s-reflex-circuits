// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

/// @title RealSafe
/// @notice The canonical Safe v1.4.1 deployment, as it already exists on BNB Smart Chain,
///         plus just enough of its interface to drive it from a fork.
///
/// Nothing here is deployed by this repository. The addresses below are Safe's own
/// deterministic v1.4.1 addresses; on a fork of BSC they already hold Safe's code, which
/// is what makes a fork test different from a mock: the Safe that refuses or forwards is
/// the real contract, at the real address, with the real bytecode.
///
///     cast call 0x41675C099F32341bf84BFc5382aF534df5C7461a "VERSION()(string)" \
///       --rpc-url https://bsc-dataseed.bnbchain.org      # -> "1.4.1"
///
/// Checked against the forked bytecode on 2026-09-16 at block 122_240_000 (chain 56):
/// the singleton exposes `execTransactionFromModule`, `execTransactionFromModuleReturnData`,
/// `enableModule` and `setGuard`, and does **not** contain the selector for
/// `checkModuleTransaction(address,uint256,bytes,uint8,address)` — the module guard hook
/// Safe added after 1.4.1. A guard therefore does not see a module's calls on this version,
/// which `ReflexModuleFork.t.sol` also proves by installing a guard that reverts on
/// everything and watching a granted tick go through anyway.
library RealSafe {
    address internal constant SINGLETON = 0x41675C099F32341bf84BFc5382aF534df5C7461a;
    address internal constant SINGLETON_L2 = 0x29fcB43b46531BcA003ddC8FCB67FFE91900C762;
    address internal constant PROXY_FACTORY = 0x4e1DCf7AD4e460CfD30791CCC4F9c8a4f820ec67;
    address internal constant FALLBACK_HANDLER = 0xfd0732Dc9E303f09fCEf3a7388Ad10A83459Ec99;
    /// @dev `multiSend` requires a delegatecall context and reverts when called plainly.
    address internal constant MULTI_SEND = 0x38869bf66a61cF6bDB996A6aE40D5853Fd43B526;
    /// @dev Batches plain calls, made from its own address rather than the caller's.
    address internal constant MULTI_SEND_CALL_ONLY = 0x9641d764fc13c8B624c04430C7356C1C7C8102e2;

    /// @notice The calldata a fresh 1-of-1 Safe is initialised with.
    function setupCalldata(address owner) internal pure returns (bytes memory) {
        address[] memory owners = new address[](1);
        owners[0] = owner;
        return abi.encodeCall(
            ISafeFull.setup, (owners, 1, address(0), "", FALLBACK_HANDLER, address(0), 0, payable(address(0)))
        );
    }

    /// @notice One entry of a MultiSend batch, in MultiSend's packed encoding.
    function packedCall(address to, uint256 value, bytes memory data) internal pure returns (bytes memory) {
        return abi.encodePacked(uint8(0), to, value, data.length, data);
    }

    /// @notice A 1-of-1 owner's signature in Safe's pre-validated form (v = 1): valid when
    ///         the owner is the caller of `execTransaction`, so no key is signed with and
    ///         none is needed.
    function preValidatedSignature(address owner) internal pure returns (bytes memory) {
        return abi.encodePacked(bytes32(uint256(uint160(owner))), bytes32(0), uint8(1));
    }
}

/// @dev Safe v1.4.1, only the parts this proof drives. `ISafe` in src/ deliberately
///      declares the single module entry point; this is the caller's side, kept out of src/.
interface ISafeFull {
    function setup(
        address[] calldata owners,
        uint256 threshold,
        address to,
        bytes calldata data,
        address fallbackHandler,
        address paymentToken,
        uint256 payment,
        address payable paymentReceiver
    ) external;

    function execTransaction(
        address to,
        uint256 value,
        bytes calldata data,
        uint8 operation,
        uint256 safeTxGas,
        uint256 baseGas,
        uint256 gasPrice,
        address gasToken,
        address payable refundReceiver,
        bytes calldata signatures
    ) external payable returns (bool success);

    function execTransactionFromModule(address to, uint256 value, bytes calldata data, uint8 operation)
        external
        returns (bool success);

    function enableModule(address module) external;
    function disableModule(address prevModule, address module) external;
    function addOwnerWithThreshold(address owner, uint256 threshold) external;
    function setGuard(address guard) external;
    function simulateAndRevert(address target, bytes calldata calldataPayload) external;

    function isModuleEnabled(address module) external view returns (bool);
    function isOwner(address owner) external view returns (bool);
    function getOwners() external view returns (address[] memory);
    function getThreshold() external view returns (uint256);
    function nonce() external view returns (uint256);
    function VERSION() external view returns (string memory);
    function getModulesPaginated(address start, uint256 pageSize)
        external
        view
        returns (address[] memory array, address next);
}

interface ISafeProxyFactory {
    function createProxyWithNonce(address singleton, bytes memory initializer, uint256 saltNonce)
        external
        returns (address proxy);
}

interface IMultiSend {
    function multiSend(bytes memory transactions) external payable;
}

/// @dev The fallback handler's `simulate` path, the one place a Safe will `delegatecall`
///      arbitrary code — and it always reverts afterwards, which the fork test asserts.
interface ISimulateTxAccessor {
    function simulate(address to, uint256 value, bytes calldata data, uint8 operation)
        external
        returns (uint256 estimate, bool success, bytes memory returnData);
}
