# SUFFICIA web app

The proof surface for evidence-bound execution (Document 05). Next.js 16 (App Router), React 19, Tailwind CSS v4,
TypeScript strict. The browser only talks to this origin: `/api/v1/*` is proxied to the SUFFICIA API by
[`src/app/api/v1/[...path]/route.ts`](src/app/api/v1/[...path]/route.ts), which reads `SUFFICIA_API_URL` (and the
optional `SUFFICIA_API_KEY`, sent as `X-API-Key`) at runtime.

```bash
npm ci
npm run dev            # http://127.0.0.1:3000, expects the API on http://127.0.0.1:8000 (make api)
npm run check          # eslint + next typegen/tsc + vitest
npm run build          # production build (NEXT_OUTPUT=standalone for the container image)
npm run e2e            # Playwright: starts a fresh API (port 8100, temp SQLite, in-process EVM) and `next start` (3100)
npm run screenshots    # Grand Prize flow only; writes A-denied / B-authorized / C-receipt to docs/evidence/screenshots
```

`npm run e2e` needs a production build first and `python3` with `apps/api` installed (`make install`). Set
`SCREENSHOT_DIR` to write the screenshots elsewhere and `PYTHON` to choose the interpreter.

## Screens

| Route | Screen | Backend |
|---|---|---|
| `/` | 01 Mission: instruction, live "Understood as …" preview, unparsed words flagged as not enforced | `POST /intents/preview`, `POST /intents`, `POST /intents/{id}/compile` |
| `/contract/{id}` | 02 Evidence Contract: "What must be true before money can move?" | `GET /contracts/{id}` |
| `/gate/{id}` | 03 Live Gate: intent, evidence ledger, causal connectors, gate; *Test the gate* conditions | `POST /contracts/{id}/acquire`, `POST /contracts/{id}/decide`, `POST /demo/contracts/{id}/test/{condition}`, `POST /decisions/{id}/authorize` |
| `/execution/{id}` | 04 Execution: authorization check, action binding, submit, confirm | `GET /authorizations/{id}`, `POST /authorizations/{id}/execute` |
| `/receipt/{id}` | 05 Receipt: history, evidence, integrity, replay, tamper test, compare runs | `GET /receipts/{id}`, `POST /receipts/{id}/replay`, `POST /receipts/{id}/tamper-test` |
| `/demo` | Grand Prize scenario, stopped before the market changes, opened on the Live Gate | `POST /demo/scenarios/grand-prize-v1/run` |

The proof drawer (header button or key `3`) shows only numbers the backend produced: the latest benchmark report,
chain mode and contract addresses, recent transactions with decoded `EvidenceGate` events, and the audit-chain check.

## Rules the UI keeps

- **The gate decides nothing.** Every state, reason, observed value and countdown comes from the API. The execute
  button is enabled only for an `AUTHORIZE` decision with an active authorization; the API refuses anything else
  with `403 EVIDENCE_AUTHORIZATION_REQUIRED` (covered by an end-to-end test that bypasses the UI).
- **Exact numbers.** Authority-carrying values arrive as decimal strings and are formatted digit by digit
  (`src/lib/format.ts`), never through `parseFloat`.
- **Honest labels.** The chain badge says *Local EVM rehearsal (not Monad)* unless the API reports a Monad chain;
  explorer links appear only for Monad. Evidence rows show their source mode (Live, Recorded, Simulated, Test
  condition) and x402 payments.
- **State is color plus icon plus word.** Deny red, allow green, stale/conflict amber, abstain grey, each with its own
  glyph, so no state depends on color alone.
- **One animation.** Connectors draw into the gate, then the gate settles; disabled under `prefers-reduced-motion`.
- **The Live Gate fits 1440×900 without vertical scroll** (checked by the end-to-end suite's viewport).

## Layout

```
src/app/                 routes (one client page per screen) + api/v1 proxy
src/components/          AppShell (stepper, chain badge, shortcuts), ProofDrawer, Drawer, Hash, StateIcon
src/components/gate/     IntentColumn, EvidenceLedger, Connectors, GatePanel, TestConditions, EvidenceInspector
src/components/receipt/  ReceiptTimeline, EvidenceTable, IntegrityPanel, ReplayPanel, TamperTest, CompareRuns
src/lib/                 typed API client, response types, formatting, decision-state vocabulary, run context
tests/unit/              vitest: formatting, state vocabulary, API client, proxy
e2e/                     Playwright: Grand Prize flow (+ screenshots), test conditions, mission, proof drawer
```
