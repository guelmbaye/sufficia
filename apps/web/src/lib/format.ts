/* Display formatting. Values are exact decimal strings from the API; formatting works on the digit
   string so nothing is rounded through binary floating point. */

import type { ClaimValue, Num } from "./types";

const DECIMAL = /^(-?)(\d+)(?:\.(\d+))?$/;

function group(intPart: string): string {
  return intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** "92440" -> "92,440"; "0.4280" -> "0.428"; exact, no float conversion. */
export function formatNumber(value: Num | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const text = String(value).trim();
  const m = DECIMAL.exec(text);
  if (!m) return text;
  const [, sign, intPart, frac = ""] = m;
  const f = frac.replace(/0+$/, "");
  const body = group((intPart ?? "0").replace(/^0+(?=\d)/, "")) + (f ? `.${f}` : "");
  return (sign === "-" && body !== "0" ? "-" : "") + body;
}

/** USD display with sign handling: "-640000" -> "-$640,000". */
export function formatUsd(value: Num | null | undefined, { signed = false } = {}): string {
  if (value === null || value === undefined) return "—";
  const n = formatNumber(value);
  if (!/^-?[\d,]+(\.\d+)?$/.test(n)) return n;
  if (n.startsWith("-")) return `-$${n.slice(1)}`;
  return `${signed && n !== "0" ? "+" : ""}$${n}`;
}

export function formatAmount(value: Num | null | undefined, currency = "USDC"): string {
  return `${formatNumber(value)} ${currency}`;
}

/** Evidence value for a claim, by unit. Signed only for flows (inflow can be negative). */
export function formatClaimValue(value: ClaimValue, unit: string | null | undefined, claim?: string): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (unit === "USD" || unit === "USDC") {
    const s = String(value);
    if (!DECIMAL.test(s)) return s;
    return formatUsd(s, { signed: claim === "smart_money_inflow_24h" });
  }
  return typeof value === "number" || DECIMAL.test(String(value)) ? formatNumber(value) : String(value);
}

/** Seconds (decimal string) to a short duration in seconds, like the engine's own reasons:
    "0.021" -> "0.02 s", "4.21" -> "4.2 s", "120.008" -> "120 s", "900" -> "15 min". */
export function formatAge(seconds: Num | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const n = Number(seconds);
  if (!Number.isFinite(n)) return String(seconds);
  if (n < 1) return `${n.toFixed(2).replace(/\.?0+$/, "") || "0"} s`;
  if (n < 10) return `${Math.round(n * 10) / 10} s`;
  if (n < 600) return `${Math.round(n)} s`;
  const m = Math.floor(n / 60);
  const s = Math.round(n % 60);
  return s ? `${m} min ${s} s` : `${m} min`;
}

/** "30 seconds", or "10 to 30 seconds" when requirements differ. */
export function maxAgePhrase(ages: number[]): string {
  if (!ages.length) return "—";
  const lo = Math.min(...ages);
  const hi = Math.max(...ages);
  return lo === hi ? `${hi} seconds` : `${lo} to ${hi} seconds`;
}

/** mm:ss countdown. */
export function formatCountdown(msLeft: number): string {
  const total = Math.max(0, Math.ceil(msLeft / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/** 0x72f1…91ab */
export function shortHash(hash: string | null | undefined, head = 6, tail = 4): string {
  if (!hash) return "—";
  if (hash.length <= head + tail + 2) return hash;
  return `${hash.slice(0, head)}…${hash.slice(-tail)}`;
}

/** UTC wall time with milliseconds: "21:37:35.403". Timestamps are forensic, so no local time zones. */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().slice(11, 23);
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.toISOString().slice(0, 10)} ${d.toISOString().slice(11, 19)} UTC`;
}

export function claimLabel(claim: string): string {
  return (
    {
      smart_money_inflow_24h: "Smart-money inflow (24h)",
      available_liquidity: "Available liquidity",
      asset_price: "Price",
      asset_listed: "Listed on venue",
      sanctions_flag: "Sanctions flag",
      venue_id: "Execution venue",
    }[claim] ?? claim.replaceAll("_", " ")
  );
}

const PROVIDERS: Record<string, string> = {
  "x402-reference": "x402 reference API",
  "x402-paid-evidence": "x402 paid evidence API",
  "simulated-market": "Simulated market",
  "demo-lab": "Test condition",
  nansen: "Nansen",
  dexscreener: "DexScreener",
  chainlink: "Chainlink",
  "uniswap-v2-pool": "Onchain pool",
};

export function providerLabel(provider: string): string {
  return PROVIDERS[provider] ?? provider;
}

/** The engine names providers by id inside its reasons, "(demo-lab)": shown with their labels. */
export function withProviderLabels(text: string): string {
  return text.replace(/\(([a-z0-9-]+)\)/g, (whole, id: string) => (PROVIDERS[id] ? `(${PROVIDERS[id]})` : whole));
}

const MODES: Record<string, string> = { LIVE: "Live", RECORDED: "Recorded", SIMULATED: "Simulated", INJECTED: "Test condition" };

/** Source mode badge (Document 05 §21): simulated or injected evidence is never presented as live. */
export function modeLabel(mode: string): string {
  return MODES[mode] ?? mode;
}

export function actionVerb(type: string | null | undefined): string {
  return ({ BUY: "Buy", SELL: "Sell", TRANSFER: "Transfer" } as Record<string, string>)[type ?? ""] ?? type ?? "";
}

const USD_CLAIMS = new Set(["smart_money_inflow_24h", "available_liquidity", "asset_price"]);

/** Display unit of a claim when only its name is known (receipts, comparisons). */
export function claimUnit(claim: string): string | null {
  return USD_CLAIMS.has(claim) ? "USD" : null;
}

/** Requirement expression in plain words: "EXISTS" -> "must exist", "> $500,000" -> "more than $500,000". */
export function plainRequirement(expression: string): string {
  const e = expression.trim();
  if (e === "EXISTS") return "must exist";
  if (e === "NOT_EXISTS") return "must not exist";
  const m = /^(>=|<=|>|<|==|!=)\s*(.+)$/.exec(e);
  if (!m) return e;
  const words: Record<string, string> = { ">": "more than", ">=": "at least", "<": "less than", "<=": "at most", "==": "exactly", "!=": "not" };
  return `${words[m[1] ?? ""] ?? m[1]} ${m[2]}`;
}
