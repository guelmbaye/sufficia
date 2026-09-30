/* Typed client for the SUFFICIA API. The browser calls same-origin /api/v1/*, which Next.js proxies
   to the FastAPI service (src/app/api/v1/[...path]/route.ts). Errors keep the API's machine code so the UI can explain
   exactly what happened (e.g. 403 EVIDENCE_AUTHORIZATION_REQUIRED). */

import type {
  AuthorizationView,
  BenchmarkReport,
  ChainTx,
  Condition,
  ContractPreview,
  ContractView,
  DecisionView,
  EvidenceSetView,
  ExecutionResult,
  IntentView,
  ReceiptView,
  ReplayResult,
  ScenarioRun,
  SystemInfo,
  TamperResult,
  TestConditionResult,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly detail: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type Fetcher = typeof fetch;

export function createClient(base = "/api/v1", fetcher: Fetcher = (...a) => fetch(...a)) {
  async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
    let res: Response;
    try {
      res = await fetcher(`${base}${path}`, {
        method,
        headers: body === undefined ? undefined : { "content-type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store",
      });
    } catch {
      throw new ApiError(0, "API_UNREACHABLE", "The SUFFICIA API cannot be reached. Start it with `make api`.");
    }
    const text = await res.text();
    let data: unknown = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        throw new ApiError(res.status, "INVALID_RESPONSE", `The API returned a non-JSON response (${res.status}).`);
      }
    }
    if (!res.ok) {
      const err = (data as { error?: { code?: string; message?: string; detail?: Record<string, unknown> } } | null)?.error;
      throw new ApiError(res.status, err?.code ?? `HTTP_${res.status}`, err?.message ?? res.statusText, err?.detail ?? {});
    }
    return data as T;
  }

  const enc = encodeURIComponent;
  return {
    system: () => call<SystemInfo>("GET", "/system"),
    previewIntent: (text: string) => call<ContractPreview>("POST", "/intents/preview", { text }),
    createIntent: (text: string) => call<IntentView>("POST", "/intents", { text }),
    compile: (intentId: string) => call<ContractView>("POST", `/intents/${enc(intentId)}/compile`),
    contract: (contractId: string) => call<ContractView>("GET", `/contracts/${enc(contractId)}`),
    acquire: (contractId: string) => call<EvidenceSetView>("POST", `/contracts/${enc(contractId)}/acquire`),
    evidence: (contractId: string) => call<EvidenceSetView>("GET", `/contracts/${enc(contractId)}/evidence`),
    evidenceSet: (id: string) => call<EvidenceSetView>("GET", `/evidence-sets/${enc(id)}`),
    decide: (contractId: string) => call<DecisionView>("POST", `/contracts/${enc(contractId)}/decide`),
    decision: (decisionId: string) => call<DecisionView>("GET", `/decisions/${enc(decisionId)}`),
    authorize: (decisionId: string) => call<AuthorizationView>("POST", `/decisions/${enc(decisionId)}/authorize`),
    executeDecision: (decisionId: string) => call<ExecutionResult>("POST", `/decisions/${enc(decisionId)}/execute`),
    authorization: (id: string) => call<AuthorizationView>("GET", `/authorizations/${enc(id)}`),
    executeAuthorization: (id: string) => call<ExecutionResult>("POST", `/authorizations/${enc(id)}/execute`),
    decisionReceipt: (decisionId: string) => call<ReceiptView>("GET", `/decisions/${enc(decisionId)}/receipt`),
    receipt: (id: string) => call<ReceiptView>("GET", `/receipts/${enc(id)}`),
    replay: (id: string) => call<ReplayResult>("POST", `/receipts/${enc(id)}/replay`),
    tamperTest: (id: string) => call<TamperResult>("POST", `/receipts/${enc(id)}/tamper-test`),
    testCondition: (contractId: string, condition: Condition) =>
      call<TestConditionResult>("POST", `/demo/contracts/${enc(contractId)}/test/${condition}`),
    runScenario: (scenarioId: string, options: { execute?: boolean; stop_before?: string } = {}) =>
      call<ScenarioRun>("POST", `/demo/scenarios/${enc(scenarioId)}/run`, options),
    chainTransactions: (limit = 20) => call<{ transactions: ChainTx[] }>("GET", `/chain/transactions?limit=${limit}`),
    auditVerify: () => call<{ valid: boolean; events: number; broken_at: number | null; head: string }>("GET", "/audit/verify"),
    benchmark: () => call<BenchmarkReport>("GET", "/benchmark"),
  };
}

export type SufficiaClient = ReturnType<typeof createClient>;
export const api = createClient();
