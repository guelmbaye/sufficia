"use client";

/* The current run (one frozen contract, its latest decision and receipt), shared by the five
   screens and remembered across reloads so the stepper can always move forward and back. */

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import type { DecisionState } from "./types";

export interface RunState {
  contractId?: string;
  decisionId?: string;
  decisionState?: DecisionState;
  receiptId?: string;
}

interface RunContextValue {
  run: RunState;
  update: (patch: Partial<RunState>) => void;
  reset: () => void;
}

const KEY = "sufficia.run.v2";
const RunContext = createContext<RunContextValue | null>(null);

/** A decision and a receipt belong to one contract: opening another contract forgets them, so the
    stepper and the shortcuts never point at a different run's records. */
export function mergeRun(prev: RunState, patch: Partial<RunState>): RunState {
  const base = patch.contractId && patch.contractId !== prev.contractId ? { contractId: patch.contractId } : prev;
  const next: RunState = { ...base, ...patch };
  if (patch.decisionId && patch.decisionId !== prev.decisionId && !("receiptId" in patch)) delete next.receiptId;
  return next;
}

export function RunProvider({ children }: { children: ReactNode }) {
  const [run, setRun] = useState<RunState>({});

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(KEY);
      // eslint-disable-next-line react-hooks/set-state-in-effect -- hydrating from storage after mount
      if (raw) setRun(JSON.parse(raw) as RunState);
    } catch {
      /* storage unavailable: the run lives in memory only */
    }
  }, []);

  const persist = useCallback((next: RunState) => {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      /* ignore */
    }
  }, []);

  const update = useCallback(
    (patch: Partial<RunState>) =>
      setRun((prev) => {
        const next = mergeRun(prev, patch);
        persist(next);
        return next;
      }),
    [persist],
  );

  const reset = useCallback(() => {
    setRun({});
    persist({});
  }, [persist]);

  const value = useMemo(() => ({ run, update, reset }), [run, update, reset]);
  return <RunContext.Provider value={value}>{children}</RunContext.Provider>;
}

export function useRun(): RunContextValue {
  const ctx = useContext(RunContext);
  if (!ctx) throw new Error("useRun must be used inside <RunProvider>");
  return ctx;
}
