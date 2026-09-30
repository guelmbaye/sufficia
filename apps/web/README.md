# SUFFICIA web app (next phase)

The backend is complete and exposes everything the UI needs over `/api/v1` (OpenAPI: `packages/schemas/openapi.json`,
live docs at `http://127.0.0.1:8000/docs`):

| Screen (Document 05) | Endpoints |
|---|---|
| 01 Intent | `POST /intents/preview`, `POST /intents`, `POST /intents/{id}/compile` |
| 02 Evidence Contract — "What must be true before money can move?" | `GET /contracts/{id}` (`summary`) |
| 03 Evidence arrival + Demo Lab (Valid · Stale · Missing · Conflict · Threshold Fail) | `POST /contracts/{id}/acquire`, `GET /contracts/{id}/evidence`, `POST /demo/contracts/{id}/conditions/{condition}` |
| 04 Decision + execution | `POST /contracts/{id}/decide`, `GET /decisions/{id}`, `POST /decisions/{id}/execute` |
| 05 Evidence Execution Receipt | `GET /receipts/{id}` (`audit_chain`, `commitment`) |
| Replay | `POST /receipts/{id}/replay` |
| Orchestrated demo | `POST /api/v1/demo/scenarios/{scenario_id}/run` |
| Proof widget | `GET /api/v1/benchmark`, `GET /api/v1/system`, `GET /api/v1/chain/transactions` |

Authority-carrying numbers arrive as exact decimal strings; never parse them with `parseFloat` for comparisons.
