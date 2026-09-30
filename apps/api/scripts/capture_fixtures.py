#!/usr/bin/env python
"""Capture RECORDED evidence fixtures from configured sources.

Usage (from apps/api)::

    # genuine captures from the real providers configured in .env (live profile order):
    python scripts/capture_fixtures.py
    # only some sources / claims, into another directory:
    python scripts/capture_fixtures.py --sources nansen,dexscreener --claims asset_price --out /tmp/rec
    # the clearly-labelled SAMPLE set shipped in the repo (captured from the local SIMULATED market):
    python scripts/capture_fixtures.py --sources scenario

Each fixture is ``<out>/<claim>.json``::

    {"provider", "source_type", "claim", "value", "unit", "captured_at", "request_descriptor",
     "response_body_b64", "upstream_source", "source_family", "endpoint_id", "network",
     "original_mode", "note"}

The bytes in ``response_body_b64`` are exactly the bytes the adapter received; ``request_descriptor``
is exactly the adapter's descriptor (no secrets: API keys are sent as headers and never recorded), so
a re-served fixture commits to the same request/response hashes as the original observation.

Rules:
* a source that fails is reported and **no fixture is written** for it (nothing is ever fabricated);
* the original mode is kept in ``original_mode``; captures from SIMULATED sources carry a ``note``
  saying they are not real provider data;
* paid (x402) sources are skipped unless ``--allow-paid`` is given (capturing them spends USDC).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import httpx  # noqa: E402

from sufficia.canonical.canonicalize import canonical_json, format_datetime  # noqa: E402
from sufficia.config import DEFAULT_SOURCES, Settings  # noqa: E402
from sufficia.domain.enums import ActionType, Operator, SourceMode  # noqa: E402
from sufficia.domain.models import EconomicLimits, EvidenceContract, IntentAction, Money, Requirement  # noqa: E402
from sufficia.evidence.adapters import build_registry  # noqa: E402
from sufficia.evidence.base import AcquisitionContext, AcquisitionError, EvidenceBudget, RawEvidence  # noqa: E402

CLAIMS = ("smart_money_inflow_24h", "available_liquidity", "asset_price")
VALUE_PLACEHOLDER = "__SUFFICIA_VALUE__"
SIMULATED_NOTE = (
    "SAMPLE captured from the local SIMULATED market (sufficia.evidence.simulated_market). "
    "This is NOT a capture of a real provider response; it only exercises the RECORDED path."
)


def fixture_json(raw: RawEvidence) -> str:
    """Serializes a fixture; the value keeps its JSON type (number vs string) exactly."""
    fixture: dict[str, Any] = {
        "provider": raw.provider,
        "source_type": raw.source_type.value,
        "claim": raw.claim,
        "value": VALUE_PLACEHOLDER,
        "unit": raw.unit,
        "captured_at": format_datetime(raw.received_at),
        "request_descriptor": json.loads(canonical_json(raw.request_descriptor), parse_float=Decimal),
        "response_body_b64": base64.b64encode(raw.response_body).decode("ascii"),
        "upstream_source": raw.upstream_source,
        "source_family": raw.source_family,
        "endpoint_id": raw.endpoint_id,
        "network": raw.network,
        "original_mode": raw.mode.value,
    }
    if raw.mode == SourceMode.SIMULATED:
        fixture["note"] = SIMULATED_NOTE
    text = json.dumps(fixture, indent=2, default=_default)
    return text.replace(json.dumps(VALUE_PLACEHOLDER), canonical_json(raw.value), 1) + "\n"


def _default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else str(o)
    raise TypeError(type(o).__name__)


def _contract(asset: str, claims: list[str]) -> EvidenceContract:
    return EvidenceContract(
        contract_id="capture",
        intent_id="capture",
        action=IntentAction(type=ActionType.BUY, asset=asset, max_amount=Money(value=Decimal(1))),
        requirements=[Requirement(id=f"cap_{c}", claim=c, operator=Operator.EXISTS) for c in claims],
        economic_limits=EconomicLimits(max_execution_value_usd=Decimal(1)),
    )


async def capture(
    settings: Settings,
    *,
    out: Path,
    claims: list[str],
    sources: list[str] | None,
    asset: str,
    allow_paid: bool,
    http: httpx.AsyncClient | None = None,
    reference_http: httpx.AsyncClient | None = None,
) -> dict[str, str]:
    """Captures one fixture per claim from the first source that succeeds. Returns claim -> status."""
    registry = build_registry(settings)
    contract = _contract(asset, claims)
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, str] = {}
    own_http = http is None
    http = http or httpx.AsyncClient(timeout=settings.http_timeout_seconds)
    try:
        ctx = AcquisitionContext(
            contract=contract,
            run_id="capture",
            settings=settings,
            budget=EvidenceBudget(settings.default_evidence_budget_usdc, settings.x402_max_per_request_usdc),
            http=http,
            reference_http=reference_http,
        )
        for requirement in contract.requirements:
            order = sources or DEFAULT_SOURCES["live"].get(requirement.claim, [])
            status = "no configured source"
            for name in order:
                adapter = registry.get(name)
                if adapter is None or requirement.claim not in adapter.claims:
                    continue
                if adapter.paid and not allow_paid:
                    status = f"{name}: skipped (paid source; use --allow-paid)"
                    continue
                if not adapter.is_configured(settings):
                    status = f"{name}: not configured"
                    continue
                try:
                    raw = await adapter.fetch(requirement, ctx)
                except AcquisitionError as err:
                    status = f"{name}: failed {err.code.value} {err.detail}"
                    continue
                if raw.mode == SourceMode.RECORDED:
                    status = f"{name}: refusing to re-record a RECORDED observation"
                    continue
                path = out / f"{requirement.claim}.json"
                path.write_text(fixture_json(raw), encoding="utf-8")
                status = f"captured from {raw.provider} ({raw.mode.value}) -> {path}"
                break
            report[requirement.claim] = status
    finally:
        if own_http:
            await http.aclose()
    return report


def main(argv: list[str] | None = None) -> int:
    settings = Settings()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=settings.recorded_fixtures_dir, help="output directory")
    parser.add_argument("--claims", default=",".join(CLAIMS))
    parser.add_argument("--sources", default=None, help="comma-separated registry names (default: live profile order)")
    parser.add_argument("--asset", default="TOKEN-X")
    parser.add_argument("--allow-paid", action="store_true")
    args = parser.parse_args(argv)
    sources = [s.strip() for s in args.sources.split(",") if s.strip()] if args.sources else None
    claims = [c.strip() for c in args.claims.split(",") if c.strip()]
    report = asyncio.run(
        capture(settings, out=Path(args.out), claims=claims, sources=sources, asset=args.asset, allow_paid=args.allow_paid)
    )
    for claim, status in report.items():
        print(f"{claim}: {status}")
    return 0 if all(s.startswith("captured") for s in report.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
