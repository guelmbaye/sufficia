// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";

import {EvidenceGate} from "../src/EvidenceGate.sol";
import {DemoERC20} from "../src/DemoERC20.sol";
import {DemoSwapRouter} from "../src/DemoSwapRouter.sol";

/// @dev The npm forge-std 1.1.2 `Vm` interface predates the env cheatcodes. These are the standard
///      Foundry cheatcode signatures, called on the same cheatcode address, so the script compiles with
///      the pinned forge-std and runs unchanged under a current `forge script`.
interface VmEnv {
    function envOr(string calldata name, address defaultValue) external returns (address);
    function envOr(string calldata name, uint256 defaultValue) external returns (uint256);
    function startBroadcast(uint256 privateKey) external;
}

/// @notice Deploys the SUFFICIA demo stack:
///         dUSDC (6 decimals), TOKENX (18 decimals), DemoSwapRouter (1 dUSDC = 2.336 TOKENX, TOKENX reserves),
///         EvidenceGate (router allowed) funded with demo dUSDC.
///
/// Env (all optional):
///   PRIVATE_KEY          broadcaster key (otherwise use `forge script --private-key/--account`)
///   AUTHORIZER_ADDRESS   AUTHORIZER_ROLE holder (backend signer); default: deployer
///   PAUSER_ADDRESS       PAUSER_ROLE holder; default: deployer
///   GATE_USDC_FUNDING    dUSDC base units minted to the gate; default 1_000e6
///   ROUTER_TOKENX_RESERVE TOKENX base units minted to the router; default 1_000_000e18
///
/// Usage: forge script script/Deploy.s.sol:Deploy --rpc-url $RPC_URL --broadcast [--private-key ...]
contract Deploy is Script {
    VmEnv internal constant vmEnv = VmEnv(address(bytes20(uint160(uint256(keccak256("hevm cheat code"))))));

    uint256 internal constant RATE_USDC_TO_TOKENX_E18 = 2.336e18;

    function run()
        external
        returns (DemoERC20 usdc, DemoERC20 tokenx, DemoSwapRouter router, EvidenceGate gate)
    {
        uint256 pk = vmEnv.envOr("PRIVATE_KEY", uint256(0));
        address deployer;
        if (pk != 0) {
            deployer = vm.addr(pk);
            vmEnv.startBroadcast(pk);
        } else {
            deployer = msg.sender;
            vm.startBroadcast();
        }

        address authorizer = vmEnv.envOr("AUTHORIZER_ADDRESS", deployer);
        address pauser = vmEnv.envOr("PAUSER_ADDRESS", deployer);
        uint256 gateFunding = vmEnv.envOr("GATE_USDC_FUNDING", uint256(1_000e6));
        uint256 routerReserve = vmEnv.envOr("ROUTER_TOKENX_RESERVE", uint256(1_000_000e18));

        usdc = new DemoERC20("Demo USDC", "dUSDC", 6, deployer);
        tokenx = new DemoERC20("Demo Token X", "TOKENX", 18, deployer);

        router = new DemoSwapRouter(deployer);
        router.setRate(address(usdc), address(tokenx), RATE_USDC_TO_TOKENX_E18);
        tokenx.mint(address(router), routerReserve);

        address[] memory targets = new address[](1);
        targets[0] = address(router);
        gate = new EvidenceGate(deployer, authorizer, pauser, targets);
        usdc.mint(address(gate), gateFunding);

        vm.stopBroadcast();

        console.log("deployer          ", deployer);
        console.log("authorizer        ", authorizer);
        console.log("pauser            ", pauser);
        console.log("DemoUSDC          ", address(usdc));
        console.log("TokenX            ", address(tokenx));
        console.log("DemoSwapRouter    ", address(router));
        console.log("EvidenceGate      ", address(gate));
        console.log("chainId           ", block.chainid);
    }
}
