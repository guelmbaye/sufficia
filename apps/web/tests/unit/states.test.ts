import { describe, expect, it } from "vitest";

import { buildRows } from "@/components/gate/rows";
import { chainLabel, consequence, DECISION_META, rowStatus } from "@/lib/states";
import type { Decision, EvidenceContract, RequirementResult } from "@/lib/types";

const result = (over: Partial<RequirementResult>): RequirementResult => ({
  requirement_id: "req_liquidity",
  claim: "available_liquidity",
  operator: ">",
  required_expression: "> $500,000",
  required: true,
  status: "PASS",
  evidence_ids: [],
  observed_values: [],
  checks: { present: true, fresh: true, valid_type: true, provenance: true, conflict: false, threshold: true },
  reason_code: null,
  human_reason: "",
  ...over,
});

describe("state vocabulary", () => {
  it("never confuses denied with failed, and every state has an icon", () => {
    expect(DECISION_META.DENY.headline).toBe("Execution denied");
    expect(DECISION_META.ABSTAIN.headline).toBe("Agent abstained");
    expect(DECISION_META.REFRESH.headline).toBe("Evidence expired");
    expect(DECISION_META.ESCALATE.headline).toBe("Human review required");
    expect(DECISION_META.AUTHORIZE.headline).toBe("Execution authorized");
    expect(new Set(Object.values(DECISION_META).map((m) => m.icon)).size).toBe(5);
    expect(Object.values(DECISION_META).some((m) => /fail/i.test(m.headline))).toBe(false);
  });
  it("states the consequence for money", () => {
    expect(consequence("DENY", "100 USDC")).toContain("100 USDC did not move");
    expect(consequence("AUTHORIZE", "100 USDC")).toContain("bound to this evidence set and this exact action");
  });
  it("labels the chain honestly", () => {
    expect(chainLabel("LOCAL_EVM").honest).toContain("not Monad");
    expect(chainLabel("MONAD_TESTNET")).toMatchObject({ name: "Monad", isMonad: true });
  });
});

describe("rows follow the engine result, never recompute it", () => {
  it("maps requirement results to row statuses", () => {
    expect(rowStatus(undefined)).toBe("PENDING");
    expect(rowStatus(result({ status: "PASS" }))).toBe("PASS");
    expect(rowStatus(result({ status: "FAIL", reason_code: "THRESHOLD_NOT_MET" }))).toBe("FAIL");
    expect(rowStatus(result({ status: "UNKNOWN", reason_code: "EVIDENCE_STALE" }))).toBe("STALE");
    expect(rowStatus(result({ status: "UNKNOWN", reason_code: "CRITICAL_SOURCE_CONFLICT" }))).toBe("CONFLICT");
    expect(rowStatus(result({ status: "UNKNOWN", reason_code: "MISSING_REQUIRED_EVIDENCE" }))).toBe("MISSING");
  });
  it("marks only the rows that explain the decision as causal", () => {
    const contract = {
      requirements: [
        { id: "req_inflow", claim: "smart_money_inflow_24h" },
        { id: "req_liquidity", claim: "available_liquidity" },
      ],
    } as unknown as EvidenceContract;
    const decision = {
      state: "DENY",
      requirement_results: [
        result({ requirement_id: "req_inflow", claim: "smart_money_inflow_24h", status: "PASS" }),
        result({ requirement_id: "req_liquidity", status: "FAIL", reason_code: "THRESHOLD_NOT_MET" }),
      ],
      evidence_assessments: [],
      reasons: [],
    } as unknown as Decision;
    const rows = buildRows(contract, null, decision);
    expect(rows.map((r) => [r.requirement.id, r.status, r.causal])).toEqual([
      ["req_inflow", "PASS", false],
      ["req_liquidity", "FAIL", true],
    ]);
  });
});
