import { formatAmount, plainRequirement } from "@/lib/format";
import type { ContractView, DecisionView } from "@/lib/types";

/** Left column: what the agent wants, what must be true, and what is at stake (Document 05 §77-78). */
export function IntentColumn({ contract, decision }: { contract: ContractView; decision: DecisionView | null }) {
  const action = contract.contract.action;
  const verb = { BUY: "Buy", SELL: "Sell", TRANSFER: "Transfer" }[action.type] ?? action.type;
  const executed = decision?.executions.some((e) => e.status === "EXECUTED");
  const cost = decision?.decision.evidence_cost_usd;
  const maxAge = contract.contract.requirements[0]?.max_age_seconds ?? 30;

  return (
    <aside className="flex min-h-0 flex-col gap-5" aria-label="Intent">
      <div>
        <p className="text-sm text-muted">The agent wants to</p>
        <p className="mt-1 font-expanded text-[2.1rem] font-extrabold leading-none">
          {verb} {action.asset}
        </p>
        <p className="mt-2 font-wide text-lg tabular">up to {formatAmount(action.max_amount.value, action.max_amount.currency)}</p>
      </div>

      <div>
        <p className="text-sm text-muted">Only if</p>
        <ul className="mt-2 space-y-1.5 text-[0.9375rem]">
          {contract.summary.requirements.map((r) => (
            <li key={r.id} className="flex gap-2">
              <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-ink/60" aria-hidden />
              <span>
                {r.label} <span className="font-semibold tabular">{plainRequirement(r.expression)}</span>
              </span>
            </li>
          ))}
          <li className="flex gap-2">
            <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-ink/60" aria-hidden />
            <span>
              Evidence at most <span className="font-semibold tabular">{maxAge} seconds</span> old
            </span>
          </li>
        </ul>
      </div>

      <div className={`rounded-sm border px-4 py-3 ${executed ? "border-allow bg-allow-tint" : "border-ink/70 bg-paper"}`} data-testid="protected-capital">
        <p className="text-sm text-muted">Protected capital</p>
        <p className="font-wide text-2xl font-bold tabular">{formatAmount(action.max_amount.value, action.max_amount.currency)}</p>
        <p className={`mt-0.5 text-sm font-semibold ${executed ? "text-allow" : "text-ink"}`}>{executed ? "Executed" : "Not moved"}</p>
      </div>

      <div className="px-1">
        <p className="text-sm text-muted">Evidence spend</p>
        <p className="font-wide text-lg font-bold tabular">{cost !== undefined ? formatAmount(cost, "USDC") : "—"}</p>
        <p className="text-xs text-muted">
          spent to establish whether {formatAmount(action.max_amount.value, action.max_amount.currency)} may move.
        </p>
      </div>
    </aside>
  );
}
