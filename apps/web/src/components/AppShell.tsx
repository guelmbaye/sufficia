"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { useSystem } from "@/lib/hooks";
import { useRun } from "@/lib/run";
import { chainLabel } from "@/lib/states";

import { ProofDrawer } from "./ProofDrawer";

interface Step {
  n: number;
  label: string;
  href: string | null;
  match: (path: string) => boolean;
}

/* Five primary views, no dashboard (Document 05 §7). The steps are a real sequence, so they are
   numbered; a step is reachable only once the run has produced what it shows. */
export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname() ?? "/";
  const router = useRouter();
  const { run } = useRun();
  const { system, error: systemError } = useSystem();
  const [proofOpen, setProofOpen] = useState(false);
  const chain = chainLabel(system?.chain?.mode);

  const steps: Step[] = [
    { n: 1, label: "Mission", href: "/", match: (p) => p === "/" || p === "/demo" },
    { n: 2, label: "Evidence Contract", href: run.contractId ? `/contract/${run.contractId}` : null, match: (p) => p.startsWith("/contract") },
    { n: 3, label: "Live gate", href: run.contractId ? `/gate/${run.contractId}` : null, match: (p) => p.startsWith("/gate") },
    {
      n: 4,
      label: "Execution",
      href: run.decisionId && run.decisionState === "AUTHORIZE" ? `/execution/${run.decisionId}` : null,
      match: (p) => p.startsWith("/execution"),
    },
    { n: 5, label: "Receipt", href: run.receiptId ? `/receipt/${run.receiptId}` : null, match: (p) => p.startsWith("/receipt") },
  ];

  const openProof = useCallback(() => setProofOpen(true), []);

  // Q&A shortcuts (Document 05 §105): 1 live gate, 2 receipt, 3 proof.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.metaKey || e.ctrlKey || e.altKey || (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)))) return;
      if (e.key === "1" && run.contractId) router.push(`/gate/${run.contractId}`);
      if (e.key === "2" && run.receiptId) router.push(`/receipt/${run.receiptId}`);
      if (e.key === "3") setProofOpen((o) => !o);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router, run.contractId, run.receiptId]);

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-40 border-b border-rule bg-paper">
        <div className="mx-auto flex h-14 max-w-[1600px] items-center gap-3 px-4 sm:gap-6 sm:px-6">
          <Link href="/" className="flex shrink-0 items-center" aria-label="SUFFICIA home">
            <Image src="/logo-wordmark.png" alt="SUFFICIA" width={144} height={30} priority className="hidden h-[26px] w-auto sm:block" />
            <Image src="/logo-mark.png" alt="SUFFICIA" width={25} height={28} priority className="h-7 w-auto sm:hidden" />
          </Link>
          <nav aria-label="Run steps" className="hidden min-w-0 flex-1 items-center gap-1 overflow-x-auto md:flex">
            {steps.map((s) => {
              const active = s.match(pathname);
              const body = (
                <>
                  <span
                    className={`grid h-5 w-5 shrink-0 place-items-center rounded-full text-2xs tabular ${
                      active ? "bg-ink text-paper" : s.href ? "border border-ink/40 text-ink" : "border border-rule-strong text-faint"
                    }`}
                  >
                    {s.n}
                  </span>
                  <span className="whitespace-nowrap">{s.label}</span>
                </>
              );
              const cls = `flex items-center gap-2 rounded-sm px-2.5 py-1.5 text-sm ${
                active ? "font-semibold text-ink" : s.href ? "text-ink-2 hover:bg-canvas" : "cursor-not-allowed text-faint"
              }`;
              return s.href && !active ? (
                <Link key={s.n} href={s.href} className={cls}>
                  {body}
                </Link>
              ) : (
                <span key={s.n} className={cls} aria-current={active ? "step" : undefined} aria-disabled={!s.href}>
                  {body}
                </span>
              );
            })}
          </nav>
          <div className="ml-auto flex shrink-0 items-center gap-2 sm:gap-3">
            <span
              className={`flex items-center gap-2 rounded-sm border px-2.5 py-1 text-xs ${
                chain.isMonad ? "border-signal/40 text-signal" : "border-rule-strong text-ink-2"
              }`}
              title={system?.chain?.evidence_gate ? `EvidenceGate ${system.chain.evidence_gate}` : undefined}
              data-testid="chain-badge"
            >
              <span className={`h-2 w-2 rounded-full ${systemError ? "bg-deny" : chain.isMonad ? "bg-signal" : "bg-hold"}`} aria-hidden />
              {system ? (
                <>
                  <span className="sm:hidden">{chain.short}</span>
                  <span className="hidden sm:inline">{chain.honest}</span>
                </>
              ) : systemError ? (
                "API unreachable"
              ) : (
                "Connecting…"
              )}
            </span>
            <button
              type="button"
              onClick={openProof}
              className="rounded-sm border border-ink/80 px-3 py-1 text-sm font-medium text-ink hover:bg-ink hover:text-paper"
            >
              Proof
            </button>
          </div>
        </div>
      </header>
      <main className="flex-1">{children}</main>
      <footer className="border-t border-rule bg-paper">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-2 px-6 py-2 text-2xs text-muted">
          <span>Demo asset on a testnet-first system. Not an investment recommendation.</span>
          <span className="tabular">
            SUFFICIA {system?.version ?? ""}, decision engine {system?.engine_version ?? ""}. Shortcuts: 1 live gate, 2 receipt, 3 proof.
          </span>
        </div>
      </footer>
      <ProofDrawer open={proofOpen} onClose={() => setProofOpen(false)} />
    </div>
  );
}
