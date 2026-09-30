"use client";

/* The current run (one intent, one frozen contract, its latest decision and receipt), shared by the
   five screens and remembered across reloads so the stepper can always move forward and back. */

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import type { DecisionState } from "./types";

export interface RunState {
  intentId?: string;
  contractId?: string;
  decisionId?: string;
  decisionState?: DecisionState;
  authorizationId?: string;
  receiptId?: string;
  intentText?: string;
}

interface RunContextValue {
  run: RunState;
  update: (patch: Partial<RunState>) => void;
  reset: () => void;
  ready: boolean;
}

const KEY = "sufficia.run.v1";
const RunContext = createContext<RunContextValue | null>(null);

export function RunProvider({ children }: { children: ReactNode }) {
  const [run, setRun] = useState<RunState>({});
  const [ready, setReady] = useState(false);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(KEY);
      // eslint-disable-next-line react-hooks/set-state-in-effect -- hydrating from storage after mount
      if (raw) setRun(JSON.parse(raw) as RunState);
    } catch {
      /* storage unavailable: the run lives in memory only */
    }
    setReady(true);
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
        const next = { ...prev, ...patch };
        persist(next);
        return next;
      }),
    [persist],
  );

  const reset = useCallback(() => {
    setRun({});
    persist({});
  }, [persist]);

  const value = useMemo(() => ({ run, update, reset, ready }), [run, update, reset, ready]);
  return <RunContext.Provider value={value}>{children}</RunContext.Provider>;
}

export function useRun(): RunContextValue {
  const ctx = useContext(RunContext);
  if (!ctx) throw new Error("useRun must be used inside <RunProvider>");
  return ctx;
}
