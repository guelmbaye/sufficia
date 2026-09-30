"use client";

import Link from "next/link";
import { forwardRef } from "react";

import { Hash } from "@/components/Hash";
import { StateIcon } from "@/components/StateIcon";
import { formatAmount, formatCountdown, formatUsd } from "@/lib/format";
import { useCountdown } from "@/lib/hooks";
import { chainLabel, consequence, DECISION_META, TONE_CLASSES } from "@/lib/states";
import type { AuthorizationView, ContractView, DecisionView } from "@/lib/types";

import type { GateRow } from "./rows";

export type AuthPhase = { kind: "idle" } | { kind: "publishing" } | { kind: "active"; auth: AuthorizationView } | { kind: "error"; message: string; code: string };

function Metric({ label, value, tone }: { label: string; value: string; tone?: "deny" | "allow" | "hold" }) {
  const color = tone === "deny" ? "text-deny" : tone === "allow" ? "text-allow" : tone === "hold" ? "text-hold" : "text-ink";
  return (
    <div className="bg-paper px-3 py-2.5">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className={`font-wide text-lg font-bold tabular ${color}`}>{value}</dd>
    </div>
  );
}

/* The gate: the largest element on the product is the decision (Document 05 §14). */
export const GatePanel = forwardRef<
  HTMLDivElement,
  {
    contract: ContractView;
    view: DecisionView | null;
    rows: GateRow[];
    authPhase: AuthPhase;
    chainMode: string | undefined;
    busy: boolean;
    stage: string | null;
    onExecute: () => void;
    onRefresh: () => void;
    onInspect: (row: GateRow) => void;
    onReauthorize: () => void;
  }
>(function GatePanel({ contract, view, rows, authPhase, chainMode, busy, stage, onExecute, onRefresh, onInspect, onReauthorize }, headRef) {
  const d = view?.decision;
  const meta = d ? DECISION_META[d.state] : null;
  const tone = TONE_CLASSES[meta?.tone ?? "neutral"];
  const amount = formatAmount(contract.contract.action.max_amount.value, contract.contract.action.max_amount.currency);
  const chain = chainLabel(chainMode);
  const leadReason = d?.reasons.find((r) => r.code !== "ALL_REQUIREMENTS_SATISFIED") ?? d?.reasons[0];
  const expires = authPhase.kind === "active" ? authPhase.auth.expires_at : view?.authority?.expires_at;
  const left = useCountdown(d?.state === "AUTHORIZE" ? expires : null);
  const expired = left !== null && left <= 0;
  const ttl = Number(d?.authority_window?.ttl_seconds ?? 30);

  const present = rows.filter((r) => r.result?.checks.present).length;
  const anyFail = rows.some((r) => r.status === "FAIL");
  const allPass = rows.length > 0 && rows.every((r) => r.status === "PASS");
  const conflictRow = rows.find((r) => r.status === "CONFLICT");
  const executed = view?.executions.some((e) => e.status === "EXECUTED");

  return (
    <section
      aria-label="Gate"
      aria-live="polite"
      data-testid="gate"
      data-state={d?.state ?? "PENDING"}
      className="flex min-h-0 flex-col border-y border-r border-rule bg-paper"
      style={{ borderLeft: `10px solid ${tone.stroke}` }}
    >
      <div className="border-b border-rule px-6 py-2.5 text-sm text-muted">Gate</div>
      <div className="flex flex-1 flex-col px-6 pb-5 pt-5">
        {!d ? (
          <div ref={headRef} className="flex items-start gap-3">
            <StateIcon name="pending" size={40} className="mt-1 text-faint" />
            <div>
              <h2 className="font-expanded text-[2.6rem] font-extrabold leading-[1] text-faint">{busy ? "Checking evidence" : "Waiting for evidence"}</h2>
              <p className="mt-2 text-sm text-muted">No authorization exists. No protected transaction has been submitted.</p>
            </div>
          </div>
        ) : (
          <div key={d.decision_id} className="gate-in">
            <div ref={headRef} className={`flex items-start gap-3 ${tone.text}`}>
              <StateIcon name={meta!.icon} size={46} className="mt-1 shrink-0" />
              <h2 className="font-expanded text-[2.1rem] font-extrabold sm:text-[2.85rem] leading-[0.98] tracking-[-0.015em]" data-testid="decision-headline">
                {executed ? `Executed on ${chain.name === "Monad" ? "Monad" : "the gate"}` : meta!.headline}
              </h2>
            </div>
            <p className="mt-4 text-lg leading-snug text-ink">{leadReason?.message ?? meta!.lead}</p>

            {d.state === "DENY" && leadReason?.observed?.length ? (
              <dl className="mt-4 grid grid-cols-2 gap-4">
                <div>
                  <dt className="text-xs text-muted">Observed</dt>
                  <dd className="font-wide text-2xl font-bold tabular text-deny">{formatUsd(leadReason.observed[0])}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted">Required</dt>
                  <dd className="whitespace-nowrap font-wide text-2xl font-bold tabular">{leadReason.required ?? "—"}</dd>
                </div>
              </dl>
            ) : null}

            <p className={`mt-4 border-t border-rule pt-3 text-base font-semibold ${d.state === "AUTHORIZE" ? "text-ink" : "text-ink"}`} data-testid="consequence">
              {executed ? `${amount} moved through the EvidenceGate, once, under this authorization.` : consequence(d.state, amount)}
            </p>
          </div>
        )}

        <dl className="mt-5 grid grid-cols-2 gap-px sm:grid-cols-4 overflow-hidden rounded-sm border border-rule bg-rule">
          <Metric label="Coverage" value={d ? `${present}/${rows.length}` : "—"} tone={d ? (present === rows.length ? "allow" : "hold") : undefined} />
          <Metric label="Freshness" value={d ? (d.freshness_pass ? "Pass" : "Fail") : "—"} tone={d ? (d.freshness_pass ? "allow" : "hold") : undefined} />
          <Metric label="Conflicts" value={d ? String(d.conflicts) : "—"} tone={d ? (d.conflicts ? "hold" : "allow") : undefined} />
          <Metric label="Policy" value={d ? (anyFail ? "Fail" : allPass ? "Pass" : "Incomplete") : "—"} tone={d ? (anyFail ? "deny" : allPass ? "allow" : "hold") : undefined} />
        </dl>

        <div className="mt-auto pt-5">
          {stage ? (
            <p className="mb-3 flex items-center gap-2 text-sm text-ink-2" data-testid="stage">
              <span className="pulse-dot h-2 w-2 rounded-full bg-signal" aria-hidden />
              {stage}
            </p>
          ) : null}

          {d?.state === "AUTHORIZE" && !executed ? (
            authPhase.kind === "active" && !expired ? (
              <div data-testid="authorization">
                <div className="flex items-baseline justify-between">
                  <p className="text-sm font-semibold text-allow">Authorization active</p>
                  <p className="text-sm text-ink-2">
                    Expires in <span className="font-wide text-lg font-bold tabular text-ink" data-testid="countdown">{formatCountdown(left ?? 0)}</span>
                  </p>
                </div>
                <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-rule" aria-hidden>
                  <div className="h-full bg-allow transition-[width] duration-200 ease-linear" style={{ width: `${Math.max(0, Math.min(100, ((left ?? 0) / 1000 / ttl) * 100))}%` }} />
                </div>
                <dl className="mt-3 space-y-1 text-xs">
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-muted">Evidence root</dt>
                    <dd>
                      <Hash value={authPhase.auth.evidence_root} />
                    </dd>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-muted">Action hash</dt>
                    <dd>
                      <Hash value={authPhase.auth.action_hash} />
                    </dd>
                  </div>
                </dl>
                <button
                  type="button"
                  onClick={onExecute}
                  className="mt-4 w-full rounded-sm bg-allow px-5 py-3.5 text-lg font-bold text-paper hover:bg-[#0b6a41]"
                  data-testid="execute"
                >
                  Execute on {chain.name === "Monad" ? "Monad" : "local EVM"}
                </button>
              </div>
            ) : authPhase.kind === "publishing" ? (
              <p className="text-sm text-ink-2">Publishing authorization to {chain.name === "Monad" ? "Monad" : "the local EVM"}…</p>
            ) : expired ? (
              <div>
                <p className="text-sm font-semibold text-hold">Authorization expired: authority is temporary because evidence is temporary.</p>
                <button type="button" onClick={onRefresh} disabled={busy} className="mt-3 w-full rounded-sm border border-ink px-5 py-3 font-semibold hover:bg-ink hover:text-paper disabled:opacity-50">
                  Refresh evidence
                </button>
              </div>
            ) : authPhase.kind === "error" ? (
              <div>
                <p className="text-sm font-semibold text-deny">{authPhase.message}</p>
                <button type="button" onClick={authPhase.code === "AUTHORIZATION_EXPIRED" ? onRefresh : onReauthorize} disabled={busy} className="mt-3 w-full rounded-sm border border-ink px-5 py-3 font-semibold hover:bg-ink hover:text-paper disabled:opacity-50">
                  {authPhase.code === "AUTHORIZATION_EXPIRED" ? "Refresh evidence" : "Publish authorization again"}
                </button>
              </div>
            ) : null
          ) : d && d.state !== "AUTHORIZE" ? (
            <div>
              <button type="button" disabled className="w-full cursor-not-allowed rounded-sm border border-rule-strong bg-canvas px-5 py-3 text-lg font-bold text-faint" data-testid="execute-disabled">
                Execute on {chain.name === "Monad" ? "Monad" : "local EVM"}
              </button>
              <p className="mt-2 text-sm text-muted">No authorization exists, so the EvidenceGate would reject this action.</p>
              <div className="mt-3 flex flex-wrap items-center gap-4 text-sm">
                {d.state === "REFRESH" ? (
                  <button type="button" onClick={onRefresh} disabled={busy} className="rounded-sm bg-ink px-4 py-2 font-semibold text-paper disabled:opacity-50">
                    Refresh evidence
                  </button>
                ) : null}
                {d.state === "ABSTAIN" ? (
                  <button type="button" onClick={onRefresh} disabled={busy} className="rounded-sm bg-ink px-4 py-2 font-semibold text-paper disabled:opacity-50">
                    Acquire evidence again
                  </button>
                ) : null}
                {d.state === "ESCALATE" && conflictRow ? (
                  <button type="button" onClick={() => onInspect(conflictRow)} className="rounded-sm bg-ink px-4 py-2 font-semibold text-paper">
                    Inspect conflict
                  </button>
                ) : null}
                {view?.receipts.length ? (
                  <Link href={`/receipt/${view.receipts[view.receipts.length - 1]!.receipt_id}`} className="text-signal underline underline-offset-4">
                    Open blocked action receipt
                  </Link>
                ) : null}
              </div>
            </div>
          ) : executed ? (
            <div className="flex items-center gap-4 text-sm">
              {view?.receipts.length ? (
                <Link href={`/receipt/${view.receipts[view.receipts.length - 1]!.receipt_id}`} className="rounded-sm bg-ink px-4 py-2 font-semibold text-paper">
                  Open receipt
                </Link>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
});
