"use client";

import { CONDITIONS } from "@/lib/states";
import type { Condition } from "@/lib/types";

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
