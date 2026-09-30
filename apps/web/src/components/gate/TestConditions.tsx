"use client";

import type { Condition } from "@/lib/types";

const CONDITIONS: { id: Condition; label: string; hint: string }[] = [
  { id: "valid", label: "Valid", hint: "Every requirement satisfied by fresh evidence. Expected: authorized." },
  { id: "threshold", label: "Threshold fail", hint: "Liquidity observed at $92,440 against > $500,000. Expected: denied." },
  { id: "missing", label: "Missing", hint: "Price evidence removed. Expected: agent abstains." },
  { id: "stale", label: "Stale", hint: "Price observation 120 s old against a 30 s maximum. Expected: evidence expired." },
  { id: "conflict", label: "Conflict", hint: "A second liquidity source disagrees by 87%. Expected: human review." },
];

/* Judge control (Document 05 §24-26): each condition changes exactly one piece of evidence on the
   same contract. Labelled as test conditions, never as "demo mode". */
export function TestConditions({
  onRun,
  busy,
  active,
}: {
  onRun: (c: Condition) => void;
  busy: boolean;
  active: Condition | null;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3" role="group" aria-label="Test the gate">
      <span className="text-sm font-semibold text-ink-2">Test the gate</span>
      <div className="flex w-full overflow-hidden rounded-sm border border-ink/70 sm:w-auto">
        {CONDITIONS.map((c, i) => (
          <button
            key={c.id}
            type="button"
            title={c.hint}
            disabled={busy}
            onClick={() => onRun(c.id)}
            aria-pressed={active === c.id}
            data-testid={`condition-${c.id}`}
            className={`flex-1 px-2 py-1.5 text-sm font-medium leading-tight transition-colors sm:flex-none sm:px-3.5 disabled:cursor-wait ${i ? "border-l border-ink/30" : ""} ${
              active === c.id ? "bg-ink text-paper" : "bg-paper text-ink hover:bg-canvas"
            }`}
          >
            {c.label}
          </button>
        ))}
      </div>
    </div>
  );
}
