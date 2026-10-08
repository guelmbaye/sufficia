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
faucets `https://faucet.monad.xyz` and `https://faucet.quicknode.com/monad/testnet`.

Nothing to sign up for: the public RPCs, the explorers and the faucets need no account and no subscription. The only
thing to obtain is testnet MON (free, from a faucet), and only for the deployer address.

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
- **Reserve balance.** Below 10 MON, an account may lower its balance by a transfer only if it sent nothing in the
  previous 3 blocks (otherwise the transfer reverts and still pays gas). `scripts/testnet.py distribute` waits 4 blocks
  between transfers; send one transfer at a time from a wallet too. Contract calls without MON value are unaffected.

### Steps

Every command runs from `apps/api` with the virtual environment active. `scripts/testnet.py status` tells you at any
point what is missing and which command comes next.

1. **Create the four testnet wallets**: `python scripts/testnet.py keys` (`make testnet-keys`). It generates the
   deployer (pays the deployment, becomes admin, tops up the gate), authorizer (holds `AUTHORIZER_ROLE`; the backend
   signs authorizations and receipt commitments with it), executor (the only address allowed to execute) and pauser,
   writes their keys and the Monad testnet settings into `apps/api/.env` (gitignored), and prints the four addresses.
   It never replaces a key that is already set and never prints key material. `CHAIN_MODE` is left alone, so the app
   keeps running on the local chain until step 5.
2. **Get testnet MON for the deployer address only** from `https://faucet.monad.xyz` or
   `https://faucet.quicknode.com/monad/testnet` (paste the address; no account). `python scripts/testnet.py status`
   shows the balance and how much is still missing: about 2 MON at the minimum base fee (1.5 x the deployment cost,
   plus 20 executed runs for the authorizer and the executor), about 1.2 MON for 5 runs. Faucet drips vary with the
   receiving wallet's history and are repeatable every 6-24 h; a personal wallet with Ethereum mainnet history often
   gets more, and can then send MON to the deployer (one transfer at a time).
3. **Fund the other wallets from the deployer**: `python scripts/testnet.py distribute` (`make testnet-distribute`).
   It keeps 1.5 x the deployment cost on the deployer and sends the authorizer, the executor and the pauser what 20
   runs need (`--runs N` to change; `--dry-run` to preview). When the deployer holds less, it funds as many runs as it
   can and says what is missing. Run it again any time: it only sends what is missing.
4. **Check, deploy, check again** (`make preflight-testnet` and `make deploy-testnet` do the same):

   ```bash
   python scripts/preflight.py --no-deployment        # RPCs, chain id, clock, keys, balances
   python scripts/deploy.py --network monad-testnet   # 7 transactions, writes deployments/monad-testnet.json
   python scripts/preflight.py                        # + contracts, AUTHORIZER_ROLE, allow-list, gate treasury
   ```

   The deployment mints 100,000 demo dUSDC to the EvidenceGate (1,000 executions of 100 USDC; `GATE_USDC_FUNDING`
   overrides it in base units) and 1,000,000 TOKEN-X to the router. Top the treasury up later with
   `python scripts/fund_gate.py --amount 10000`. (`contracts/script/Deploy.s.sol` is the equivalent Foundry script.)
5. **Switch the app to Monad**: `python scripts/testnet.py mode monad` writes `CHAIN_MODE=MONAD_TESTNET` into
   `apps/api/.env` (`mode local` goes back to the local chain; the wallets stay).
6. **Run the API and the web app** as in section 1 (`python -m uvicorn sufficia.api.app:app --port 8000` from
   `apps/api`, then `npm run dev` in `apps/web`). The header now says *Monad testnet*, execution says *Execute on
   Monad*, and every transaction links to MonadVision. `make demo` (or `/demo` in the browser) runs the Grand Prize
   scenario.
7. **Produce the proof files** from that chain: `make evidence` (or `python -m sufficia.evidence_pack --out ../../docs/evidence`)
   with the same `.env`; `docs/evidence/04-monad-proof.md` then links the deployment, authorization, execution and
   receipt-commitment transactions and loses its "local rehearsal" banner. Take the screenshots from the running app
   (the E2E suite starts its own local-EVM API).
8. **Live checks before a demo**: `SUFFICIA_LIVE=1 make live` (PowerShell: `$env:SUFFICIA_LIVE="1"; python -m pytest -m live tests/live`)
   runs the Grand Prize end to end against the deployed gate and checks the x402 facilitator.

If the repository sits under a web server's document root (XAMPP `htdocs`, for instance), Apache could serve
`apps/api/.env` and its keys to anyone who can reach the machine. The repository ships an `.htaccess` that denies
everything (effective with XAMPP's default `AllowOverride All`); moving the repository out of `htdocs`, or stopping
Apache, is safer still.

Missing keys, addresses or a chain-id mismatch make the API refuse protected execution (`503 CHAIN_UNAVAILABLE` with
the configuration error); it never falls back to the local chain. After a testnet reset the contracts are gone:
preflight reports "no contract at …", and you deploy again.

### Dry run on your own machine (optional)

Anvil (Foundry) can stand in for the testnet locally, so the whole path above runs once without spending faucet MON:

```bash
anvil --chain-id 10143 --base-fee 100000000000 --hardfork prague --block-time 1
# in apps/api/.env: MONAD_RPC_URL=http://127.0.0.1:8545 and MONAD_RPC_FALLBACK_URL=http://127.0.0.1:8545, then
python scripts/testnet.py keys
# the "faucet": one of anvil's printed accounts sends MON to the deployer
cast send --rpc-url http://127.0.0.1:8545 --private-key <anvil key> <deployer address> --value 2ether
# then steps 3-8
```

This rehearsal (wallets, distribution, preflight, deployment, top-up, API + web in `MONAD_TESTNET` mode configured
from `.env` only, Grand Prize, replay with onchain verification, live tests, evidence pack, back to local mode) was run
before release: the scenario completes in about 3.5 s with 1 s blocks. Anvil lets the base fee fall below Monad's
100 gwei floor, so the MON amounts it shows are smaller than on the testnet.

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

## 5. Live demo on Render

`render.yaml` describes two free Render services: the API (`sufficia-api`, in `MONAD_TESTNET` mode) and the web
app (`sufficia-web`). The API keeps the whole repository (it reads `demo/scenarios`, `docs/evidence` and
`deployments/`); the web app builds from `apps/web` alone.

1. Deploy on Monad testnet first (section 2) and push `deployments/monad-testnet.json`: the API reads it from the
   repository.
2. Render dashboard → New → Blueprint → this repository. Render asks for the values marked `sync: false`:
   - `AUTHORIZER_PRIVATE_KEY`, `EXECUTOR_PRIVATE_KEY`, `PAUSER_PRIVATE_KEY`: the values in your `apps/api/.env`.
     The deployer key never goes on the server; deploying and `scripts/fund_gate.py` stay on your computer.
   - `SUFFICIA_API_URL`: the API's public URL, `https://sufficia-api.onrender.com` unless Render assigns another
     one (then correct it in the web service's Environment tab and redeploy the web service).
   `API_KEY` is generated on the API and passed to the web app, so only the web app can send mutating requests.
3. When both services are live, open `https://<web service>.onrender.com/demo`.

Free instances sleep after 15 minutes without traffic and take about a minute to wake, so a first visit can wait up
to about two minutes while both services wake (the web proxy waits up to `SUFFICIA_API_TIMEOUT_MS`, 120 s here).
Their filesystem is ephemeral: runs and receipts reset when the API sleeps or redeploys. Each executed run costs
about 0.053 MON from the authorizer and the executor: run `python scripts/testnet.py status` regularly while the demo
is public, and `distribute` after a faucet top-up.
