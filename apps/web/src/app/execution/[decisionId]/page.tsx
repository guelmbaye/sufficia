"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { Field } from "@/components/Drawer";
import { Hash } from "@/components/Hash";
import { LoadError } from "@/components/LoadError";
import { StateIcon } from "@/components/StateIcon";
import { api, type ApiError, toApiError } from "@/lib/api";
import { actionVerb, formatAmount, formatCountdown, formatDateTime, formatNumber, formatTime, shortHash } from "@/lib/format";
import { useCountdown, useSystem } from "@/lib/hooks";
import { useRun } from "@/lib/run";
import { chainLabel, DECISION_META, explorerTx } from "@/lib/states";
import type { AuthorizationView, ChainTx, DecisionView } from "@/lib/types";

type StepState = "todo" | "doing" | "done" | "fail";
type StepKey = "check" | "bind" | "submit" | "confirm";
interface Step {
  key: StepKey;
  state: StepState;
  detail?: string;
}

/** What an executed authorization left behind, from the execute call or, on a revisit, from reads. */
interface Executed {
  txHash: string | null;
  blockNumber: number | null;
  receiptId: string | null;
  transactions: ChainTx[];
}

const STEP_LABELS: Record<StepKey, (where: string) => string> = {
  check: () => "Checking authorization",
  bind: () => "Binding action",
  submit: (where) => `Submitting to ${where}`,
  confirm: () => "Confirming",
};

const initialSteps = (): Step[] => (["check", "bind", "submit", "confirm"] as StepKey[]).map((key) => ({ key, state: "todo" }));

function authorityLeft(dv: DecisionView): number {
  const until = dv.authority?.expires_at ?? dv.decision.authority_window?.expires_at;
  return until ? new Date(until).getTime() - Date.now() : 0;
}

/* Screen 04 — Execution (Document 05 §28-32). Accessible only from AUTHORIZE. The four steps show
   what the EvidenceGate holds; the API decides whether the action runs and records the outcome. */
function Execution() {
  const { decisionId } = useParams<{ decisionId: string }>();
  const search = useSearchParams();
  const { update } = useRun();
  const { system } = useSystem();
  const chainMode = system?.chain?.mode;
  const chain = chainLabel(chainMode);

  const [view, setView] = useState<DecisionView | null>(null);
  const [auth, setAuth] = useState<AuthorizationView | null>(null);
  const [executed, setExecuted] = useState<Executed | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [running, setRunning] = useState(false);
  const [failure, setFailure] = useState<{ error: ApiError; receiptId: string | null } | null>(null);
  const [steps, setSteps] = useState<Step[]>(initialSteps);
  const loaded = useRef(false);

  const set = (key: StepKey, state: StepState, detail?: string) =>
    setSteps((prev) => prev.map((s) => (s.key === key ? { ...s, state, detail: detail ?? s.detail } : s)));

  const execute = useCallback(
    async (authorizationId: string, fresh?: AuthorizationView) => {
      setRunning(true);
      setFailure(null);
      setSteps(initialSteps());
      try {
        set("check", "doing");
        const a = fresh ?? (await api.authorization(authorizationId));
        setAuth(a);
        const oc = a.onchain;
        const usable = Boolean(oc?.exists && !oc.consumed && oc.active);
        if (usable) {
          set("check", "done", `Active on the EvidenceGate, unconsumed, expires ${formatDateTime(a.expires_at)}`);
          set("bind", "doing");
          const bound = (oc?.action_hash ?? "").toLowerCase() === a.action_hash.toLowerCase();
          set("bind", bound ? "done" : "fail", bound ? `Action hash ${shortHash(a.action_hash, 8, 6)} matches the authorization` : "The onchain action hash differs from the prepared action");
          if (bound) set("submit", "doing", "Waiting for the executor's transaction to confirm…");
        } else {
          set(
            "check",
            "fail",
            !oc?.exists ? "Not found on the EvidenceGate" : oc.consumed ? "Already consumed on the EvidenceGate" : `Expired on the EvidenceGate at ${formatTime(a.expires_at)} UTC`,
          );
        }
        // The API re-checks everything and records the outcome (an expired attempt gets its receipt).
        const r = await api.executeAuthorization(authorizationId);
        set("submit", "done", `Transaction ${shortHash(r.tx_hash, 8, 6)} sent by the executor`);
        set("confirm", "done", r.block_number ? `Confirmed in block ${formatNumber(r.block_number)}` : "Confirmed");
        setAuth((prev) => (prev ? { ...prev, status: r.authorization.status } : r.authorization));
        setExecuted({ txHash: r.tx_hash, blockNumber: r.block_number, receiptId: r.receipt_id, transactions: r.transactions });
        update({ receiptId: r.receipt_id ?? undefined });
      } catch (e) {
        const err = toApiError(e);
        const receiptId = (err.detail?.receipt_id as string | undefined) ?? null;
        setFailure({ error: err, receiptId });
        setSteps((prev) => prev.map((s) => (s.state === "doing" ? { ...s, state: "fail", detail: err.message } : s)));
        if (receiptId) update({ receiptId });
      } finally {
        setRunning(false);
      }
    },
    [update],
  );

  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;
    (async () => {
      const dv = await api.decision(decisionId);
      setView(dv);
      update({ contractId: dv.decision.contract_id, decisionId, decisionState: dv.decision.state, receiptId: dv.receipts.at(-1)?.receipt_id });
      if (dv.decision.state !== "AUTHORIZE") return;
      const done = dv.executions.find((e) => e.status === "EXECUTED");
      let a = dv.authorization;
      if (!done && (!a || a.status === "ISSUED" || a.status === "PUBLISH_FAILED")) {
        if (authorityLeft(dv) <= 1000) return; // nothing to publish: the expired panel explains it
        a = await api.authorize(decisionId);
      }
      if (!a) return;
      const full = await api.authorization(a.authorization_id);
      setAuth(full);
      if (done) {
        const receipt = dv.receipts.find((r) => r.kind === "EXECUTED");
        setExecuted({ txHash: done.tx_hash, blockNumber: done.block_number, receiptId: receipt?.receipt_id ?? null, transactions: full.transactions ?? [] });
        setSteps([
          { key: "check", state: "done", detail: "Found active on the EvidenceGate before submission" },
          { key: "bind", state: "done", detail: `Action hash ${shortHash(full.action_hash, 8, 6)} matched the authorization` },
          { key: "submit", state: "done", detail: `Transaction ${shortHash(done.tx_hash, 8, 6)} sent by the executor` },
          { key: "confirm", state: "done", detail: done.block_number ? `Confirmed in block ${formatNumber(done.block_number)}` : "Confirmed" },
        ]);
      } else if (search.get("execute") === "1") {
        await execute(full.authorization_id, full);
      }
    })().catch((e: unknown) => setError(toApiError(e)));
  }, [decisionId, execute, search, update]);

  const expiresAt = auth?.expires_at ?? view?.authority?.expires_at ?? view?.decision.authority_window?.expires_at;
  const left = useCountdown(executed ? null : expiresAt);
  const expired = left !== null && left <= 0 && !executed;
  const d = view?.decision;

  if (error) return <LoadError error={error} what="Decision" id={decisionId} />;
  if (!view || !d) return <p className="mx-auto max-w-[1200px] px-6 py-12 text-muted">Loading the authorization…</p>;

  if (d.state !== "AUTHORIZE") {
    return (
      <div className="mx-auto max-w-[900px] px-6 py-14">
        <h1 className="font-expanded text-3xl font-extrabold">No execution authority</h1>
        <p className="mt-3 text-lg">
          This decision did not authorize execution ({DECISION_META[d.state].headline.toLowerCase()}), and the EvidenceGate rejects any action
          without an authorization.
        </p>
        <Link href={`/gate/${d.contract_id}`} className="mt-6 inline-block text-signal underline underline-offset-4">
          Back to the live gate
        </Link>
      </div>
    );
  }

  const action = auth?.action;
  const consumed = Boolean(executed) || auth?.status === "CONSUMED";
  const authTx = auth?.transactions?.filter((t) => t.purpose === "AUTHORIZATION").at(-1);
  const execTx = executed?.transactions.filter((t) => t.purpose === "PROTECTED_EXECUTION").at(-1);
  const receiptTx = executed?.transactions.filter((t) => t.purpose === "RECEIPT_COMMITMENT").at(-1);
  const link = (hash: string | null | undefined, tx?: ChainTx) => explorerTx(system?.chain?.explorer_url, chainMode, hash, tx);
  const refreshLink = (
    <Link href={`/gate/${d.contract_id}`} className="mt-3 inline-block rounded-sm bg-ink px-4 py-2 text-sm font-semibold text-paper">
      Refresh evidence on the live gate
    </Link>
  );

  return (
    <div className="mx-auto max-w-[1300px] px-6 pb-12 pt-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm text-muted">Decision {d.decision_id}, authorized</p>
          <h1 className="font-expanded text-[2rem] font-extrabold leading-tight sm:text-[2.6rem]">Evidence-bound authorization</h1>
        </div>
        {!executed && !expired && expiresAt ? (
          <div className="text-right" data-testid="exec-countdown">
            <p className="text-sm text-muted">Authorization expires in</p>
            <p className="font-expanded text-4xl font-extrabold tabular text-ink">{formatCountdown(left ?? 0)}</p>
          </div>
        ) : null}
      </div>
      <p className="mt-2 max-w-[70ch] text-ink-2">Authority is temporary because evidence is temporary. This authorization can execute one exact action, once.</p>

      <div className="mt-8 grid grid-cols-1 gap-px overflow-hidden rounded-sm border border-rule bg-rule lg:grid-cols-[1.05fr_1fr]">
        <section className="bg-paper p-6" aria-label="Authorization">
          <h2 className="text-sm text-muted">Action</h2>
          <p className="mt-1 font-expanded text-2xl font-extrabold">
            {actionVerb(action?.action_type)} {action?.asset}
          </p>
          <p className="font-wide text-lg tabular">{action ? formatAmount(action.amount.value, action.amount.currency) : "—"}</p>
          {action?.description ? <p className="mt-1 text-sm text-ink-2">{action.description}</p> : null}

          <h2 className="mt-6 text-sm text-muted">Authorization</h2>
          <p className={`mt-1 text-lg font-semibold ${consumed ? "text-ink" : expired ? "text-hold" : "text-allow"}`}>
            {!auth ? (expired ? "Never published: the authority window closed first" : "Publishing…") : consumed ? "Consumed by this execution" : expired ? "Expired" : "Active"}
          </p>

          <h2 className="mt-6 text-sm text-muted">Bound to</h2>
          <dl className="mt-1">
            <Field label="Evidence Contract">
              <Hash value={auth?.contract_hash ?? d.contract_hash} />
            </Field>
            <Field label="Evidence root">
              <Hash value={auth?.evidence_root ?? d.evidence_root} />
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
              <Hash value={authTx?.tx_hash ?? auth?.authorize_tx_hash} href={link(authTx?.tx_hash ?? auth?.authorize_tx_hash, authTx)} />
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
                  <span className={`block font-semibold ${s.state === "todo" ? "text-faint" : "text-ink"}`}>{STEP_LABELS[s.key](chain.where)}</span>
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
                  Executed on {chain.isMonad ? "Monad" : "the local EVM"}
                </p>
                <p className="mt-1 text-ink-2">
                  {chain.isMonad ? "" : "A rehearsal chain running the real EvidenceGate, not Monad. "}This action used the evidence-bound authorization
                  shown on the left.
                </p>
              </div>
            ) : failure ? (
              <div data-testid="execution-failed">
                <p className="flex items-center gap-2 font-expanded text-2xl font-extrabold text-deny">
                  <StateIcon name="deny" size={28} />
                  {failure.error.code === "AUTHORIZATION_EXPIRED"
                    ? "Authorization expired"
                    : failure.error.code === "EXECUTION_REVERTED"
                      ? "Execution failed"
                      : "Execution refused"}
                </p>
                <p className="mt-1 text-sm text-ink-2">
                  {failure.error.message.charAt(0).toUpperCase() + failure.error.message.slice(1)}.
                </p>
                <div className="flex flex-wrap items-center gap-4">
                  {refreshLink}
                  {failure.receiptId ? (
                    <Link href={`/receipt/${failure.receiptId}`} className="mt-3 text-sm text-signal underline underline-offset-4">
                      Open receipt
                    </Link>
                  ) : null}
                </div>
              </div>
            ) : expired ? (
              <div data-testid="authorization-expired">
                <p className="font-expanded text-2xl font-extrabold text-hold">Authorization expired</p>
                <p className="mt-1 text-sm text-ink-2">
                  The evidence behind it expired at {formatTime(expiresAt)} UTC, before it was used. No transaction was submitted.
                </p>
                {refreshLink}
              </div>
            ) : (
              <button
                type="button"
                onClick={() => auth && execute(auth.authorization_id)}
                disabled={!auth || running}
                className="w-full rounded-sm bg-allow px-5 py-3.5 text-lg font-bold text-paper hover:bg-[#0b6a41] disabled:cursor-not-allowed disabled:opacity-50"
                data-testid="execute-now"
              >
                {running ? "Executing…" : `Execute on ${chain.isMonad ? "Monad" : chain.name}`}
              </button>
            )}
          </div>
        </section>
      </div>

      {executed ? (
        <section className="mt-6 rounded-sm border border-ink/80 bg-paper p-6" aria-label="Onchain proof" data-testid="monad-proof">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 className="text-lg font-semibold">{chain.isMonad ? "Monad proof" : "Onchain proof (local EVM rehearsal, not Monad)"}</h2>
            {executed.receiptId ? (
              <Link href={`/receipt/${executed.receiptId}`} className="rounded-sm bg-ink px-5 py-2.5 font-semibold text-paper hover:bg-ink-2">
                Open receipt
              </Link>
            ) : null}
          </div>
          <dl className="mt-3 grid grid-cols-1 gap-x-10 md:grid-cols-2">
            <Field label="Execution">
              <Hash value={execTx?.tx_hash ?? executed.txHash} href={link(execTx?.tx_hash ?? executed.txHash, execTx)} />
            </Field>
            <Field label="Receipt committed">
              <Hash value={receiptTx?.tx_hash} href={link(receiptTx?.tx_hash, receiptTx)} />
            </Field>
          </dl>
          {execTx?.events?.length ? (
            <p className="mt-3 text-sm text-muted">
              EvidenceGate emitted {execTx.events.map((e) => e.event).join(" and ")} in block {formatNumber(execTx.block_number)}
              {receiptTx?.events?.length ? `, then ${receiptTx.events.map((e) => e.event).join(" and ")} in block ${formatNumber(receiptTx.block_number)}` : ""}.
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
