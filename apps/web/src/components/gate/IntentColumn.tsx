import { actionVerb, formatAmount, maxAgePhrase, plainRequirement } from "@/lib/format";
import type { ContractView, DecisionView } from "@/lib/types";

/** Left column: what the agent wants, what must be true, and what is at stake (Document 05 §77-78). */
export function IntentColumn({ contract, decision }: { contract: ContractView; decision: DecisionView | null }) {
  const action = contract.contract.action;
  // this decision executed, or an earlier decision of the same intent did
  const executed = Boolean(decision?.executions.some((e) => e.status === "EXECUTED") || decision?.spent_by);
  const cost = decision?.decision.evidence_cost_usd;
  const amount = formatAmount(action.max_amount.value, action.max_amount.currency);

  return (
    <aside className="flex min-h-0 flex-col gap-5" aria-label="Intent">
      <div>
        <p className="text-sm text-muted">The agent wants to</p>
        <p className="mt-1 font-expanded text-[2.1rem] font-extrabold leading-none">
          {actionVerb(action.type)} {action.asset}
        </p>
        <p className="mt-2 font-wide text-lg tabular">up to {amount}</p>
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
              Evidence at most <span className="font-semibold tabular">{maxAgePhrase(contract.contract.requirements.map((r) => r.max_age_seconds))}</span> old
            </span>
          </li>
        </ul>
      </div>

      {/* Whether this capital moved is stated by the gate; the card only changes once it has. */}
      <div className={`rounded-sm border px-4 py-3 ${executed ? "border-allow bg-allow-tint" : "border-ink/70 bg-paper"}`} data-testid="protected-capital">
        <p className="text-sm text-muted">Protected capital</p>
        <p className="font-wide text-2xl font-bold tabular">{amount}</p>
        {executed ? <p className="mt-0.5 text-sm font-semibold text-allow">Executed</p> : null}
      </div>

      <div className="px-1">
        <p className="text-sm text-muted">Evidence spend</p>
        <p className="font-wide text-lg font-bold tabular">{cost !== undefined ? formatAmount(cost, "USDC") : "—"}</p>
        <p className="text-xs text-muted">
          spent to establish whether {amount} may move.
        </p>
      </div>
    </aside>
  );
}
