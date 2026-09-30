"""Intermediate, pre-authoritative representation of a human instruction.

A :class:`ParsedIntent` is what a compiler *understood* from the text. It is not authority: it only
becomes an :class:`~sufficia.domain.models.EvidenceContract` through
:func:`sufficia.intent.builder.build_contract`, which applies policy defaults and validates it.
Both the deterministic parser and the optional LLM draft produce this same shape, so the two can be
compared structurally (``sufficia.intent.preservation``).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..domain.enums import ActionType, ConflictPolicy, Operator

USDC = "USDC"


class ParsedCondition(BaseModel):
    """One constraint the instruction places on a canonical claim."""

    model_config = ConfigDict(extra="forbid")

    claim: str
    operator: Operator
    threshold: Optional[Decimal] = None  # numeric claims (canonical unit of the claim, USD)
    max_age_seconds: Optional[int] = None  # per-condition freshness, when stated for this condition
    expected: Optional[bool | str] = None  # non-numeric equality (== / !=)
    values: Optional[list[str]] = None  # IN
    origin: str = "USER"  # USER | IMPLICIT (added by the compiler, always restrictive)
    required: bool = True
    # Resolved per-requirement policy; only populated when derived from a built contract.
    conflict_tolerance: Optional[Decimal] = None
    minimum_items: Optional[int] = None
    conflict_policy: Optional[ConflictPolicy] = None
    text: Optional[str] = None  # the source fragment (presentation only, never compared)


class ParsedIntent(BaseModel):
    """What a compiler understood from one instruction."""

    model_config = ConfigDict(extra="forbid")

    text: str = ""
    source: str = "deterministic"  # deterministic | llm | override
    language: str = "en"
    action: Optional[ActionType] = None
    asset: Optional[str] = None
    max_amount: Optional[Decimal] = None
    currency: Optional[str] = None
    conditions: list[ParsedCondition] = Field(default_factory=list)
    max_age_seconds: Optional[int] = None  # global evidence age limit
    minimum_required_evidence: Optional[int] = None  # "at least N sources"
    conflict_policy: Optional[ConflictPolicy] = None  # "abstain on conflict" / "escalate conflicts"
    conflict_tolerance: Optional[Decimal] = None  # fraction, "agree within 5%" -> 0.05
    evidence_budget_usdc: Optional[Decimal] = None  # "evidence budget $0.10"
    unparsed: list[str] = Field(default_factory=list)  # fragments NOT represented in the contract
    warnings: list[str] = Field(default_factory=list)

    @property
    def missing(self) -> list[str]:
        """Mandatory parts of an instruction that were not found."""
        missing: list[str] = []
        if self.action is None:
            missing.append("action (buy, sell or transfer)")
        if not self.asset:
            missing.append("asset (e.g. TOKEN-X)")
        if self.max_amount is None:
            missing.append("maximum amount (e.g. 'up to 100 USDC')")
        elif not self.currency:
            missing.append("currency of the maximum amount (e.g. '100 USDC')")
        return missing

    @property
    def complete(self) -> bool:
        """Everything in the instruction was understood (nothing unparsed)."""
        return not self.unparsed
