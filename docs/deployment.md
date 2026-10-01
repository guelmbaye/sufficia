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

Network (Monad docs): chain id `10143`, RPC `https://testnet-rpc.monad.xyz` (fallbacks
`https://rpc.ankr.com/monad_testnet`, `https://rpc-testnet.monadinfra.com`), explorers
`https://testnet.monadvision.com` / `https://testnet.monadscan.com`, faucet `https://faucet.monad.xyz`.

1. Create four keys (deployer, authorizer, executor, pauser) and fund the deployer, authorizer and executor with testnet
   MON from the faucet.
2. Deploy the suite (DemoERC20 dUSDC + TOKEN-X, DemoSwapRouter at 1 USDC = 2.336 TOKEN-X with reserves, EvidenceGate with
   the router and USDC allow-listed, 1,000 dUSDC minted to the gate):

   ```bash
   export DEPLOYER_PRIVATE_KEY=0x... AUTHORIZER_ADDRESS=0x... EXECUTOR_ADDRESS=0x... PAUSER_ADDRESS=0x...
   make deploy-testnet               # writes deployments/monad-testnet.json and prints explorer links
   ```

   (`contracts/script/Deploy.s.sol` is the equivalent Foundry script: `forge script script/Deploy.s.sol --rpc-url ... --broadcast`.)
3. Run the API against it:

   ```bash
   export CHAIN_MODE=MONAD_TESTNET DEPLOYMENT_FILE=../../deployments/monad-testnet.json \
          AUTHORIZER_PRIVATE_KEY=0x... EXECUTOR_PRIVATE_KEY=0x... PAUSER_PRIVATE_KEY=0x...
   make api && make demo
   ```
4. Produce the proof files from that run: `CHAIN_MODE=MONAD_TESTNET ... make evidence` — `docs/evidence/04-monad-proof.md`
   then contains explorer links for the authorization, execution and receipt-commitment transactions.
5. Live checks before a demo: `SUFFICIA_LIVE=1 CHAIN_MODE=MONAD_TESTNET make live`.

Missing keys, addresses or a chain-id mismatch make the API refuse to start protected execution
(`503 CHAIN_UNAVAILABLE` with the configuration error) — it never falls back to the local chain.

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
