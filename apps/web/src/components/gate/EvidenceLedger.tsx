"use client";

import { forwardRef } from "react";

import { StateIcon } from "@/components/StateIcon";
import { claimLabel, formatAge, formatAmount, formatClaimValue, modeLabel, plainRequirement, providerLabel } from "@/lib/format";
import { ROW_META, TONE_CLASSES } from "@/lib/states";
import type { EvidenceItemView } from "@/lib/types";

import { usedItems, type GateRow } from "./rows";

/* Each row states facts about its evidence (value, requirement, source, age). Why those facts lead
   to the decision is said once, in the gate. */
function SourceLine({ view, age, maxAge }: { view: EvidenceItemView; age?: string | number | null; maxAge?: number }) {
  const it = view.item;
  const pay = it.economic.payment;
  const provider = providerLabel(it.source.provider);
  const mode = modeLabel(it.source.mode);
  return (
    <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
      <span className="text-ink-2">{provider}</span>
      {mode !== provider ? (
        <span className="rounded-sm border border-rule-strong px-1.5 py-px text-2xs text-ink-2" title={`Source mode: ${it.source.mode}`}>
          {mode}
        </span>
      ) : null}
      {pay ? (
        <span
          className="rounded-sm border border-signal/40 px-1.5 py-px text-2xs text-signal"
          title={`${pay.protocol} ${pay.scheme ?? ""} on ${pay.network ?? "?"}, settlement ${pay.status}, reference ${pay.payment_reference ?? "—"}`}
        >
          Paid {formatAmount(pay.settled_amount ?? pay.quoted_amount, pay.currency)} via x402
        </span>
      ) : null}
      {age !== undefined && age !== null ? (
        <span className={`tabular ${maxAge !== undefined ? "font-semibold text-hold" : ""}`}>
          age at decision {formatAge(age)}
          {maxAge !== undefined ? `, maximum ${maxAge} s` : ""}
        </span>
      ) : null}
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
    const causes = [
      ...row.failures.map((f) => `${providerLabel(f.provider)}: ${f.code.replaceAll("_", " ").toLowerCase()}`),
      ...row.assessments.filter((a) => a.status === "INVALID").flatMap((a) => a.reason_codes.map((c) => c.replaceAll("_", " ").toLowerCase())),
    ];
    return (
      <>
        <p className="mt-1 font-wide text-[1.75rem] font-bold leading-tight text-abstain">No usable evidence</p>
        {causes.length ? <p className="mt-1 text-xs text-muted">{causes.join("; ")}</p> : null}
      </>
    );
  }
  if (row.status === "CONFLICT") {
    return (
      <div className="mt-1 grid grid-cols-2 gap-4">
        {used.slice(0, 2).map((v, i) => (
          <div key={v.item.evidence_id}>
            <p className="text-xs text-muted">Source {i === 0 ? "A" : "B"}</p>
            <p className="font-wide text-[1.75rem] font-bold leading-tight tabular text-hold">{formatClaimValue(v.item.claim.value, v.item.claim.unit, v.item.claim.name)}</p>
            <SourceLine view={v} />
          </div>
        ))}
      </div>
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
      {v ? <SourceLine view={v} age={age} maxAge={row.status === "STALE" ? req.max_age_seconds : undefined} /> : null}
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
