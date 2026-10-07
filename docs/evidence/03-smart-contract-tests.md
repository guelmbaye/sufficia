# 03 — Smart contract tests

Generated 2026-10-07T06:25:20.204703+00:00 · commit `16d89fa5ce9a73e9b0ea5315fb4859ad89d0d343`

## Executed EVM tests (py-evm, real EvidenceGate bytecode)

`cd apps/api && python -m pytest tests/contracts tests/blockchain`

- EvidenceGate contract suite (`tests/contracts`): **216/216 passed** (includes 4 seeded fuzz properties × 40 runs: action-hash parity, calldata/amount sensitivity, authorization-id parity)
- Contract + chain-gateway lifecycle suites combined: **293/293 passed**, 0 failed

## Foundry suite

`cd contracts && forge test` (Foundry; runs in CI)

- `contracts/test/EvidenceGate.t.sol`: 30 test functions, of which 3 fuzz tests (256 runs each).
- Executed with forge Version: 1.5.1-stable: **30/30 passed**, 0 failed.

## Compiler

- solc `0.8.28+commit.7893614a.Emscripten.clang`, EVM `cancun`, optimizer on (200 runs)

## Deployment used by the proof run

- Network: local-evm (py-evm, not Monad) (chain id 131277322940537)
- EvidenceGate: `0x6E05f58eEddA592f34DD9105b1827f252c509De0`
- Execution router: `0x7d73424a8256C0b2BA245e5d5a3De8820E45F390` · USDC: `0xAE519FC2Ba8e6fFE6473195c092bF1BAe986ff90` · TOKEN-X: `0x73b647cbA2FE75Ba05B8e12ef8F8D6327D6367bF`
