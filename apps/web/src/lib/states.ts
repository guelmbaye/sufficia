/* Vocabulary of states (Document 05 §15-20, §63-71). One source for headline, tone, icon and the
   next action, so a state is never named two ways. DENIED (policy) is never FAILED (execution). */

import type { DecisionState, RequirementResult } from "./types";

export type Tone = "deny" | "allow" | "hold" | "abstain" | "neutral";
export type IconName = "deny" | "allow" | "stale" | "conflict" | "missing" | "invalid" | "pending";

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

/** The status a requirement row shows, from the engine's own result (never recomputed here). */
export function rowStatus(result: RequirementResult | undefined): RowStatus {
  if (!result) return "PENDING";
  if (result.status === "PASS") return "PASS";
  if (result.status === "FAIL") return "FAIL";
  switch (result.reason_code) {
    case "EVIDENCE_STALE":
      return "STALE";
    case "CRITICAL_SOURCE_CONFLICT":
      return "CONFLICT";
    default:
      return "MISSING";
  }
}

/** Consequence line (Document 05 §15: "This last sentence is extremely important."). */
export function consequence(state: DecisionState, amount: string): string {
  return state === "AUTHORIZE"
    ? `Authority for ${amount} is bound to this evidence set and this exact action, and expires with the evidence.`
    : `${amount} did not move. No execution authorization was issued.`;
}

export function chainLabel(mode: string | undefined): { name: string; honest: string; short: string; isMonad: boolean } {
  switch (mode) {
    case "MONAD_MAINNET":
      return { name: "Monad", honest: "Monad mainnet", short: "Monad mainnet", isMonad: true };
    case "MONAD_TESTNET":
      return { name: "Monad", honest: "Monad testnet", short: "Monad testnet", isMonad: true };
    case "LOCAL_EVM":
      return { name: "local EVM", honest: "Local EVM rehearsal (not Monad)", short: "Local EVM, not Monad", isMonad: false };
    case "DISABLED":
      return { name: "chain", honest: "Chain disabled", short: "Chain disabled", isMonad: false };
    default:
      return { name: "chain", honest: "Chain status unknown", short: "Chain unknown", isMonad: false };
  }
}
