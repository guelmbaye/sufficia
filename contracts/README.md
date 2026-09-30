# SUFFICIA contracts

The onchain half of **Evidence-Bound Execution**. The backend decides offchain, from evidence, whether an
agent's economic action is authorized. `EvidenceGate` makes sure that only that exact action runs, only
before it expires, only once, and only when sent by the executor that was named.

| Contract | Role |
|---|---|
| `src/EvidenceGate.sol` | Core gate, kept small so it can be audited. It holds the funds and executes one exact authorized action per authorization. OpenZeppelin v5 `AccessControl`, `Pausable`, `ReentrancyGuard` and `SafeERC20`. Immutable (no proxy). Custom errors only. |
| `src/DemoERC20.sol` | Mintable demo token with configurable decimals (`dUSDC` = 6, `TOKENX` = 18). Testnet/local only. |
| `src/DemoSwapRouter.sol` | Fixed-rate demo swap venue with owner-set rates and its own reserves. `amountOut = amountIn * rateE18 * 10^decOut / (1e18 * 10^decIn)`, computed with `Math.mulDiv`. |
| `test/mocks/RevertingTarget.sol` | `fail()` always reverts with `TargetAlwaysReverts(42)`. `ok()` counts calls and records `msg.value`. |
| `test/mocks/ReentrantTarget.sol` | Calls back into `executeAuthorized` while the gate is executing, to exercise `nonReentrant`. |

## Build

```bash
npm install            # solc 0.8.28 (solcjs), @openzeppelin/contracts 5.6.1, forge-std 1.1.2, ds-test (all pinned)
npm run build          # compiles src/** + test/mocks/*, writes out/<Name>.json and the API artifact copies
npm run check:foundry  # same, plus a compile check of test/*.t.sol and script/*.s.sol
```

Compiler settings: solc 0.8.28, `evmVersion: cancun`, optimizer on with 200 runs, `viaIR: false`.
Each artifact has the shape `{contractName, sourceName, abi, bytecode, deployedBytecode, compiler}`.
The build copies `EvidenceGate`, `DemoERC20`, `DemoSwapRouter`, `RevertingTarget` and `ReentrantTarget` to
`apps/api/sufficia/blockchain/artifacts/`. A compiler error exits non-zero and leaves no partial artifacts.

## Flow

1. `registerIntent(intentId, contractHash)`, called by the AUTHORIZER. It binds an intent to its contract hash
   and is idempotent for the same pair.
2. `authorize(authorizationId, intentId, contractHash, evidenceRoot, actionHash, executor, issuedAt, expiresAt, nonce)`,
   called by the AUTHORIZER. It commits a single-use authorization for one exact action, with a TTL of at most 600 s.
3. `executeAuthorized(authorizationId, action)`, called by the named executor. It rechecks everything,
   consumes the authorization, approves exactly `maxAmountIn`, calls the target, resets the approval to 0,
   and checks that the recipient's `assetOut` balance increased by at least `minAmountOut`.
4. `recordReceipt(authorizationId, receiptHash)`, called by the AUTHORIZER once execution is done. It commits
   the offchain receipt hash, once.

## Encodings (the backend must reproduce these byte for byte)

```
ACTION_DOMAIN = keccak256("EVIDENCE_GATE_ACTION_V1") = 0xa0293d46a7c9b7da44ed21625e0c7a1798ded5355a560a1c0960ae7905011b48
AUTH_DOMAIN   = keccak256("EVIDENCE_GATE_AUTH_V1")   = 0x9b081057a31d7cfa3ab651d8256cd6620ba475a29406af2e3499c7e4108ce507

actionHash = keccak256(abi.encode(
    ACTION_DOMAIN, block.chainid, address(gate),
    target, value, keccak256(data), assetIn, maxAmountIn,
    assetOut, minAmountOut, recipient, deadline, nonce))
  types: bytes32,uint256,address,address,uint256,bytes32,address,uint256,address,uint256,address,uint64,bytes32

authorizationId = keccak256(abi.encode(AUTH_DOMAIN, contractHash, evidenceRoot, actionHash, executor, nonce))
  types: bytes32,bytes32,bytes32,bytes32,address,bytes32
```

In Python: `keccak(eth_abi.encode(types, values))`. Both hashes are also exposed onchain as
`computeActionHash(Action)` and `computeAuthorizationId(...)`. Putting `chainid` and the gate address in the
action hash stops an authorization from being replayed on another chain or another deployment.

## Security properties (each one is covered by tests)

- **Exact action.** Any change to target, value, calldata, assets, amounts, recipient, deadline or nonce
  changes `actionHash` and the call fails with `ActionHashMismatch`.
- **Bound executor.** Only the `executor` named in the authorization can execute it (`UnauthorizedExecutor`).
- **Short-lived.** `expiresAt` must be later than the current block and at most `block.timestamp + 600` when
  the authorization is committed. Execution is allowed while `block.timestamp <= expiresAt`. The action's own
  `deadline` is checked as well.
- **Single use.** The authorization is marked consumed before the external call, so a second attempt fails with
  `AuthorizationAlreadyConsumed`.
- **Atomic.** If the target reverts, the whole transaction reverts with `ExecutionFailed(returndata)` and
  the authorization stays unconsumed, so it can be retried until it expires. The same applies to a
  slippage failure (`SlippageExceeded`).
- **Bounded spend.** The gate approves exactly `maxAmountIn` for the duration of the call, then resets the
  approval to 0. It forwards only `msg.value == action.value` (`ValueMismatch`) and never its own ETH balance.
- **Output floor.** The gate measures the balance increase of `assetOut` at `recipient` itself, independent of
  what the target reports.
- **Target allowlist.** The admin maintains the list of callable targets. The gate itself and `address(0)`
  can never be added. Removing a target also blocks authorizations that were already issued for it.
- **Reentrancy.** `executeAuthorized` and `sweep` are `nonReentrant`.
- **Kill switch.** The PAUSER can pause `authorize` and `executeAuthorized`. `recordReceipt` keeps working
  while paused, so receipts can still be committed.
- **Roles.** `DEFAULT_ADMIN_ROLE` manages targets, `sweep` and roles. `AUTHORIZER_ROLE` is the backend
  signer. `PAUSER_ROLE` controls pause and unpause. None of them can execute an action unless it is also
  named as the executor.

## Events and errors

Events (the Python tests pin these signatures, so any change to them breaks the tests):

```
IntentRegistered(bytes32 indexed intentId, bytes32 indexed contractHash, uint256 registeredAt)
ExecutionAuthorized(bytes32 indexed authorizationId, bytes32 indexed intentId, bytes32 indexed evidenceRoot,
                    bytes32 contractHash, bytes32 actionHash, address executor, uint64 expiresAt)
AuthorizationConsumed(bytes32 indexed authorizationId, bytes32 indexed actionHash, address indexed executor)
ProtectedExecution(bytes32 indexed authorizationId, address indexed target, bytes32 indexed actionHash, bytes32 resultHash)
ReceiptCommitted(bytes32 indexed authorizationId, bytes32 indexed receiptHash)
TargetAllowed(address indexed target, bool allowed)
Swept(address indexed token, address indexed to, uint256 amount)
```

Custom errors: `InvalidAddress()`, `InvalidTarget()`, `InvalidIntent()`, `IntentAlreadyRegistered()`,
`IntentNotRegistered()`, `ContractHashMismatch()`, `InvalidAuthorization()`, `AuthorizationIdMismatch()`,
`AuthorizationExists()`, `AuthorizationExpired()`, `TtlTooLong()`, `AuthorizationNotFound()`,
`AuthorizationAlreadyConsumed()`, `AuthorizationNotConsumed()`, `UnauthorizedExecutor()`,
`ActionHashMismatch()`, `TargetNotAllowed()`, `ActionDeadlinePassed()`, `ValueMismatch()`,
`ExecutionFailed(bytes)`, `SlippageExceeded(uint256,uint256)`, `ReceiptAlreadyRecorded()`, `InvalidReceipt()`.
The contract also inherits these OpenZeppelin errors: `AccessControlUnauthorizedAccount(address,bytes32)`,
`EnforcedPause()`, `ExpectedPause()`, `ReentrancyGuardReentrantCall()` and `SafeERC20FailedOperation(address)`.

## Tests: which ones run where

- **Executed here:** `apps/api/tests/contracts/test_evidence_gate_evm.py` runs with pytest on py-evm
  (web3 8, eth-tester, the Prague fork, which includes Cancun) against the compiled artifacts. It
  recomputes every hash independently in Python and checks it against the contract, then covers each
  property above plus seeded fuzzing of the hash encodings.
  ```bash
  cd apps/api && python -m pytest tests/contracts -q
  ```
- **Foundry:** `test/EvidenceGate.t.sol` and `script/Deploy.s.sol`. Foundry could not be installed
  through `foundryup` in this environment because GitHub is blocked. `npm run check:foundry` compiles
  these files with solcjs, which checks that they compile but does not run them. They only use the
  forge-std 1.1.2 API (`vm.prank`, `vm.warp`, `vm.expectRevert`, `vm.expectEmit`, `vm.assume`), which works
  with both the pinned npm copy and a current forge-std. For reference, they were also run once with the
  official forge 1.7.1 binary from npm (`@foundry-rs/forge-linux-amd64`, compiling through a solcjs shim):
  30/30 tests passed, and `forge script` ran the deploy script. Normal usage:
  ```bash
  forge test          # uses foundry.toml, which remaps @openzeppelin/, forge-std/ and ds-test/ to node_modules
  ```

## Deploy (Foundry)

```bash
AUTHORIZER_ADDRESS=0x... PAUSER_ADDRESS=0x... \
forge script script/Deploy.s.sol:Deploy --rpc-url $RPC_URL --broadcast --private-key $PK
```

The script deploys dUSDC (6 decimals) and TOKENX (18 decimals), then the router with a rate of
1 dUSDC = 2.336 TOKENX and 1,000,000 TOKENX of reserves, then the gate with the router allowlisted, and
mints 1,000 dUSDC to the gate. Environment variables, all optional: `PRIVATE_KEY`, `AUTHORIZER_ADDRESS`
and `PAUSER_ADDRESS` (both default to the deployer), `GATE_USDC_FUNDING`, `ROUTER_TOKENX_RESERVE`.
The backend has its own Python deployer and does not depend on this script.
