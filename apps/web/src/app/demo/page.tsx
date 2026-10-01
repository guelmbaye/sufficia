"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ErrorNote } from "@/components/ErrorNote";
import { StateIcon } from "@/components/StateIcon";
import { api, type ApiError, toApiError } from "@/lib/api";
import { GRAND_PRIZE_INTENT, GRAND_PRIZE_SCENARIO } from "@/lib/constants";
import { useRun } from "@/lib/run";

/* One-click demo (Document 05 §54-56, §115): compile, acquire, decide, then stop at the denial.
   The presenter triggers "Valid" and "Execute" on the live gate: nothing is auto-executed. */
export default function DemoPage() {
  const router = useRouter();
  const { reset, update } = useRun();
  const [error, setError] = useState<ApiError | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    reset();
    api
      .runScenario(GRAND_PRIZE_SCENARIO, { stop_before: "market" })
      .then((run) => {
        const decide = [...run.timeline].reverse().find((s) => s.op === "decide");
        update({
          contractId: run.contract_id,
          decisionId: decide?.decision_id as string | undefined,
          decisionState: run.final_state ?? undefined,
          receiptId: (decide?.receipt_id as string | undefined) ?? undefined,
        });
        router.replace(`/gate/${run.contract_id}`);
      })
      .catch((e: unknown) => setError(toApiError(e)));
  }, [reset, router, update]);

  return (
    <div className="mx-auto max-w-[900px] px-6 py-16">
      <p className="text-sm text-muted">Scenario {GRAND_PRIZE_SCENARIO}</p>
      <h1 className="mt-1 font-expanded text-3xl font-extrabold leading-tight">This agent wants to spend 100 USDC.</h1>
      <p className="mt-3 text-lg text-ink-2">&ldquo;{GRAND_PRIZE_INTENT}&rdquo;</p>
      {error ? (
        <ErrorNote error={error} className="mt-8" />
      ) : (
        <ol className="mt-8 space-y-3 text-base" aria-live="polite">
          {["Freezing the Evidence Contract", "Acquiring evidence, paying for liquidity data with x402", "Checking the evidence against the contract"].map((s) => (
            <li key={s} className="flex items-center gap-3 text-ink-2">
              <StateIcon name="pending" size={18} className="text-signal" />
              {s}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
