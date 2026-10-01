"use client";

import Link from "next/link";
import { forwardRef } from "react";

import { Hash } from "@/components/Hash";
import { StateIcon } from "@/components/StateIcon";
import { formatAmount, formatCountdown, formatTime, withProviderLabels } from "@/lib/format";
import { useCountdown } from "@/lib/hooks";
import { ALREADY_EXECUTED, chainLabel, consequence, DECISION_META, executionNote, EXPIRED_AUTHORITY, NOT_EXECUTABLE, TONE_CLASSES } from "@/lib/states";
import type { AuthorizationView, ContractView, DecisionView } from "@/lib/types";

import type { GateRow } from "./rows";

export type AuthPhase = { kind: "idle" } | { kind: "publishing" } | { kind: "active"; auth: AuthorizationView } | { kind: "error"; message: string; code: string };

function Metric({ label, value, tone }: { label: string; value: string; tone?: "deny" | "allow" | "hold" }) {
  const color = tone === "deny" ? "text-deny" : tone === "allow" ? "text-allow" : tone === "hold" ? "text-hold" : "text-ink";
  return (
    <div className="bg-paper px-3 py-2">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className={`font-wide text-lg font-bold tabular ${color}`}>{value}</dd>
    </div>
  );
}

const primary = "rounded-sm bg-ink px-4 py-2 font-semibold text-paper hover:bg-ink-2 disabled:opacity-50";

/* The gate: the largest element on the product is the decision (Document 05 §14). Every sentence
   in it says something the evidence rows do not: the decision, its reason, and what it means for
   the money. */
export const GatePanel = forwardRef<
  HTMLDivElement,
  {
    contract: ContractView;
    view: DecisionView | null;
    rows: GateRow[];
    authPhase: AuthPhase;
    expired: boolean;
    chainMode: string | undefined;
    busy: boolean;
    stage: string | null;
    onExecute: () => void;
    onRefresh: () => void;
    onInspect: (row: GateRow) => void;
    onReauthorize: () => void;
  }
>(function GatePanel({ contract, view, rows, authPhase, expired, chainMode, busy, stage, onExecute, onRefresh, onInspect, onReauthorize }, headRef) {
  const d = view?.decision;
  const chain = chainLabel(chainMode);
  const amount = formatAmount(contract.contract.action.max_amount.value, contract.contract.action.max_amount.currency);
  const executed = Boolean(view?.executions.some((e) => e.status === "EXECUTED"));
  const expires = authPhase.kind === "active" ? authPhase.auth.expires_at : view?.authority?.expires_at;
  const left = useCountdown(d?.state === "AUTHORIZE" && !executed && !expired ? expires : null);
  const ttl = Number(d?.authority_window?.ttl_seconds ?? contract.contract.authorization.ttl_seconds);

  const meta = d ? DECISION_META[d.state] : null;
  const spent = executed ? null : (view?.spent_by ?? null);
  const authorizeLike = d?.state === "AUTHORIZE" && !executed;
  const alreadyDone = authorizeLike && Boolean(spent);
  const cannotExecute = authorizeLike && !spent && view !== null && !view.execution.executable;
  const look = alreadyDone ? ALREADY_EXECUTED : cannotExecute ? NOT_EXECUTABLE : expired ? EXPIRED_AUTHORITY : meta;
  const tone = TONE_CLASSES[look?.tone ?? "neutral"];
  const leadReason = d?.reasons.find((r) => r.code !== "ALL_REQUIREMENTS_SATISFIED") ?? d?.reasons[0];
  const headline = executed ? `Executed on ${chain.isMonad ? "Monad" : "the local EVM"}` : look?.headline;
  const lead = alreadyDone
    ? `Every mandatory evidence requirement is satisfied, but this intent's ${amount} already moved once, under an earlier decision.`
    : cannotExecute
      ? `Every mandatory evidence requirement is satisfied, but ${executionNote(view?.execution)}.`
      : expired
        ? `Every requirement was satisfied at decision time, but the authority window closed at ${formatTime(expires)} UTC, before execution.`
        : withProviderLabels(leadReason?.message ?? meta?.lead ?? "");
  const outcome = executed
    ? `${amount} moved through the EvidenceGate, once, under this authorization.`
    : alreadyDone
      ? "This decision authorizes nothing new: an intent is spent at most once."
      : spent
        ? `This intent's ${amount} already moved once, under an earlier decision; this decision authorizes nothing new.`
        : cannotExecute
          ? `${amount} did not move. No authorization is issued.`
        : expired
          ? `${amount} did not move. This authorization can no longer be used.`
          : d
            ? consequence(d.state, amount)
            : "";
  const executeLabel = `Execute on ${chain.isMonad ? "Monad" : chain.name}`;

  const present = rows.filter((r) => r.result?.checks.present).length;
  const anyFail = rows.some((r) => r.status === "FAIL");
  const allPass = rows.length > 0 && rows.every((r) => r.status === "PASS");
  const conflictRow = rows.find((r) => r.status === "CONFLICT");
  const receiptId = view?.receipts.at(-1)?.receipt_id;

  return (
    <section
      aria-label="Gate"
      aria-live="polite"
      data-testid="gate"
      data-state={d?.state ?? "PENDING"}
      data-expired={expired || undefined}
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
          <div key={`${d.decision_id}-${expired}`} className="gate-in">
            <div ref={headRef} className={`flex items-start gap-3 ${tone.text}`}>
              <StateIcon name={executed ? "allow" : look!.icon} size={46} className="mt-1 shrink-0" />
              <h2
                className={`font-expanded font-extrabold leading-[0.98] tracking-[-0.015em] ${
                  (headline ?? "").split(" ").some((w) => w.length > 10) ? "text-[1.7rem] sm:text-[2rem]" : "text-[2.1rem] sm:text-[2.85rem]"
                }`}
                data-testid="decision-headline"
              >
                {headline}
              </h2>
            </div>
            <p className="mt-4 text-lg leading-snug text-ink" data-testid="decision-reason">
              {lead}
            </p>
            <p id="gate-consequence" className="mt-4 border-t border-rule pt-3 text-base font-semibold text-ink" data-testid="consequence">
              {outcome}
            </p>
          </div>
        )}

        <dl className="mt-5 grid grid-cols-2 gap-px overflow-hidden rounded-sm border border-rule bg-rule">
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

          {authorizeLike && (alreadyDone || cannotExecute) ? (
            <div className="flex flex-wrap items-center gap-4 text-sm">
              {alreadyDone && spent?.receipt_id ? (
                <Link href={`/receipt/${spent.receipt_id}`} className={primary}>
                  Open receipt
                </Link>
              ) : null}
              <Link href="/" className="text-signal underline underline-offset-4">
                Start a new mission
              </Link>
            </div>
          ) : authorizeLike ? (
            expired ? (
              <button type="button" onClick={onRefresh} disabled={busy} className={`w-full py-3 text-base ${primary}`}>
                Refresh evidence
              </button>
            ) : authPhase.kind === "active" ? (
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
                  {executeLabel}
                </button>
              </div>
            ) : authPhase.kind === "error" ? (
              <div>
                <p className="text-sm font-semibold text-deny">{authPhase.message}</p>
                <button
                  type="button"
                  onClick={authPhase.code === "AUTHORIZATION_EXPIRED" ? onRefresh : onReauthorize}
                  disabled={busy}
                  className={`mt-3 w-full py-3 ${primary}`}
                >
                  {authPhase.code === "AUTHORIZATION_EXPIRED" ? "Refresh evidence" : "Publish authorization again"}
                </button>
              </div>
            ) : null
          ) : d && d.state !== "AUTHORIZE" ? (
            <div>
              <button
                type="button"
                disabled
                aria-describedby="gate-consequence"
                className="w-full cursor-not-allowed rounded-sm border border-rule-strong bg-canvas px-5 py-3 text-lg font-bold text-faint"
                data-testid="execute-disabled"
              >
                {executeLabel}
              </button>
              <div className="mt-3 flex flex-wrap items-center gap-4 text-sm">
                {d.state === "REFRESH" ? (
                  <button type="button" onClick={onRefresh} disabled={busy} className={primary}>
                    Refresh evidence
                  </button>
                ) : null}
                {d.state === "ABSTAIN" ? (
                  <button type="button" onClick={onRefresh} disabled={busy} className={primary}>
                    Acquire evidence again
                  </button>
                ) : null}
                {d.state === "ESCALATE" && conflictRow ? (
                  <button type="button" onClick={() => onInspect(conflictRow)} className={primary}>
                    Inspect conflict
                  </button>
                ) : null}
                {receiptId ? (
                  <Link href={`/receipt/${receiptId}`} className="text-signal underline underline-offset-4">
                    Open receipt
                  </Link>
                ) : null}
              </div>
            </div>
          ) : executed && receiptId ? (
            <Link href={`/receipt/${receiptId}`} className={`inline-block text-sm ${primary}`}>
              Open receipt
            </Link>
          ) : null}
        </div>
      </div>
    </section>
  );
});
