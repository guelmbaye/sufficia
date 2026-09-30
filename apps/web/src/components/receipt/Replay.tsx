"use client";

import { useState } from "react";

import { Hash } from "@/components/Hash";
import { StateIcon } from "@/components/StateIcon";
import { api, ApiError } from "@/lib/api";
import { claimLabel, formatNumber } from "@/lib/format";
import { DECISION_META, TONE_CLASSES } from "@/lib/states";
import type { ReplayResult, TamperResult } from "@/lib/types";

const CHECK_LABEL: Record<string, string> = {
  receipt_hash: "Receipt hash",
  receipt_hash_in_body: "Receipt hash in body",
  intent_hash: "Intent hash",
  receipt_intent_hash: "Receipt intent hash",
  decision_state: "Decision state",
  reason_codes: "Reason codes",
  contract_hash: "Contract hash",
  evidence_root: "Evidence root",
  fingerprint: "Decision fingerprint",
  decision_id: "Decision id",
  engine_version: "Engine version",
  explanation: "Explanation",
  requirement_statuses: "Requirement results",
  frozen_contract_hash: "Frozen contract hash",
  receipt_decision_state: "Receipt decision state",
  receipt_evidence_root: "Receipt evidence root",
  binding_mismatches: "Authorization binding",
  receipt_action_hash: "Receipt action hash",
  onchain_exists: "Authorization onchain",
  onchain_contract_hash: "Onchain contract hash",
  onchain_evidence_root: "Onchain evidence root",
  onchain_action_hash: "Onchain action hash",
  onchain_consumed: "Consumed onchain",
  onchain_receipt_commitment: "Onchain receipt commitment",
  no_authorization_for_non_authorize: "No authorization issued",
  no_transaction: "No transaction submitted",
  onchain_state: "Onchain state",
};

const HEADLINE_CHECKS = ["contract_hash", "evidence_root", "engine_version", "decision_state", "reason_codes", "explanation"];

/** Decision Replay (Document 05 §37-40): re-evaluates the original contract against the exact evidence
    snapshot used at decision time. Never "run again": no market data is queried. */
export function ReplayPanel({ receiptId }: { receiptId: string }) {
  const [result, setResult] = useState<ReplayResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showAll, setShowAll] = useState(false);

  const replay = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.replay(receiptId));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const ok = result?.result === "MATCH";
  const byName = new Map(result?.checks.map((c) => [c.check, c]) ?? []);
  const headline = HEADLINE_CHECKS.map((k) => byName.get(k)).filter(Boolean);
  const passed = result?.checks.filter((c) => c.ok === true).length ?? 0;

  return (
    <section aria-label="Decision replay" data-testid="replay">
      <div className="flex flex-wrap items-center gap-4">
        <button
          type="button"
          onClick={replay}
          disabled={busy}
          className="rounded-sm bg-ink px-5 py-3 text-base font-semibold text-paper hover:bg-ink-2 disabled:opacity-60"
          data-testid="replay-button"
        >
          {busy ? "Replaying decision…" : result ? "Replay decision again" : "Replay decision"}
        </button>
        <p className="max-w-[56ch] text-sm text-ink-2">
          Re-evaluate the original contract against the exact evidence snapshot used at decision time. No market data is queried.
        </p>
      </div>
      {error ? <p className="mt-3 text-sm text-deny">{error}</p> : null}

      {result ? (
        <div className="mt-5">
          <p className={`flex items-center gap-2 font-expanded text-2xl font-extrabold ${ok ? "text-allow" : "text-deny"}`} data-testid="replay-result">
            <StateIcon name={ok ? "allow" : "deny"} size={28} />
            {ok ? "Deterministic replay verified" : "Replay mismatch"}
          </p>

          <div className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-sm border border-rule bg-rule">
            {(["original", "replay"] as const).map((side) => {
              const s = result[side];
              const meta = DECISION_META[s.state];
              return (
                <div key={side} className="bg-paper p-4">
                  <p className="text-xs text-muted">{side === "original" ? "Original decision" : "Replay"}</p>
                  <p className={`mt-1 font-expanded text-xl font-extrabold ${TONE_CLASSES[meta.tone].text}`}>{s.state}</p>
                  <p className="mt-1 text-sm text-ink-2">{s.reason}</p>
                  <p className="mt-1 font-mono text-2xs text-muted">{s.reason_codes.join(", ")}</p>
                </div>
              );
            })}
          </div>

          <ul className="mt-4 grid grid-cols-2 gap-x-8 gap-y-1 text-sm md:grid-cols-3">
            {headline.map((c) => (
              <li key={c!.check} className="flex items-center justify-between border-b border-rule py-1.5">
                <span>{CHECK_LABEL[c!.check] ?? c!.check}</span>
                <span className={`font-bold ${c!.ok ? "text-allow" : "text-deny"}`}>{c!.ok ? "Match" : "Mismatch"}</span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-sm text-muted">
            {passed} of {result.checks.filter((c) => c.ok !== null).length} checks passed. Chain verification:{" "}
            {result.chain_verification === "VERIFIED"
              ? "the EvidenceGate's stored authorization and receipt commitment match."
              : result.chain_verification === "SKIPPED"
                ? "skipped, the chain that holds this authorization is not reachable from this server."
                : "not applicable, no authorization exists for this decision."}{" "}
            Snapshot <Hash value={result.inputs.snapshot_hash} />
          </p>
          <button type="button" onClick={() => setShowAll((v) => !v)} className="mt-2 text-sm text-signal underline underline-offset-4">
            {showAll ? "Hide all checks" : "Show all checks"}
          </button>
          {showAll ? (
            <table className="mt-2 w-full text-left text-xs">
              <tbody>
                {result.checks.map((c) => (
                  <tr key={`${c.group}-${c.check}`} className="border-b border-rule align-top">
                    <td className="py-1.5 pr-3 text-muted">{c.group}</td>
                    <td className="py-1.5 pr-3">{CHECK_LABEL[c.check] ?? c.check}</td>
                    <td className={`py-1.5 font-bold ${c.ok === null ? "text-muted" : c.ok ? "text-allow" : "text-deny"}`}>
                      {c.ok === null ? "Skipped" : c.ok ? "Match" : "Mismatch"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

/** Receipt integrity test (Document 05 §41): one value changed by the smallest unit, in memory only. */
export function TamperTest({ receiptId }: { receiptId: string }) {
  const [t, setT] = useState<TamperResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = async () => {
    setError(null);
    try {
      setT(await api.tamperTest(receiptId));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  };
  return (
    <section aria-label="Receipt integrity test" data-testid="tamper">
      <button type="button" onClick={run} className="rounded-sm border border-ink/70 px-4 py-2 text-sm font-semibold hover:bg-canvas">
        Test receipt integrity
      </button>
      {error ? <p className="mt-2 text-sm text-deny">{error}</p> : null}
      {t ? (
        <div className="mt-3 text-sm">
          <p>
            {claimLabel(t.claim)} changed from <span className="font-bold tabular">{formatNumber(t.original_value)}</span> to{" "}
            <span className="font-bold tabular text-deny">{formatNumber(t.tampered_value)}</span>, in memory only.
          </p>
          <dl className="mt-2 divide-y divide-rule border-y border-rule">
            <div className="grid grid-cols-[10rem_1fr_auto] items-center gap-3 py-1.5">
              <dt className="text-muted">Evidence root</dt>
              <dd>
                <Hash value={t.tampered_evidence_root} />
              </dd>
              <dd className="font-bold text-deny">{t.evidence_root === "MISMATCH" ? "Mismatch" : "Match"}</dd>
            </div>
            <div className="grid grid-cols-[10rem_1fr_auto] items-center gap-3 py-1.5">
              <dt className="text-muted">Receipt integrity</dt>
              <dd>
                <Hash value={t.tampered_receipt_hash} />
              </dd>
              <dd className="font-bold text-deny">{t.receipt_integrity === "INVALID" ? "Invalid" : "Valid"}</dd>
            </div>
          </dl>
          <p className="mt-2 text-xs text-muted">Nothing stored was modified; replaying the real receipt still matches.</p>
        </div>
      ) : null}
    </section>
  );
}
