# 03 — Smart contract tests

Generated 2026-10-01T17:36:28.820305+00:00 · commit `a445e5face24d16aca745fbd15f0f6f1cbd9ce5f`

## Executed EVM tests (py-evm, real EvidenceGate bytecode)

`cd apps/api && python -m pytest tests/contracts tests/blockchain`

- EvidenceGate contract suite (`tests/contracts`): **216/216 passed** (includes 4 seeded fuzz properties × 40 runs: action-hash parity, calldata/amount sensitivity, authorization-id parity)
- Contract + chain-gateway lifecycle suites combined: **287/287 passed**, 0 failed

## Foundry suite

`cd contracts && forge test -vv` (Foundry) · compile-checked in CI with `npm run check:foundry`

- `contracts/test/EvidenceGate.t.sol`: 30 test functions, of which 3 fuzz tests.
- In this build environment Foundry binaries cannot be downloaded, so the Foundry suite is compile-checked with solcjs;
  the same scenarios are executed against the compiled bytecode by the py-evm suite above.

## Compiler

- solc `0.8.28+commit.7893614a.Emscripten.clang`, EVM `cancun`, optimizer on (200 runs)

## Deployment used by the proof run

- Network: local-evm (py-evm, not Monad) (chain id 131277322940537)
- EvidenceGate: `0x6E05f58eEddA592f34DD9105b1827f252c509De0`
- Execution router: `0x7d73424a8256C0b2BA245e5d5a3De8820E45F390` · USDC: `0xAE519FC2Ba8e6fFE6473195c092bF1BAe986ff90` · TOKEN-X: `0x73b647cbA2FE75Ba05B8e12ef8F8D6327D6367bF`
