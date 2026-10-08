import type { ApiError } from "@/lib/api";

/** Errors explain what happened and what to do, in the interface's voice (no apologies). */
export function ErrorNote({ error, className = "" }: { error: ApiError | null; className?: string }) {
  if (!error) return null;
  const hint =
    error.code === "API_UNREACHABLE"
      ? "A hosted demo may be waking up: reload in a minute. Running locally: start the API with `make api`."
      : error.code === "CHAIN_UNAVAILABLE" || error.code === "CHAIN_DISABLED"
        ? "Protected execution needs a chain: check CHAIN_MODE and the deployment."
        : error.code === "DEMO_DISABLED"
          ? "Scenarios and test conditions run only when the API has DEMO_MODE=true. Start from the mission instead."
          : null;
  return (
    <div role="alert" className={`rounded-sm border border-deny/40 bg-deny-tint px-4 py-3 text-sm ${className}`}>
      <p className="font-semibold text-deny">
        {error.message}
        <span className="ml-2 font-mono text-2xs font-normal text-deny/80">{error.code}</span>
      </p>
      {hint ? <p className="mt-1 text-ink-2">{hint}</p> : null}
    </div>
  );
}
