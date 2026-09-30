"""RECORDED source: re-serves a previously captured provider response (resilience / demo continuity).

Fixture file: ``{recorded_fixtures_dir}/<claim>.json``::

    {"provider", "source_type", "claim", "value", "unit", "captured_at", "request_descriptor",
     "response_body_b64", "upstream_source", "source_family", "endpoint_id"}

Labelling rules (Document 05 §58 — modes are never blurred):

* ``mode`` is always ``RECORDED``, whatever the original mode of the capture was;
* ``provider`` keeps the original provider name (e.g. "nansen"), so provenance still says *who*
  produced the bytes — and the engine's permitted-sources check still applies to that provider;
* ``source.captured_at`` preserves the original capture time;
* ``observed_at`` is **re-stamped to now** when the fixture is re-served. This is an explicit demo
  continuity choice: without it every recorded observation would be stale by construction. Anyone
  auditing the item sees ``mode=RECORDED`` and the true ``captured_at`` next to the re-stamped
  ``observed_at``;
* request/response hashes are computed over the *captured* request descriptor and response bytes,
  so a recorded item commits to exactly what was captured.

A fixture whose ``claim`` differs from its file name, or that cannot be read/decoded, is an
acquisition failure (never a silently substituted value).
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from ...domain.enums import SourceMode, SourceType
from ...domain.models import Requirement
from ...domain.reasons import ReasonCode
from ..base import AcquisitionContext, AcquisitionError, RawEvidence, SourceAdapter
from ._common import parse_timestamp

if TYPE_CHECKING:  # pragma: no cover
    from ...config import Settings

FIXTURE_FIELDS = (
    "provider",
    "source_type",
    "claim",
    "value",
    "unit",
    "captured_at",
    "request_descriptor",
    "response_body_b64",
    "upstream_source",
    "source_family",
    "endpoint_id",
)


def load_fixture(directory: str | Path, claim: str) -> dict[str, Any]:
    path = Path(directory) / f"{claim}.json"
    if not path.is_file():
        raise AcquisitionError(ReasonCode.SOURCE_NOT_CONFIGURED, f"no recorded fixture for '{claim}' in {directory}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcquisitionError(ReasonCode.INVALID_RESPONSE, f"recorded fixture {path.name} is unreadable: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise AcquisitionError(ReasonCode.INVALID_RESPONSE, f"recorded fixture {path.name} is not an object")
    return data


class RecordedFixtureAdapter(SourceAdapter):
    name = "recorded"  # registry key; provenance keeps the fixture's original provider
    source_type = SourceType.API
    mode = SourceMode.RECORDED
    claims = frozenset({"smart_money_inflow_24h", "available_liquidity", "asset_price"})

    def __init__(self, directory: Optional[str | Path] = None):
        self.directory = directory

    def _dir(self, settings: "Settings") -> Path:
        return Path(self.directory or settings.recorded_fixtures_dir)

    def available_claims(self, settings: "Settings") -> set[str]:
        d = self._dir(settings)
        return {p.stem for p in d.glob("*.json")} if d.is_dir() else set()

    def fixture_providers(self, settings: "Settings") -> dict[str, set[str]]:
        """provider name -> claims, as recorded in the fixtures (feeds the engine's permitted_sources)."""
        out: dict[str, set[str]] = {}
        d = self._dir(settings)
        if not d.is_dir():
            return out
        for path in sorted(d.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and isinstance(data.get("provider"), str):
                out.setdefault(data["provider"], set()).add(str(data.get("claim") or path.stem))
        return out

    def is_configured(self, settings: "Settings") -> bool:
        return bool(self.available_claims(settings))

    async def fetch(self, requirement: Requirement, ctx: AcquisitionContext) -> RawEvidence:
        claim = requirement.claim
        data = load_fixture(self._dir(ctx.settings), claim)
        missing = [f for f in ("provider", "claim", "value", "captured_at", "request_descriptor", "response_body_b64") if f not in data]
        if missing:
            raise AcquisitionError(ReasonCode.MISSING_FIELD, f"recorded fixture for '{claim}' lacks {', '.join(missing)}")
        if data["claim"] != claim:
            raise AcquisitionError(ReasonCode.INVALID_RESPONSE, f"recorded fixture {claim}.json holds claim '{data['claim']}'")
        try:
            body = base64.b64decode(str(data["response_body_b64"]), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AcquisitionError(ReasonCode.INVALID_RESPONSE, "recorded fixture response_body_b64 is not base64") from exc
        captured = parse_timestamp(data["captured_at"])
        if not isinstance(captured, datetime):
            raise AcquisitionError(ReasonCode.INVALID_RESPONSE, f"recorded fixture captured_at '{data['captured_at']}' is not an aware timestamp")
        descriptor = data["request_descriptor"]
        if not isinstance(descriptor, dict):
            raise AcquisitionError(ReasonCode.INVALID_RESPONSE, "recorded fixture request_descriptor must be an object")
        try:
            source_type = SourceType(str(data.get("source_type") or SourceType.API.value))
        except ValueError as exc:
            raise AcquisitionError(ReasonCode.INVALID_RESPONSE, f"recorded fixture source_type '{data.get('source_type')}' is unknown") from exc

        now = ctx.now()
        return RawEvidence(
            provider=str(data["provider"]),
            source_type=source_type,
            mode=SourceMode.RECORDED,
            claim=claim,
            value=data["value"],
            unit=data.get("unit"),
            # Demo continuity: the recorded observation is re-stamped to the time it is re-served.
            # The genuine capture time is preserved in source.captured_at (see module docstring).
            observed_at=now,
            received_at=now,
            request_descriptor=descriptor,
            response_body=body,
            endpoint_id=data.get("endpoint_id"),
            upstream_source=data.get("upstream_source"),
            source_family=data.get("source_family"),
            network=data.get("network"),
            captured_at=captured,
        )
