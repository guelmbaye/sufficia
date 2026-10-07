# Deployment

## 1. Local (no keys, no network)

```bash
make install && make api          # CHAIN_MODE=LOCAL_EVM: the real EvidenceGate bytecode on an in-process EVM
make web                          # web app on http://127.0.0.1:3000 (open /demo)
make demo                         # grand-prize-v1 end to end over HTTP, no browser
```

Docker: `docker compose up --build` starts the API (`:8000`, SQLite volume) and the web app (`:3000`, Next.js
standalone image, `SUFFICIA_API_URL=http://api:8000`), or `docker compose --profile postgres up` with
`DATABASE_URL=postgresql://sufficia:sufficia@postgres:5432/sufficia` and `make migrate`.

The web image is built once and pointed at any API at runtime: `SUFFICIA_API_URL` (and `SUFFICIA_API_KEY` when the API
runs with `API_KEY`) are read by the `/api/v1` proxy on each request.

### Windows (PowerShell, no `make`, no C compiler)

Every dependency installs from wheels (Python 3.11–3.13); nothing needs Visual Studio or the Windows SDK.

```powershell
cd apps\api
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e ".[dev]" -e ..\..\packages\sdk\python
python -m uvicorn sufficia.api.app:app --port 8000      # http://127.0.0.1:8000/docs

# second terminal
cd apps\web
npm ci
npm run dev                                              # http://127.0.0.1:3000/demo
```

Checks without `make`: `python -m pytest -q` and `python -m sufficia.benchmark` in `apps\api`;
`python tests\benchmark\generate_fixtures.py --check` at the root; `npm run check`, `npm run build` and
`npm run e2e` in `apps\web` (the E2E suite uses `apps\api\.venv` automatically, or `$env:PYTHON`).
The repository pins LF line endings (`.gitattributes`): the benchmark fixtures are hashed byte for byte.

## 2. Monad testnet

Network (Monad docs, testnet reset from genesis on 2025-12-16): chain id `10143`, currency MON, RPC
`https://testnet-rpc.monad.xyz` (50 rps), fallbacks `https://rpc.ankr.com/monad_testnet` and
`https://rpc-testnet.monadinfra.com`, explorers `https://testnet.monadvision.com` / `https://testnet.monadscan.com`,
faucet `https://faucet.monad.xyz`.

What matters on Monad for this deployment:

- **Fees are charged on the gas limit, not the gas used**, at `min(base + priority, max fee)`; the base fee never goes
  below 100 gwei. The backend sets limits at 1.25 x the estimate, so plan on: deployment ~5.1 M gas (~0.52 MON at the
  minimum base fee), and per executed run ~340 k gas for the authorizer (registerIntent + authorize + recordReceipt)
  and ~180 k for the executor (~0.035 MON and ~0.018 MON).
- **The `pending` tag behaves like `latest`, and a transaction with too little MON may be accepted, then never land.**
  The backend sends one transaction per signer at a time and waits for it, so nonces never race; an empty account
  shows up as a 30 s confirmation timeout. `scripts/preflight.py` checks balances first.
- **Evidence and authority are counted in seconds.** Evidence older than 30 s is stale and the authorization expires
  with it, so this computer's clock must match the chain (preflight measures the skew; Windows: `w32tm /resync`).

### Steps

1. **Four keys, never reused elsewhere.** Deployer (pays the deployment, becomes admin), authorizer (holds
   `AUTHORIZER_ROLE`; the backend signs authorizations and receipt commitments with it), executor (the only address
   allowed to execute), pauser. One way to create them:
   `python -c "from eth_account import Account; a = Account.create(); print(a.address, a.key.hex())"`.
2. **Fund them** from the faucet: about 1 MON for the deployer, 1 MON for the authorizer, 0.5 MON for the executor
   (enough for dozens of runs). The pauser needs MON only to pause.
3. **Configure** `apps/api/.env` (gitignored; the API, the deploy script and the preflight all read it):

   ```dotenv
   CHAIN_MODE=MONAD_TESTNET
   MONAD_RPC_URL=https://testnet-rpc.monad.xyz
   MONAD_RPC_FALLBACK_URL=https://rpc.ankr.com/monad_testnet
   MONAD_CHAIN_ID=10143
   MONAD_EXPLORER_URL=https://testnet.monadvision.com
   DEPLOYMENT_FILE=../../deployments/monad-testnet.json
   DEPLOYER_PRIVATE_KEY=0x...
   AUTHORIZER_PRIVATE_KEY=0x...
   EXECUTOR_PRIVATE_KEY=0x...
   PAUSER_PRIVATE_KEY=0x...
   ```

4. **Check, deploy, check again** (from `apps/api`; `make preflight-testnet` and `make deploy-testnet` do the same):

   ```bash
   python scripts/preflight.py --no-deployment        # RPCs, chain id, clock, keys, balances
   python scripts/deploy.py --network monad-testnet   # 7 transactions, writes deployments/monad-testnet.json
   python scripts/preflight.py                        # + contracts, AUTHORIZER_ROLE, allow-list, gate treasury
   ```

   The deployment mints 100,000 demo dUSDC to the EvidenceGate (1,000 executions of 100 USDC; `GATE_USDC_FUNDING`
   overrides it in base units) and 1,000,000 TOKEN-X to the router. Top the treasury up later with
   `python scripts/fund_gate.py --amount 10000`. (`contracts/script/Deploy.s.sol` is the equivalent Foundry script.)
5. **Run the API and the web app** as in section 1 (`python -m uvicorn sufficia.api.app:app --port 8000`, then
   `npm run dev` in `apps/web`). The header now says *Monad testnet*, execution says *Execute on Monad*, and every
   transaction links to MonadVision. `make demo` (or `/demo` in the browser) runs the Grand Prize scenario.
6. **Produce the proof files** from that chain: `make evidence` (or `python -m sufficia.evidence_pack --out ../../docs/evidence`)
   with the same `.env`; `docs/evidence/04-monad-proof.md` then links the deployment, authorization, execution and
   receipt-commitment transactions and loses its "local rehearsal" banner. Take the screenshots from the running app
   (the E2E suite starts its own local-EVM API).
7. **Live checks before a demo**: `SUFFICIA_LIVE=1 make live` (PowerShell: `$env:SUFFICIA_LIVE="1"; python -m pytest -m live tests/live`)
   runs the Grand Prize end to end against the deployed gate.

Missing keys, addresses or a chain-id mismatch make the API refuse protected execution (`503 CHAIN_UNAVAILABLE` with
the configuration error); it never falls back to the local chain. After a testnet reset the contracts are gone:
preflight reports "no contract at …", and you deploy again.

### Dry run on your own machine (optional)

Anvil (Foundry) can stand in for the testnet locally, so the whole path above runs once without spending faucet MON:

```bash
anvil --chain-id 10143 --base-fee 100000000000 --hardfork prague --block-time 1
# fund the four addresses from one of anvil's printed accounts, e.g.
cast send --rpc-url http://127.0.0.1:8545 --private-key <anvil key> <address> --value 1ether
# then steps 3-6 with MONAD_RPC_URL=http://127.0.0.1:8545 and an empty MONAD_RPC_FALLBACK_URL
```

This rehearsal (preflight, deployment, top-up, API + web in `MONAD_TESTNET` mode, Grand Prize, replay with onchain
verification, evidence pack) was run before release: the scenario completes in about 4.5 s with 1 s blocks.

## 3. Live evidence

```bash
export EVIDENCE_PROFILE=live ASSET_TOKEN_ADDRESS=0x...        # token on the data providers
export NANSEN_API_KEY=...                                     # smart_money_inflow_24h
export DEXSCREENER_PAIR_ADDRESS=0x...                         # available_liquidity / asset_price (optional pin)
export CHAINLINK_FEED_ADDRESS=0x... POOL_ADDRESS=0x...        # oracle price / onchain pool liquidity
export X402_EVIDENCE_URL=https://.../paid-liquidity EVIDENCE_PAYMENT_PRIVATE_KEY=0x...   # paid x402 evidence
```

Order of sources per claim is configurable with `EVIDENCE_SOURCES_JSON`. `sources_per_requirement=2` on
`POST /contracts/{id}/acquire` collects two independent observations per claim so disagreements escalate.
To capture genuine provider responses for the `recorded` profile: `python apps/api/scripts/capture_fixtures.py`.

## 4. Production notes

- One API process (`--workers 1`): signer queues and the local chain are in-process; scale reads behind a cache, not
  by adding writers.
- Set `API_KEY`, `DEMO_MODE=false`, `CORS_ORIGINS`, PostgreSQL, and keep the four keys in a secret manager.
  Give the web app the same key as `SUFFICIA_API_KEY`; it stays server-side.
- `DEMO_MODE=false` removes the Demo Lab routes; the web app then hides *Test the gate* and the Grand Prize shortcut
  (`/demo` explains that the scenario is unavailable). The rest of the flow is unchanged.
- The API rate limit is per client address. Behind the web proxy every viewer shares the web server's address, so
  size `RATE_LIMIT_PER_MINUTE` for the whole audience (the E2E suite sets it to 0).
- `EvidenceGate` is immutable (no proxy); rotate the authorizer with `grantRole`/`revokeRole`; `pause()` stops new
  authorizations and executions while receipts can still be committed.
