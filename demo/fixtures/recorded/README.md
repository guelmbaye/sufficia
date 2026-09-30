# RECORDED evidence fixtures

Files in this directory are re-served by `RecordedFixtureAdapter`
(`apps/api/sufficia/evidence/adapters/recorded.py`) when the evidence profile is `recorded`
(`EVIDENCE_PROFILE=recorded`). One file per claim: `<claim>.json`.

## The sample set in this directory is SIMULATED, not real provider data

The build environment for this repository has no outbound network, so no genuine provider response
could be captured here, and none has been fabricated. The three files shipped here were captured from
the local **SIMULATED** market (`provider: "simulated-market"`, `original_mode: "SIMULATED"`, and a
`note` field saying so). They exist only so the RECORDED code path can be exercised end to end.

To replace them with genuine captures, configure the live sources in `.env` and run from `apps/api`:

```bash
python scripts/capture_fixtures.py                    # live profile order, free sources only
python scripts/capture_fixtures.py --allow-paid       # also x402 paid sources (spends USDC)
python scripts/capture_fixtures.py --sources scenario # regenerate this SIMULATED sample set
```

The script writes a fixture only when a source actually answered; a failed source writes nothing.

## Format

```json
{
  "provider": "nansen",
  "source_type": "API",
  "claim": "smart_money_inflow_24h",
  "value": 1340000,
  "unit": "USD",
  "captured_at": "2026-09-30T12:00:00.000000Z",
  "request_descriptor": {"method": "POST", "url": "https://api.nansen.ai/api/v1/smart-money/netflow", "body": {}},
  "response_body_b64": "<base64 of the exact response bytes>",
  "upstream_source": "nansen:smart-money-netflow",
  "source_family": "nansen",
  "endpoint_id": "nansen:/api/v1/smart-money/netflow",
  "network": "monad",
  "original_mode": "LIVE"
}
```

| field | meaning |
| --- | --- |
| `provider` | original provider name, kept in provenance (the engine's permitted-sources check still applies to it) |
| `source_type` | `API` / `ONCHAIN` / `ORACLE` / `DERIVED` |
| `value`, `unit` | the value exactly as the adapter extracted it (a string stays a string; the normalizer parses it strictly) |
| `captured_at` | when the response was originally received (preserved as `source.captured_at`) |
| `request_descriptor` | the adapter's request descriptor, no secrets (API keys are headers and are never recorded) |
| `response_body_b64` | the exact response bytes; `response_hash` is computed over these bytes |
| `upstream_source`, `source_family`, `endpoint_id`, `network` | provenance / independence metadata |
| `original_mode`, `note` | informational: how the capture was produced |

## How a recorded observation is labelled when re-served

- `source.mode` is always **`RECORDED`**, whatever the original mode was.
- `source.captured_at` is the original capture time.
- `temporal.observed_at` is **re-stamped to the time the fixture is re-served**. This is an explicit
  demo-continuity choice: without it every recorded observation would be stale by construction. It is
  visible to anyone auditing the item, because `mode=RECORDED` and the true `captured_at` travel with it.
- `integrity.request_hash` / `response_hash` commit to the captured descriptor and bytes.
