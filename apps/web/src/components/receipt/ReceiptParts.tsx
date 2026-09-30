"use client";

import { useState } from "react";

import { Hash } from "@/components/Hash";
import { StateIcon } from "@/components/StateIcon";
import { claimLabel, formatClaimValue, formatTime, plainRequirement, providerLabel } from "@/lib/format";
import { DECISION_META, ROW_META, TONE_CLASSES } from "@/lib/states";
import type { DecisionState } from "@/lib/types";
import type { ReceiptView } from "@/lib/types";

const MODE_LABEL: Record<string, string> = { LIVE: "live", RECORDED: "recorded", SIMULATED: "simulated", INJECTED: "test condition" };

/** Chronological, forensic history of one decision (Document 05 §34), from stored timestamps only. */
export function ReceiptTimeline({ view, explorer }: { view: ReceiptView; explorer: (h: string) => string | null }) {
  return (
    <ol className="relative" data-testid="timeline">
      {view.timeline.map((s, i) => {
        const tone =
          s.step === "blocked" || s.step === "expired"
            ? "text-deny"
            : s.step === "executed" || s.step === "committed" || s.step === "authorized"
              ? "text-allow"
              : "text-ink";
        return (
          <li key={`${s.step}-${i}`} className="relative grid grid-cols-[2.25rem_7.5rem_1fr] items-start gap-3 pb-4 last:pb-0">
            {i < view.timeline.length - 1 ? <span className="absolute left-[0.72rem] top-6 h-[calc(100%-1rem)] w-px bg-rule-strong" aria-hidden /> : null}
            <span className="relative z-10 grid h-6 w-6 place-items-center rounded-full border border-ink/70 bg-paper text-2xs font-semibold tabular">
              {String(i + 1).padStart(2, "0")}
            </span>
            <span className="pt-0.5 font-mono text-xs text-muted tabular">{formatTime(s.at)}</span>
            <span className="pt-px">
              <span className={`font-semibold ${tone}`}>{s.label}</span>
              {s.state ? <span className="ml-2 text-sm text-muted">{DECISION_META[s.state as DecisionState]?.short.toLowerCase() ?? s.state}</span> : null}
              {s.tx_hash ? (
                <span className="mt-0.5 block text-sm">
                  <Hash value={s.tx_hash} href={explorer(s.tx_hash)} />
                </span>
              ) : null}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/** Requirement / observed / required / result (Document 05 §35). No raw payloads by default. */
export function EvidenceTable({ view }: { view: ReceiptView }) {
  return (
    <table className="w-full min-w-[36rem] border-collapse text-left text-sm" data-testid="evidence-table">
      <thead>
        <tr className="border-b border-ink/70 text-muted">
          <th className="py-2 pr-4 font-medium">Requirement</th>
          <th className="py-2 pr-4 font-medium">Observed</th>
          <th className="py-2 pr-4 font-medium">Required</th>
          <th className="py-2 pr-4 font-medium">Source</th>
          <th className="py-2 font-medium">Result</th>
        </tr>
      </thead>
      <tbody>
        {view.receipt.evidence.map((e) => {
          const status = e.status === "PASS" ? "PASS" : e.status === "FAIL" ? "FAIL" : "MISSING";
          const meta = ROW_META[status];
          const tone = TONE_CLASSES[meta.tone];
          const unit = e.claim === "asset_price" || e.claim.includes("liquidity") || e.claim.includes("inflow") ? "USD" : null;
          return (
            <tr key={e.requirement_id} className="border-b border-rule">
              <td className="py-3 pr-4 font-medium">{claimLabel(e.claim)}</td>
              <td className="py-3 pr-4 font-wide font-bold tabular">
                {e.observed.length ? e.observed.map((o) => formatClaimValue(o, unit, e.claim)).join(" vs ") : "none"}
              </td>
              <td className="py-3 pr-4 tabular">{plainRequirement(e.required)}</td>
              <td className="py-3 pr-4 text-muted">
                {e.providers.map(providerLabel).join(", ") || "—"}
                {e.modes.length ? <span className="block text-2xs">{e.modes.map((m) => MODE_LABEL[m] ?? m).join(", ")}</span> : null}
              </td>
              <td className={`py-3 font-bold ${tone.text}`}>
                <span className="inline-flex items-center gap-1.5">
                  <StateIcon name={meta.icon} size={16} />
                  {e.status === "UNKNOWN" ? "Not established" : meta.label}
                </span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** Receipt integrity (Document 05 §36) with "Copy verification data". */
export function IntegrityPanel({ view, explorer }: { view: ReceiptView; explorer: (h: string) => string | null }) {
  const [copied, setCopied] = useState(false);
  const r = view.receipt;
  const data = {
    receipt_id: r.receipt_id,
    receipt_hash: view.receipt_hash,
    intent_hash: r.intent_hash,
    contract_hash: r.contract_hash,
    evidence_root: r.evidence_root,
    action_hash: r.action_hash,
    authorization_id: (r.authorization?.authorization_id as string | undefined) ?? null,
    execution_tx: r.execution.tx_hash,
    receipt_commitment_tx: view.commitment.tx_hash,
    engine_version: r.engine_version,
    evaluated_at: r.evaluated_at,
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };
  const rows: [string, string | null, boolean][] = [
    ["Contract hash", r.contract_hash, false],
    ["Evidence root", r.evidence_root, false],
    ["Action hash", r.action_hash, false],
    ["Receipt hash", view.receipt_hash, false],
    ["Receipt committed onchain", view.commitment.tx_hash, true],
  ];
  return (
    <div>
      <dl className="divide-y divide-rule border-y border-rule">
        {rows.map(([label, value, isTx]) => (
          <div key={label} className="grid grid-cols-[11rem_1fr] items-center gap-3 py-2 text-sm">
            <dt className="text-muted">{label}</dt>
            <dd className="min-w-0">{value ? <Hash value={value} href={isTx ? explorer(value) : null} /> : <span className="text-muted">none</span>}</dd>
          </div>
        ))}
        <div className="grid grid-cols-[11rem_1fr] items-center gap-3 py-2 text-sm">
          <dt className="text-muted">Engine version</dt>
          <dd className="font-mono text-[0.8125rem]">{r.engine_version}</dd>
        </div>
      </dl>
      <button type="button" onClick={copy} className="mt-3 rounded-sm border border-ink/70 px-3 py-1.5 text-sm font-medium hover:bg-canvas">
        {copied ? "Verification data copied" : "Copy verification data"}
      </button>
    </div>
  );
}
