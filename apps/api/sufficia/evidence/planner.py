"""Acquisition planner: Evidence Contract x evidence profile -> ordered sources per requirement.

For every requirement the planner lists the profile's sources for its claim (primary first, then
fallbacks) and keeps those that exist in the registry, serve the claim and are configured. Every
source left out is kept in ``skipped`` with a reason, so the UI and receipts can say *why* a source
was not tried. A claim with no usable source still yields a step (with no adapters): acquisition then
records SOURCE_NOT_CONFIGURED for it, and the engine abstains on a mandatory requirement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..domain.models import EvidenceContract
from .base import SourceAdapter

if TYPE_CHECKING:  # pragma: no cover
    from ..config import Settings

SKIP_UNKNOWN = "UNKNOWN_SOURCE"
SKIP_CLAIM = "CLAIM_NOT_SUPPORTED"
SKIP_NOT_CONFIGURED = "NOT_CONFIGURED"
SKIP_NO_PROFILE_ENTRY = "NO_SOURCE_IN_PROFILE"


@dataclass
class PlanStep:
    requirement_id: str
    claim: str
    adapters: list[str] = field(default_factory=list)  # registry names, in order of preference
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (registry name, reason)

    @property
    def has_sources(self) -> bool:
        return bool(self.adapters)


def plan(contract: EvidenceContract, settings: "Settings", registry: dict[str, SourceAdapter]) -> list[PlanStep]:
    profile = settings.sources_for_profile()
    steps: list[PlanStep] = []
    for requirement in contract.requirements:
        step = PlanStep(requirement_id=requirement.id, claim=requirement.claim)
        names = profile.get(requirement.claim, [])
        if not names:
            step.skipped.append(("*", SKIP_NO_PROFILE_ENTRY))
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            adapter = registry.get(name)
            if adapter is None:
                step.skipped.append((name, SKIP_UNKNOWN))
            elif requirement.claim not in adapter.claims:
                step.skipped.append((name, SKIP_CLAIM))
            elif not _configured(adapter, settings):
                step.skipped.append((name, SKIP_NOT_CONFIGURED))
            else:
                step.adapters.append(name)
        steps.append(step)
    return steps


def _configured(adapter: SourceAdapter, settings: "Settings") -> bool:
    try:
        return bool(adapter.is_configured(settings))
    except Exception:  # noqa: BLE001 - a misconfigured adapter is treated as not configured
        return False
