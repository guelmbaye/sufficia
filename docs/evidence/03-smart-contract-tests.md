# 03 — Smart contract tests

Generated 2026-10-12T07:16:59.828589+00:00 · commit `76a872cf144d4a3528736b8060bec6384bc60a2f`

## Executed EVM tests (py-evm, real EvidenceGate bytecode)

`cd apps/api && python -m pytest tests/contracts tests/blockchain`

- EvidenceGate contract suite (`tests/contracts`): **216/216 passed** (includes 4 seeded fuzz properties × 40 runs: action-hash parity, calldata/amount sensitivity, authorization-id parity)
- Contract + chain-gateway lifecycle suites combined: **300/300 passed**, 0 failed

## Foundry suite

`cd contracts && forge test` (Foundry; runs in CI)

- `contracts/test/EvidenceGate.t.sol`: 30 test functions, of which 3 fuzz tests (256 runs each).
- forge is not installed where this pack was generated: the suite was compile-checked with solcjs, and the same
  scenarios run against the compiled bytecode in the py-evm suite above.

## Compiler

- solc `0.8.28+commit.7893614a.Emscripten.clang`, EVM `cancun`, optimizer on (200 runs)

## Deployment used by the proof run

- Network: monad-testnet (chain id 10143)
- EvidenceGate: `0x1d8948F033d89A7943ab5B0886626CaA486390F6`
- Execution router: `0x59726E38704E01a5337FCAf02a462f861903Caa8` · USDC: `0x4104e82a7390446814EadF48D1d0cBC374044b38` · TOKEN-X: `0xd603014FAC6c1B042C9E35897132D78cE8D23746`
