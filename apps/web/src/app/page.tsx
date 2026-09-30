"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ErrorNote } from "@/components/ErrorNote";
import { api, ApiError } from "@/lib/api";
import { GRAND_PRIZE_INTENT } from "@/lib/constants";
import { plainRequirement } from "@/lib/format";
import { useSystem } from "@/lib/hooks";
import { useRun } from "@/lib/run";
import { chainLabel } from "@/lib/states";
import type { ContractPreview } from "@/lib/types";

/* Screen 01 — Mission (Document 05 §8-9): what should the agent do? Consequence before architecture. */
export default function MissionPage() {
  const router = useRouter();
  const { update, reset } = useRun();
  const { system } = useSystem();
  const [text, setText] = useState(GRAND_PRIZE_INTENT);
  const [preview, setPreview] = useState<ContractPreview | null>(null);
  const [previewError, setPreviewError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const chain = chainLabel(system?.chain?.mode);

  // Live understanding of the instruction, debounced; nothing is stored until "Compile".
  useEffect(() => {
    const t = window.setTimeout(() => {
      if (!text.trim()) {
        setPreview(null);
        return;
      }
      api
        .previewIntent(text)
        .then((p) => {
          setPreview(p);
          setPreviewError(null);
        })
        .catch((e: unknown) => {
          setPreview(null);
          setPreviewError(e instanceof ApiError ? e : null);
        });
    }, 350);
    return () => window.clearTimeout(t);
  }, [text]);

  const compile = async () => {
    setBusy(true);
    setError(null);
    try {
      const intent = await api.createIntent(text);
      const contract = await api.compile(intent.intent.intent_id);
      reset();
      update({ intentId: intent.intent.intent_id, contractId: contract.contract.contract_id, intentText: text });
      router.push(`/contract/${contract.contract.contract_id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "UNKNOWN", String(e)));
      setBusy(false);
    }
  };

  const summary = preview?.summary;

  return (
    <div className="mx-auto max-w-[1100px] px-5 pb-16 pt-10 sm:px-6 sm:pt-14">
      <h1 className="font-expanded text-4xl font-extrabold leading-[1.05] tracking-[-0.01em]">What should the agent do?</h1>
      <p className="mt-3 max-w-[62ch] text-lg text-ink-2">
        Write the instruction you would give an autonomous agent that can spend money. SUFFICIA turns it into the facts that must be
        true before any of that money can move.
      </p>

      <label htmlFor="intent" className="mt-10 block text-sm font-medium text-ink-2">
        Instruction
      </label>
      <textarea
        id="intent"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        maxLength={2000}
        className="mt-2 w-full resize-none rounded-sm border border-rule-strong bg-paper px-4 py-3 font-wide text-lg leading-snug sm:px-5 sm:py-4 sm:text-xl text-ink outline-none focus:border-signal"
      />

      <dl className="mt-6 grid grid-cols-1 divide-y divide-rule border-y border-rule sm:grid-cols-3 sm:divide-x sm:divide-y-0">
        <div className="py-3 sm:py-4 sm:pr-6">
          <dt className="text-sm text-muted">Protected capital</dt>
          <dd className="mt-1 font-wide text-2xl font-bold tabular">{summary?.maximum ?? "—"}</dd>
        </div>
        <div className="py-3 sm:px-6 sm:py-4">
          <dt className="text-sm text-muted">Network</dt>
          <dd className="mt-1 font-wide text-2xl font-bold">{system ? (chain.isMonad ? chain.honest : "Local EVM") : "—"}</dd>
          {system && !chain.isMonad ? <dd className="text-xs text-muted">Real EvidenceGate bytecode, not Monad</dd> : null}
        </div>
        <div className="py-3 sm:px-6 sm:py-4">
          <dt className="text-sm text-muted">Evidence budget</dt>
          <dd className="mt-1 font-wide text-2xl font-bold tabular">{summary?.evidence_budget ?? "—"}</dd>
        </div>
      </dl>

      <div className="mt-4 min-h-[3.25rem] text-sm" aria-live="polite">
        {summary ? (
          <p className="text-ink-2">
            Understood as <span className="font-semibold text-ink">{summary.action}</span> with {summary.mandatory_requirements} mandatory
            requirements: {summary.requirements.map((r) => `${r.label.toLowerCase()} ${plainRequirement(r.expression)}`).join(", ")}.
            {summary.unparsed.length ? (
              <span className="mt-1 block text-hold">Not understood, so not enforced: {summary.unparsed.join("; ")}.</span>
            ) : null}
          </p>
        ) : previewError ? (
          <p className="text-hold">{previewError.message}</p>
        ) : (
          <p className="text-muted">Reading the instruction…</p>
        )}
      </div>

      <ErrorNote error={error} className="mt-4" />

      <div className="mt-6 flex flex-wrap items-center gap-4">
        <button
          type="button"
          onClick={compile}
          disabled={busy || !summary}
          className="rounded-sm bg-signal px-6 py-3 text-base font-semibold text-paper hover:bg-[#1a44c4] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Compiling Evidence Contract…" : "Compile Evidence Contract"}
        </button>
        {system?.demo_mode ? (
          <Link
            href="/demo"
            className="rounded-sm border border-ink px-6 py-3 text-base font-semibold text-ink hover:bg-ink hover:text-paper"
          >
            Run Grand Prize scenario
          </Link>
        ) : null}
        {text !== GRAND_PRIZE_INTENT ? (
          <button type="button" onClick={() => setText(GRAND_PRIZE_INTENT)} className="text-sm text-signal underline underline-offset-4">
            Use Grand Prize instruction
          </button>
        ) : null}
      </div>

      <section aria-label="How it works" className="mt-20 border-t border-rule pt-6">
        <ol className="grid grid-cols-1 gap-5 text-sm sm:grid-cols-2 lg:grid-cols-5 lg:gap-6">
          {[
            ["Intent", "Your instruction, frozen into an Evidence Contract before any evidence is read."],
            ["Evidence", "Observations from registered sources, paid for with x402 when required."],
            ["Verify", "A deterministic engine checks every requirement: present, fresh, consistent, satisfied."],
            ["Authorize", "Only then a short-lived authorization bound to this evidence and this exact action."],
            [chain.isMonad ? "Monad" : "Execute", "The EvidenceGate contract lets that exact action execute once, before the evidence expires."],
          ].map(([title, body], i) => (
            <li key={title}>
              <span className="text-xs text-muted tabular">{i + 1}</span>
              <p className="font-semibold">{title}</p>
              <p className="mt-1 text-ink-2">{body}</p>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
