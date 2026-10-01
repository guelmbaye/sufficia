# 04 — Monad proof

> **LOCAL REHEARSAL — NOT MONAD.** This file was generated with `CHAIN_MODE=LOCAL_EVM`: the real EvidenceGate bytecode ran on an in-process py-evm chain. Regenerate on Monad testnet with `CHAIN_MODE=MONAD_TESTNET make evidence` after `make deploy-testnet` (see docs/deployment.md).

Generated 2026-10-01T18:14:10.755101+00:00 · commit `a36d59483e79c4abb30064f096d58f749d06a21a`

## Network

- Mode: `LOCAL_EVM` · network: local-evm (py-evm, not Monad) · chain id: `131277322940537`
- EvidenceGate: `0x6E05f58eEddA592f34DD9105b1827f252c509De0`
- Authorizer: `0x1563915e194D8CfBA1943570603F7606A3115508` · Executor: `0x5CbDd86a2FA8Dc4bDdd8a8f69dBa48572EeC07FB`
- Deployment: `{"deploy_usdc": "0xd62b2ff60630b97f482dfc70135fba8c9a77c971306e3940737676138fa1432f", "deploy_token_x": "0xf1044435c119a3bf7a1ffd376ddfed681dfb4793164705e7dc1b17539e8caf55", "deploy_router": "0xa7a100784d4ebc575e1e5a2af76188811cfad016b90125a3333d25303eb2ae11", "set_rate": "0x416ae6a927744a204f851618efbb2ed724971ad0bd25ecbd80140a6ffb9fb685", "mint_router_reserve": "0x35551e25f0b734dabdfe67fa497a94494d76294eaa01d8278b1379b1259ccfd1", "deploy_gate": "0xc66b49d7af530bb8c1f74374fc19d1aa2450319e5501be6a488a30942b9eb84e", "fund_gate": "0x955f0c7ac912731b8121cee299239fb6492c07e81dfacb735deee0125f8048e6"}`

## Successful protected execution (grand-prize-v1)

- Evidence Contract hash: `0x7ed7409fed71e5a564460609d2823bfd7b0201e59af3c53c63e0b8068116838e`
- Evidence root: `0x65b17ba78f08f6aa4418c6686fc3ebbb61c1a048aee6cac9f66fec976593cf88`
- Decision: `AUTHORIZE` (`dec_b5d352cbf14d47a3`)
- Action hash: `0x984e1ea2684612828bbbc13d785b66eac3bab592d801282f5b6aa913e9bc56a6`
- Authorization id: `0x69a17dfcea0f40d752c86b2a8b9991e9a8ca031df44fa2498a6cb375e216bbd6` (expires 2026-10-01T18:14:39.337327Z)
- Intent registration tx: `0x32008817477bd89db912b69f8546107970c9d4b9c42075d6f9dc66b510bbfda2`
- Authorization tx: `0x369c796e635e8ec1b176ae1fec4045d284d06d295e6a9a87551db855a3bef06c` (block 14)
- Execution tx: `0xa15cd14e80c54be2b0da0651978d56a09d976d33442c55976c7f80eeb6523aae` (status EXECUTED)
- Receipt hash: `0xa02d5043935d8ed785d4f7d71d55f4e58f8e6e550fd17f4f103c61e7452ae182` · commitment tx: `0xc4ec691e421af9c8b9c6d96b86097bb8698887c5f8c56e48127e402143c3652e` (CONFIRMED)

## EvidenceGate events (decoded from receipts)

- INTENT_REGISTER `IntentRegistered` {"contractHash": "0x7ed7409fed71e5a564460609d2823bfd7b0201e59af3c53c63e0b8068116838e", "intentId": "0x8cb5e89cd98045a9192eada8fec0bfacaf152d4d67cae778bc79a4519bbeaccd", "registeredAt": 1790878449}
- AUTHORIZATION `ExecutionAuthorized` {"actionHash": "0x984e1ea2684612828bbbc13d785b66eac3bab592d801282f5b6aa913e9bc56a6", "authorizationId": "0x69a17dfcea0f40d752c86b2a8b9991e9a8ca031df44fa2498a6cb375e216bbd6", "contractHash": "0x7ed7409fed71e5a564460609d2823bfd7b0201e59af3c53c63e0b8068116838e", "evidenceRoot": "0x65b17ba78f08f6aa4418c6686fc3ebbb61c1a048aee6cac9f66fec976593cf88", "executor": "0x5CbDd86a2FA8Dc4bDdd8a8f69dBa48572EeC07FB", "expiresAt": 1790878479, "intentId": "0x8cb5e89cd98045a9192eada8fec0bfacaf152d4d67cae778bc79a4519bbeaccd"}
- PROTECTED_EXECUTION `AuthorizationConsumed` {"actionHash": "0x984e1ea2684612828bbbc13d785b66eac3bab592d801282f5b6aa913e9bc56a6", "authorizationId": "0x69a17dfcea0f40d752c86b2a8b9991e9a8ca031df44fa2498a6cb375e216bbd6", "executor": "0x5CbDd86a2FA8Dc4bDdd8a8f69dBa48572EeC07FB"}
- PROTECTED_EXECUTION `ProtectedExecution` {"actionHash": "0x984e1ea2684612828bbbc13d785b66eac3bab592d801282f5b6aa913e9bc56a6", "authorizationId": "0x69a17dfcea0f40d752c86b2a8b9991e9a8ca031df44fa2498a6cb375e216bbd6", "resultHash": "0xc17bc06792c2ffb20ca1334de224715d29c84cec91ac45e70473e3964c7e3628", "target": "0x7d73424a8256C0b2BA245e5d5a3De8820E45F390"}
- RECEIPT_COMMITMENT `ReceiptCommitted` {"authorizationId": "0x69a17dfcea0f40d752c86b2a8b9991e9a8ca031df44fa2498a6cb375e216bbd6", "receiptHash": "0xa02d5043935d8ed785d4f7d71d55f4e58f8e6e550fd17f4f103c61e7452ae182"}

## Blocked decision (no transaction)

- Decision `dec_f62808eedcc502f7`: **DENY** ['THRESHOLD_NOT_MET'] — Available liquidity is $92,440. The Evidence Contract requires more than $500,000. No execution authorization was issued. 100 USDC did not move.
- Receipt `rcpt_e4bded2d6ad1c973`: execution status `BLOCKED`, tx_hash `None`

## Replay

- Result **MATCH**, chain verification `VERIFIED`, 24/24 checks passing, market queried: False
