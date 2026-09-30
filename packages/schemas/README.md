# @sufficia/schemas

Canonical, versioned JSON Schemas of the SUFFICIA authority domain and the OpenAPI document of the API.
They are **generated** from the typed pydantic models in `apps/api/sufficia/domain/models.py` — never edited by hand.

```bash
python apps/api/scripts/export_schemas.py          # regenerate
python apps/api/scripts/export_schemas.py --check  # CI gate: fails if stale
```

| Schema | Object |
|---|---|
| `json/intent.v1.json` | Natural-language intent as submitted |
| `json/evidence-contract.v1.json` | Frozen Evidence Contract (requirements, policy, limits, TTL) |
| `json/evidence-item.v1.json` | One observation with provenance, freshness and economics |
| `json/evidence-set.v1.json` | Items + acquisition failures evaluated together |
| `json/decision.v1.json` | Deterministic decision (state, reason codes, hashes, authority window) |
| `json/canonical-action.v1.json` | Exact action an authorization permits (hashed by `EvidenceGate.computeActionHash`) |
| `json/authorization.v1.json` | Evidence-bound authorization |
| `json/execution.v1.json` | Protected execution outcome |
| `json/evidence-receipt.v1.json` | Evidence Execution Receipt (hashed and committed onchain) |
| `openapi.json` | HTTP API |

Numbers that carry authority (thresholds, values, amounts) are exact decimals: the API serializes them as strings,
and integers beyond 2^53 (raw token amounts) as decimal strings.
