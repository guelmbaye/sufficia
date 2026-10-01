/* Types of the SUFFICIA API responses (apps/api/sufficia/api/routes.py).
   Authority-carrying numbers arrive as exact decimal strings (or JSON integers when they are
   whole and within 2^53). They are displayed, never used for authority decisions in the browser. */

export type Num = string | number;
export type ClaimValue = string | number | boolean | null;

export type DecisionState = "AUTHORIZE" | "DENY" | "ABSTAIN" | "REFRESH" | "ESCALATE";
export type RequirementStatus = "PASS" | "FAIL" | "UNKNOWN";
export type SourceMode = "LIVE" | "RECORDED" | "SIMULATED" | "INJECTED";
export type Condition = "valid" | "threshold" | "missing" | "stale" | "conflict";

export interface Money {
  value: Num;
  currency: string;
}

export interface Intent {
  intent_id: string;
  text: string;
  created_at: string;
  network: string;
  protected_amount: Money | null;
  evidence_budget: Money | null;
}

export interface IntentView {
  intent: Intent;
  intent_hash: string;
  contract_id: string | null;
}

export interface Requirement {
  id: string;
  claim: string;
  operator: string;
  threshold: Num | null;
  values: string[] | null;
  expected: boolean | string | null;
  unit: string | null;
  required: boolean;
  max_age_seconds: number;
  minimum_items: number;
  conflict_tolerance: Num;
  conflict_policy: string;
  origin: "USER" | "IMPLICIT" | string;
  label: string | null;
}

export interface EvidenceContract {
  contract_id: string;
  intent_id: string;
  schema_version: number;
  policy_version: number;
  action: { type: string; asset: string; max_amount: Money };
  requirements: Requirement[];
  policy: Record<string, unknown>;
  economic_limits: { max_execution_value_usd: Num; max_evidence_cost_usd: Num };
  authorization: { ttl_seconds: number };
}

export interface SummaryRequirement {
  id: string;
  label: string;
  expression: string;
  origin: string;
  max_age_seconds: number;
  claim?: string;
  required?: boolean;
  note?: string;
}

export interface ContractSummary {
  action: string;
  maximum: string;
  requirements: SummaryRequirement[];
  time: string;
  authority: string;
  evidence_budget: string;
  intent_preserved: boolean;
  mandatory_requirements: number;
  minimum_required_evidence?: number;
  conflict_policy?: string | null;
  unparsed: string[];
  warnings?: string[];
  compiler?: string;
  preservation_status?: string;
  fallback_reason?: string;
}

/** Whether this server's execution adapter can ever perform the contract's action. */
export interface ExecutionCapability {
  executable: boolean;
  adapter: string;
  label?: string;
  code: string | null;
  message: string | null;
}

/** The execution that already moved an intent's protected amount (an intent is spent at most once). */
export interface SpentBy {
  decision_id: string;
  execution_id: string;
  tx_hash: string | null;
  receipt_id: string | null;
}

export interface ContractView {
  contract: EvidenceContract;
  contract_hash: string;
  compiler: string;
  frozen_at: string;
  intent_id: string;
  preservation: Record<string, unknown>;
  summary: ContractSummary;
  evidence_sets: { evidence_set_id: string; origin: string; created_at: string }[];
  decisions: { decision_id: string; state: DecisionState; evaluated_at: string }[];
  execution: ExecutionCapability;
  spent_by: SpentBy | null;
}

export interface ContractPreview {
  preview: true;
  contract: EvidenceContract;
  contract_hash: string;
  compiler: string;
  summary: ContractSummary;
  preservation: Record<string, unknown>;
  execution: ExecutionCapability;
}

export interface EvidencePayment {
  protocol: string;
  network: string | null;
  scheme: string | null;
  quoted_amount: Num | null;
  settled_amount: Num | null;
  currency: string;
  payment_reference: string | null;
  status: string;
  payer: string | null;
  pay_to: string | null;
}

export interface EvidenceItem {
  evidence_id: string;
  contract_id: string;
  requirement_id: string;
  claim: { name: string; value: ClaimValue; unit: string | null };
  temporal: { observed_at: string | null; received_at: string | null };
  source: {
    provider: string;
    source_type: string;
    mode: SourceMode;
    endpoint_id: string | null;
    upstream_source: string | null;
    source_family: string | null;
    network: string | null;
    captured_at: string | null;
  };
  integrity: { request_hash: string | null; response_hash: string | null; chain_ref: Record<string, unknown> | null };
  economic: { cost: Num; currency: string; payment: EvidencePayment | null };
  derivation: unknown;
  acquisition_id: string | null;
}

export interface EvidenceItemView {
  item: EvidenceItem;
  badge: string;
  item_hash: string;
  age_seconds_now: Num | null;
}

export interface PaymentView {
  acquisition_id: string;
  evidence_set_id: string;
  evidence_id: string | null;
  requirement_id: string;
  provider: string;
  protocol: string;
  network: string | null;
  scheme: string | null;
  quoted: Num | null;
  settled: Num | null;
  currency: string;
  status: string;
  payment_reference: string | null;
  payer: string | null;
  pay_to: string | null;
  error: string | null;
  created_at: string;
}

export interface AcquisitionFailure {
  requirement_id: string;
  provider: string;
  code: string;
  detail: string;
  cost_spent: Num;
}

export interface TimelineEvent {
  event: string;
  requirement_id: string | null;
  provider: string | null;
  at: string;
  detail: string;
  [key: string]: unknown;
}

export interface EvidenceSetView {
  evidence_set_id: string | null;
  contract_id: string;
  origin?: string;
  label?: string;
  parent_id?: string | null;
  created_at?: string;
  evidence_root?: string;
  items: EvidenceItemView[];
  failures: AcquisitionFailure[];
  payments: PaymentView[];
  acquisition?: { spent?: Num; timeline?: TimelineEvent[]; started_at?: string; finished_at?: string; [k: string]: unknown };
  injection?: { condition: string; requirement_id: string; detail: string };
}

export interface Reason {
  code: string;
  requirement_id: string | null;
  claim: string | null;
  observed: string[] | null;
  required: string | null;
  causes: string[];
  detail: Record<string, unknown>;
  message: string;
}

export interface RequirementResult {
  requirement_id: string;
  claim: string;
  operator: string;
  required_expression: string;
  required: boolean;
  status: RequirementStatus;
  evidence_ids: string[];
  observed_values: string[];
  checks: { present: boolean; fresh: boolean; valid_type: boolean; provenance: boolean; conflict: boolean; threshold: boolean | null };
  reason_code: string | null;
  human_reason: string;
}

export interface EvidenceAssessment {
  evidence_id: string;
  requirement_id: string;
  status: "VALID" | "STALE" | "INVALID" | "CONFLICTED" | "DUPLICATE" | "MISSING";
  reason_codes: string[];
  age_seconds: Num | null;
  expires_at: string | null;
  normalized_value: string | null;
  item_hash: string | null;
}

export interface Decision {
  decision_id: string;
  contract_id: string;
  intent_id: string;
  state: DecisionState;
  reason_codes: string[];
  reasons: Reason[];
  headline: string;
  summary: string;
  requirement_results: RequirementResult[];
  evidence_assessments: EvidenceAssessment[];
  evidence_ids: string[];
  evidence_count: number;
  independent_source_count: number | null;
  independence_known: boolean;
  coverage: Num;
  freshness_pass: boolean;
  conflicts: number;
  evidence_cost_usd: Num;
  contract_hash: string;
  evidence_root: string;
  engine_version: string;
  evaluated_at: string;
  fingerprint: string;
  authority_window: { issued_at: string; expires_at: string; ttl_seconds: Num; bounded_by: string } | null;
}

export interface ChainTx {
  id: number;
  operation_id: string;
  purpose: string;
  status: string;
  tx_hash: string | null;
  block_number: number | null;
  gas_used: number | null;
  from_address: string;
  to_address: string | null;
  error_name: string | null;
  error: string | null;
  explorer_url: string | null;
  submitted_at: string | null;
  confirmed_at: string | null;
  events?: { event: string; log_index: number | null; args: Record<string, unknown> }[];
}

export interface CanonicalAction {
  action_type: string;
  asset: string;
  amount: Money;
  chain_id: Num;
  gate_address: string;
  target: string;
  value: Num;
  data: string;
  asset_in: string;
  max_amount_in: Num;
  asset_out: string;
  min_amount_out: Num;
  recipient: string;
  deadline: Num;
  nonce: string;
  adapter: string;
  description: string;
  amount_decimals: number;
  spend_raw: Num | null;
}

export interface AuthorizationView {
  authorization_id: string;
  decision_id: string;
  status: "ISSUED" | "ACTIVE" | "CONSUMED" | "EXPIRED" | "PUBLISH_FAILED";
  onchain_intent_id: string;
  contract_hash: string;
  evidence_root: string;
  action_hash: string;
  executor: string;
  nonce: string;
  issued_at: string;
  expires_at: string;
  chain_id: Num;
  gate_address: string;
  register_tx_hash: string | null;
  authorize_tx_hash: string | null;
  error: string | null;
  action: CanonicalAction;
  transactions?: ChainTx[];
  onchain?: { exists?: boolean; consumed?: boolean; active?: boolean; expires_at?: number; action_hash?: string; evidence_root?: string; error?: string };
}

export interface ExecutionView {
  execution_id: string;
  authorization_id: string | null;
  decision_id: string;
  status: "SUBMITTED" | "EXECUTED" | "FAILED" | "BLOCKED";
  tx_hash: string | null;
  block_number: number | null;
  error_code: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface ExecutionResult extends ExecutionView {
  transactions: ChainTx[];
  receipt_id: string | null;
  idempotent: boolean;
  authorization: AuthorizationView;
}

export interface DecisionView {
  decision: Decision;
  evidence_set_id: string;
  snapshot_hash: string;
  authority: { expires_at: string; bounded_by: string; remaining_seconds: Num } | null;
  authorization: AuthorizationView | null;
  executions: ExecutionView[];
  receipts: { receipt_id: string; kind: string; created_at: string }[];
  /** Set when another decision of the same intent already executed. */
  spent_by: SpentBy | null;
  execution: ExecutionCapability;
  /** AUTHORIZE, intent not yet spent, and an action this server can execute. */
  can_execute: boolean;
}

export interface TestConditionResult {
  condition: Condition;
  requirement_id: string;
  expected_state: DecisionState;
  evidence: EvidenceSetView;
  decision: DecisionView;
}

export interface ReceiptBody {
  receipt_id: string;
  kind: "BLOCKED" | "EXECUTED" | "FAILED" | "EXPIRED";
  intent_id: string;
  intent_text: string;
  intent_hash: string;
  contract_id: string;
  contract_hash: string;
  evidence_root: string;
  action_hash: string | null;
  decision_id: string;
  decision_state: DecisionState;
  reason_codes: string[];
  decision_reason: string;
  engine_version: string;
  evaluated_at: string;
  evidence_count: number;
  evidence_cost_usdc: Num;
  coverage: Num;
  freshness_pass: boolean;
  conflicts: number;
  evidence: { requirement_id: string; claim: string; required: string; observed: string[]; status: RequirementStatus; providers: string[]; modes: SourceMode[] }[];
  payments: { evidence_id: string | null; provider: string; amount: Num | null; currency: string; status: string; payment_reference: string | null; network: string | null }[];
  authorization: Record<string, unknown> | null;
  execution: {
    execution_id: string | null;
    status: string;
    network: string;
    chain_id: Num | null;
    tx_hash: string | null;
    block_number: number | null;
    amount_usdc: Num | null;
    error_code: string | null;
    confirmed_at: string | null;
  };
  protected_amount: Money;
  network: string;
  chain_mode: string;
  created_at: string;
  receipt_hash: string | null;
}

export interface ReceiptView {
  receipt: ReceiptBody;
  receipt_hash: string;
  commitment: { status: string; tx_hash: string | null; error: string | null; transaction: ChainTx | null };
  audit_chain: { step: string; value: string | null; status?: string; committed_tx?: string | null }[];
  timeline: {
    step: "intent" | "contract" | "evidence" | "decision" | "blocked" | "authorized" | "committed" | "executed" | "failed" | "expired" | "receipt";
    label: string;
    at: string | null;
    state?: DecisionState;
    tx_hash?: string | null;
    receipt_hash?: string;
  }[];
  /** Engine reason code per requirement (null when it passed). */
  evidence_reasons: Record<string, string | null>;
}

export interface ReplayCheck {
  group: "receipt" | "decision" | "authority" | "chain" | string;
  check: string;
  expected: unknown;
  actual: unknown;
  ok: boolean | null;
}

export interface ReplayResult {
  result: "MATCH" | "MISMATCH";
  receipt_id: string;
  replayed_at: string;
  market_queried: boolean;
  inputs: { snapshot_hash: string; contract_hash: string; evidence_root: string; evaluated_at: string; engine_version: string; current_engine_version: string; evidence_items: number };
  original: { state: DecisionState; reason_codes: string[]; decision_id: string; reason: string | null };
  replay: { state: DecisionState; reason_codes: string[]; decision_id: string; reason: string | null };
  chain_verification: "VERIFIED" | "SKIPPED" | "NOT_APPLICABLE";
  checks: ReplayCheck[];
}

export interface TamperResult {
  receipt_id: string;
  evidence_id: string;
  claim: string;
  original_value: Num;
  tampered_value: Num;
  original_evidence_root: string;
  recomputed_evidence_root: string;
  tampered_evidence_root: string;
  evidence_root: "MATCH" | "MISMATCH";
  original_receipt_hash: string;
  tampered_receipt_hash: string;
  receipt_integrity: "VALID" | "INVALID";
  onchain_commitment_tx: string | null;
  stored_data_modified: boolean;
}

export interface ChainInfoPublic {
  mode: "LOCAL_EVM" | "MONAD_TESTNET" | "MONAD_MAINNET" | "DISABLED" | string;
  chain_id?: number;
  network?: string;
  evidence_gate?: string;
  usdc?: string | null;
  token_x?: string | null;
  router?: string | null;
  explorer_url?: string | null;
  authorizer?: string;
  executor?: string;
  deployment?: Record<string, unknown>;
  error?: string | null;
}

export interface SystemInfo {
  version: string;
  engine_version: string;
  demo_mode: boolean;
  environment: string;
  intent_compiler: string;
  evidence: { profile: string; sources: Record<string, string[]>; configured_adapters: string[] };
  x402: { enabled: boolean; network: string; asset: string; facilitator: string; max_per_request_usdc: Num; reference_server: string | null; live_evidence_url_configured: boolean };
  chain: ChainInfoPublic;
  execution_adapter: string;
  database: string;
  policy_defaults: Record<string, Num>;
}

export interface ScenarioStep {
  op: string;
  ok: boolean;
  at: string;
  label?: string;
  [key: string]: unknown;
}

export interface ScenarioRun {
  scenario_id: string;
  version: number;
  title: string;
  description: string;
  intent_id: string;
  contract_id: string;
  final_state: DecisionState | null;
  receipt_id: string | null;
  passed: boolean;
  started_at: string;
  finished_at: string;
  timeline: ScenarioStep[];
}

export interface BenchmarkReport {
  passed: boolean;
  total: number;
  engine_version: string;
  benchmark_version: string;
  generated_at: string;
  commit: string;
  metrics: Record<string, Num>;
  categories: Record<string, { total: number; passed: number; expected: string }> | unknown;
  [key: string]: unknown;
}
