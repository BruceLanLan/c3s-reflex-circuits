// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

/// @title ISafe
/// @notice The one Safe entry point a module needs. Declared here rather than pulled from
///         safe-contracts so this repository carries no dependency on it; the selector is
///         the same one Safe v1.3.0 through v1.5.0 expose
///         (`execTransactionFromModuleReturnData(address,uint256,bytes,uint8)`).
interface ISafe {
    /// Mirrors `Enum.Operation` in safe-contracts. Only `Call` is ever used here.
    enum Operation {
        Call,
        DelegateCall
    }

    function execTransactionFromModuleReturnData(address to, uint256 value, bytes memory data, Operation operation)
        external
        returns (bool success, bytes memory returnData);
}
