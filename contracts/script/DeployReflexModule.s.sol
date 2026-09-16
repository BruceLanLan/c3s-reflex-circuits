// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {Script} from "forge-std/Script.sol";
import {console2} from "forge-std/console2.sol";
import {NandMachine} from "../src/NandMachine.sol";
import {ISafe} from "../src/ISafe.sol";
import {ReflexModule} from "../src/ReflexModule.sol";

/// Deploys the evaluator and one `ReflexModule` for a compiled policy, for the owner of a
/// Safe who wants a verified circuit between an agent and that Safe. Everything the
/// module is built from comes from the environment, so the same script serves any policy:
///
///     NETLIST          hex bytes, the output of c3s.netlist.to_bytes(policy.build())
///     NETLIST_SHA256   0x-prefixed sha256 of those bytes, from the same export
///     N_IN             len(policy.input_names()), 4..8
///     OPTIONAL_INPUTS  bitmask of the optional inputs the policy reads:
///                      1 irreversible, 2 failed, 4 heartbeat, 8 confirm_b (4 + popcount == N_IN)
///     N_STATE          policy.state_bits
///     SAFE             the Safe the module will be enabled on
///     SUPERVISOR       the key that writes blocked / confirm / heartbeat / the selector list
///     SUPERVISOR_B     the second key, only for two_key policies (optional, default 0)
///     NAND_MACHINE     an already deployed evaluator to reuse (optional; deploys one otherwise)
///
///     cd contracts
///     forge script script/DeployReflexModule.s.sol --rpc-url <url> --broadcast
///
/// with PRIVATE_KEY set, or `--ledger`, or any other signer Foundry supports. The
/// constructor recomputes the SHA-256 and refuses a netlist that does not match; this
/// script checks it once more after deployment. The project deploys nothing and holds no
/// key: the transaction is signed on the machine that runs this command, and enabling the
/// module is a separate transaction that only the Safe's owners can send.
contract DeployReflexModule is Script {
    function run() external returns (NandMachine machine, ReflexModule module) {
        bytes memory netlist = vm.envBytes("NETLIST");
        bytes32 want = vm.envBytes32("NETLIST_SHA256");
        uint256 nIn = vm.envUint("N_IN");
        uint256 optional = vm.envUint("OPTIONAL_INPUTS");
        uint256 nState = vm.envUint("N_STATE");
        address safe = vm.envAddress("SAFE");
        address supervisor = vm.envAddress("SUPERVISOR");
        address supervisorB = vm.envOr("SUPERVISOR_B", address(0));
        address existingMachine = vm.envOr("NAND_MACHINE", address(0));

        require(sha256(netlist) == want, "NETLIST does not hash to NETLIST_SHA256");
        require(optional <= 0x0F, "OPTIONAL_INPUTS is a 4-bit mask");

        vm.startBroadcast();
        machine = existingMachine == address(0) ? new NandMachine() : NandMachine(existingMachine);
        module = new ReflexModule(machine, netlist, want, nIn, uint8(optional), nState, ISafe(safe), supervisor, supervisorB);
        vm.stopBroadcast();

        require(module.netlistSha256() == want, "deployed netlist is not the one exported");
        require(keccak256(module.netlist()) == keccak256(netlist), "deployed netlist does not read back");
        console2.log("NandMachine  ", address(machine));
        console2.log("ReflexModule ", address(module));
        console2.log("Safe         ", safe);
        console2.log("Supervisor   ", supervisor);
        console2.log("Next: the Safe's owners enable the module (enableModule), then the agent calls act.");
    }
}
