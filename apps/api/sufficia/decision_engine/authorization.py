"""Evidence-bound authorization (Document 03 §37-45, Document 04 §15-21).

An Authorization can only be derived from an AUTHORIZE decision and binds:
    contract_hash + evidence_root + action_hash + executor + nonce + expiry
Its id is ``EvidenceGate.computeAuthorizationId`` of those values, so the chain can check it.
"""

from __future__ import annotations

import math
from decimal import Decimal

from ..canonical.hashes import ZERO_ADDRESS, authorization_id, authorization_nonce, canonical_action_hash
from ..domain.enums import AuthorizationStatus, DecisionState
from ..domain.models import Authorization, CanonicalAction, Decision, EvidenceContract, Money
from ..domain.reasons import ReasonCode


class AuthorityError(Exception):
    """Raised when an operation would create or use authority that does not exist."""

    def __init__(self, code: ReasonCode, message: str):
        super().__init__(message)
        self.code = code


def unix_seconds_floor(dt) -> int:
    return math.floor(dt.timestamp())


def build_authorization(
    *,
    decision: Decision,
    contract: EvidenceContract,
    action: CanonicalAction,
    executor: str,
    onchain_intent_id: str,
) -> Authorization:
    # INV-01 — no authorization without AUTHORIZE.
    if decision.state != DecisionState.AUTHORIZE or decision.authority_window is None:
        raise AuthorityError(
            ReasonCode.EVIDENCE_AUTHORIZATION_REQUIRED,
            f"decision {decision.decision_id} is {decision.state.value}; no execution authority exists",
        )
    if decision.contract_id != contract.contract_id:
        raise AuthorityError(ReasonCode.CONTRACT_MISMATCH, "decision was produced for another Evidence Contract")

    # Invariant A1 — execution-time authority can never exceed the frozen contract.
    intent = contract.action
    if action.action_type != intent.type or action.asset.upper() != intent.asset:
        raise AuthorityError(ReasonCode.AUTHORITY_EXPANSION, "prepared action does not match the contract action/asset")
    if action.amount.currency != intent.max_amount.currency or action.amount.value > intent.max_amount.value:
        raise AuthorityError(ReasonCode.AUTHORITY_EXPANSION, "prepared amount exceeds the contract maximum")
    if action.amount.value <= 0:
        raise AuthorityError(ReasonCode.AUTHORITY_EXPANSION, "prepared amount must be positive")
    # The raw units the gate can actually spend are bounded too — the display amount alone proves nothing.
    if not 0 <= action.amount_decimals <= 36:
        raise AuthorityError(ReasonCode.AUTHORITY_EXPANSION, "invalid amount decimals")
    cap_raw = math.floor(intent.max_amount.value * (Decimal(10) ** action.amount_decimals))
    if action.value != 0:
        raise AuthorityError(ReasonCode.AUTHORITY_EXPANSION, "native value transfers are not authorized in this version")
    if action.asset_in.lower() != ZERO_ADDRESS and action.max_amount_in > cap_raw:
        raise AuthorityError(ReasonCode.AUTHORITY_EXPANSION, "approval (max_amount_in) exceeds the contract maximum")
    if action.spend_raw is None:
        if action.asset_in.lower() == ZERO_ADDRESS:
            raise AuthorityError(ReasonCode.AUTHORITY_EXPANSION, "action does not declare the amount it spends")
    elif not 0 < action.spend_raw <= cap_raw:
        raise AuthorityError(ReasonCode.AUTHORITY_EXPANSION, "declared spend exceeds the contract maximum")

    window = decision.authority_window
    expires_ts = unix_seconds_floor(window.expires_at)
    # INV-09 — authority (and the action deadline) cannot outlive the supporting evidence.
    if action.deadline > expires_ts:
        raise AuthorityError(ReasonCode.AUTHORITY_EXPANSION, "action deadline exceeds the evidence-bound expiry")

    nonce = authorization_nonce(decision.fingerprint)
    if action.nonce.lower() != nonce.lower():
        raise AuthorityError(ReasonCode.ACTION_HASH_MISMATCH, "action nonce is not bound to this decision")

    a_hash = canonical_action_hash(action)
    auth_id = authorization_id(decision.contract_hash, decision.evidence_root, a_hash, executor, nonce)
    return Authorization(
        authorization_id=auth_id,
        decision_id=decision.decision_id,
        intent_id=decision.intent_id,
        onchain_intent_id=onchain_intent_id,
        contract_hash=decision.contract_hash,
        evidence_root=decision.evidence_root,
        action_hash=a_hash,
        executor=executor,
        nonce=nonce,
        issued_at=window.issued_at,
        expires_at=window.expires_at,
        max_amount=Money(value=action.amount.value, currency=action.amount.currency),
        asset=action.asset,
        status=AuthorizationStatus.ISSUED,
    )


def verify_binding(authorization: Authorization, decision: Decision, action: CanonicalAction) -> list[str]:
    """Recomputes every binding of an authorization. Returns the list of mismatches (empty == valid)."""
    problems: list[str] = []
    if decision.state != DecisionState.AUTHORIZE:
        problems.append("decision_not_authorize")
    if authorization.contract_hash != decision.contract_hash:
        problems.append("contract_hash")
    if authorization.evidence_root != decision.evidence_root:
        problems.append("evidence_root")
    a_hash = canonical_action_hash(action)
    if a_hash != authorization.action_hash:
        problems.append("action_hash")
    expected_id = authorization_id(
        authorization.contract_hash, authorization.evidence_root, a_hash, authorization.executor, authorization.nonce
    )
    if expected_id != authorization.authorization_id:
        problems.append("authorization_id")
    if authorization.nonce != authorization_nonce(decision.fingerprint):
        problems.append("nonce")
    return problems
