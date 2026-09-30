"""Shared helpers for source adapters: HTTP error mapping, strict JSON and timestamp parsing."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import httpx

from ...domain.reasons import ReasonCode
from ..base import AcquisitionError


def status_code_reason(status: int) -> ReasonCode:
    """Provider HTTP status -> acquisition failure code (never a policy outcome)."""
    if status in (401, 403):
        return ReasonCode.SOURCE_NOT_CONFIGURED  # missing / rejected credentials
    if status == 429:
        return ReasonCode.RATE_LIMITED
    if status >= 500:
        return ReasonCode.SOURCE_UNAVAILABLE
    return ReasonCode.INVALID_RESPONSE


def raise_for_status(response: httpx.Response, provider: str, **extra: Any) -> None:
    if response.is_success:
        return
    raise AcquisitionError(status_code_reason(response.status_code), f"{provider} returned HTTP {response.status_code}", **extra)


async def http_request(
    http: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    provider: str,
    timeout: Optional[float] = None,
    **kwargs: Any,
) -> httpx.Response:
    """One HTTP call with transport failures mapped to SOURCE_UNAVAILABLE."""
    if timeout is not None:
        kwargs["timeout"] = timeout
    try:
        return await http.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise AcquisitionError(ReasonCode.SOURCE_UNAVAILABLE, f"{provider} timed out: {type(exc).__name__}") from exc
    except httpx.TransportError as exc:
        raise AcquisitionError(ReasonCode.SOURCE_UNAVAILABLE, f"{provider} unreachable: {type(exc).__name__}") from exc


def parse_json_bytes(body: bytes, provider: str, **extra: Any) -> Any:
    """Strict JSON parsing; floats become Decimal so no binary rounding enters the evidence."""
    try:
        return json.loads(body.decode("utf-8"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise AcquisitionError(ReasonCode.INVALID_RESPONSE, f"{provider} returned malformed JSON: {type(exc).__name__}", **extra) from exc


def parse_timestamp(value: Any) -> datetime | str | None:
    """Timezone-aware ISO-8601 (``Z`` or offset) or integer unix seconds -> aware UTC datetime.

    Anything else is returned unchanged (as a string when it is one) so the engine can flag
    INVALID_TIMESTAMP instead of the adapter guessing a timezone.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.isoformat()
    if isinstance(value, int) or (isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value()):
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return str(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text)
        except ValueError:
            return value
        if parsed.tzinfo is None:
            return value  # naive: refuse to assume UTC
        return parsed.astimezone(timezone.utc)
    return str(value)


def as_decimal_or_none(value: Any) -> Optional[Decimal]:
    """Used only for *selection* heuristics (e.g. max-liquidity pair), never for evidence values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, Decimal)):
        d = Decimal(value)
        return d if d.is_finite() else None
    if isinstance(value, str):
        try:
            d = Decimal(value)
        except Exception:  # noqa: BLE001
            return None
        return d if d.is_finite() else None
    return None
