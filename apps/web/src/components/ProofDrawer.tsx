"use client";

import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useSystem } from "@/lib/hooks";
import { chainLabel, explorerTx } from "@/lib/states";
import type { BenchmarkReport, ChainTx } from "@/lib/types";

import { Drawer, Field } from "./Drawer";
import { Hash } from "./Hash";

const PURPOSE: Record<string, string> = {
  INTENT_REGISTER: "Intent registered",
  AUTHORIZATION: "Authorization committed",
  PROTECTED_EXECUTION: "Protected execution",
  RECEIPT_COMMITMENT: "Receipt committed",
  EVIDENCE_PAYMENT: "Evidence payment",
};

/** Q&A proof surface (Document 05 §105-113, §120): only values that were actually verified. */
export function ProofDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { system } = useSystem();
  const [bench, setBench] = useState<BenchmarkReport | null>(null);
  const [benchMissing, setBenchMissing] = useState(false);
  const [txs, setTxs] = useState<ChainTx[]>([]);
  const [audit, setAudit] = useState<{ valid: boolean; events: number; head: string } | null>(null);

  // The benchmark report is a file that does not change while the page is open: read it once. Chain
  // transactions and the audit chain grow with every run: read them each time the drawer opens.
  const benchRequested = useRef(false);
  useEffect(() => {
    if (!open || benchRequested.current) return;
    benchRequested.current = true;
    api.benchmark().then(setBench, () => setBenchMissing(true));
  }, [open]);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    api.chainTransactions(12).then((r) => alive && setTxs(r.transactions), () => alive && setTxs([]));
    api.auditVerify().then((a) => alive && setAudit(a), () => alive && setAudit(null));
    return () => {
      alive = false;
    };
  }, [open]);

  const chain = chainLabel(system?.chain?.mode);
  const explorer = system?.chain?.explorer_url;
  const m = bench?.metrics ?? {};

  return (
    <Drawer open={open} onClose={onClose} title="Proof" width="38rem">
      <section className="mb-8">
        <h3 className="mb-3 text-base font-semibold">Validation</h3>
        {bench ? (
          <>
            <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-sm border border-rule bg-rule">
              {[
                ["Benchmark cases matched", `${m.decision_correct ?? "—"} of ${m.total_cases ?? "—"}`],
                ["Unauthorized authorizations", String(m.unauthorized_authorizations ?? "—")],
                ["Replay fidelity", `${m.replay_fidelity_pct ?? "—"}%`],
                ["Explanation fidelity", `${m.explanation_fidelity_pct ?? "—"}%`],
              ].map(([k, v]) => (
                <div key={k} className="bg-paper px-4 py-3">
                  <dt className="text-xs text-muted">{k}</dt>
                  <dd className="mt-1 font-wide text-xl font-bold tabular">{v}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-2 text-xs text-muted">
              {m.total_cases ?? bench.total} hermetic cases (valid, missing, stale, threshold, conflict, adversarial), engine {bench.engine_version},
              generated{" "}
              {formatDateTime(bench.generated_at)} at commit <span className="font-mono">{String(bench.commit).slice(0, 10)}</span>.
            </p>
          </>
        ) : (
          <p className="text-sm text-muted">
            {benchMissing ? "No benchmark report yet. Run `make benchmark` to generate one." : "Loading the benchmark report…"}
          </p>
        )}
      </section>

      <section className="mb-8">
        <h3 className="mb-2 text-base font-semibold">Onchain</h3>
        <dl>
          <Field label="Network">
            {system?.chain?.network ?? chain.display}, chain id {system?.chain?.chain_id ?? "—"}
          </Field>
          <Field label="EvidenceGate">
            <Hash value={system?.chain?.evidence_gate} href={explorer && chain.isMonad ? `${explorer}/address/${system?.chain?.evidence_gate}` : null} />
          </Field>
          <Field label="Authorizer">
            <Hash value={system?.chain?.authorizer} />
          </Field>
          <Field label="Executor">
            <Hash value={system?.chain?.executor} />
          </Field>
        </dl>
        <h4 className="mb-2 mt-5 text-sm font-semibold">Latest transactions</h4>
        {txs.length === 0 ? (
          <p className="text-sm text-muted">No transaction has been submitted yet.</p>
        ) : (
          <ul className="divide-y divide-rule border-y border-rule">
            {txs.map((t) => (
              <li key={t.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                <span className="min-w-0">
                  <span className="block">{PURPOSE[t.purpose] ?? t.purpose}</span>
                  <span className="text-xs text-muted">
                    {t.status === "CONFIRMED" ? `Confirmed in block ${t.block_number}` : t.status}
                    {t.error_name ? `, ${t.error_name}` : ""}
                  </span>
                </span>
                <Hash value={t.tx_hash} href={explorerTx(explorer, system?.chain?.mode, t.tx_hash, t)} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="mb-8">
        <h3 className="mb-2 text-base font-semibold">Audit log</h3>
        {audit ? (
          <p className="text-sm">
            {audit.valid ? "Hash chain intact" : "Hash chain broken"} across {audit.events} events. Head{" "}
            <Hash value={audit.head} />
          </p>
        ) : (
          <p className="text-sm text-muted">Checking the audit chain…</p>
        )}
      </section>

      <section className="mb-8">
        <h3 className="mb-2 text-base font-semibold">How authority is produced</h3>
        <p className="text-sm text-ink-2">
          A language model may only draft the Evidence Contract, and a draft weaker than the instruction is rejected. A deterministic
          engine decides from evidence; the EvidenceGate contract enforces the exact authorized action, once, before the evidence expires.
        </p>
      </section>

      <section>
        <h3 className="mb-3 text-base font-semibold">Why not wallet limits?</h3>
        <div className="grid grid-cols-2 gap-px overflow-hidden rounded-sm border border-rule bg-rule text-sm">
          <div className="bg-paper p-4">
            <p className="text-xs text-muted">A wallet asks</p>
            <p className="mt-1 font-medium">Can this agent spend this amount?</p>
          </div>
          <div className="bg-paper p-4">
            <p className="text-xs text-muted">SUFFICIA asks</p>
            <p className="mt-1 font-medium">Has the evidence required for this particular action been established, right now?</p>
          </div>
        </div>
      </section>
    </Drawer>
  );
}
