"use client";

import { useState } from "react";

import { shortHash } from "@/lib/format";

/** A hash shown short, copyable in full, optionally linked to a block explorer. */
export function Hash({
  value,
  href,
  className = "",
}: {
  value: string | null | undefined;
  href?: string | null;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  if (!value) return <span className="text-faint">—</span>;
  const text = shortHash(value, 8, 6);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      /* clipboard unavailable */
    }
  };
  return (
    <span className={`inline-flex min-w-0 items-center gap-1.5 ${className}`}>
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noreferrer"
          title={value}
          className="truncate font-mono text-[0.8125rem] text-signal underline decoration-signal/30 underline-offset-2 hover:decoration-signal"
        >
          {text}
        </a>
      ) : (
        <span title={value} className="truncate font-mono text-[0.8125rem] text-ink-2">
          {text}
        </span>
      )}
      <button
        type="button"
        onClick={copy}
        className="shrink-0 rounded-sm px-1 text-2xs text-muted hover:text-ink"
        aria-label={`Copy ${value}`}
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </span>
  );
}
