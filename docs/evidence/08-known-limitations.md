# 08 — Known limitations

SUFFICIA makes a narrow, verifiable claim: **an agent's economic action executes only if the evidence its principal
required was established, fresh, consistent and bound to that exact action.** It does not claim more.

- **Registered-provider trust.** Evidence is only as good as the registered sources. SUFFICIA proves *which* source said
  *what* and *when* (request/response hashes, evidence root); it does not prove that a source told the truth.
- **No objective-truth guarantee.** Thresholds are the principal's policy, not investment advice. "AUTHORIZE" means
  "the declared conditions held", not "this trade is good".
- **Centralized hackathon authorizer.** One backend key holds `AUTHORIZER_ROLE`. The contract independently enforces
  exact action binding, expiry, executor and single use, but a compromised authorizer could authorize a *different
  decision*. Production would use threshold signatures or a verifier network.
- **Limited Evidence Contract DSL.** Six claims (inflow, liquidity, price, listing, sanctions flag, venue), nine operators,
  per-requirement freshness/tolerance/minimum items. No arbitrary expressions, no cross-claim formulas.
- **Single execution vertical.** One swap adapter (DemoSwapRouter) and one transfer adapter. Other venues need an adapter
  with calldata verification (`verify_spend`).
- **Demo market and reference x402 server.** The demo profile reads a *simulated* market and pays a *local* x402
  reference server whose settlement is `SIMULATED`. Every card is badged with its mode; nothing simulated is presented
  as live. Live adapters (Nansen, DexScreener, Chainlink, Uniswap-V2 pool, paid x402 endpoint) are implemented and tested
  against recorded/mocked responses but require keys and network access.
- **Testnet-first.** The default chain is an in-process EVM running the real contract; Monad testnet/mainnet are
  configuration (`CHAIN_MODE`, deployment file). No production custody claims; demo tokens only.
- **Clock model.** Freshness is evaluated at decision time with a 2 s clock-skew allowance; authority windows are whole
  seconds onchain (a window shorter than 1 s is refused as `AUTHORITY_WINDOW_EXHAUSTED`).
- **No distributed verifier network**, no multi-tenant auth, single API process (signer queues and the local chain are
  in-process state).
