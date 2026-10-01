"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Hash } from "@/components/Hash";
import { api } from "@/lib/api";
import { claimLabel, claimUnit, formatClaimValue } from "@/lib/format";
import { DECISION_META, TONE_CLASSES } from "@/lib/states";
import type { DecisionView } from "@/lib/types";

/** Compare runs (Document 05 §97-98): same intent, amount, asset and wallet; only evidence differs. */
export function CompareRuns({ contractId }: { contractId: string }) {
  const [pair, setPair] = useState<[DecisionView, DecisionView] | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      const c = await api.contract(contractId);
      const deny = [...c.decisions].reverse().find((d) => d.state === "DENY");
      const allow = [...c.decisions].reverse().find((d) => d.state === "AUTHORIZE");
      if (!deny || !allow) return;
      const [a, b] = await Promise.all([api.decision(deny.decision_id), api.decision(allow.decision_id)]);
      if (alive) setPair([a, b]);
    })().catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [contractId]);

  if (!pair) return null;
  const [a, b] = pair;
  const reqs = a.decision.requirement_results.map((r) => r.requirement_id);

  return (
    <section aria-label="Compare runs" className="rounded-sm border border-rule bg-paper p-5 sm:p-6" data-testid="compare">
      <h2 className="font-expanded text-xl font-extrabold">One evidence change. One authority change.</h2>
      <p className="mt-1 text-sm text-ink-2">Same agent, same intent, same amount, same contract. Only the evidence differs.</p>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[34rem] text-left text-sm">
          <thead>
            <tr className="border-b border-ink/70 text-muted">
              <th className="py-2 pr-4 font-medium" />
              <th className="py-2 pr-4 font-medium">Run A</th>
              <th className="py-2 font-medium">Run B</th>
            </tr>
          </thead>
          <tbody>
            {reqs.map((id) => {
              const ra = a.decision.requirement_results.find((r) => r.requirement_id === id);
              const rb = b.decision.requirement_results.find((r) => r.requirement_id === id);
              const va = ra?.observed_values.join(", ");
              const vb = rb?.observed_values.join(", ");
              const changed = va !== vb;
              const fmt = (v: string | undefined, claim: string) =>
                v ? v.split(", ").map((x) => formatClaimValue(x, claimUnit(claim), claim)).join(", ") : "none";
              return (
                <tr key={id} className={`border-b border-rule ${changed ? "bg-hold-tint/60" : ""}`}>
                  <td className="py-2.5 pr-4">{claimLabel(ra?.claim ?? id)}</td>
                  <td className={`py-2.5 pr-4 font-wide font-bold tabular ${ra?.status === "FAIL" ? "text-deny" : ""}`}>{fmt(va, ra?.claim ?? "")}</td>
                  <td className="py-2.5 font-wide font-bold tabular">{fmt(vb, rb?.claim ?? "")}</td>
                </tr>
              );
            })}
            <tr className="border-b border-rule">
              <td className="py-2.5 pr-4">Decision</td>
              {[a, b].map((v) => (
                <td key={v.decision.decision_id} className={`py-2.5 pr-4 font-expanded font-extrabold ${TONE_CLASSES[DECISION_META[v.decision.state].tone].text}`}>
                  {DECISION_META[v.decision.state].short}
                </td>
              ))}
            </tr>
            <tr>
              <td className="py-2.5 pr-4">Execution</td>
              {[a, b].map((v) => {
                const ex = v.executions.find((e) => e.status === "EXECUTED");
                const rec = v.receipts.at(-1);
                return (
                  <td key={v.decision.decision_id} className="py-2.5 pr-4">
                    {ex ? <Hash value={ex.tx_hash} /> : <span className="text-muted">No transaction</span>}
                    {rec ? (
                      <Link href={`/receipt/${rec.receipt_id}`} className="ml-2 text-xs text-signal underline underline-offset-4">
                        Open receipt
                      </Link>
                    ) : null}
                  </td>
                );
              })}
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}
