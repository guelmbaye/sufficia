import { rowStatus, type RowStatus } from "@/lib/states";
import type {
  AcquisitionFailure,
  Decision,
  EvidenceAssessment,
  EvidenceContract,
  EvidenceItemView,
  EvidenceSetView,
  Reason,
  Requirement,
  RequirementResult,
} from "@/lib/types";

/** One row of the Live Gate: a contract requirement joined with the engine's result for it. */
export interface GateRow {
  requirement: Requirement;
  result: RequirementResult | undefined;
  status: RowStatus;
  items: EvidenceItemView[];
  assessments: EvidenceAssessment[];
  reason: Reason | undefined;
  failures: AcquisitionFailure[];
  causal: boolean;
}

/** Which rows explain the decision (their connector into the gate is highlighted). */
function isCausal(state: Decision["state"] | undefined, status: RowStatus): boolean {
  switch (state) {
    case "DENY":
      return status === "FAIL";
    case "REFRESH":
      return status === "STALE";
    case "ESCALATE":
      return status === "CONFLICT";
    case "ABSTAIN":
      return status === "MISSING";
    case "AUTHORIZE":
      return status === "PASS";
    default:
      return false;
  }
}

export function buildRows(contract: EvidenceContract, evidence: EvidenceSetView | null, decision: Decision | null): GateRow[] {
  return contract.requirements.map((requirement) => {
    const result = decision?.requirement_results.find((r) => r.requirement_id === requirement.id);
    const status = decision ? rowStatus(result) : "PENDING";
    const items = (evidence?.items ?? []).filter((v) => v.item.requirement_id === requirement.id);
    const assessments = (decision?.evidence_assessments ?? []).filter((a) => a.requirement_id === requirement.id);
    return {
      requirement,
      result,
      status,
      items,
      assessments,
      reason: decision?.reasons.find((r) => r.requirement_id === requirement.id),
      failures: (evidence?.failures ?? []).filter((f) => f.requirement_id === requirement.id),
      causal: isCausal(decision?.state, status),
    };
  });
}

/** Evidence items the engine actually used for a row (valid, stale or conflicted), in engine order. */
export function usedItems(row: GateRow): EvidenceItemView[] {
  const ids = row.result?.evidence_ids ?? [];
  const byId = new Map(row.items.map((v) => [v.item.evidence_id, v]));
  const used = ids.map((id) => byId.get(id)).filter((v): v is EvidenceItemView => Boolean(v));
  return used.length ? used : row.items;
}
