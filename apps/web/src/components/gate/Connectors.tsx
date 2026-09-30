"use client";

import { useLayoutEffect, useState, type RefObject } from "react";

import { ROW_META, TONE_CLASSES } from "@/lib/states";

import type { GateRow } from "./rows";

interface Path {
  id: string;
  d: string;
  y1: number;
  y2: number;
  stroke: string;
  width: number;
  causal: boolean;
  dashed: boolean;
  len: number;
}

/* Causal connectors (Document 05 §75-76): every evidence row is wired into the gate. The rows that
   explain the decision are drawn thick in the decision's color; the others stay quiet. */
export function Connectors({
  rows,
  rowRefs,
  gutterRef,
  targetRef,
  animateKey,
}: {
  rows: GateRow[];
  rowRefs: RefObject<(HTMLDivElement | null)[]>;
  gutterRef: RefObject<HTMLDivElement | null>;
  targetRef: RefObject<HTMLElement | null>;
  animateKey: string;
}) {
  const [paths, setPaths] = useState<Path[]>([]);
  const [size, setSize] = useState({ w: 0, h: 0 });

  useLayoutEffect(() => {
    const measure = () => {
      const gutter = gutterRef.current;
      const target = targetRef.current;
      if (!gutter || !target) return;
      const g = gutter.getBoundingClientRect();
      const t = target.getBoundingClientRect();
      const w = g.width;
      const center = t.top + t.height / 2 - g.top;
      const n = rows.length;
      const next: Path[] = [];
      rows.forEach((row, i) => {
        const el = rowRefs.current?.[i];
        if (!el) return;
        const r = el.getBoundingClientRect();
        const y1 = r.top + Math.min(r.height / 2, 44) - g.top;
        const y2 = center + (i - (n - 1) / 2) * 9;
        const tone = TONE_CLASSES[ROW_META[row.status].tone];
        const emphasized = row.causal;
        next.push({
          id: row.requirement.id,
          d: `M 0 ${y1} C ${w * 0.6} ${y1}, ${w * 0.4} ${y2}, ${w} ${y2}`,
          y1,
          y2,
          stroke: emphasized ? tone.stroke : "var(--color-rule-strong)",
          width: emphasized ? 3.25 : 1.5,
          causal: emphasized,
          dashed: row.status === "PENDING",
          len: Math.ceil(w + Math.abs(y2 - y1) * 1.3),
        });
      });
      setPaths(next);
      setSize({ w, h: g.height });
    };

    measure();
    const ro = new ResizeObserver(measure);
    if (gutterRef.current) ro.observe(gutterRef.current);
    rowRefs.current?.forEach((el) => el && ro.observe(el));
    if (targetRef.current) ro.observe(targetRef.current);
    window.addEventListener("resize", measure);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [rows, gutterRef, rowRefs, targetRef]);

  return (
    <svg
      className="pointer-events-none absolute inset-0 h-full w-full overflow-visible"
      width={size.w}
      height={size.h}
      aria-hidden
      data-testid="connectors"
    >
      {/* quiet lines first, causal lines on top */}
      {[...paths]
        .sort((a, b) => Number(a.causal) - Number(b.causal))
        .map((p) => (
          <g key={`${animateKey}-${p.id}`}>
            <circle cx={1.5} cy={p.y1} r={3} fill={p.stroke} />
            <path
              d={p.d}
              fill="none"
              stroke={p.stroke}
              strokeWidth={p.width}
              strokeLinecap="round"
              strokeDasharray={p.dashed ? "3 5" : undefined}
              className={p.dashed ? undefined : "connector-draw"}
              style={{ ["--len" as string]: p.len }}
              data-causal={p.causal}
            />
            <circle cx={size.w - 1} cy={p.y2} r={p.causal ? 4.5 : 3} fill={p.stroke} />
          </g>
        ))}
    </svg>
  );
}
