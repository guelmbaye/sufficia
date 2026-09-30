# 04 — Monad proof

> **LOCAL REHEARSAL — NOT MONAD.** This file was generated with `CHAIN_MODE=LOCAL_EVM`: the real EvidenceGate bytecode ran on an in-process py-evm chain. Regenerate on Monad testnet with `CHAIN_MODE=MONAD_TESTNET make evidence` after `make deploy-testnet` (see docs/deployment.md).

Generated 2026-09-30T22:07:30.795434+00:00 · commit `364154322be75056eed7a55374ac8c9dfc6cd274`

## Network

- Mode: `LOCAL_EVM` · network: local-evm (py-evm, not Monad) · chain id: `131277322940537`
- EvidenceGate: `0x6E05f58eEddA592f34DD9105b1827f252c509De0`
- Authorizer: `0x1563915e194D8CfBA1943570603F7606A3115508` · Executor: `0x5CbDd86a2FA8Dc4bDdd8a8f69dBa48572EeC07FB`
- Deployment: `{"deploy_usdc": "0xd62b2ff60630b97f482dfc70135fba8c9a77c971306e3940737676138fa1432f", "deploy_token_x": "0xf1044435c119a3bf7a1ffd376ddfed681dfb4793164705e7dc1b17539e8caf55", "deploy_router": "0xa7a100784d4ebc575e1e5a2af76188811cfad016b90125a3333d25303eb2ae11", "set_rate": "0x416ae6a927744a204f851618efbb2ed724971ad0bd25ecbd80140a6ffb9fb685", "mint_router_reserve": "0x35551e25f0b734dabdfe67fa497a94494d76294eaa01d8278b1379b1259ccfd1", "deploy_gate": "0xc66b49d7af530bb8c1f74374fc19d1aa2450319e5501be6a488a30942b9eb84e", "fund_gate": "0x955f0c7ac912731b8121cee299239fb6492c07e81dfacb735deee0125f8048e6"}`

## Successful protected execution (grand-prize-v1)

- Evidence Contract hash: `0x7ed7409fed71e5a564460609d2823bfd7b0201e59af3c53c63e0b8068116838e`
- Evidence root: `0x973d7f2a136b0eadb62234d1b6add58f0fa9f1086ccf518889680e42df876a95`
- Decision: `AUTHORIZE` (`dec_86503b37b11aa0e0`)
- Action hash: `0x432175dc5a2177167021e96221d600fa7e67bee1d8f3414c328c94fed9be87ab`
- Authorization id: `0x374687089ab0dde06ea2cb157d7f71e8af3581cdac68a7068625c2c6bc4c35d9` (expires 2026-09-30T22:07:59.143660Z)
- Intent registration tx: `0xeda4c42e1df14d6a869f2dadaa79bee936e0f448ee228ece3ab08cb86ce50726`
- Authorization tx: `0x7ae66207233def2725ad6d89d2d28e6f4eb72fb5657a6d6ee9b23547f87de64b` (block 14)
- Execution tx: `0xb98827681f0cf432fe5537bd430a7a9f1c89b51c25c949a04fcd068c0b7fd40d` (status EXECUTED)
- Receipt hash: `0xe728647c190ee19306dd7e3d04cde008446fc59f64f8eec9b8cd3356fb892bcc` · commitment tx: `0x0f40d3145f2eb4332198b2b7789fb2ce3aed117ec41489e36ac0bf61600eb5de` (CONFIRMED)

## EvidenceGate events (decoded from receipts)

- INTENT_REGISTER `IntentRegistered` {"contractHash": "0x7ed7409fed71e5a564460609d2823bfd7b0201e59af3c53c63e0b8068116838e", "intentId": "0x1813a3766513443fc043b3521c0ac2e619c1dd6f00675d5836e9207d63cb4c18", "registeredAt": 1790806049}
- AUTHORIZATION `ExecutionAuthorized` {"actionHash": "0x432175dc5a2177167021e96221d600fa7e67bee1d8f3414c328c94fed9be87ab", "authorizationId": "0x374687089ab0dde06ea2cb157d7f71e8af3581cdac68a7068625c2c6bc4c35d9", "contractHash": "0x7ed7409fed71e5a564460609d2823bfd7b0201e59af3c53c63e0b8068116838e", "evidenceRoot": "0x973d7f2a136b0eadb62234d1b6add58f0fa9f1086ccf518889680e42df876a95", "executor": "0x5CbDd86a2FA8Dc4bDdd8a8f69dBa48572EeC07FB", "expiresAt": 1790806079, "intentId": "0x1813a3766513443fc043b3521c0ac2e619c1dd6f00675d5836e9207d63cb4c18"}
- PROTECTED_EXECUTION `AuthorizationConsumed` {"actionHash": "0x432175dc5a2177167021e96221d600fa7e67bee1d8f3414c328c94fed9be87ab", "authorizationId": "0x374687089ab0dde06ea2cb157d7f71e8af3581cdac68a7068625c2c6bc4c35d9", "executor": "0x5CbDd86a2FA8Dc4bDdd8a8f69dBa48572EeC07FB"}
- PROTECTED_EXECUTION `ProtectedExecution` {"actionHash": "0x432175dc5a2177167021e96221d600fa7e67bee1d8f3414c328c94fed9be87ab", "authorizationId": "0x374687089ab0dde06ea2cb157d7f71e8af3581cdac68a7068625c2c6bc4c35d9", "resultHash": "0xc17bc06792c2ffb20ca1334de224715d29c84cec91ac45e70473e3964c7e3628", "target": "0x7d73424a8256C0b2BA245e5d5a3De8820E45F390"}
- RECEIPT_COMMITMENT `ReceiptCommitted` {"authorizationId": "0x374687089ab0dde06ea2cb157d7f71e8af3581cdac68a7068625c2c6bc4c35d9", "receiptHash": "0xe728647c190ee19306dd7e3d04cde008446fc59f64f8eec9b8cd3356fb892bcc"}

## Blocked decision (no transaction)

- Decision `dec_1bc763874888427d`: **DENY** ['THRESHOLD_NOT_MET'] — Available liquidity is $92,440. The Evidence Contract requires more than $500,000. No execution authorization was issued. 100 USDC did not move.
- Receipt `rcpt_b03efd1ebc0f090f`: execution status `BLOCKED`, tx_hash `None`

## Replay

- Result **MATCH**, chain verification `VERIFIED`, 24/24 checks passing, market queried: False
