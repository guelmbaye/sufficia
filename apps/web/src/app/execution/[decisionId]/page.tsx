"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { Field } from "@/components/Drawer";
import { ErrorNote } from "@/components/ErrorNote";
import { Hash } from "@/components/Hash";
import { StateIcon } from "@/components/StateIcon";
import { api, ApiError } from "@/lib/api";
import { formatAmount, formatCountdown, formatDateTime, formatNumber } from "@/lib/format";
import { useCountdown, useSystem } from "@/lib/hooks";
import { useRun } from "@/lib/run";
import { chainLabel, DECISION_META } from "@/lib/states";
import type { AuthorizationView, ChainTx, DecisionView, ExecutionResult } from "@/lib/types";

type StepState = "todo" | "doing" | "done" | "fail";
interface Step {
  key: "check" | "bind" | "submit" | "confirm";
  label: string;
  state: StepState;
  detail?: string;
}

function toApiError(e: unknown): ApiError {
  return e instanceof ApiError ? e : new ApiError(0, "UNKNOWN", String(e));
}

function txOf(auth: AuthorizationView | null, purpose: string): ChainTx | undefined {
  return auth?.transactions?.filter((t) => t.purpose === purpose).at(-1);
}

/* Screen 04 — Execution (Document 05 §28-32). Accessible only from AUTHORIZE. */
function Execution() {
  const { decisionId } = useParams<{ decisionId: string }>();
  const search = useSearchParams();
  const { update } = useRun();
  const { system } = useSystem();
  const chain = chainLabel(system?.chain?.mode);
  const where = chain.isMonad ? "Monad" : "the local EVM";

  const [view, setView] = useState<DecisionView | null>(null);
  const [auth, setAuth] = useState<AuthorizationView | null>(null);
  const [result, setResult] = useState<ExecutionResult | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [running, setRunning] = useState(false);
  const [failure, setFailure] = useState<ApiError | null>(null);
  const [steps, setSteps] = useState<Step[]>([
    { key: "check", label: "Checking authorization", state: "todo" },
    { key: "bind", label: "Binding action", state: "todo" },
    { key: "submit", label: "Submitting to the chain", state: "todo" },
    { key: "confirm", label: "Confirming", state: "todo" },
  ]);
  const autoRan = useRef(false);
  const loaded = useRef(false);

  const set = (key: Step["key"], state: StepState, detail?: string) =>
    setSteps((prev) => prev.map((s) => (s.key === key ? { ...s, state, detail: detail ?? s.detail } : s)));

  const execute = useCallback(
    async (authorizationId: string) => {
      setRunning(true);
      setFailure(null);
      try {
        set("check", "doing");
        const a = await api.authorization(authorizationId);
        setAuth(a);
        const oc = a.onchain;
        if (!oc?.exists) throw new ApiError(409, "AUTHORIZATION_NOT_FOUND", "The authorization is not on the EvidenceGate.");
        if (oc.consumed) throw new ApiError(409, "AUTHORIZATION_ALREADY_CONSUMED", "This authorization has already been used.");
        if (!oc.active) throw new ApiError(409, "AUTHORIZATION_EXPIRED", "The authorization expired before execution.");
        set("check", "done", `Active on the EvidenceGate, unconsumed, expires ${formatDateTime(a.expires_at)}`);

        set("bind", "doing");
        if ((oc.action_hash ?? "").toLowerCase() !== a.action_hash.toLowerCase()) {
          throw new ApiError(409, "ACTION_HASH_MISMATCH", "The onchain action hash does not match the prepared action.");
        }
        set("bind", "done", `Action hash ${a.action_hash.slice(0, 10)}… matches the authorization`);

        set("submit", "doing", `Submitting the protected execution to ${where} and waiting for confirmation…`);
        const r = await api.executeAuthorization(authorizationId);
        setResult(r);
        set("submit", "done", `Transaction ${r.tx_hash?.slice(0, 12)}… sent by the executor`);
        set("confirm", "done", r.block_number ? `Confirmed in block ${formatNumber(r.block_number)}` : "Confirmed");
        setAuth(await api.authorization(authorizationId));
        update({ receiptId: r.receipt_id ?? undefined, authorizationId });
      } catch (e) {
        const err = toApiError(e);
        setFailure(err);
        setSteps((prev) => {
          const i = prev.findIndex((s) => s.state === "doing");
          return prev.map((s, j) => (j === i ? { ...s, state: "fail", detail: err.message } : s));
        });
        const receipt = (err.detail?.receipt_id as string | undefined) ?? undefined;
        if (receipt) update({ receiptId: receipt });
      } finally {
        setRunning(false);
      }
    },
    [update, where],
  );

  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;
    (async () => {
      const dv = await api.decision(decisionId);
      setView(dv);
      update({ decisionId, decisionState: dv.decision.state, contractId: dv.decision.contract_id });
      if (dv.decision.state !== "AUTHORIZE") return;
      const done = dv.executions.find((e) => e.status === "EXECUTED");
      let a = dv.authorization;
      if (!a || a.status === "ISSUED" || a.status === "PUBLISH_FAILED") a = await api.authorize(decisionId);
      const full = await api.authorization(a.authorization_id);
      setAuth(full);
      update({ authorizationId: a.authorization_id });
      if (done) {
        const r = await api.executeAuthorization(a.authorization_id); // idempotent: returns the existing execution
        setResult(r);
        setSteps((prev) => prev.map((s) => ({ ...s, state: "done" })));
        update({ receiptId: r.receipt_id ?? undefined });
      } else if (search.get("execute") === "1" && !autoRan.current) {
        autoRan.current = true;
        await execute(a.authorization_id);
      }
    })().catch((e: unknown) => setError(toApiError(e)));
  }, [decisionId, execute, search, update]);

  const left = useCountdown(result ? null : auth?.expires_at);
  const expired = left !== null && left <= 0 && !result;
  const d = view?.decision;

  if (error) {
    return (
      <div className="mx-auto max-w-[1200px] px-6 py-12">
        <ErrorNote error={error} />
      </div>
    );
  }
  if (!view || !d) return <p className="mx-auto max-w-[1200px] px-6 py-12 text-muted">Loading the authorization…</p>;

  if (d.state !== "AUTHORIZE") {
    const meta = DECISION_META[d.state];
    return (
      <div className="mx-auto max-w-[900px] px-6 py-14">
        <h1 className="font-expanded text-3xl font-extrabold">No execution authority</h1>
        <p className="mt-3 text-lg">
          This decision is <span className="font-semibold">{meta.headline.toLowerCase()}</span>. Execution is reachable only from an authorized
          decision, and the EvidenceGate rejects any action without one.
        </p>
        <Link href={`/gate/${d.contract_id}`} className="mt-6 inline-block text-signal underline underline-offset-4">
          Back to the live gate
        </Link>
      </div>
    );
  }

  const action = auth?.action;
  const executed = result?.status === "EXECUTED";
  const authTx = txOf(auth, "AUTHORIZATION");
  const execTx = txOf(auth, "PROTECTED_EXECUTION");
  const receiptTx = txOf(auth, "RECEIPT_COMMITMENT");
  const explorer = (h: string | null | undefined, t?: ChainTx) => t?.explorer_url ?? (h && system?.chain?.explorer_url && chain.isMonad ? `${system.chain.explorer_url}/tx/${h}` : null);

  return (
    <div className="mx-auto max-w-[1300px] px-6 pb-12 pt-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm text-muted">Decision {d.decision_id}, authorized</p>
          <h1 className="font-expanded text-[2.6rem] font-extrabold leading-tight">Evidence-bound authorization</h1>
        </div>
        {!executed ? (
          <div className="text-right" data-testid="exec-countdown">
            <p className="text-sm text-muted">{expired ? "Authorization expired" : "Authorization expires in"}</p>
            <p className={`font-expanded text-4xl font-extrabold tabular ${expired ? "text-hold" : "text-ink"}`}>{formatCountdown(left ?? 0)}</p>
          </div>
        ) : null}
      </div>
      <p className="mt-2 max-w-[70ch] text-ink-2">Authority is temporary because evidence is temporary. This authorization can execute one exact action, once.</p>

      <div className="mt-8 grid grid-cols-1 gap-px overflow-hidden rounded-sm border border-rule bg-rule lg:grid-cols-[1.05fr_1fr]">
        <section className="bg-paper p-6" aria-label="Authorization">
          <h2 className="text-sm text-muted">Action</h2>
          <p className="mt-1 font-expanded text-2xl font-extrabold">
            {{ BUY: "Buy", SELL: "Sell", TRANSFER: "Transfer" }[action?.action_type ?? ""] ?? action?.action_type} {action?.asset}
          </p>
          <p className="font-wide text-lg tabular">{action ? formatAmount(action.amount.value, action.amount.currency) : "—"}</p>
          {action?.description ? <p className="mt-1 text-sm text-ink-2">{action.description}</p> : null}

          <h2 className="mt-6 text-sm text-muted">Authorization</h2>
          <p className={`mt-1 text-lg font-semibold ${auth?.status === "CONSUMED" || executed ? "text-ink" : expired ? "text-hold" : "text-allow"}`}>
            {auth ? (executed || auth.status === "CONSUMED" ? "Consumed by this execution" : expired ? "Expired" : auth.status === "ACTIVE" ? "Active" : auth.status) : "Publishing…"}
          </p>

          <h2 className="mt-6 text-sm text-muted">Bound to</h2>
          <dl className="mt-1">
            <Field label="Evidence Contract">
              <Hash value={auth?.contract_hash} />
            </Field>
            <Field label="Evidence root">
              <Hash value={auth?.evidence_root} />
            </Field>
            <Field label="Action">
              <Hash value={auth?.action_hash} />
            </Field>
            <Field label="Executor">
              <Hash value={auth?.executor} />
            </Field>
            <Field label="Authorization id">
              <Hash value={auth?.authorization_id} />
            </Field>
            <Field label="Committed">
              <Hash value={authTx?.tx_hash ?? auth?.authorize_tx_hash} href={explorer(auth?.authorize_tx_hash, authTx)} />
            </Field>
          </dl>
        </section>

        <section className="flex flex-col bg-paper p-6" aria-label="Execution">
          <ol className="space-y-4" data-testid="sequence">
            {steps.map((s, i) => (
              <li key={s.key} className="flex gap-3" data-state={s.state}>
                <span className="mt-0.5 shrink-0">
                  {s.state === "done" ? (
                    <StateIcon name="allow" size={22} className="text-allow" />
                  ) : s.state === "fail" ? (
                    <StateIcon name="deny" size={22} className="text-deny" />
                  ) : s.state === "doing" ? (
                    <StateIcon name="pending" size={22} className="animate-spin text-signal motion-reduce:animate-none" />
                  ) : (
                    <span className="grid h-[22px] w-[22px] place-items-center rounded-full border border-rule-strong text-2xs text-muted">{i + 1}</span>
                  )}
                </span>
                <span>
                  <span className={`block font-semibold ${s.state === "todo" ? "text-faint" : "text-ink"}`}>
                    {s.key === "submit" ? `Submitting to ${where}` : s.label}
                  </span>
                  {s.detail ? <span className={`block text-sm ${s.state === "fail" ? "text-deny" : "text-muted"}`}>{s.detail}</span> : null}
                </span>
              </li>
            ))}
          </ol>

          <div className="mt-auto pt-6">
            {executed ? (
              <div data-testid="executed">
                <p className="flex items-center gap-2 font-expanded text-3xl font-extrabold text-allow">
                  <StateIcon name="allow" size={34} />
                  {chain.isMonad ? "Executed on Monad" : "Executed"}
                </p>
                <p className="mt-1 text-ink-2">
                  {chain.isMonad ? "" : "On the local EVM rehearsal chain, not Monad. "}This action used the evidence-bound authorization shown on the
                  left.
                </p>
              </div>
            ) : failure ? (
              <div data-testid="execution-failed">
                <p className="flex items-center gap-2 font-expanded text-2xl font-extrabold text-deny">
                  <StateIcon name="deny" size={28} />
                  {failure.code === "AUTHORIZATION_EXPIRED" ? "Authorization expired" : "Execution failed"}
                </p>
                <p className="mt-1 text-sm text-ink-2">
                  Decision: <span className="font-semibold text-allow">authorized</span>.{" "}
                  {failure.code === "AUTHORIZATION_EXPIRED"
                    ? "The evidence behind it is no longer fresh, so no transaction was submitted."
                    : "The evidence policy authorized the action, but the transaction failed during execution."}
                </p>
                <Link href={`/gate/${d.contract_id}`} className="mt-3 inline-block rounded-sm bg-ink px-4 py-2 text-sm font-semibold text-paper">
                  Refresh evidence on the live gate
                </Link>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => auth && execute(auth.authorization_id)}
                disabled={!auth || running || expired}
                className="w-full rounded-sm bg-allow px-5 py-3.5 text-lg font-bold text-paper hover:bg-[#0b6a41] disabled:cursor-not-allowed disabled:opacity-50"
                data-testid="execute-now"
              >
                {running ? "Executing…" : `Execute on ${chain.isMonad ? "Monad" : "local EVM"}`}
              </button>
            )}
          </div>
        </section>
      </div>

      {executed ? (
        <section className="mt-6 rounded-sm border border-ink/80 bg-paper p-6" aria-label="Onchain proof" data-testid="monad-proof">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 className="text-lg font-semibold">{chain.isMonad ? "Monad proof" : "Onchain proof (local EVM rehearsal, not Monad)"}</h2>
            {result?.receipt_id ? (
              <Link href={`/receipt/${result.receipt_id}`} className="rounded-sm bg-ink px-5 py-2.5 font-semibold text-paper hover:bg-ink-2">
                Open receipt
              </Link>
            ) : null}
          </div>
          <dl className="mt-3 grid grid-cols-1 gap-x-10 md:grid-cols-2">
            <Field label="Authorization">
              <Hash value={authTx?.tx_hash} href={explorer(authTx?.tx_hash, authTx)} />
            </Field>
            <Field label="Execution">
              <Hash value={execTx?.tx_hash ?? result?.tx_hash} href={explorer(result?.tx_hash, execTx)} />
            </Field>
            <Field label="Evidence root">
              <Hash value={auth?.evidence_root} />
            </Field>
            <Field label="Receipt committed">
              <Hash value={receiptTx?.tx_hash} href={explorer(receiptTx?.tx_hash, receiptTx)} />
            </Field>
          </dl>
          {execTx?.events?.length ? (
            <p className="mt-3 text-sm text-muted">
              EvidenceGate emitted {execTx.events.map((e) => e.event).join(" and ")} in block {formatNumber(execTx.block_number)}.
            </p>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

export default function ExecutionPage() {
  return (
    <Suspense fallback={<p className="mx-auto max-w-[1200px] px-6 py-12 text-muted">Loading the authorization…</p>}>
      <Execution />
    </Suspense>
  );
}
