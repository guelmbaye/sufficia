/* Vocabulary of states (Document 05 §15-20, §63-71). One source for headline, tone, icon and the
   next action, so a state is never named two ways. DENIED (policy) is never FAILED (execution). */

import type { Condition, DecisionState, RequirementResult, RequirementStatus } from "./types";

export type Tone = "deny" | "allow" | "hold" | "abstain" | "neutral";
export type IconName = "deny" | "allow" | "stale" | "conflict" | "missing" | "pending";

export interface StateMeta {
  state: DecisionState;
  headline: string;
  short: string;
  tone: Tone;
  icon: IconName;
  lead: string;
}

export const DECISION_META: Record<DecisionState, StateMeta> = {
  DENY: {
    state: "DENY",
    headline: "Execution denied",
    short: "Denied",
    tone: "deny",
    icon: "deny",
    lead: "A mandatory requirement failed on valid, fresh evidence.",
  },
  ABSTAIN: {
    state: "ABSTAIN",
    headline: "Agent abstained",
    short: "Abstained",
    tone: "abstain",
    icon: "missing",
    lead: "Required evidence could not be established. The system does not know enough to authorize execution.",
  },
  REFRESH: {
    state: "REFRESH",
    headline: "Evidence expired",
    short: "Refresh",
    tone: "hold",
    icon: "stale",
    lead: "One required observation is older than its allowed freshness window.",
  },
  ESCALATE: {
    state: "ESCALATE",
    headline: "Human review required",
    short: "Escalated",
    tone: "hold",
    icon: "conflict",
    lead: "Two valid sources disagree beyond the allowed tolerance.",
  },
  AUTHORIZE: {
    state: "AUTHORIZE",
    headline: "Execution authorized",
    short: "Authorized",
    tone: "allow",
    icon: "allow",
    lead: "Every mandatory evidence requirement is satisfied.",
  },
};

export const TONE_CLASSES: Record<Tone, { text: string; bg: string; border: string; stroke: string }> = {
  deny: { text: "text-deny", bg: "bg-deny-tint", border: "border-deny", stroke: "var(--color-deny)" },
  allow: { text: "text-allow", bg: "bg-allow-tint", border: "border-allow", stroke: "var(--color-allow)" },
  hold: { text: "text-hold", bg: "bg-hold-tint", border: "border-hold", stroke: "var(--color-hold)" },
  abstain: { text: "text-abstain", bg: "bg-abstain-tint", border: "border-abstain", stroke: "var(--color-abstain)" },
  neutral: { text: "text-muted", bg: "bg-canvas", border: "border-rule-strong", stroke: "var(--color-rule-strong)" },
};

export type RowStatus = "PASS" | "FAIL" | "STALE" | "CONFLICT" | "MISSING" | "PENDING";

export const ROW_META: Record<RowStatus, { label: string; tone: Tone; icon: IconName }> = {
  PASS: { label: "Pass", tone: "allow", icon: "allow" },
  FAIL: { label: "Fail", tone: "deny", icon: "deny" },
  STALE: { label: "Stale", tone: "hold", icon: "stale" },
  CONFLICT: { label: "Conflict", tone: "hold", icon: "conflict" },
  MISSING: { label: "Missing", tone: "abstain", icon: "missing" },
  PENDING: { label: "Waiting", tone: "neutral", icon: "pending" },
};

/** Status of one requirement from the engine's status and reason code (never recomputed here). */
export function requirementStatus(status: RequirementStatus, reasonCode: string | null | undefined): Exclude<RowStatus, "PENDING"> {
  if (status === "PASS") return "PASS";
  if (status === "FAIL") return "FAIL";
  switch (reasonCode) {
    case "EVIDENCE_STALE":
      return "STALE";
    case "CRITICAL_SOURCE_CONFLICT":
      return "CONFLICT";
    default:
      return "MISSING";
  }
}

/** The status a requirement row shows, from the engine's own result. */
export function rowStatus(result: RequirementResult | undefined): RowStatus {
  return result ? requirementStatus(result.status, result.reason_code) : "PENDING";
}

/** An AUTHORIZE decision whose authority window closed before execution (Document 05 §31). */
export const EXPIRED_AUTHORITY = {
  headline: "Authorization expired",
  tone: "hold" as Tone,
  icon: "stale" as IconName,
};

/** Consequence line (Document 05 §15: "This last sentence is extremely important."). */
export function consequence(state: DecisionState, amount: string): string {
  return state === "AUTHORIZE"
    ? `Authority for ${amount} is bound to this evidence set and this exact action, and expires with the evidence.`
    : `${amount} did not move. No execution authorization was issued.`;
}

export interface ChainLabel {
  /** "Monad" / "local EVM": inline name. */
  name: string;
  /** Header badge wording, never hiding a rehearsal. */
  honest: string;
  /** Badge wording on phones. */
  short: string;
  /** Value of a "Network" field. */
  display: string;
  /** "Submitting to {where}". */
  where: string;
  isMonad: boolean;
}

export function chainLabel(mode: string | undefined): ChainLabel {
  switch (mode) {
    case "MONAD_MAINNET":
      return { name: "Monad", honest: "Monad mainnet", short: "Monad mainnet", display: "Monad mainnet", where: "Monad", isMonad: true };
    case "MONAD_TESTNET":
      return { name: "Monad", honest: "Monad testnet", short: "Monad testnet", display: "Monad testnet", where: "Monad", isMonad: true };
    case "LOCAL_EVM":
      return {
        name: "local EVM",
        honest: "Local EVM rehearsal (not Monad)",
        short: "Local EVM, not Monad",
        display: "Local EVM",
        where: "the local EVM",
        isMonad: false,
      };
    case "DISABLED":
      return { name: "chain", honest: "Chain disabled", short: "Chain disabled", display: "Chain disabled", where: "the chain", isMonad: false };
    default:
      return { name: "chain", honest: "Chain status unknown", short: "Chain unknown", display: "Unknown", where: "the chain", isMonad: false };
  }
}

/** Explorer link for a transaction: the API's own link, else one built only for a Monad chain. */
export function explorerTx(
  explorerUrl: string | null | undefined,
  chainMode: string | undefined,
  hash: string | null | undefined,
  tx?: { explorer_url: string | null } | null,
): string | null {
  if (tx?.explorer_url) return tx.explorer_url;
  return hash && explorerUrl && chainLabel(chainMode).isMonad ? `${explorerUrl}/tx/${hash}` : null;
}

/** Test conditions (Document 05 §24-26): each changes exactly one piece of evidence on the same contract. */
export const CONDITIONS: { id: Condition; label: string; word: string; hint: string }[] = [
  { id: "valid", label: "Valid", word: "valid", hint: "Every requirement satisfied by fresh evidence. Expected: authorized." },
  { id: "threshold", label: "Threshold fail", word: "threshold fail", hint: "One requirement misses its threshold on fresh evidence. Expected: denied." },
  { id: "missing", label: "Missing", word: "missing", hint: "One required observation is removed. Expected: the agent abstains." },
  { id: "stale", label: "Stale", word: "stale", hint: "One observation is older than its maximum age. Expected: evidence expired." },
  { id: "conflict", label: "Conflict", word: "conflict", hint: "A second source disagrees beyond the tolerance. Expected: human review." },
];

/** Backend condition names (the lab says "remove") in UI words. */
export function conditionWord(condition: string): string {
  return CONDITIONS.find((c) => c.id === (condition === "remove" ? "missing" : condition))?.word ?? condition;
}
