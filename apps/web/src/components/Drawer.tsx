"use client";

import { useEffect, useRef, type ReactNode } from "react";

/** Right-hand inspector drawer (Document 05 §61-62): technical depth without a separate page. */
export function Drawer({
  open,
  title,
  onClose,
  children,
  width = "34rem",
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  width?: string;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    panel.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={title}>
      <button type="button" className="absolute inset-0 bg-ink/25" onClick={onClose} aria-label="Close" />
      <div
        ref={panel}
        tabIndex={-1}
        className="relative flex h-full flex-col border-l border-rule bg-paper outline-none"
        style={{ width: `min(${width}, 100vw)` }}
      >
        <div className="flex items-center justify-between border-b border-rule px-6 py-4">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button type="button" onClick={onClose} className="rounded-sm px-2 py-1 text-sm text-muted hover:text-ink">
            Close
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-5">{children}</div>
      </div>
    </div>
  );
}

/** Label/value pairs used inside inspectors. */
export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[9.5rem_1fr] gap-3 border-b border-rule/70 py-2 text-sm last:border-0">
      <dt className="text-muted">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}
