# 04 — Monad proof

> Generated against **monad-testnet** (chain id 10143).

Generated 2026-10-12T07:10:12.507001+00:00 · commit `76a872cf144d4a3528736b8060bec6384bc60a2f`

## Network

- Mode: `MONAD_TESTNET` · network: monad-testnet · chain id: `10143`
- EvidenceGate: `0x1d8948F033d89A7943ab5B0886626CaA486390F6`
- Authorizer: `0x9f56d727fb79ea779A65117eb2E0063824BffbCb` · Executor: `0x4F6987f9417B00F12093419fD22E8bb26C54ad08`
- Deployment transactions:
  - deploy_usdc: [`0x3656a639dec227b80f87b54b1737159c9fea52b9ebcac43412cf6c89f90c0a05`](https://testnet.monadvision.com/tx/0x3656a639dec227b80f87b54b1737159c9fea52b9ebcac43412cf6c89f90c0a05)
  - deploy_token_x: [`0x3d27df3aab46b34aa28d4d7469ac7be8c733840f85b51cf26066777b27883869`](https://testnet.monadvision.com/tx/0x3d27df3aab46b34aa28d4d7469ac7be8c733840f85b51cf26066777b27883869)
  - deploy_router: [`0xef760718412f75d8e8adc399cdb2119d51affc6d8d78f520d86bf0695575f9e0`](https://testnet.monadvision.com/tx/0xef760718412f75d8e8adc399cdb2119d51affc6d8d78f520d86bf0695575f9e0)
  - set_rate: [`0x3431d7e797d63685421b09587b414507f0da1fbec89ef5896e456554826cda21`](https://testnet.monadvision.com/tx/0x3431d7e797d63685421b09587b414507f0da1fbec89ef5896e456554826cda21)
  - mint_router_reserve: [`0x973823e8a62c475eaa4521084a3e5107065ec28d9a018180c9687fb60faf71f1`](https://testnet.monadvision.com/tx/0x973823e8a62c475eaa4521084a3e5107065ec28d9a018180c9687fb60faf71f1)
  - deploy_gate: [`0xb9dd9ec1bae046a2e4420e231e987e6ed63ab7de9089caa16d82e9565e8b67f5`](https://testnet.monadvision.com/tx/0xb9dd9ec1bae046a2e4420e231e987e6ed63ab7de9089caa16d82e9565e8b67f5)
  - fund_gate: [`0x71296a2583ae90f4b87e6e3711e72134f3cd5c4b398f42be22246d3811186e12`](https://testnet.monadvision.com/tx/0x71296a2583ae90f4b87e6e3711e72134f3cd5c4b398f42be22246d3811186e12)

## Successful protected execution (grand-prize-v1)

- Evidence Contract hash: `0x7ed7409fed71e5a564460609d2823bfd7b0201e59af3c53c63e0b8068116838e`
- Evidence root: `0xb33cdddd1f715fcb87d90e47fcef09c2661dd715bc8bbcd575c3b6b55085153e`
- Decision: `AUTHORIZE` (`dec_1738a75482dd8945`)
- Action hash: `0x3071f4c021f9e1187394a8ea75ab0c1093aff3ea52d6c40ba70c9d685653f70b`
- Authorization id: `0xa5a5096525666be11d0824da580f9ceb984d77c61b7ca65148972b89a6e7f190` (expires 2026-10-12T07:10:21.108846Z)
- Intent registration tx: [`0x1cf11d641fffd04e641b994fcfa341047b3d8c860e61015f1ff19bcdcf366731`](https://testnet.monadvision.com/tx/0x1cf11d641fffd04e641b994fcfa341047b3d8c860e61015f1ff19bcdcf366731)
- Authorization tx: [`0x31a5c64cb0970b9516dc8f658286015cac3470052a7c9e1a97e71e1613c49dde`](https://testnet.monadvision.com/tx/0x31a5c64cb0970b9516dc8f658286015cac3470052a7c9e1a97e71e1613c49dde) (block 70334222)
- Execution tx: [`0x6d657a20caaff0616ea82c9743b4718bad8a6e5c2756f45d357b1bfb9e0f81f7`](https://testnet.monadvision.com/tx/0x6d657a20caaff0616ea82c9743b4718bad8a6e5c2756f45d357b1bfb9e0f81f7) (status EXECUTED)
- Receipt hash: `0xbd6cc53ef36d3377e2f6a603ad1232ee219cb46e83d571ec234702434817eb41` · commitment tx: [`0x26d719704fba3a6d4f9b72828099420c659bad5cc1d77466c6311f978d2a560f`](https://testnet.monadvision.com/tx/0x26d719704fba3a6d4f9b72828099420c659bad5cc1d77466c6311f978d2a560f) (CONFIRMED)

## EvidenceGate events (decoded from receipts)

- INTENT_REGISTER `IntentRegistered` {"contractHash": "0x7ed7409fed71e5a564460609d2823bfd7b0201e59af3c53c63e0b8068116838e", "intentId": "0x829f60486440a1e61a2201139fa9d66e61cdae21c100e9543934c9c5572a2b4b", "registeredAt": 1791788994}
- AUTHORIZATION `ExecutionAuthorized` {"actionHash": "0x3071f4c021f9e1187394a8ea75ab0c1093aff3ea52d6c40ba70c9d685653f70b", "authorizationId": "0xa5a5096525666be11d0824da580f9ceb984d77c61b7ca65148972b89a6e7f190", "contractHash": "0x7ed7409fed71e5a564460609d2823bfd7b0201e59af3c53c63e0b8068116838e", "evidenceRoot": "0xb33cdddd1f715fcb87d90e47fcef09c2661dd715bc8bbcd575c3b6b55085153e", "executor": "0x4F6987f9417B00F12093419fD22E8bb26C54ad08", "expiresAt": 1791789021, "intentId": "0x829f60486440a1e61a2201139fa9d66e61cdae21c100e9543934c9c5572a2b4b"}
- PROTECTED_EXECUTION `AuthorizationConsumed` {"actionHash": "0x3071f4c021f9e1187394a8ea75ab0c1093aff3ea52d6c40ba70c9d685653f70b", "authorizationId": "0xa5a5096525666be11d0824da580f9ceb984d77c61b7ca65148972b89a6e7f190", "executor": "0x4F6987f9417B00F12093419fD22E8bb26C54ad08"}
- PROTECTED_EXECUTION `ProtectedExecution` {"actionHash": "0x3071f4c021f9e1187394a8ea75ab0c1093aff3ea52d6c40ba70c9d685653f70b", "authorizationId": "0xa5a5096525666be11d0824da580f9ceb984d77c61b7ca65148972b89a6e7f190", "resultHash": "0xc17bc06792c2ffb20ca1334de224715d29c84cec91ac45e70473e3964c7e3628", "target": "0x59726E38704E01a5337FCAf02a462f861903Caa8"}
- RECEIPT_COMMITMENT `ReceiptCommitted` {"authorizationId": "0xa5a5096525666be11d0824da580f9ceb984d77c61b7ca65148972b89a6e7f190", "receiptHash": "0xbd6cc53ef36d3377e2f6a603ad1232ee219cb46e83d571ec234702434817eb41"}

## Blocked decision (no transaction)

- Decision `dec_18c157018fdb2524`: **DENY** ['THRESHOLD_NOT_MET'] — Available liquidity is $92,440. The Evidence Contract requires more than $500,000. No execution authorization was issued. 100 USDC did not move.
- Receipt `rcpt_63c42604ac316bc6`: execution status `BLOCKED`, tx_hash `None`

## Replay

- Result **MATCH**, chain verification `VERIFIED`, 24/24 checks passing, market queried: False
