// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {Script} from "forge-std/Script.sol";
import {console2} from "forge-std/console2.sol";
import {NandMachine} from "../src/NandMachine.sol";
import {ReflexCore} from "../src/ReflexCore.sol";

/// Deploys the evaluator and one escape core, for anyone who would rather have the
/// circuit on chain under their own address than evaluate it through a read-only
/// `eth_call` state override (scripts/verify_onchain.py, which deploys nothing).
///
/// The netlist comes from the committed fixtures, so what gets deployed is exactly
/// the circuit this repository verifies; the constructor recomputes its SHA-256 and
/// `netlistSha256()` must equal the manifest's.
///
///     cd contracts
///     forge script script/DeployReflexCore.s.sol --rpc-url <url> --broadcast
///
/// with PRIVATE_KEY set, or `--ledger`, or any other signer Foundry supports. This
/// repository never holds a key: the transaction is signed on the machine that runs
/// this command.
contract DeployReflexCore is Script {
    function run() external returns (NandMachine machine, ReflexCore core) {
        string memory json = vm.readFile("test/fixtures/circuits.json");
        bytes memory netlist = vm.parseJsonBytes(json, ".episodes.netlist");
        uint256 nIn = vm.parseJsonUint(json, ".episodes.n_inputs");
        uint256 nOut = vm.parseJsonUint(json, ".episodes.n_outputs");
        uint256 nState = vm.parseJsonUint(json, ".episodes.n_state");
        bytes32 want = vm.parseJsonBytes32(json, ".episodes.netlist_sha256");

        vm.startBroadcast();
        machine = new NandMachine();
        core = new ReflexCore(machine, netlist, nIn, nOut, nState);
        vm.stopBroadcast();

        require(core.netlistSha256() == want, "deployed netlist is not the committed one");
        console2.log("NandMachine", address(machine));
        console2.log("ReflexCore ", address(core));
    }
}
