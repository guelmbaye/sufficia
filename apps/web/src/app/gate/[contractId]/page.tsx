"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ErrorNote } from "@/components/ErrorNote";
import { Connectors } from "@/components/gate/Connectors";
import { EvidenceInspector } from "@/components/gate/EvidenceInspector";
import { EvidenceRow } from "@/components/gate/EvidenceLedger";
import { GatePanel, type AuthPhase } from "@/components/gate/GatePanel";
import { IntentColumn } from "@/components/gate/IntentColumn";
import { buildRows, type GateRow } from "@/components/gate/rows";
import { TestConditions } from "@/components/gate/TestConditions";
import { Hash } from "@/components/Hash";
import { LoadError } from "@/components/LoadError";
import { api, type ApiError, toApiError } from "@/lib/api";
import { actionVerb, formatAmount, formatTime } from "@/lib/format";
import { useDeadlinePassed, useSystem } from "@/lib/hooks";
import { useRun } from "@/lib/run";
import { chainLabel, conditionWord } from "@/lib/states";
import type { Condition, ContractView, DecisionView, EvidenceSetView } from "@/lib/types";

/** Authority left on a decision, in milliseconds (the server's own window, read against this clock). */
function authorityLeft(dv: DecisionView): number {
  const until = dv.authority?.expires_at ?? dv.decision.authority_window?.expires_at;
  return until ? new Date(until).getTime() - Date.now() : 0;
}

/* Screen 03 — Live Gate (Document 05 §13-27): intent | evidence | gate. The hero of the product. */
function LiveGate() {
  const { contractId } = useParams<{ contractId: string }>();
  const search = useSearchParams();
  const router = useRouter();
  const { update } = useRun();
  const { system } = useSystem();
  const chain = chainLabel(system?.chain?.mode);

  const [contract, setContract] = useState<ContractView | null>(null);
  const [evidence, setEvidence] = useState<EvidenceSetView | null>(null);
  const [view, setView] = useState<DecisionView | null>(null);
  const [auth, setAuth] = useState<AuthPhase>({ kind: "idle" });
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [active, setActive] = useState<Condition | null>(null);
  const [inspect, setInspect] = useState<GateRow | null>(null);

  const rowRefs = useRef<(HTMLDivElement | null)[]>([]);
  const gutterRef = useRef<HTMLDivElement | null>(null);
  const headRef = useRef<HTMLDivElement | null>(null);
  const started = useRef(false);

  const publish = useCallback(
    async (decisionId: string) => {
      setAuth({ kind: "publishing" });
      setStage(`Publishing the authorization to ${chain.where}…`);
      try {
        const a = await api.authorize(decisionId);
        setAuth({ kind: "active", auth: a });
      } catch (e) {
        const err = toApiError(e);
        setAuth({ kind: "error", message: err.message, code: err.code });
      } finally {
        setStage(null);
      }
    },
    [chain.where],
  );

  const applyDecision = useCallback(
    (dv: DecisionView) => {
      setView(dv);
      update({ contractId, decisionId: dv.decision.decision_id, decisionState: dv.decision.state, receiptId: dv.receipts.at(-1)?.receipt_id });
      if (dv.decision.state !== "AUTHORIZE") {
        setAuth({ kind: "idle" });
      } else if (dv.authorization && (dv.authorization.status === "ACTIVE" || dv.authorization.status === "CONSUMED")) {
        setAuth({ kind: "active", auth: dv.authorization });
      } else if (dv.can_execute && !dv.executions.some((e) => e.status === "EXECUTED") && authorityLeft(dv) > 1000) {
        // Publish only an executable, unspent intent, and only while authority remains: opening an old
        // page must never write anything.
        void publish(dv.decision.decision_id);
      } else {
        setAuth({ kind: "idle" });
      }
    },
    [contractId, publish, update],
  );

  const acquireAndDecide = useCallback(async () => {
    if (!contract && !contractId) return;
    setBusy(true);
    setError(null);
    setActive(null);
    try {
      const n = contract?.contract.requirements.length ?? 3;
      setStage(`Acquiring evidence for ${n} requirements…`);
      const ev = await api.acquire(contractId);
      setEvidence(ev);
      setStage("Checking the evidence against the Evidence Contract…");
      const dv = await api.decide(contractId);
      setStage(null);
      applyDecision(dv);
    } catch (e) {
      setError(toApiError(e));
      setStage(null);
    } finally {
      setBusy(false);
    }
  }, [applyDecision, contract, contractId]);

  const runCondition = useCallback(
    async (c: Condition) => {
      setBusy(true);
      setError(null);
      setActive(c);
      setStage(`Test condition “${conditionWord(c)}”: acquiring fresh evidence and checking the Evidence Contract…`);
      try {
        const r = await api.testCondition(contractId, c);
        setEvidence(r.evidence);
        setStage(null);
        applyDecision(r.decision);
      } catch (e) {
        setError(toApiError(e));
        setStage(null);
      } finally {
        setBusy(false);
      }
    },
    [applyDecision, contractId],
  );

  // Initial load: the latest decision of this contract, or a first acquisition when arriving from Screen 02.
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      const c = await api.contract(contractId);
      setContract(c);
      update({ contractId });
      const last = c.decisions.at(-1);
      if (last) {
        const dv = await api.decision(last.decision_id);
        setEvidence(await api.evidenceSet(dv.evidence_set_id));
        applyDecision(dv);
      } else if (search.get("acquire") === "1") {
        setBusy(true);
        setStage(`Acquiring evidence for ${c.contract.requirements.length} requirements…`);
        const ev = await api.acquire(contractId);
        setEvidence(ev);
        setStage("Checking the evidence against the Evidence Contract…");
        const dv = await api.decide(contractId);
        setStage(null);
        setBusy(false);
        applyDecision(dv);
        router.replace(`/gate/${contractId}`); // a reload must not acquire again
      } else {
        const ev = await api.evidence(contractId);
        if (ev.evidence_set_id) setEvidence(ev);
      }
    })().catch((e: unknown) => {
      setError(toApiError(e));
      setBusy(false);
      setStage(null);
    });
  }, [applyDecision, contractId, router, search, update]);

  const executed = Boolean(view?.executions.some((e) => e.status === "EXECUTED"));
  const deadline =
    view?.decision.state === "AUTHORIZE" && view.can_execute && !executed ? (auth.kind === "active" ? auth.auth.expires_at : view.authority?.expires_at) : null;
  const expired = useDeadlinePassed(deadline);
  // Once authority has expired, no evidence row explains the gate any more: the connectors go quiet.
  const rows = useMemo(() => {
    const built = contract ? buildRows(contract.contract, evidence, view?.decision ?? null) : [];
    return expired ? built.map((r) => ({ ...r, causal: false })) : built;
  }, [contract, evidence, view, expired]);
  const animateKey = `${view?.decision.decision_id ?? "pending"}-${expired}`;

  if (!contract) {
    if (error) return <LoadError error={error} what="Evidence Contract" id={contractId} />;
    return <p className="mx-auto max-w-[1600px] px-6 py-10 text-muted">Loading the live gate…</p>;
  }

  const action = contract.contract.action;
  return (
    <div className="mx-auto max-w-[1600px] px-6 pb-6 pt-5">
      <h1 className="sr-only">
        Live gate: {actionVerb(action.type)} {action.asset} up to {formatAmount(action.max_amount.value, action.max_amount.currency)}
      </h1>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[264px_minmax(0,1fr)_72px_minmax(380px,430px)] lg:gap-0">
        <div className="lg:pr-8">
          <IntentColumn contract={contract} decision={view} />
        </div>

        <section aria-label="Evidence" className="min-w-0">
          <div className="mb-3 flex min-h-9 flex-wrap items-center justify-between gap-3">
            {system?.demo_mode ? <TestConditions onRun={runCondition} busy={busy} active={active} /> : <span className="text-sm font-semibold text-ink-2">Evidence</span>}
          </div>
          <div className="space-y-2.5">
            {rows.map((row, i) => (
              <EvidenceRow
                key={row.requirement.id}
                row={row}
                onInspect={setInspect}
                ref={(el) => {
                  rowRefs.current[i] = el;
                }}
              />
            ))}
          </div>
          <p className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
            {evidence?.evidence_set_id ? (
              <>
                <span>
                  {evidence.origin === "INJECTION" && evidence.injection
                    ? `Test condition “${conditionWord(evidence.injection.condition)}” applied to ${
                        contract.contract.requirements.find((r) => r.id === evidence.injection?.requirement_id)?.label ?? evidence.injection.requirement_id
                      }`
                    : evidence.origin === "INJECTION"
                      ? "Evidence changed by a test condition"
                      : "Evidence acquired"}{" "}
                  at {formatTime(evidence.created_at)} UTC
                </span>
                <span className="flex items-center gap-1">
                  Evidence root <Hash value={evidence.evidence_root} />
                </span>
              </>
            ) : (
              <span>No evidence acquired yet.</span>
            )}
          </p>
          <ErrorNote error={error} className="mt-3" />
        </section>

        <div ref={gutterRef} className="relative hidden lg:block" aria-hidden>
          <Connectors rows={rows} rowRefs={rowRefs} gutterRef={gutterRef} targetRef={headRef} animateKey={animateKey} />
        </div>

        <GatePanel
          ref={headRef}
          contract={contract}
          view={view}
          rows={rows}
          authPhase={auth}
          expired={expired}
          chainMode={system?.chain?.mode}
          busy={busy}
          stage={stage}
          onExecute={() => view && router.push(`/execution/${view.decision.decision_id}?execute=1`)}
          onRefresh={acquireAndDecide}
          onInspect={setInspect}
          onReauthorize={() => view && publish(view.decision.decision_id)}
        />
      </div>
      <EvidenceInspector row={inspect} onClose={() => setInspect(null)} />
    </div>
  );
}

export default function GatePage() {
  return (
    <Suspense fallback={<p className="mx-auto max-w-[1600px] px-6 py-10 text-muted">Loading the live gate…</p>}>
      <LiveGate />
    </Suspense>
  );
}
