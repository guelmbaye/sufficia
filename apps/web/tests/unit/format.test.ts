import { describe, expect, it } from "vitest";

import {
  formatAge,
  formatAmount,
  formatClaimValue,
  formatCountdown,
  formatNumber,
  formatTime,
  formatUsd,
  plainRequirement,
  shortHash,
} from "@/lib/format";

describe("exact number formatting (no float round trip)", () => {
  it("groups thousands and strips trailing zeros", () => {
    expect(formatNumber("92440")).toBe("92,440");
    expect(formatNumber(1340000)).toBe("1,340,000");
    expect(formatNumber("0.4280")).toBe("0.428");
    expect(formatNumber("100.000000")).toBe("100");
  });
  it("keeps precision a float would lose", () => {
    expect(formatNumber("500000.00000000000000000000001")).toBe("500,000.00000000000000000000001");
    expect(formatNumber("231308000000000000000")).toBe("231,308,000,000,000,000,000");
  });
  it("formats USD with sign handling", () => {
    expect(formatUsd("-640000")).toBe("-$640,000");
    expect(formatUsd("1340000", { signed: true })).toBe("+$1,340,000");
    expect(formatUsd("0", { signed: true })).toBe("$0");
    expect(formatAmount("0.02", "USDC")).toBe("0.02 USDC");
  });
  it("formats claim values by unit and claim", () => {
    expect(formatClaimValue(92440, "USD", "available_liquidity")).toBe("$92,440");
    expect(formatClaimValue("1340000", "USD", "smart_money_inflow_24h")).toBe("+$1,340,000");
    expect(formatClaimValue(true, "BOOL")).toBe("Yes");
    expect(formatClaimValue("more than enough", "USD")).toBe("more than enough"); // malformed stays visible
  });
});

describe("time and hashes", () => {
  it("formats ages and countdowns", () => {
    expect(formatAge("0.021")).toBe("0.02 s");
    expect(formatAge("4.2")).toBe("4.2 s");
    expect(formatAge("120.008")).toBe("2 min");
    expect(formatCountdown(12_400)).toBe("00:13");
    expect(formatCountdown(-5)).toBe("00:00");
  });
  it("shows UTC wall time", () => {
    expect(formatTime("2026-10-05T18:42:15.403892Z")).toBe("18:42:15.403");
  });
  it("shortens hashes", () => {
    expect(shortHash("0x7ed7409fed71e5a564460609d2823bfd7b0201e59af3c53c63e0b8068116838e")).toBe("0x7ed7…838e");
    expect(shortHash(null)).toBe("—");
  });
});

describe("plain requirement wording", () => {
  it("reads operators as words", () => {
    expect(plainRequirement("> $500,000")).toBe("more than $500,000");
    expect(plainRequirement(">= $1,000,000")).toBe("at least $1,000,000");
    expect(plainRequirement("< $0.5")).toBe("less than $0.5");
    expect(plainRequirement("EXISTS")).toBe("must exist");
    expect(plainRequirement("NOT_EXISTS")).toBe("must not exist");
  });
});
