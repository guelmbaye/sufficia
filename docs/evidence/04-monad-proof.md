# 04 — Monad proof

> **LOCAL REHEARSAL — NOT MONAD.** This file was generated with `CHAIN_MODE=LOCAL_EVM`: the real EvidenceGate bytecode ran on an in-process py-evm chain. Regenerate on Monad testnet with `CHAIN_MODE=MONAD_TESTNET make evidence` after `make deploy-testnet` (see docs/deployment.md).

Generated 2026-09-30T21:18:28.447428+00:00 · commit `7af4b7712a69ee13816ca1dc2f2fb8c7ead2c4bf`

## Network

- Mode: `LOCAL_EVM` · network: local-evm (py-evm, not Monad) · chain id: `131277322940537`
- EvidenceGate: `0x6E05f58eEddA592f34DD9105b1827f252c509De0`
- Authorizer: `0x1563915e194D8CfBA1943570603F7606A3115508` · Executor: `0x5CbDd86a2FA8Dc4bDdd8a8f69dBa48572EeC07FB`
- Deployment: `{"deploy_usdc": "0xd62b2ff60630b97f482dfc70135fba8c9a77c971306e3940737676138fa1432f", "deploy_token_x": "0xf1044435c119a3bf7a1ffd376ddfed681dfb4793164705e7dc1b17539e8caf55", "deploy_router": "0xa7a100784d4ebc575e1e5a2af76188811cfad016b90125a3333d25303eb2ae11", "set_rate": "0x416ae6a927744a204f851618efbb2ed724971ad0bd25ecbd80140a6ffb9fb685", "mint_router_reserve": "0x35551e25f0b734dabdfe67fa497a94494d76294eaa01d8278b1379b1259ccfd1", "deploy_gate": "0xc66b49d7af530bb8c1f74374fc19d1aa2450319e5501be6a488a30942b9eb84e", "fund_gate": "0x955f0c7ac912731b8121cee299239fb6492c07e81dfacb735deee0125f8048e6"}`

## Successful protected execution (grand-prize-v1)

- Evidence Contract hash: `0x7ed7409fed71e5a564460609d2823bfd7b0201e59af3c53c63e0b8068116838e`
- Evidence root: `0xe829ba48bd66e840c3c2cfe843552436756ab3afc6818ee8b7f420ce066faf90`
- Decision: `AUTHORIZE` (`dec_8fd22a9909767c1a`)
- Action hash: `0xd545afc5f28881822d72841be25a1b615c81b26a78e3dbe3c89829e29520895c`
- Authorization id: `0x97d6c906f9cc2b1c8e45d3d00240a345c2964f67b3c3b163541d3955fb5aeb94` (expires 2026-09-30T21:18:56.858607Z)
- Intent registration tx: `0x2980619572aab9c7147f1f7bc7eb69b656ada436298b588f55d58456ba1e4556`
- Authorization tx: `0x828346ece32d987d71b326d32b8d05df37cb93e697b5d16c3f95ed032cb4d094` (block 14)
- Execution tx: `0x472bf27a15d2abc0a1a5682e9536f7a1586be865e0582eba1679b1569aabfb66` (status EXECUTED)
- Receipt hash: `0x01eb2c0e343fc05887154f819bae6143d4e6e1d835e16c266fcff82c2ccc2339` · commitment tx: `0x22dcec1dc50d108525d89fb9714e056b0f3d66526b0eb9254c91801f1fccdf17` (CONFIRMED)

## Execution events


## Blocked decision (no transaction)

- Decision `dec_0d80adaa303bc855`: **DENY** ['THRESHOLD_NOT_MET'] — Available liquidity is $92,440. The Evidence Contract requires more than $500,000. No execution authorization was issued. 100 USDC did not move.
- Receipt `rcpt_7005901a9fb59940`: execution status `BLOCKED`, tx_hash `None`

## Replay

- Result **MATCH**, chain verification `VERIFIED`, 22/22 checks passing, market queried: False
