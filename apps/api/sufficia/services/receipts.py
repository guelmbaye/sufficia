"""Evidence Execution Receipts (Document 02 §17, Document 03 §45-46, Document 04 §79-81).

A receipt joins intent, contract, evidence, decision, authority and execution into one canonical
body; ``receipt_hash = keccak(RECEIPT_DOMAIN || canonical(body))``. Blocked decisions get a receipt
too: ``execution.status = BLOCKED`` with ``tx_hash = null`` — the absence of a transaction is part
of the proof. The hash is committed onchain (EvidenceGate.recordReceipt) for executed actions.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Optional

from ..canonical.hashes import REQUEST_DOMAIN, raw_hash, receipt_hash
from ..domain.enums import DecisionState, PaymentStatus
from ..domain.models import (
    Authorization,
    CanonicalAction,
    Decision,
    EvidenceContract,
    EvidenceItem,
    EvidenceReceipt,
    Execution,
    Intent,
    ReceiptEvidenceLine,
    ReceiptPaymentLine,
)

RECEIPT_KINDS = ("BLOCKED", "EXECUTED", "FAILED", "EXPIRED")


def receipt_id_for(decision_id: str, kind: str, execution_id: Optional[str]) -> str:
    return "rcpt_" + raw_hash(REQUEST_DOMAIN, f"receipt|{decision_id}|{kind}|{execution_id or '-'}".encode())[2:18]


def evidence_lines(decision: Decision, items: Iterable[EvidenceItem]) -> list[ReceiptEvidenceLine]:
    by_id = {it.evidence_id: it for it in items}
    lines = []
    for r in decision.requirement_results:
        used = [by_id[e] for e in r.evidence_ids if e in by_id]
        lines.append(
            ReceiptEvidenceLine(
                requirement_id=r.requirement_id,
                claim=r.claim,
                required=r.required_expression,
                observed=r.observed_values,
                status=r.status,
                providers=sorted({u.source.provider for u in used}),
                modes=sorted({u.source.mode for u in used}, key=lambda m: m.value),
            )
        )
    return lines


def payment_lines(payments: Iterable[dict[str, Any]]) -> list[ReceiptPaymentLine]:
    out = []
    for p in sorted(payments, key=lambda p: (p.get("requirement_id") or "", p.get("provider") or "", p.get("acquisition_id") or "")):
        amount = p.get("settled") if p.get("settled") is not None else p.get("quoted")
        out.append(
            ReceiptPaymentLine(
                evidence_id=p.get("evidence_id"),
                provider=p["provider"],
                amount=Decimal(str(amount)) if amount is not None else None,
                currency=p.get("currency") or "USDC",
                status=PaymentStatus(p["status"]),
                payment_reference=p.get("payment_reference"),
                network=p.get("network"),
            )
        )
    return out


def build_receipt(
    *,
    kind: str,
    intent: Intent,
    intent_hash: str,
    contract: EvidenceContract,
    decision: Decision,
    items: list[EvidenceItem],
    payments: list[dict[str, Any]],
    network: str,
    chain_mode: str,
    created_at: datetime,
    authorization: Optional[Authorization] = None,
    action: Optional[CanonicalAction] = None,
    authorization_txs: Optional[dict[str, Any]] = None,
    execution: Optional[Execution] = None,
    gate_address: Optional[str] = None,
) -> EvidenceReceipt:
    if kind not in RECEIPT_KINDS:
        raise ValueError(kind)
    if kind == "BLOCKED" and decision.state == DecisionState.AUTHORIZE:
        raise ValueError("an AUTHORIZE decision is never BLOCKED")
    if kind in ("EXECUTED", "FAILED") and (authorization is None or execution is None):
        raise ValueError(f"{kind} receipts require an authorization and an execution")

    reason = decision.reasons[0] if decision.reasons else None
    auth_block = None
    if authorization is not None:
        auth_block = {
            "authorization_id": authorization.authorization_id,
            "onchain_intent_id": authorization.onchain_intent_id,
            "executor": authorization.executor,
            "nonce": authorization.nonce,
            "issued_at": authorization.issued_at,
            "expires_at": authorization.expires_at,
            "max_amount": authorization.max_amount,
            "gate_address": gate_address,
            "action": None
            if action is None
            else {
                "adapter": action.adapter,
                "description": action.description,
                "target": action.target,
                "asset_in": action.asset_in,
                "max_amount_in": action.max_amount_in,
                "asset_out": action.asset_out,
                "min_amount_out": action.min_amount_out,
                "recipient": action.recipient,
                "deadline": action.deadline,
            },
            **(authorization_txs or {}),
        }
    if execution is not None:
        exec_block: dict[str, Any] = {
            "execution_id": execution.execution_id,
            "status": execution.status.value,
            "network": execution.network,
            "chain_id": execution.chain_id,
            "tx_hash": execution.tx_hash,
            "block_number": execution.block_number,
            "amount_usdc": execution.amount.value if execution.amount else None,
            "error_code": execution.error_code,
            "confirmed_at": execution.confirmed_at,
        }
    else:
        exec_block = {
            "execution_id": None,
            "status": "BLOCKED" if kind == "BLOCKED" else kind,
            "network": network,
            "chain_id": None,
            "tx_hash": None,
            "block_number": None,
            "amount_usdc": Decimal(0),
            "error_code": decision.reason_codes[0].value if kind == "BLOCKED" and decision.reason_codes else (
                "AUTHORIZATION_EXPIRED" if kind == "EXPIRED" else None
            ),
            "confirmed_at": None,
        }

    body = EvidenceReceipt(
        receipt_id=receipt_id_for(decision.decision_id, kind, exec_block.get("execution_id")),
        kind=kind,
        intent_id=intent.intent_id,
        intent_text=intent.text,
        intent_hash=intent_hash,
        contract_id=contract.contract_id,
        contract_hash=decision.contract_hash,
        evidence_root=decision.evidence_root,
        action_hash=authorization.action_hash if authorization else None,
        decision_id=decision.decision_id,
        decision_state=decision.state,
        reason_codes=decision.reason_codes,
        decision_reason=reason.message if reason else decision.summary,
        engine_version=decision.engine_version,
        evaluated_at=decision.evaluated_at,
        evidence_count=decision.evidence_count,
        evidence_cost_usdc=decision.evidence_cost_usd,
        coverage=decision.coverage,
        freshness_pass=decision.freshness_pass,
        conflicts=decision.conflicts,
        evidence=evidence_lines(decision, items),
        payments=payment_lines(payments),
        authorization=auth_block,
        execution=exec_block,
        protected_amount=contract.action.max_amount,
        network=network,
        chain_mode=chain_mode,
        created_at=created_at,
    )
    return body.model_copy(update={"receipt_hash": compute_receipt_hash(body)})


def compute_receipt_hash(receipt: EvidenceReceipt) -> str:
    body = receipt.model_dump(mode="python")
    body.pop("receipt_hash", None)
    return receipt_hash(body)
