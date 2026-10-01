"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect } from "react";

import { LoadError } from "@/components/LoadError";
import { CompareRuns } from "@/components/receipt/CompareRuns";
import { EvidenceTable, IntegrityPanel, ReceiptTimeline } from "@/components/receipt/ReceiptParts";
import { ReplayPanel, TamperTest } from "@/components/receipt/Replay";
import { StateIcon } from "@/components/StateIcon";
import { api } from "@/lib/api";
import { formatAmount, formatDateTime, withProviderLabels } from "@/lib/format";
import { useLoad, useSystem } from "@/lib/hooks";
import { useRun } from "@/lib/run";
import { chainLabel, DECISION_META, explorerTx, TONE_CLASSES } from "@/lib/states";

const HEADLINE: Record<string, string> = {
  EXECUTED: "Why did this action happen?",
  BLOCKED: "Blocked action receipt",
  FAILED: "Failed execution receipt",
  EXPIRED: "Expired authorization receipt",
};

/* Screen 05 — Evidence Execution Receipt and Decision Replay (Document 05 §33-41, §95-98). */
export default function ReceiptPage() {
  const { receiptId } = useParams<{ receiptId: string }>();
  const { update } = useRun();
  const { system } = useSystem();
  const { data: view, error } = useLoad(receiptId, () => api.receipt(receiptId));

  useEffect(() => {
    if (view) {
      update({ receiptId, decisionId: view.receipt.decision_id, decisionState: view.receipt.decision_state, contractId: view.receipt.contract_id });
    }
  }, [view, receiptId, update]);

  if (error) return <LoadError error={error} what="Receipt" id={receiptId} />;
  if (!view) return <p className="mx-auto max-w-[1300px] px-6 py-12 text-muted">Loading the receipt…</p>;

  const r = view.receipt;
  // The chain this receipt was produced on, not the chain the server runs on today.
  const chain = chainLabel(r.chain_mode);
  const meta = DECISION_META[r.decision_state];
  const tone = TONE_CLASSES[meta.tone];
  const executed = r.kind === "EXECUTED";
  const amount = formatAmount(r.protected_amount.value, r.protected_amount.currency);
  const explorer = (h: string) => explorerTx(system?.chain?.explorer_url, r.chain_mode, h);
  const execLabel = { EXECUTED: "Confirmed", BLOCKED: "Blocked", FAILED: "Failed", EXPIRED: "Not submitted" }[r.kind];
  const callout =
    r.kind === "BLOCKED"
      ? { border: tone.border, text: withProviderLabels(r.decision_reason) }
      : r.kind === "EXPIRED"
        ? { border: "border-hold", text: "The authorization expired before execution, so no transaction was submitted." }
        : r.kind === "FAILED"
          ? { border: "border-deny", text: `The authorized transaction failed during execution${r.execution.error_code ? ` (${r.execution.error_code})` : ""}.` }
          : null;

  return (
    <div className="mx-auto max-w-[1300px] px-6 pb-16 pt-8">
      <p className="text-sm text-muted">
        Receipt {r.receipt_id}, created {formatDateTime(r.created_at)}
      </p>
      <h1 className="mt-1 font-expanded text-[2rem] font-extrabold leading-tight sm:text-[2.6rem]" data-testid="receipt-headline">
        {HEADLINE[r.kind]}
      </h1>
      <p className="mt-2 max-w-[80ch] text-lg text-ink-2">&ldquo;{r.intent_text}&rdquo;</p>

      <dl className="mt-6 grid grid-cols-2 gap-px overflow-hidden rounded-sm border border-rule bg-rule md:grid-cols-4">
        <div className="bg-paper px-4 py-4 sm:px-5">
          <dt className="text-sm text-muted">Decision</dt>
          <dd className={`mt-1 flex items-center gap-2 font-expanded text-lg font-extrabold sm:text-xl ${tone.text}`}>
            <StateIcon name={meta.icon} size={22} />
            {meta.short}
          </dd>
        </div>
        <div className="bg-paper px-4 py-4 sm:px-5">
          <dt className="text-sm text-muted">Execution</dt>
          <dd className={`mt-1 font-expanded text-lg font-extrabold sm:text-xl ${executed ? "text-allow" : r.kind === "FAILED" ? "text-deny" : "text-ink"}`}>{execLabel}</dd>
        </div>
        <div className="bg-paper px-4 py-4 sm:px-5">
          <dt className="text-sm text-muted">{executed ? "Amount" : "Protected amount"}</dt>
          <dd className="mt-1 font-wide text-lg font-bold sm:text-xl tabular">{amount}</dd>
          {!executed ? <dd className="text-sm font-semibold">did not move</dd> : null}
        </div>
        <div className="bg-paper px-4 py-4 sm:px-5">
          <dt className="text-sm text-muted">Network</dt>
          <dd className="mt-1 font-wide text-lg font-bold sm:text-xl">{chain.display}</dd>
          {r.chain_mode === "LOCAL_EVM" ? <dd className="text-xs text-muted">rehearsal, not Monad</dd> : null}
        </div>
      </dl>

      {callout ? (
        <p className={`mt-4 border-l-[5px] ${callout.border} bg-paper px-5 py-4 font-semibold`} data-testid="receipt-callout">
          {callout.text}
        </p>
      ) : null}

      <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
        <section aria-label="Timeline">
          <h2 className="mb-4 text-lg font-semibold">History</h2>
          <ReceiptTimeline view={view} explorer={explorer} />
        </section>
        <section aria-label="Evidence and integrity" className="space-y-8">
          <div>
            <h2 className="mb-2 text-lg font-semibold">Evidence</h2>
            <div className="overflow-x-auto">
              <EvidenceTable view={view} />
            </div>
            <p className="mt-2 text-sm text-muted">
              {r.evidence_count} observations, {formatAmount(r.evidence_cost_usdc)} spent on evidence, evaluated {formatDateTime(r.evaluated_at)}.
            </p>
          </div>
          <div>
            <h2 className="mb-2 text-lg font-semibold">Integrity</h2>
            <IntegrityPanel view={view} explorer={explorer} />
          </div>
        </section>
      </div>

      <div className="mt-10 grid grid-cols-1 gap-8 border-t border-ink/80 pt-8 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <ReplayPanel receiptId={receiptId} />
        <TamperTest receiptId={receiptId} />
      </div>

      <div className="mt-10">
        <CompareRuns contractId={r.contract_id} />
      </div>

      <p className="mt-8 text-sm">
        <Link href={`/gate/${r.contract_id}`} className="text-signal underline underline-offset-4">
          Back to the live gate
        </Link>
      </p>
    </div>
  );
}
