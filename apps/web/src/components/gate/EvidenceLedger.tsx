"use client";

import { forwardRef } from "react";

import { StateIcon } from "@/components/StateIcon";
import { claimLabel, formatAge, formatAmount, formatClaimValue, plainRequirement, providerLabel } from "@/lib/format";
import { ROW_META, TONE_CLASSES } from "@/lib/states";
import type { EvidenceItemView } from "@/lib/types";

import { usedItems, type GateRow } from "./rows";

const MODE_LABEL: Record<string, string> = {
  LIVE: "Live",
  RECORDED: "Recorded",
  SIMULATED: "Simulated",
  INJECTED: "Test condition",
};

function SourceLine({ view, age }: { view: EvidenceItemView; age?: string | number | null }) {
  const it = view.item;
  const pay = it.economic.payment;
  return (
    <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
      <span className="text-ink-2">{providerLabel(it.source.provider)}</span>
      <span className="rounded-sm border border-rule-strong px-1.5 py-px text-2xs text-ink-2" title={`Source mode: ${it.source.mode}`}>
        {MODE_LABEL[it.source.mode] ?? it.source.mode}
      </span>
      {pay ? (
        <span
          className="rounded-sm border border-signal/40 px-1.5 py-px text-2xs text-signal"
          title={`${pay.protocol} ${pay.scheme ?? ""} on ${pay.network ?? "?"}, settlement ${pay.status}, reference ${pay.payment_reference ?? "—"}`}
        >
          Paid {formatAmount(pay.settled_amount ?? pay.quoted_amount, pay.currency)} via x402
        </span>
      ) : null}
      {age !== undefined && age !== null ? <span className="tabular">age at decision {formatAge(age)}</span> : null}
    </p>
  );
}

function RowBody({ row }: { row: GateRow }) {
  const req = row.requirement;
  const used = usedItems(row);
  const ageOf = (id: string) => row.assessments.find((a) => a.evidence_id === id)?.age_seconds;

  if (row.status === "PENDING") {
    return <p className="mt-1 text-sm text-muted">Waiting for evidence</p>;
  }
  if (row.status === "MISSING") {
    const causes = row.failures.map((f) => `${providerLabel(f.provider)}: ${f.code.replaceAll("_", " ").toLowerCase()}`);
    const invalid = row.assessments.filter((a) => a.status === "INVALID").flatMap((a) => a.reason_codes);
    return (
      <>
        <p className="mt-1 font-wide text-[1.75rem] font-bold leading-tight text-abstain">No usable evidence</p>
        <p className="mt-1 text-sm text-ink-2">{row.reason?.message ?? row.result?.human_reason}</p>
        {causes.length || invalid.length ? (
          <p className="mt-1 text-xs text-muted">{[...causes, ...invalid.map((c) => c.replaceAll("_", " ").toLowerCase())].join("; ")}</p>
        ) : null}
      </>
    );
  }
  if (row.status === "CONFLICT") {
    const d = (row.reason?.detail ?? {}) as Record<string, string>;
    const pct = d.relative_difference ? `${Math.round(Number(d.relative_difference) * 1000) / 10}%` : null;
    return (
      <>
        <div className="mt-1 grid grid-cols-2 gap-4">
          {used.slice(0, 2).map((v, i) => (
            <div key={v.item.evidence_id}>
              <p className="text-xs text-muted">Source {i === 0 ? "A" : "B"}</p>
              <p className="font-wide text-[1.75rem] font-bold leading-tight tabular">{formatClaimValue(v.item.claim.value, v.item.claim.unit, v.item.claim.name)}</p>
              <SourceLine view={v} />
            </div>
          ))}
        </div>
        {pct ? (
          <p className="mt-2 text-sm text-hold">
            {pct} apart. The contract tolerates {Math.round(Number(d.tolerance ?? 0.1) * 100)}%, so no source is chosen.
          </p>
        ) : null}
      </>
    );
  }
  const v = used[0];
  const age = v ? ageOf(v.item.evidence_id) : null;
  return (
    <>
      <div className="mt-1 flex flex-wrap items-baseline gap-x-5">
        <p className={`font-wide text-[2rem] font-bold leading-tight tabular ${row.status === "FAIL" ? "text-deny" : "text-ink"}`}>
          {v ? formatClaimValue(v.item.claim.value, v.item.claim.unit, v.item.claim.name) : "—"}
        </p>
        <p className="text-sm text-ink-2">
          Required <span className="font-semibold tabular">{plainRequirement(row.result?.required_expression ?? req.operator)}</span>
        </p>
      </div>
      {row.status === "STALE" && age !== undefined && age !== null ? (
        <p className="mt-1 text-sm font-semibold text-hold">
          Observed {formatAge(age)} before the decision. Maximum age {req.max_age_seconds} s.
        </p>
      ) : null}
      {v ? <SourceLine view={v} age={row.status === "STALE" ? null : age} /> : null}
    </>
  );
}

export const EvidenceRow = forwardRef<HTMLDivElement, { row: GateRow; onInspect: (row: GateRow) => void }>(function EvidenceRow(
  { row, onInspect },
  ref,
) {
  const meta = ROW_META[row.status];
  const tone = TONE_CLASSES[meta.tone];
  const label = row.requirement.label ?? claimLabel(row.requirement.claim);
  return (
    <div
      ref={ref}
      data-testid={`row-${row.requirement.id}`}
      data-status={row.status}
      className={`relative border-l-[5px] bg-paper py-4 pl-5 pr-4 ${tone.border} ${row.causal && row.status !== "PASS" ? tone.bg : ""}`}
    >
      <div className="flex items-start justify-between gap-4">
        <h3 className="text-[0.9375rem] font-semibold text-ink-2">{label}</h3>
        <div className="flex items-center gap-3">
          <button type="button" onClick={() => onInspect(row)} className="text-xs text-muted underline-offset-4 hover:text-ink hover:underline">
            Details
          </button>
          <span className={`flex items-center gap-1.5 text-sm font-bold ${tone.text}`}>
            <StateIcon name={meta.icon} size={18} />
            {meta.label}
          </span>
        </div>
      </div>
      <RowBody row={row} />
    </div>
  );
});
