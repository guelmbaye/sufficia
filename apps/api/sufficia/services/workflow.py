"""SUFFICIA workflow orchestrator.

    Intent -> Evidence Contract (frozen) -> Evidence acquisition -> Decision -> Authorization
          -> Protected execution on the EvidenceGate -> Evidence Execution Receipt -> Replay

"Reasoning proposes. Evidence constrains. Policy decides. Authorization binds. Monad executes."

Guards implemented here (the contract re-checks everything independently):
  * authorize: decision exists, is AUTHORIZE, its snapshot replays to the same fingerprint, the
    prepared action matches its calldata (verify_spend) and the contract limits (build_authorization),
    the authority window is still open at chain time (Document 04 §64);
  * execute: the onchain authorization exists, is unconsumed, unexpired, bound to our executor and
    to the exact action hash we are about to send (Document 04 §66);
  * a non-AUTHORIZE decision can never reach the chain: 403 EVIDENCE_AUTHORIZATION_REQUIRED (Doc 02 §23).
Transactions are persisted before/after submission and reconciled by hash, never blindly resent
(Document 04 §84).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import secrets
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..blockchain.gateway import ChainGateway, TxResult
from ..canonical.canonicalize import canonical_json, loads
from ..canonical.hashes import (
    canonical_action_hash,
    evidence_item_hash,
    evidence_root,
    intent_hash,
    onchain_intent_id,
)
from ..db.models import (
    AuthorizationRow,
    ChainEventRow,
    ChainTransactionRow,
    ContractRow,
    DecisionRow,
    EvidenceItemRow,
    EvidenceSetRow,
    ExecutionRow,
    IntentRow,
    PaymentRow,
    ReceiptRow,
    RequirementRow,
    VerificationResultRow,
)
from ..decision_engine import (
    ENGINE_VERSION,
    AuthorityError,
    InvalidContractError,
    build_authorization,
    evaluate,
    load_snapshot,
    make_snapshot,
    replay_snapshot,
    snapshot_hash,
    unix_seconds_floor,
    verify_binding,
)
from ..domain.enums import (
    AuthorizationStatus,
    DecisionState,
    ExecutionStatus,
    TxPurpose,
    TxStatus,
)
from ..domain.models import (
    AcquisitionFailure,
    Authorization,
    CanonicalAction,
    Decision,
    EvidenceContract,
    EvidenceItem,
    EvidenceReceipt,
    EvidenceSet,
    Execution,
    Intent,
    Money,
)
from ..evidence.acquisition import AcquisitionResult, acquire
from ..evidence.base import AcquisitionContext, EvidenceBudget
from ..evidence.planner import plan as plan_acquisition
from ..execution.adapters import AdapterError, prepare_action, verify_spend
from ..intent import IntentCompilationError, compile_intent
from . import audit
from .errors import ApiError, authorization_required, not_found
from .receipts import build_receipt, compute_receipt_hash, receipt_id_for
from .runtime import Runtime

REFERENCE_BASE_URL = "http://x402-reference.local"
MAX_INTENT_CHARS = 2000


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def _run_async(factory):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(factory())).result()


def badge(item: EvidenceItem) -> str:
    """Source-mode badge (Document 05 §58-59): never blur LIVE / RECORDED / SIMULATED / INJECTED."""
    text = item.source.mode.value
    if item.economic.payment is not None:
        text += " · PAID"
    return text


class Sufficia:
    def __init__(self, runtime: Runtime):
        self.rt = runtime
        self.settings = runtime.settings

    # =========================================================================================
    # infrastructure helpers
    # =========================================================================================

    def _audit(self, event_type: str, subject_id: str, payload: Any = None) -> None:
        with self.rt.audit_lock, self.rt.db.session() as s:
            audit.append(s, event_type, subject_id, self.rt.now(), payload)

    def _record_tx(self, s: Session, tx: TxResult, *, operation_id: str, related_id: Optional[str], chain_id: int) -> ChainTransactionRow:
        row = ChainTransactionRow(
            operation_id=operation_id,
            chain_id=chain_id,
            purpose=tx.purpose.value,
            related_id=related_id,
            tx_hash=tx.tx_hash,
            from_address=tx.from_address,
            to_address=tx.to_address,
            status=tx.status.value,
            block_number=tx.block_number,
            gas_used=tx.gas_used,
            error_name=tx.error_name,
            error=tx.error,
            explorer_url=tx.explorer_url,
            submitted_at=tx.submitted_at,
            confirmed_at=tx.confirmed_at,
            created_at=self.rt.now(),
        )
        s.add(row)
        s.flush()
        for ev in tx.events:
            s.add(
                ChainEventRow(
                    chain_tx_id=row.id,
                    tx_hash=tx.tx_hash,
                    event=ev.get("event", "?"),
                    log_index=ev.get("log_index"),
                    args=canonical_json(ev.get("args", {})),
                )
            )
        return row

    @staticmethod
    def _tx_view(tx: TxResult) -> dict[str, Any]:
        return {
            "purpose": tx.purpose.value,
            "status": tx.status.value,
            "tx_hash": tx.tx_hash,
            "block_number": tx.block_number,
            "gas_used": tx.gas_used,
            "error_name": tx.error_name,
            "error": tx.error,
            "explorer_url": tx.explorer_url,
            "events": tx.events,
        }

    # ---- loaders ------------------------------------------------------------------------------

    def _intent(self, s: Session, intent_id: str) -> tuple[IntentRow, Intent]:
        row = s.get(IntentRow, intent_id)
        if row is None:
            raise not_found("intent", intent_id)
        return row, Intent.model_validate(loads(row.payload))

    def _contract(self, s: Session, contract_id: str) -> tuple[ContractRow, EvidenceContract]:
        row = s.get(ContractRow, contract_id)
        if row is None:
            raise not_found("contract", contract_id)
        return row, EvidenceContract.model_validate(loads(row.payload))

    def _decision(self, s: Session, decision_id: str) -> tuple[DecisionRow, Decision]:
        row = s.get(DecisionRow, decision_id)
        if row is None:
            raise not_found("decision", decision_id)
        return row, Decision.model_validate(loads(row.payload))

    def _latest_set(self, s: Session, contract_id: str) -> Optional[EvidenceSetRow]:
        return s.execute(
            select(EvidenceSetRow)
            .where(EvidenceSetRow.contract_id == contract_id)
            .order_by(EvidenceSetRow.created_at.desc(), EvidenceSetRow.id.desc())
            .limit(1)
        ).scalar_one_or_none()

    def _set_items(self, s: Session, set_row: EvidenceSetRow) -> list[EvidenceItem]:
        ids = loads(set_row.item_ids)
        rows = {r.id: r for r in s.execute(select(EvidenceItemRow).where(EvidenceItemRow.id.in_(ids))).scalars()}
        return [EvidenceItem.model_validate(loads(rows[i].payload)) for i in ids]

    def _set_failures(self, set_row: EvidenceSetRow) -> list[AcquisitionFailure]:
        return [AcquisitionFailure.model_validate(f) for f in loads(set_row.failures)]

    def _evidence_set(self, s: Session, set_row: EvidenceSetRow) -> EvidenceSet:
        return EvidenceSet(
            contract_id=set_row.contract_id, items=self._set_items(s, set_row), acquisition_failures=self._set_failures(set_row)
        )

    def _payments_for(self, s: Session, set_row: EvidenceSetRow, item_ids: set[str]) -> list[dict[str, Any]]:
        """Payments that bought evidence in this set, plus failed/abandoned payments of its acquisition."""
        lineage: list[str] = []
        cur: Optional[EvidenceSetRow] = set_row
        while cur is not None:
            lineage.append(cur.id)
            cur = s.get(EvidenceSetRow, cur.parent_id) if cur.parent_id else None
        rows = s.execute(select(PaymentRow).where(PaymentRow.contract_id == set_row.contract_id)).scalars().all()
        out = []
        for p in rows:
            if (p.evidence_id and p.evidence_id in item_ids) or (p.evidence_id is None and p.evidence_set_id in lineage):
                out.append(self._payment_view(p))
        return out

    @staticmethod
    def _payment_view(p: PaymentRow) -> dict[str, Any]:
        return {
            "acquisition_id": p.acquisition_id,
            "evidence_set_id": p.evidence_set_id,
            "evidence_id": p.evidence_id,
            "requirement_id": p.requirement_id,
            "provider": p.provider,
            "protocol": p.protocol,
            "network": p.network,
            "scheme": p.scheme,
            "quoted": Decimal(p.quoted) if p.quoted is not None else None,
            "settled": Decimal(p.settled) if p.settled is not None else None,
            "currency": p.currency,
            "status": p.status,
            "payment_reference": p.payment_reference,
            "payer": p.payer,
            "pay_to": p.pay_to,
            "error": p.error,
            "created_at": p.created_at,
        }

    # =========================================================================================
    # Intents & contracts
    # =========================================================================================

    def create_intent(
        self,
        text: str,
        *,
        network: str = "monad",
        protected_amount: Optional[Decimal] = None,
        evidence_budget: Optional[Decimal] = None,
    ) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ApiError(422, "INTENT_EMPTY", "the intent text is required")
        if len(text) > MAX_INTENT_CHARS:
            raise ApiError(422, "INTENT_TOO_LONG", f"the intent text is limited to {MAX_INTENT_CHARS} characters")
        for name, value in (("protected_amount", protected_amount), ("evidence_budget", evidence_budget)):
            if value is not None and (not Decimal(value).is_finite() or Decimal(value) <= 0):
                raise ApiError(422, "INVALID_AMOUNT", f"{name} must be a positive number")
        intent = Intent(
            intent_id=new_id("int"),
            text=text,
            created_at=self.rt.now(),
            network=network,
            protected_amount=Money(value=protected_amount) if protected_amount is not None else None,
            evidence_budget=Money(value=evidence_budget) if evidence_budget is not None else None,
        )
        ih = intent_hash(intent)
        with self.rt.db.session() as s:
            s.add(
                IntentRow(
                    id=intent.intent_id,
                    text=intent.text,
                    network=network,
                    intent_hash=ih,
                    created_at=intent.created_at,
                    payload=canonical_json(intent),
                )
            )
        self._audit("INTENT_CREATED", intent.intent_id, {"intent_hash": ih})
        return {"intent": intent, "intent_hash": ih, "contract_id": None}

    def get_intent(self, intent_id: str) -> dict[str, Any]:
        with self.rt.db.session() as s:
            row, intent = self._intent(s, intent_id)
            c = s.execute(select(ContractRow.id).where(ContractRow.intent_id == intent_id)).scalar_one_or_none()
            return {"intent": intent, "intent_hash": row.intent_hash, "contract_id": c}

    def list_intents(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.rt.db.session() as s:
            rows = s.execute(select(IntentRow).order_by(IntentRow.created_at.desc()).limit(limit)).scalars().all()
            contracts = dict(
                s.execute(select(ContractRow.intent_id, ContractRow.id).where(ContractRow.intent_id.in_([r.id for r in rows]))).all()
            )
            return [
                {"intent_id": r.id, "text": r.text, "created_at": r.created_at, "intent_hash": r.intent_hash, "contract_id": contracts.get(r.id)}
                for r in rows
            ]

    def compile(self, intent_id: str, *, mode: Optional[str] = None, override: Optional[dict] = None) -> dict[str, Any]:
        with self.rt.db.session() as s:
            _, intent = self._intent(s, intent_id)
            existing = s.execute(select(ContractRow).where(ContractRow.intent_id == intent_id)).scalar_one_or_none()
            if existing is not None:
                raise ApiError(
                    409,
                    "CONTRACT_FROZEN",
                    "this intent already has a frozen Evidence Contract; create a new intent to change it",
                    {"contract_id": existing.id, "contract_hash": existing.contract_hash},
                )
        try:
            compiled = compile_intent(intent, contract_id=new_id("ec"), settings=self.settings, mode=mode, override=override)
        except IntentCompilationError as exc:
            raise ApiError(
                422, exc.code, str(exc), {"missing": exc.missing, "problems": exc.problems, "unparsed": exc.unparsed}
            ) from exc
        except InvalidContractError as exc:
            raise ApiError(422, "INVALID_CONTRACT", str(exc), {"problems": exc.problems}) from exc
        except ValueError as exc:
            raise ApiError(422, "INVALID_CONTRACT", str(exc)) from exc
        if override is not None and not compiled.preservation.get("accepted", False):
            raise ApiError(
                422,
                "INTENT_RELAXATION_REJECTED",
                "the edited Evidence Contract is weaker than the instruction; authority can never be relaxed",
                {"preservation": compiled.preservation},
            )
        contract = compiled.contract
        now = self.rt.now()
        with self.rt.db.session() as s:
            s.add(
                ContractRow(
                    id=contract.contract_id,
                    intent_id=intent_id,
                    contract_hash=compiled.contract_hash,
                    compiler=compiled.compiler.value,
                    frozen_at=now,
                    payload=canonical_json(contract),
                    preservation=canonical_json(compiled.preservation),
                    summary=canonical_json(compiled.summary),
                )
            )
            s.flush()
            for r in contract.requirements:
                s.add(
                    RequirementRow(
                        contract_id=contract.contract_id,
                        requirement_id=r.id,
                        claim=r.claim,
                        operator=r.operator.value,
                        threshold=None if r.threshold is None else str(r.threshold),
                        max_age_seconds=r.max_age_seconds,
                        required=r.required,
                        origin=r.origin,
                    )
                )
        self._audit(
            "CONTRACT_FROZEN",
            contract.contract_id,
            {"intent_id": intent_id, "contract_hash": compiled.contract_hash, "compiler": compiled.compiler.value},
        )
        return self.get_contract(contract.contract_id)

    def get_contract(self, contract_id: str) -> dict[str, Any]:
        with self.rt.db.session() as s:
            row, contract = self._contract(s, contract_id)
            sets = s.execute(
                select(EvidenceSetRow.id, EvidenceSetRow.origin, EvidenceSetRow.created_at)
                .where(EvidenceSetRow.contract_id == contract_id)
                .order_by(EvidenceSetRow.created_at, EvidenceSetRow.id)
            ).all()
            decisions = s.execute(
                select(DecisionRow.id, DecisionRow.state, DecisionRow.evaluated_at)
                .where(DecisionRow.contract_id == contract_id)
                .order_by(DecisionRow.evaluated_at)
            ).all()
            return {
                "contract": contract,
                "contract_hash": row.contract_hash,
                "compiler": row.compiler,
                "frozen_at": row.frozen_at,
                "intent_id": row.intent_id,
                "preservation": loads(row.preservation),
                "summary": loads(row.summary),
                "evidence_sets": [{"evidence_set_id": i, "origin": o, "created_at": t} for i, o, t in sets],
                "decisions": [{"decision_id": i, "state": st, "evaluated_at": t} for i, st, t in decisions],
            }

    # =========================================================================================
    # Evidence
    # =========================================================================================

    def _already_spent(self, s: Session, contract_id: str) -> Decimal:
        total = Decimal(0)
        for (acq,) in s.execute(
            select(EvidenceSetRow.acquisition).where(
                EvidenceSetRow.contract_id == contract_id, EvidenceSetRow.origin == "ACQUISITION"
            )
        ):
            total += Decimal(str(loads(acq).get("spent", "0")))
        return total

    def _run_acquisition(self, contract: EvidenceContract, spent: Decimal, sources_per_requirement: int) -> AcquisitionResult:
        settings = self.settings
        registry = self.rt.registry
        steps = plan_acquisition(contract, settings, registry)
        run_id = new_id("acq")
        reference_app = self.rt.reference_app

        async def run() -> AcquisitionResult:
            async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as http:
                ref = (
                    httpx.AsyncClient(transport=httpx.ASGITransport(app=reference_app), base_url=REFERENCE_BASE_URL)
                    if reference_app is not None
                    else None
                )
                try:
                    ctx = AcquisitionContext(
                        contract=contract,
                        run_id=run_id,
                        settings=settings,
                        budget=EvidenceBudget(
                            limit=contract.economic_limits.max_evidence_cost_usd,
                            max_per_request=settings.x402_max_per_request_usdc,
                            already_spent=spent,
                        ),
                        http=http,
                        now=self.rt.now,
                        reference_http=ref,
                    )
                    return await acquire(contract, steps, ctx, registry, sources_per_requirement=sources_per_requirement)
                finally:
                    if ref is not None:
                        await ref.aclose()

        return _run_async(run)

    def _store_set(
        self,
        s: Session,
        *,
        contract_id: str,
        items: list[EvidenceItem],
        failures: list[AcquisitionFailure],
        origin: str,
        label: str,
        parent_id: Optional[str],
        acquisition: dict[str, Any],
    ) -> EvidenceSetRow:
        now = self.rt.now()
        for it in items:
            if s.get(EvidenceItemRow, it.evidence_id) is None:
                s.add(
                    EvidenceItemRow(
                        id=it.evidence_id,
                        contract_id=contract_id,
                        requirement_id=it.requirement_id,
                        claim=it.claim.name,
                        provider=it.source.provider,
                        mode=it.source.mode.value,
                        source_type=it.source.source_type.value,
                        item_hash=evidence_item_hash(it),
                        cost=str(it.economic.cost),
                        created_at=now,
                        payload=canonical_json(it),
                    )
                )
        row = EvidenceSetRow(
            id=new_id("es"),
            contract_id=contract_id,
            parent_id=parent_id,
            origin=origin,
            label=label[:128],
            created_at=now,
            evidence_root=evidence_root(evidence_item_hash(i) for i in items),
            item_ids=canonical_json([i.evidence_id for i in items]),
            failures=canonical_json(failures),
            acquisition=canonical_json(acquisition),
        )
        s.add(row)
        s.flush()
        return row

    def acquire(self, contract_id: str, *, sources_per_requirement: int = 1) -> dict[str, Any]:
        if not 1 <= sources_per_requirement <= 3:
            raise ApiError(422, "INVALID_PARAMETER", "sources_per_requirement must be within 1..3")
        with self.rt.db.session() as s:
            _, contract = self._contract(s, contract_id)
            spent = self._already_spent(s, contract_id)
        result = self._run_acquisition(contract, spent, sources_per_requirement)
        with self.rt.db.session() as s:
            set_row = self._store_set(
                s,
                contract_id=contract_id,
                items=result.items,
                failures=result.failures,
                origin="ACQUISITION",
                label=f"acquisition ({len(result.items)} items, {len(result.failures)} failures)",
                parent_id=None,
                acquisition={
                    "started_at": result.started_at,
                    "finished_at": result.finished_at,
                    "spent": result.spent,
                    "timeline": result.timeline,
                    "sources_per_requirement": sources_per_requirement,
                    "profile": self.settings.evidence_profile,
                },
            )
            for p in result.payments:
                s.add(
                    PaymentRow(
                        evidence_set_id=set_row.id,
                        contract_id=contract_id,
                        acquisition_id=p.acquisition_id,
                        evidence_id=p.evidence_id,
                        requirement_id=p.requirement_id,
                        provider=p.provider,
                        protocol=p.protocol,
                        network=p.network,
                        scheme=p.scheme,
                        quoted=None if p.quoted is None else str(p.quoted),
                        settled=None if p.settled is None else str(p.settled),
                        currency=p.currency,
                        status=getattr(p.status, "value", str(p.status)),
                        payment_reference=p.payment_reference,
                        payer=p.payer,
                        pay_to=p.pay_to,
                        error=p.error,
                        created_at=p.created_at or self.rt.now(),
                    )
                )
            set_id = set_row.id
        self._audit(
            "EVIDENCE_ACQUIRED",
            contract_id,
            {
                "evidence_set_id": set_id,
                "evidence_ids": [i.evidence_id for i in result.items],
                "spent": result.spent,
                "payments": len(result.payments),
            },
        )
        for f in result.failures:
            self._audit(
                "EVIDENCE_REJECTED", contract_id, {"requirement_id": f.requirement_id, "provider": f.provider, "code": f.code.value}
            )
        return self.get_evidence_set(set_id)

    def get_evidence_set(self, evidence_set_id: str) -> dict[str, Any]:
        with self.rt.db.session() as s:
            row = s.get(EvidenceSetRow, evidence_set_id)
            if row is None:
                raise not_found("evidence_set", evidence_set_id)
            return self._set_view(s, row)

    def current_evidence(self, contract_id: str) -> dict[str, Any]:
        with self.rt.db.session() as s:
            self._contract(s, contract_id)
            row = self._latest_set(s, contract_id)
            if row is None:
                return {"contract_id": contract_id, "evidence_set_id": None, "items": [], "failures": [], "payments": []}
            return self._set_view(s, row)

    def _set_view(self, s: Session, row: EvidenceSetRow) -> dict[str, Any]:
        items = self._set_items(s, row)
        now = self.rt.now()
        views = []
        for it in items:
            age = None
            if isinstance(it.temporal.observed_at, datetime):
                age = Decimal(str((now - it.temporal.observed_at).total_seconds())).quantize(Decimal("0.001"))
            views.append({"item": it, "badge": badge(it), "item_hash": evidence_item_hash(it), "age_seconds_now": age})
        acq = loads(row.acquisition)
        return {
            "evidence_set_id": row.id,
            "contract_id": row.contract_id,
            "origin": row.origin,
            "label": row.label,
            "parent_id": row.parent_id,
            "created_at": row.created_at,
            "evidence_root": row.evidence_root,
            "items": views,
            "failures": loads(row.failures),
            "payments": self._payments_for(s, row, {i.evidence_id for i in items}),
            "acquisition": acq,
        }

    # =========================================================================================
    # Verification & decisions
    # =========================================================================================

    def _resolve_set(self, s: Session, contract_id: str, evidence_set_id: Optional[str]) -> EvidenceSetRow:
        if evidence_set_id:
            row = s.get(EvidenceSetRow, evidence_set_id)
            if row is None or row.contract_id != contract_id:
                raise not_found("evidence_set", evidence_set_id)
            return row
        row = self._latest_set(s, contract_id)
        if row is None:
            raise ApiError(409, "EVIDENCE_NOT_ACQUIRED", "acquire evidence for this contract before deciding")
        return row

    def verify(self, contract_id: str, *, evidence_set_id: Optional[str] = None) -> dict[str, Any]:
        """Dry run of the engine on the current evidence (nothing persisted, no authority)."""
        with self.rt.db.session() as s:
            _, contract = self._contract(s, contract_id)
            set_row = self._resolve_set(s, contract_id, evidence_set_id)
            evs = self._evidence_set(s, set_row)
        d = evaluate(contract, evs, self.rt.now(), permitted_sources=self.rt.permitted)
        return {
            "preview": True,
            "contract_id": contract_id,
            "evidence_set_id": set_row.id,
            "state": d.state,
            "headline": d.headline,
            "summary": d.summary,
            "reason_codes": d.reason_codes,
            "requirement_results": d.requirement_results,
            "evidence_assessments": d.evidence_assessments,
            "coverage": d.coverage,
            "freshness_pass": d.freshness_pass,
            "conflicts": d.conflicts,
            "evidence_cost_usd": d.evidence_cost_usd,
            "evaluated_at": d.evaluated_at,
        }

    def decide(self, contract_id: str, *, evidence_set_id: Optional[str] = None) -> dict[str, Any]:
        with self.rt.db.session() as s:
            crow, contract = self._contract(s, contract_id)
            set_row = self._resolve_set(s, contract_id, evidence_set_id)
            evs = self._evidence_set(s, set_row)
            set_id = set_row.id
        t = self.rt.now()
        decision = evaluate(contract, evs, t, permitted_sources=self.rt.permitted)
        if decision.contract_hash != crow.contract_hash:  # frozen contract integrity
            raise ApiError(500, "CONTRACT_INTEGRITY", "stored contract no longer matches its frozen hash")
        snapshot = make_snapshot(contract, evs, t, decision.engine_version, self.rt.permitted)
        with self.rt.db.session() as s:
            if s.get(DecisionRow, decision.decision_id) is None:
                s.add(
                    DecisionRow(
                        id=decision.decision_id,
                        contract_id=contract_id,
                        evidence_set_id=set_id,
                        state=decision.state.value,
                        reason_codes=canonical_json(decision.reason_codes),
                        fingerprint=decision.fingerprint,
                        contract_hash=decision.contract_hash,
                        evidence_root=decision.evidence_root,
                        engine_version=decision.engine_version,
                        evaluated_at=decision.evaluated_at,
                        snapshot=snapshot,
                        payload=canonical_json(decision),
                    )
                )
                s.flush()
                for r in decision.requirement_results:
                    s.add(
                        VerificationResultRow(
                            decision_id=decision.decision_id,
                            requirement_id=r.requirement_id,
                            status=r.status.value,
                            reason_code=r.reason_code.value if r.reason_code else None,
                            payload=canonical_json(r),
                        )
                    )
        self._audit(
            "DECISION_CREATED",
            decision.decision_id,
            {
                "contract_id": contract_id,
                "evidence_set_id": set_id,
                "state": decision.state.value,
                "reason_codes": [c.value for c in decision.reason_codes],
                "fingerprint": decision.fingerprint,
            },
        )
        if decision.state != DecisionState.AUTHORIZE:
            self._blocked_receipt(decision.decision_id)
        return self.get_decision(decision.decision_id)

    def get_decision(self, decision_id: str) -> dict[str, Any]:
        with self.rt.db.session() as s:
            row, decision = self._decision(s, decision_id)
            auth = s.execute(select(AuthorizationRow).where(AuthorizationRow.decision_id == decision_id)).scalar_one_or_none()
            receipts = s.execute(
                select(ReceiptRow.id, ReceiptRow.kind, ReceiptRow.created_at)
                .where(ReceiptRow.decision_id == decision_id)
                .order_by(ReceiptRow.created_at)
            ).all()
            executions = s.execute(
                select(ExecutionRow).where(ExecutionRow.decision_id == decision_id).order_by(ExecutionRow.created_at)
            ).scalars().all()
            window = None
            if decision.authority_window is not None:
                remaining = (decision.authority_window.expires_at - self.rt.now()).total_seconds()
                window = {
                    "expires_at": decision.authority_window.expires_at,
                    "bounded_by": decision.authority_window.bounded_by,
                    "remaining_seconds": max(0, round(remaining, 3)),
                }
            return {
                "decision": decision,
                "evidence_set_id": row.evidence_set_id,
                "snapshot_hash": snapshot_hash(row.snapshot),
                "authority": window,
                "authorization": self._auth_view(auth) if auth else None,
                "executions": [self._exec_view(e) for e in executions],
                "receipts": [{"receipt_id": i, "kind": k, "created_at": t} for i, k, t in receipts],
                "can_execute": decision.state == DecisionState.AUTHORIZE,
            }

    # =========================================================================================
    # Authorization (Document 04 §63-65)
    # =========================================================================================

    @staticmethod
    def _auth_view(row: AuthorizationRow) -> dict[str, Any]:
        return {
            "authorization_id": row.id,
            "decision_id": row.decision_id,
            "status": row.status,
            "onchain_intent_id": row.onchain_intent_id,
            "contract_hash": row.contract_hash,
            "evidence_root": row.evidence_root,
            "action_hash": row.action_hash,
            "executor": row.executor,
            "nonce": row.nonce,
            "issued_at": row.issued_at,
            "expires_at": row.expires_at,
            "chain_id": row.chain_id,
            "gate_address": row.gate_address,
            "register_tx_hash": row.register_tx_hash,
            "authorize_tx_hash": row.authorize_tx_hash,
            "error": row.error,
            "action": loads(row.action),
        }

    def authorize(self, decision_id: str) -> dict[str, Any]:
        with self.rt.db.session() as s:
            drow, decision = self._decision(s, decision_id)
            existing = s.execute(select(AuthorizationRow).where(AuthorizationRow.decision_id == decision_id)).scalar_one_or_none()
            snapshot = drow.snapshot
            crow = s.get(ContractRow, drow.contract_id)
            irow = s.get(IntentRow, crow.intent_id)
        if decision.state != DecisionState.AUTHORIZE:
            self._audit("AUTHORIZATION_REFUSED", decision_id, {"state": decision.state.value, "result": "NO_AUTHORIZATION"})
            raise authorization_required(decision.state.value, decision_id)
        if existing is not None:
            return self._reconcile_authorization(existing.id)

        # Double guard 1: the persisted inputs still replay to this exact decision.
        replayed = replay_snapshot(snapshot)
        if replayed.fingerprint != decision.fingerprint or replayed.state != decision.state:
            raise ApiError(409, "DECISION_INTEGRITY", "the decision no longer replays from its snapshot")
        if decision.contract_hash != crow.contract_hash:
            raise ApiError(409, "CONTRACT_HASH_MISMATCH", "decision was produced for another contract version")
        snap = load_snapshot(snapshot)
        contract: EvidenceContract = snap["contract"]
        items: list[EvidenceItem] = snap["evidence_set"].items

        gw = self.rt.gateway()
        info = gw.info
        try:
            action = prepare_action(
                self.settings.execution_adapter, contract=contract, decision=decision, evidence_items=items, chain=info, settings=self.settings
            )
        except AdapterError as exc:
            raise ApiError(409, exc.code, str(exc)) from exc
        problems = verify_spend(action, info)
        if problems:
            raise ApiError(409, "ACTION_VERIFICATION_FAILED", "prepared action does not match its calldata", {"mismatches": problems})
        try:
            auth = build_authorization(
                decision=decision,
                contract=contract,
                action=action,
                executor=info.executor,
                onchain_intent_id=onchain_intent_id(irow.intent_hash),
            )
        except AuthorityError as exc:
            raise ApiError(403, exc.code.value, str(exc)) from exc

        chain_ts = gw.block_timestamp()
        if unix_seconds_floor(auth.expires_at) <= chain_ts:
            self._audit("AUTHORIZATION_EXPIRED", decision_id, {"expires_at": auth.expires_at, "chain_time": chain_ts})
            self._expired_receipt(decision_id, None)
            raise ApiError(
                409,
                "AUTHORIZATION_EXPIRED",
                "the authority window of this decision has closed; refresh the evidence and decide again",
                {"expires_at": auth.expires_at, "chain_time": chain_ts},
            )

        now = self.rt.now()
        with self.rt.db.session() as s:
            s.add(
                AuthorizationRow(
                    id=auth.authorization_id,
                    decision_id=decision_id,
                    status=AuthorizationStatus.ISSUED.value,
                    onchain_intent_id=auth.onchain_intent_id,
                    contract_hash=auth.contract_hash,
                    evidence_root=auth.evidence_root,
                    action_hash=auth.action_hash,
                    executor=auth.executor,
                    nonce=auth.nonce,
                    issued_at=auth.issued_at,
                    expires_at=auth.expires_at,
                    chain_id=info.chain_id,
                    gate_address=info.gate_address,
                    created_at=now,
                    updated_at=now,
                    payload=canonical_json(auth),
                    action=canonical_json(action),
                )
            )
        self._audit(
            "AUTHORIZATION_ISSUED",
            auth.authorization_id,
            {"decision_id": decision_id, "action_hash": auth.action_hash, "evidence_root": auth.evidence_root, "expires_at": auth.expires_at},
        )
        return self._publish_authorization(auth.authorization_id)

    def _publish_authorization(self, authorization_id: str) -> dict[str, Any]:
        gw = self.rt.gateway()
        with self.rt.db.session() as s:
            row = s.get(AuthorizationRow, authorization_id)
            auth = Authorization.model_validate(loads(row.payload))
        op = new_id("op")
        reg = gw.register_intent(auth.onchain_intent_id, auth.contract_hash)
        with self.rt.db.session() as s:
            if reg.tx_hash is not None or not reg.ok:
                self._record_tx(s, reg, operation_id=op, related_id=authorization_id, chain_id=gw.info.chain_id)
            row = s.get(AuthorizationRow, authorization_id)
            row.register_tx_hash = reg.tx_hash or row.register_tx_hash
            row.updated_at = self.rt.now()
            if not reg.ok:
                row.status = AuthorizationStatus.PUBLISH_FAILED.value if reg.status != TxStatus.SUBMITTED else row.status
                row.error = f"registerIntent: {reg.error_name or reg.error or reg.status.value}"
        if not reg.ok:
            code = "CHAIN_TX_PENDING" if reg.status == TxStatus.SUBMITTED else "CHAIN_TX_FAILED"
            raise ApiError(504 if code == "CHAIN_TX_PENDING" else 502, code, "intent registration did not confirm", self._tx_view(reg))

        tx = gw.authorize(auth)
        with self.rt.db.session() as s:
            self._record_tx(s, tx, operation_id=op, related_id=authorization_id, chain_id=gw.info.chain_id)
            row = s.get(AuthorizationRow, authorization_id)
            row.authorize_tx_hash = tx.tx_hash or row.authorize_tx_hash
            row.updated_at = self.rt.now()
            if tx.ok:
                row.status = AuthorizationStatus.ACTIVE.value
                row.error = None
            elif tx.status != TxStatus.SUBMITTED:
                row.status = AuthorizationStatus.PUBLISH_FAILED.value
                row.error = f"authorize: {tx.error_name or tx.error or tx.status.value}"
        if tx.ok:
            self._audit("AUTHORIZATION_PUBLISHED", authorization_id, {"tx_hash": tx.tx_hash, "block_number": tx.block_number})
        elif tx.status == TxStatus.SUBMITTED:
            raise ApiError(504, "CHAIN_TX_PENDING", "authorization submitted, awaiting confirmation", self._tx_view(tx))
        else:
            raise ApiError(502, "CHAIN_TX_FAILED", f"authorize reverted: {tx.error_name or tx.error}", self._tx_view(tx))
        return self.get_authorization(authorization_id)

    def _reconcile_authorization(self, authorization_id: str) -> dict[str, Any]:
        """Idempotent re-entry: read chain state first; only republish what is provably not onchain."""
        with self.rt.db.session() as s:
            row = s.get(AuthorizationRow, authorization_id)
            status = row.status
        if status in (AuthorizationStatus.ACTIVE.value, AuthorizationStatus.CONSUMED.value, AuthorizationStatus.EXPIRED.value):
            return self.get_authorization(authorization_id)
        gw = self.rt.gateway()
        onchain = gw.get_authorization(authorization_id)
        if onchain.exists:
            with self.rt.db.session() as s:
                row = s.get(AuthorizationRow, authorization_id)
                row.status = (AuthorizationStatus.CONSUMED if onchain.consumed else AuthorizationStatus.ACTIVE).value
                row.error = None
                row.updated_at = self.rt.now()
            return self.get_authorization(authorization_id)
        with self.rt.db.session() as s:
            row = s.get(AuthorizationRow, authorization_id)
            expires = row.expires_at
        if unix_seconds_floor(expires) <= gw.block_timestamp():
            with self.rt.db.session() as s:
                row = s.get(AuthorizationRow, authorization_id)
                row.status = AuthorizationStatus.EXPIRED.value
                row.updated_at = self.rt.now()
            self._audit("AUTHORIZATION_EXPIRED", authorization_id, {"reason": "never published before expiry"})
            return self.get_authorization(authorization_id)
        return self._publish_authorization(authorization_id)

    def get_authorization(self, authorization_id: str) -> dict[str, Any]:
        with self.rt.db.session() as s:
            row = s.get(AuthorizationRow, authorization_id)
            if row is None:
                raise not_found("authorization", authorization_id)
            view = self._auth_view(row)
            txs = s.execute(
                select(ChainTransactionRow).where(ChainTransactionRow.related_id == authorization_id).order_by(ChainTransactionRow.id)
            ).scalars().all()
            view["transactions"] = self._tx_views(s, list(txs))
        gw = self.rt.gateway(required=False)
        if gw is not None and gw.info.gate_address.lower() == view["gate_address"].lower():
            try:
                oc = gw.get_authorization(authorization_id)
                view["onchain"] = {
                    "exists": oc.exists,
                    "consumed": oc.consumed,
                    "active": oc.active,
                    "expires_at": oc.expires_at,
                    "action_hash": oc.action_hash,
                    "evidence_root": oc.evidence_root,
                }
            except Exception as exc:  # read-only enrichment; never hides the stored state
                view["onchain"] = {"error": f"{type(exc).__name__}: {exc}"}
        return view

    def _tx_views(self, s: Session, rows: list[ChainTransactionRow]) -> list[dict[str, Any]]:
        """Transactions with their decoded EvidenceGate events (from chain_events)."""
        views = [self._chain_tx_view(t) for t in rows]
        ids = [t.id for t in rows]
        if ids:
            events: dict[int, list[dict[str, Any]]] = {}
            for e in s.execute(select(ChainEventRow).where(ChainEventRow.chain_tx_id.in_(ids)).order_by(ChainEventRow.id)).scalars():
                events.setdefault(e.chain_tx_id, []).append({"event": e.event, "log_index": e.log_index, "args": loads(e.args)})
            for v in views:
                v["events"] = events.get(v["id"], [])
        return views

    @staticmethod
    def _chain_tx_view(t: ChainTransactionRow) -> dict[str, Any]:
        return {
            "id": t.id,
            "operation_id": t.operation_id,
            "purpose": t.purpose,
            "status": t.status,
            "tx_hash": t.tx_hash,
            "block_number": t.block_number,
            "gas_used": t.gas_used,
            "from_address": t.from_address,
            "to_address": t.to_address,
            "error_name": t.error_name,
            "error": t.error,
            "explorer_url": t.explorer_url,
            "submitted_at": t.submitted_at,
            "confirmed_at": t.confirmed_at,
        }

    # =========================================================================================
    # Execution (Document 04 §66-67)
    # =========================================================================================

    @staticmethod
    def _exec_view(row: ExecutionRow) -> dict[str, Any]:
        return {
            "execution_id": row.id,
            "authorization_id": row.authorization_id,
            "decision_id": row.decision_id,
            "status": row.status,
            "tx_hash": row.tx_hash,
            "block_number": row.block_number,
            "error_code": row.error_code,
            "error": row.error,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def execute_decision(self, decision_id: str) -> dict[str, Any]:
        with self.rt.db.session() as s:
            _, decision = self._decision(s, decision_id)
        if decision.state != DecisionState.AUTHORIZE:
            self._audit("EXECUTION_BLOCKED", decision_id, {"state": decision.state.value, "code": "EVIDENCE_AUTHORIZATION_REQUIRED"})
            raise authorization_required(decision.state.value, decision_id)
        auth = self.authorize(decision_id)
        return self.execute_authorization(auth["authorization_id"])

    def execute_authorization(self, authorization_id: str) -> dict[str, Any]:
        with self.rt.db.session() as s:
            row = s.get(AuthorizationRow, authorization_id)
            if row is None:
                raise not_found("authorization", authorization_id)
            done = s.execute(
                select(ExecutionRow).where(
                    ExecutionRow.authorization_id == authorization_id, ExecutionRow.status == ExecutionStatus.EXECUTED.value
                )
            ).scalar_one_or_none()
            pending = s.execute(
                select(ExecutionRow).where(
                    ExecutionRow.authorization_id == authorization_id, ExecutionRow.status == ExecutionStatus.SUBMITTED.value
                )
            ).scalar_one_or_none()
            decision_id = row.decision_id
        if done is not None:  # idempotent: the same authorization never executes twice
            return self._execution_result(done.id, idempotent=True)
        if row.status in (AuthorizationStatus.ISSUED.value, AuthorizationStatus.PUBLISH_FAILED.value):
            self._reconcile_authorization(authorization_id)
            with self.rt.db.session() as s:
                row = s.get(AuthorizationRow, authorization_id)
        if row.status == AuthorizationStatus.EXPIRED.value:
            self._expired_receipt(decision_id, None)
            raise ApiError(409, "AUTHORIZATION_EXPIRED", "the authorization expired before execution")
        if row.status != AuthorizationStatus.ACTIVE.value:
            raise ApiError(409, "AUTHORIZATION_NOT_ACTIVE", f"authorization is {row.status}")

        gw = self.rt.gateway()
        info = gw.info
        action = CanonicalAction.model_validate(loads(row.action))
        if pending is not None and pending.tx_hash:
            found = gw.find_transaction(pending.tx_hash)
            if found is not None and found.status in (TxStatus.CONFIRMED, TxStatus.REVERTED):
                return self._finalize_execution(pending.id, found, action, reconciled=True)
            if found is not None:
                raise ApiError(504, "CHAIN_TX_PENDING", "a previous execution is still pending", {"tx_hash": pending.tx_hash})

        # Read onchain state before submitting anything (the contract re-checks all of it).
        oc = gw.get_authorization(authorization_id)
        local_hash = canonical_action_hash(action)
        if not oc.exists:
            raise ApiError(409, "AUTHORIZATION_NOT_FOUND", "the authorization is not onchain")
        if oc.consumed:
            with self.rt.db.session() as s:
                s.get(AuthorizationRow, authorization_id).status = AuthorizationStatus.CONSUMED.value
            raise ApiError(409, "AUTHORIZATION_ALREADY_CONSUMED", "this authorization has already been used")
        chain_ts = gw.block_timestamp()
        if oc.expires_at is not None and chain_ts > oc.expires_at:
            with self.rt.db.session() as s:
                r = s.get(AuthorizationRow, authorization_id)
                r.status = AuthorizationStatus.EXPIRED.value
                r.updated_at = self.rt.now()
            self._audit("AUTHORIZATION_EXPIRED", authorization_id, {"expires_at": oc.expires_at, "chain_time": chain_ts})
            receipt = self._expired_receipt(decision_id, authorization_id)
            raise ApiError(
                409,
                "AUTHORIZATION_EXPIRED",
                "the authorization expired onchain; no transaction was submitted",
                {"expires_at": oc.expires_at, "chain_time": chain_ts, "receipt_id": receipt},
            )
        if (oc.executor or "").lower() != info.executor.lower():
            raise ApiError(409, "UNAUTHORIZED_EXECUTOR", "the onchain executor is not this backend's executor")
        if (oc.action_hash or "").lower() != local_hash.lower() or local_hash.lower() != row.action_hash.lower():
            raise ApiError(409, "ACTION_HASH_MISMATCH", "the action to execute is not the authorized action")

        exec_id = new_id("exe")
        now = self.rt.now()
        with self.rt.db.session() as s:
            s.add(
                ExecutionRow(
                    id=exec_id,
                    authorization_id=authorization_id,
                    decision_id=decision_id,
                    status=ExecutionStatus.SUBMITTED.value,
                    created_at=now,
                    updated_at=now,
                    payload="{}",
                )
            )
        self._audit("EXECUTION_SUBMITTED", exec_id, {"authorization_id": authorization_id, "action_hash": local_hash})
        tx = gw.execute(authorization_id, action)
        return self._finalize_execution(exec_id, tx, action)

    def _finalize_execution(self, exec_id: str, tx: TxResult, action: CanonicalAction, *, reconciled: bool = False) -> dict[str, Any]:
        gw = self.rt.gateway()
        info = gw.info
        status = {
            TxStatus.CONFIRMED: ExecutionStatus.EXECUTED,
            TxStatus.SUBMITTED: ExecutionStatus.SUBMITTED,
            TxStatus.PENDING: ExecutionStatus.SUBMITTED,
        }.get(tx.status, ExecutionStatus.FAILED)
        with self.rt.db.session() as s:
            row = s.get(ExecutionRow, exec_id)
            if not reconciled or tx.tx_hash:
                self._record_tx(s, tx, operation_id=exec_id, related_id=row.authorization_id, chain_id=info.chain_id)
            execution = Execution(
                execution_id=exec_id,
                authorization_id=row.authorization_id,
                status=status,
                network=info.network_name,
                chain_id=info.chain_id,
                tx_hash=tx.tx_hash,
                block_number=tx.block_number,
                error_code=tx.error_name,
                error=tx.error,
                submitted_at=tx.submitted_at,
                confirmed_at=tx.confirmed_at,
                amount=action.amount,
            )
            row.status = status.value
            row.tx_hash = tx.tx_hash
            row.block_number = tx.block_number
            row.error_code = tx.error_name
            row.error = tx.error
            row.updated_at = self.rt.now()
            row.payload = canonical_json(execution)
            authorization_id = row.authorization_id
            decision_id = row.decision_id
            if status == ExecutionStatus.EXECUTED:
                a = s.get(AuthorizationRow, authorization_id)
                a.status = AuthorizationStatus.CONSUMED.value
                a.updated_at = self.rt.now()
        if status == ExecutionStatus.SUBMITTED:
            raise ApiError(504, "CHAIN_TX_PENDING", "execution submitted, awaiting confirmation", self._tx_view(tx))
        if status == ExecutionStatus.EXECUTED:
            self._audit("AUTHORIZATION_CONSUMED", authorization_id, {"execution_id": exec_id})
            self._audit("EXECUTION_CONFIRMED", exec_id, {"tx_hash": tx.tx_hash, "block_number": tx.block_number})
            receipt_id = self._execution_receipt(decision_id, exec_id, "EXECUTED")
            self._commit_receipt(receipt_id, authorization_id)
            return self._execution_result(exec_id)
        self._audit("EXECUTION_FAILED", exec_id, {"error_name": tx.error_name, "error": tx.error})
        receipt_id = self._execution_receipt(decision_id, exec_id, "FAILED")
        raise ApiError(
            409,
            "EXECUTION_REVERTED",
            f"the EvidenceGate rejected the execution: {tx.error_name or tx.error}",
            {"execution_id": exec_id, "receipt_id": receipt_id, "transaction": self._tx_view(tx)},
        )

    def _execution_result(self, exec_id: str, *, idempotent: bool = False) -> dict[str, Any]:
        with self.rt.db.session() as s:
            row = s.get(ExecutionRow, exec_id)
            view = self._exec_view(row)
            txs = s.execute(select(ChainTransactionRow).where(ChainTransactionRow.operation_id == exec_id)).scalars().all()
            view["transactions"] = self._tx_views(s, list(txs))
            rec = s.execute(select(ReceiptRow).where(ReceiptRow.execution_id == exec_id)).scalar_one_or_none()
            view["receipt_id"] = rec.id if rec else None
            view["idempotent"] = idempotent
            view["authorization"] = self._auth_view(s.get(AuthorizationRow, row.authorization_id))
        return view

    # =========================================================================================
    # Receipts
    # =========================================================================================

    def _receipt_inputs(self, s: Session, decision_id: str) -> dict[str, Any]:
        drow, decision = self._decision(s, decision_id)
        snap = load_snapshot(drow.snapshot)
        crow = s.get(ContractRow, drow.contract_id)
        irow, intent = self._intent(s, crow.intent_id)
        set_row = s.get(EvidenceSetRow, drow.evidence_set_id)
        items = snap["evidence_set"].items
        return {
            "decision": decision,
            "contract": snap["contract"],
            "items": items,
            "intent": intent,
            "intent_hash": irow.intent_hash,
            "payments": self._payments_for(s, set_row, {i.evidence_id for i in items}),
        }

    def _chain_labels(self) -> tuple[str, str]:
        gw = self.rt.gateway(required=False)
        if gw is not None:
            return gw.info.network_name, gw.info.mode.value
        return "none", self.settings.chain_mode.value

    def _store_receipt(self, s: Session, receipt: EvidenceReceipt, execution_id: Optional[str]) -> None:
        s.add(
            ReceiptRow(
                id=receipt.receipt_id,
                decision_id=receipt.decision_id,
                execution_id=execution_id,
                kind=receipt.kind,
                receipt_hash=receipt.receipt_hash,
                body=canonical_json(receipt),
                commitment_status="NONE",
                created_at=receipt.created_at,
            )
        )

    def _blocked_receipt(self, decision_id: str) -> str:
        rid = receipt_id_for(decision_id, "BLOCKED", None)
        with self.rt.db.session() as s:
            if s.get(ReceiptRow, rid) is not None:
                return rid
            inp = self._receipt_inputs(s, decision_id)
            network, mode = self._chain_labels()
            receipt = build_receipt(kind="BLOCKED", network=network, chain_mode=mode, created_at=self.rt.now(), **inp)
            self._store_receipt(s, receipt, None)
        self._audit("RECEIPT_CREATED", rid, {"kind": "BLOCKED", "decision_id": decision_id, "receipt_hash": receipt.receipt_hash})
        return rid

    def _expired_receipt(self, decision_id: str, authorization_id: Optional[str]) -> str:
        rid = receipt_id_for(decision_id, "EXPIRED", None)
        with self.rt.db.session() as s:
            if s.get(ReceiptRow, rid) is not None:
                return rid
            inp = self._receipt_inputs(s, decision_id)
            auth_row = s.get(AuthorizationRow, authorization_id) if authorization_id else None
            network, mode = self._chain_labels()
            receipt = build_receipt(
                kind="EXPIRED",
                network=network,
                chain_mode=mode,
                created_at=self.rt.now(),
                authorization=Authorization.model_validate(loads(auth_row.payload)) if auth_row else None,
                action=CanonicalAction.model_validate(loads(auth_row.action)) if auth_row else None,
                authorization_txs={"register_tx_hash": auth_row.register_tx_hash, "authorize_tx_hash": auth_row.authorize_tx_hash}
                if auth_row
                else None,
                gate_address=auth_row.gate_address if auth_row else None,
                **inp,
            )
            self._store_receipt(s, receipt, None)
        self._audit("RECEIPT_CREATED", rid, {"kind": "EXPIRED", "decision_id": decision_id, "receipt_hash": receipt.receipt_hash})
        return rid

    def _execution_receipt(self, decision_id: str, exec_id: str, kind: str) -> str:
        with self.rt.db.session() as s:
            inp = self._receipt_inputs(s, decision_id)
            erow = s.get(ExecutionRow, exec_id)
            arow = s.get(AuthorizationRow, erow.authorization_id)
            network, mode = self._chain_labels()
            receipt = build_receipt(
                kind=kind,
                network=network,
                chain_mode=mode,
                created_at=self.rt.now(),
                authorization=Authorization.model_validate(loads(arow.payload)),
                action=CanonicalAction.model_validate(loads(arow.action)),
                authorization_txs={"register_tx_hash": arow.register_tx_hash, "authorize_tx_hash": arow.authorize_tx_hash},
                execution=Execution.model_validate(loads(erow.payload)),
                gate_address=arow.gate_address,
                **inp,
            )
            if s.get(ReceiptRow, receipt.receipt_id) is None:
                self._store_receipt(s, receipt, exec_id)
        self._audit("RECEIPT_CREATED", receipt.receipt_id, {"kind": kind, "decision_id": decision_id, "receipt_hash": receipt.receipt_hash})
        return receipt.receipt_id

    def _commit_receipt(self, receipt_id: str, authorization_id: str) -> None:
        """EvidenceGate.recordReceipt(authorizationId, receiptHash). A failure never undoes the execution."""
        if not self.settings.commit_receipts_onchain:
            return
        gw = self.rt.gateway()
        with self.rt.db.session() as s:
            rh = s.get(ReceiptRow, receipt_id).receipt_hash
        try:
            tx = gw.record_receipt(authorization_id, rh)
        except Exception as exc:  # pragma: no cover - chain outage after execution
            with self.rt.db.session() as s:
                r = s.get(ReceiptRow, receipt_id)
                r.commitment_status = "FAILED"
                r.commitment_error = f"{type(exc).__name__}: {exc}"
            return
        with self.rt.db.session() as s:
            self._record_tx(s, tx, operation_id=receipt_id, related_id=authorization_id, chain_id=gw.info.chain_id)
            r = s.get(ReceiptRow, receipt_id)
            r.commitment_status = tx.status.value
            r.commitment_tx_hash = tx.tx_hash
            r.commitment_error = None if tx.ok else (tx.error_name or tx.error)
        if tx.ok:
            self._audit("RECEIPT_COMMITTED", receipt_id, {"tx_hash": tx.tx_hash, "receipt_hash": rh})

    def get_receipt(self, receipt_id: str) -> dict[str, Any]:
        with self.rt.db.session() as s:
            row = s.get(ReceiptRow, receipt_id)
            if row is None:
                raise not_found("receipt", receipt_id)
            body = EvidenceReceipt.model_validate(loads(row.body))
            commit_tx = None
            if row.commitment_tx_hash:
                t = s.execute(select(ChainTransactionRow).where(ChainTransactionRow.tx_hash == row.commitment_tx_hash)).scalars().first()
                commit_tx = self._chain_tx_view(t) if t else None
            audit_chain = self._audit_chain(s, body, row)
        return {
            "receipt": body,
            "receipt_hash": row.receipt_hash,
            "commitment": {"status": row.commitment_status, "tx_hash": row.commitment_tx_hash, "error": row.commitment_error, "transaction": commit_tx},
            "audit_chain": audit_chain,
        }

    def _audit_chain(self, s: Session, body: EvidenceReceipt, row: ReceiptRow) -> list[dict[str, Any]]:
        """Document 04 §81: contract hash -> evidence root -> decision -> action hash -> authorization id
        -> authorization tx -> execution tx -> receipt hash."""
        chain = [
            {"step": "Evidence Contract Hash", "value": body.contract_hash},
            {"step": "Evidence Root", "value": body.evidence_root},
            {"step": "Decision", "value": f"{body.decision_state.value} ({body.decision_id})"},
        ]
        if body.authorization:
            chain += [
                {"step": "Action Hash", "value": body.action_hash},
                {"step": "Authorization ID", "value": body.authorization.get("authorization_id")},
                {"step": "Authorization TX", "value": body.authorization.get("authorize_tx_hash")},
            ]
        chain.append({"step": "Execution TX", "value": body.execution.get("tx_hash"), "status": body.execution.get("status")})
        chain.append({"step": "Receipt Hash", "value": row.receipt_hash, "committed_tx": row.commitment_tx_hash})
        return chain

    def list_receipts(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.rt.db.session() as s:
            rows = s.execute(select(ReceiptRow).order_by(ReceiptRow.created_at.desc()).limit(limit)).scalars().all()
            out = []
            for r in rows:
                b = loads(r.body)
                out.append(
                    {
                        "receipt_id": r.id,
                        "kind": r.kind,
                        "decision_id": r.decision_id,
                        "decision_state": b.get("decision_state"),
                        "reason_codes": b.get("reason_codes"),
                        "execution_status": b.get("execution", {}).get("status"),
                        "tx_hash": b.get("execution", {}).get("tx_hash"),
                        "receipt_hash": r.receipt_hash,
                        "commitment_status": r.commitment_status,
                        "created_at": r.created_at,
                    }
                )
            return out

    def decision_receipt(self, decision_id: str) -> dict[str, Any]:
        with self.rt.db.session() as s:
            self._decision(s, decision_id)
            r = s.execute(
                select(ReceiptRow).where(ReceiptRow.decision_id == decision_id).order_by(ReceiptRow.created_at.desc()).limit(1)
            ).scalar_one_or_none()
        if r is None:
            raise ApiError(404, "RECEIPT_NOT_FOUND", "no receipt yet: the decision is authorized but not executed")
        return self.get_receipt(r.id)

    # =========================================================================================
    # Replay (Document 03 §47-49, Document 04 §80)
    # =========================================================================================

    def replay_receipt(self, receipt_id: str) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        def check(name: str, expected: Any, actual: Any, *, group: str) -> None:
            checks.append({"group": group, "check": name, "expected": expected, "actual": actual, "ok": expected == actual})

        with self.rt.db.session() as s:
            rrow = s.get(ReceiptRow, receipt_id)
            if rrow is None:
                raise not_found("receipt", receipt_id)
            body = EvidenceReceipt.model_validate(loads(rrow.body))
            drow, original = self._decision(s, rrow.decision_id)
            crow = s.get(ContractRow, drow.contract_id)
            irow, intent = self._intent(s, crow.intent_id)
            arow = s.execute(select(AuthorizationRow).where(AuthorizationRow.decision_id == drow.id)).scalar_one_or_none()
            snapshot = drow.snapshot

        # 1. receipt integrity
        check("receipt_hash", rrow.receipt_hash, compute_receipt_hash(body), group="receipt")
        check("receipt_hash_in_body", rrow.receipt_hash, body.receipt_hash, group="receipt")
        check("intent_hash", irow.intent_hash, intent_hash(intent), group="receipt")
        check("receipt_intent_hash", irow.intent_hash, body.intent_hash, group="receipt")

        # 2. deterministic replay from the decision-time snapshot (no market query)
        replayed = replay_snapshot(snapshot)
        check("decision_state", original.state.value, replayed.state.value, group="decision")
        check("reason_codes", [c.value for c in original.reason_codes], [c.value for c in replayed.reason_codes], group="decision")
        check("contract_hash", original.contract_hash, replayed.contract_hash, group="decision")
        check("evidence_root", original.evidence_root, replayed.evidence_root, group="decision")
        check("fingerprint", original.fingerprint, replayed.fingerprint, group="decision")
        check("decision_id", original.decision_id, replayed.decision_id, group="decision")
        check(
            "requirement_statuses",
            [(r.requirement_id, r.status.value) for r in original.requirement_results],
            [(r.requirement_id, r.status.value) for r in replayed.requirement_results],
            group="decision",
        )
        check("frozen_contract_hash", crow.contract_hash, replayed.contract_hash, group="decision")
        check("receipt_decision_state", body.decision_state.value, replayed.state.value, group="decision")
        check("receipt_evidence_root", body.evidence_root, replayed.evidence_root, group="decision")

        # 3. authority binding
        chain_status = "NOT_APPLICABLE"
        if arow is not None:
            auth = Authorization.model_validate(loads(arow.payload))
            action = CanonicalAction.model_validate(loads(arow.action))
            check("binding_mismatches", [], verify_binding(auth, replayed, action), group="authority")
            check("receipt_action_hash", arow.action_hash, body.action_hash if body.authorization else arow.action_hash, group="authority")
            gw = self.rt.gateway(required=False)
            if gw is None or gw.info.gate_address.lower() != arow.gate_address.lower() or gw.info.chain_id != arow.chain_id:
                chain_status = "SKIPPED"
                checks.append(
                    {
                        "group": "chain",
                        "check": "onchain_state",
                        "expected": "readable",
                        "actual": "the EvidenceGate that holds this authorization is not reachable from this process"
                        " (e.g. the in-memory local chain was restarted)",
                        "ok": None,
                    }
                )
            else:
                chain_status = "VERIFIED"
                oc = gw.get_authorization(arow.id)
                check("onchain_exists", True, oc.exists, group="chain")
                check("onchain_contract_hash", arow.contract_hash.lower(), (oc.contract_hash or "").lower(), group="chain")
                check("onchain_evidence_root", arow.evidence_root.lower(), (oc.evidence_root or "").lower(), group="chain")
                check("onchain_action_hash", arow.action_hash.lower(), (oc.action_hash or "").lower(), group="chain")
                if body.kind == "EXECUTED":
                    check("onchain_consumed", True, oc.consumed, group="chain")
                    if rrow.commitment_status == TxStatus.CONFIRMED.value:
                        check("onchain_receipt_commitment", rrow.receipt_hash.lower(), (gw.receipt_commitment(arow.id) or "").lower(), group="chain")
        else:
            check("no_authorization_for_non_authorize", original.state != DecisionState.AUTHORIZE, True, group="authority")
            check("no_transaction", None, body.execution.get("tx_hash"), group="authority")

        failed = [c for c in checks if c["ok"] is False]
        result = "MATCH" if not failed else "MISMATCH"
        self._audit("RECEIPT_REPLAYED", receipt_id, {"result": result, "failed": [c["check"] for c in failed]})
        return {
            "result": result,
            "receipt_id": receipt_id,
            "replayed_at": self.rt.now(),
            "market_queried": False,
            "inputs": {
                "snapshot_hash": snapshot_hash(snapshot),
                "contract_hash": original.contract_hash,
                "evidence_root": original.evidence_root,
                "evaluated_at": original.evaluated_at,
                "engine_version": original.engine_version,
                "current_engine_version": ENGINE_VERSION,
                "evidence_items": original.evidence_count,
            },
            "original": {"state": original.state, "reason_codes": original.reason_codes, "decision_id": original.decision_id},
            "replay": {"state": replayed.state, "reason_codes": replayed.reason_codes, "decision_id": replayed.decision_id},
            "chain_verification": chain_status,
            "checks": checks,
        }

    # =========================================================================================
    # Audit & chain views
    # =========================================================================================

    def audit_events(self, *, subject_id: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
        from ..db.models import AuditEventRow

        with self.rt.db.session() as s:
            q = select(AuditEventRow).order_by(AuditEventRow.seq.desc()).limit(limit)
            if subject_id:
                q = q.where(AuditEventRow.subject_id == subject_id)
            return [audit.to_dict(r) for r in s.execute(q).scalars().all()]

    def audit_verify(self) -> dict[str, Any]:
        with self.rt.db.session() as s:
            return audit.verify_chain(s)

    def chain_transactions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.rt.db.session() as s:
            rows = s.execute(select(ChainTransactionRow).order_by(ChainTransactionRow.id.desc()).limit(limit)).scalars().all()
            return self._tx_views(s, list(rows))

    def stats(self) -> dict[str, Any]:
        with self.rt.db.session() as s:
            states = dict(s.execute(select(DecisionRow.state, func.count()).group_by(DecisionRow.state)).all())
            exec_states = dict(s.execute(select(ExecutionRow.status, func.count()).group_by(ExecutionRow.status)).all())
            receipts = s.execute(select(func.count()).select_from(ReceiptRow)).scalar_one()
        return {"decisions": states, "executions": exec_states, "receipts": receipts}

    # expose gateway for callers (demo, API)
    def gateway(self, *, required: bool = True) -> Optional[ChainGateway]:
        return self.rt.gateway(required=required)
