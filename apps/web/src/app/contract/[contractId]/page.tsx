"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect } from "react";

import { ErrorNote } from "@/components/ErrorNote";
import { Hash } from "@/components/Hash";
import { StateIcon } from "@/components/StateIcon";
import { api } from "@/lib/api";
import { formatDateTime, plainRequirement } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { useRun } from "@/lib/run";

/* Screen 02 — Evidence Contract (Document 05 §10-12): a clear promise, not developer JSON. */
export default function ContractPage() {
  const { contractId } = useParams<{ contractId: string }>();
  const { update } = useRun();
  const { data, error } = useLoad(contractId, () => api.contract(contractId));

  useEffect(() => {
    if (data) update({ contractId: data.contract.contract_id, intentId: data.intent_id });
  }, [data, update]);

  if (error) {
    return (
      <div className="mx-auto max-w-[1100px] px-6 py-14">
        <ErrorNote error={error} />
      </div>
    );
  }
  if (!data) return <p className="mx-auto max-w-[1100px] px-6 py-14 text-muted">Loading the Evidence Contract…</p>;

  const { summary, contract } = data;
  const action = contract.action;
  const verb = { BUY: "Buy", SELL: "Sell", TRANSFER: "Transfer" }[action.type] ?? action.type;
  const preservedOk = summary.intent_preserved;

  return (
    <div className="mx-auto max-w-[1100px] px-6 pb-16 pt-14">
      <p className="text-sm text-muted">Evidence Contract, frozen {formatDateTime(data.frozen_at)}</p>
      <h1 className="mt-1 font-expanded text-4xl font-extrabold leading-[1.05] tracking-[-0.01em]">
        What must be true before money can move?
      </h1>

      <div className="mt-10 grid grid-cols-[1fr_1.6fr] gap-px overflow-hidden rounded-sm border border-rule bg-rule">
        <section className="bg-paper p-6">
          <h2 className="text-sm text-muted">Action</h2>
          <p className="mt-2 font-expanded text-3xl font-extrabold">
            {verb} {action.asset}
          </p>
          <p className="mt-1 font-wide text-xl tabular">Maximum {summary.maximum}</p>

          <h2 className="mt-8 text-sm text-muted">Time</h2>
          <p className="mt-2 text-lg font-semibold">{summary.time}</p>

          <h2 className="mt-8 text-sm text-muted">Authority</h2>
          <p className="mt-2 text-lg font-semibold">Authorization expires when the supporting evidence expires</p>
          <p className="text-sm text-muted">and never later than {contract.authorization.ttl_seconds} seconds after the decision.</p>
        </section>

        <section className="bg-paper p-6">
          <h2 className="text-sm text-muted">Required evidence</h2>
          <ol className="mt-3 divide-y divide-rule border-y border-rule">
            {summary.requirements.map((r) => (
              <li key={r.id} className="grid grid-cols-[1fr_auto] items-baseline gap-4 py-4">
                <div>
                  <p className="text-lg font-semibold">{r.label}</p>
                  {r.origin === "IMPLICIT" ? (
                    <p className="text-sm text-muted">Added automatically: a price is needed to bound slippage. It can only restrict.</p>
                  ) : null}
                </div>
                <p className="font-wide text-2xl font-bold tabular">{plainRequirement(r.expression)}</p>
              </li>
            ))}
          </ol>
          <p className="mt-3 text-sm text-muted">
            Every requirement is mandatory. Each observation must be at most {summary.requirements[0]?.max_age_seconds ?? 30} seconds old,
            and two sources more than 10% apart escalate to a human instead of being averaged.
          </p>
        </section>
      </div>

      <section aria-label="Contract confirmation" className="mt-8">
        <ul className="grid grid-cols-4 gap-px overflow-hidden rounded-sm border border-rule bg-rule text-sm">
          <li className="flex items-start gap-3 bg-paper p-4">
            <StateIcon name={preservedOk ? "allow" : "conflict"} className={preservedOk ? "text-allow" : "text-hold"} />
            <span>
              <span className="block font-semibold">{preservedOk ? "Intent preserved" : "Review the intent"}</span>
              <span className="text-muted">
                {preservedOk ? "No requirement was relaxed while compiling." : "Some words were not understood and are not enforced."}
              </span>
            </span>
          </li>
          <li className="flex items-start gap-3 bg-paper p-4">
            <StateIcon name="allow" className="text-allow" />
            <span>
              <span className="block font-semibold">{summary.mandatory_requirements} mandatory requirements</span>
              <span className="text-muted">All must pass. There is no score.</span>
            </span>
          </li>
          <li className="bg-paper p-4">
            <span className="block text-muted">Max protected amount</span>
            <span className="font-wide text-xl font-bold tabular">{summary.maximum}</span>
          </li>
          <li className="bg-paper p-4">
            <span className="block text-muted">Max evidence spend</span>
            <span className="font-wide text-xl font-bold tabular">{summary.evidence_budget}</span>
          </li>
        </ul>
        {summary.unparsed.length ? (
          <p className="mt-3 text-sm text-hold">Not understood, so not enforced: {summary.unparsed.join("; ")}.</p>
        ) : null}
      </section>

      <div className="mt-10 flex flex-wrap items-center gap-6">
        <Link
          href={`/gate/${contract.contract_id}?acquire=1`}
          className="rounded-sm bg-signal px-6 py-3 text-base font-semibold text-paper hover:bg-[#1a44c4]"
        >
          Acquire evidence
        </Link>
        <span className="text-sm text-muted">
          Contract hash <Hash value={data.contract_hash} />
        </span>
      </div>

      <details className="mt-10 border-t border-rule pt-4">
        <summary className="cursor-pointer text-sm text-ink-2">View raw contract</summary>
        <pre className="mt-3 max-h-[28rem] overflow-auto rounded-sm border border-rule bg-paper p-4 font-mono text-xs leading-relaxed text-ink-2">
          {JSON.stringify(contract, null, 2)}
        </pre>
      </details>
    </div>
  );
}
