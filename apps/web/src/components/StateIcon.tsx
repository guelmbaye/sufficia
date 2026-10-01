import type { IconName } from "@/lib/states";

/* State glyphs drawn on a 24px grid with currentColor. Color is never the only signal: every
   state has its own shape (check, cross, clock, split, dash, ring). */
export function StateIcon({ name, size = 20, className = "" }: { name: IconName; size?: number; className?: string }) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2.2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    className,
    "aria-hidden": true,
  };
  switch (name) {
    case "allow":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="10" />
          <path d="M7.5 12.5l3 3 6-7" />
        </svg>
      );
    case "deny":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="10" />
          <path d="M8.5 8.5l7 7M15.5 8.5l-7 7" />
        </svg>
      );
    case "stale":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="10" />
          <path d="M12 7v5.5l3.5 2" />
        </svg>
      );
    case "conflict":
      return (
        <svg {...common}>
          <path d="M12 2.8l9.6 17.2H2.4z" />
          <path d="M12 9.5v4.5M12 17.2v.3" />
        </svg>
      );
    case "missing":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="10" strokeDasharray="3.2 2.6" />
          <path d="M8 12h8" />
        </svg>
      );
    default:
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="9" strokeOpacity="0.35" />
          <path d="M12 3a9 9 0 0 1 9 9" />
        </svg>
      );
  }
}
