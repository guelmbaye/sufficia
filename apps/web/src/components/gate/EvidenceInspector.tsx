"use client";

import { Drawer, Field } from "@/components/Drawer";
import { Hash } from "@/components/Hash";
import { formatAge, formatAmount, formatClaimValue, formatDateTime, providerLabel } from "@/lib/format";

import type { GateRow } from "./rows";

/** Evidence Inspector (Document 05 §61): provenance, freshness and payment of each observation. */
export function EvidenceInspector({ row, onClose }: { row: GateRow | null; onClose: () => void }) {
  return (
    <Drawer open={Boolean(row)} onClose={onClose} title={row ? `Evidence: ${row.requirement.label ?? row.requirement.claim}` : "Evidence"}>
      {row ? (
        <>
          <dl className="mb-6">
            <Field label="Requirement">
              <span className="font-mono text-xs">{row.requirement.id}</span>, {row.result?.required_expression ?? row.requirement.operator}
            </Field>
            <Field label="Engine result">
              {row.result?.status ?? "not evaluated"}
              {row.result?.reason_code ? <span className="ml-2 font-mono text-xs text-muted">{row.result.reason_code}</span> : null}
            </Field>
            <Field label="Explanation">{row.result?.human_reason || "—"}</Field>
          </dl>

          {row.items.length === 0 ? <p className="mb-6 text-sm text-muted">No observation was acquired for this requirement.</p> : null}

          {row.items.map((v) => {
            const it = v.item;
            const a = row.assessments.find((x) => x.evidence_id === it.evidence_id);
            const pay = it.economic.payment;
            return (
              <section key={it.evidence_id} className="mb-6 border-t border-rule pt-4">
                <h3 className="mb-2 font-wide text-xl font-bold tabular">{formatClaimValue(it.claim.value, it.claim.unit, it.claim.name)}</h3>
                <dl>
                  <Field label="Evidence id">
                    <span className="font-mono text-xs">{it.evidence_id}</span>
                  </Field>
                  <Field label="Provider">
                    {providerLabel(it.source.provider)} <span className="text-muted">({it.source.provider})</span>
                  </Field>
                  <Field label="Source mode">{it.source.mode}</Field>
                  <Field label="Source type">{it.source.source_type}</Field>
                  {it.source.upstream_source ? <Field label="Upstream">{it.source.upstream_source}</Field> : null}
                  <Field label="Observed at">{formatDateTime(it.temporal.observed_at)}</Field>
                  <Field label="Received at">{formatDateTime(it.temporal.received_at)}</Field>
                  <Field label="At decision">
                    {a ? `${a.status}, age ${formatAge(a.age_seconds)}` : "—"}
                    {a?.reason_codes.length ? <span className="ml-2 font-mono text-xs text-muted">{a.reason_codes.join(", ")}</span> : null}
                  </Field>
                  <Field label="Request hash">
                    <Hash value={it.integrity.request_hash} />
                  </Field>
                  <Field label="Response hash">
                    <Hash value={it.integrity.response_hash} />
                  </Field>
                  <Field label="Item hash">
                    <Hash value={v.item_hash} />
                  </Field>
                  <Field label="Payment">
                    {pay ? (
                      <>
                        {pay.protocol} {pay.scheme} on {pay.network}, {formatAmount(pay.settled_amount ?? pay.quoted_amount, pay.currency)},{" "}
                        settlement {pay.status}
                        <div className="mt-1">
                          <Hash value={pay.payment_reference} />
                        </div>
                      </>
                    ) : (
                      "Not paid"
                    )}
                  </Field>
                </dl>
                <details className="mt-3">
                  <summary className="cursor-pointer text-sm text-ink-2">Canonical JSON</summary>
                  <pre className="mt-2 max-h-72 overflow-auto rounded-sm border border-rule bg-canvas p-3 font-mono text-[0.7rem] leading-relaxed">
                    {JSON.stringify(it, null, 2)}
                  </pre>
                </details>
              </section>
            );
          })}

          {row.failures.length ? (
            <section className="border-t border-rule pt-4">
              <h3 className="mb-2 text-base font-semibold">Acquisition failures</h3>
              <ul className="space-y-1 text-sm">
                {row.failures.map((f, i) => (
                  <li key={i}>
                    {providerLabel(f.provider)}: <span className="font-mono text-xs">{f.code}</span> {f.detail}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </>
      ) : null}
    </Drawer>
  );
}
