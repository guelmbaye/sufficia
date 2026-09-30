"""Exact JSON responses.

FastAPI's default encoder turns Decimal into float, which would silently alter thresholds, values and
amounts. Every endpoint therefore returns an ``ExactJSONResponse`` built from ``to_jsonable``:
  * Decimal  -> exact plain-notation string ("92440", "0.428", "0.02")
  * datetime -> ISO-8601 UTC with microseconds and "Z"
  * int beyond JavaScript's safe range (|n| > 2^53 - 1, e.g. raw token amounts in wei) -> decimal string
  * Enum -> value, pydantic models -> python dump (recursively), dataclasses -> dict, bytes -> 0x-hex
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel
from starlette.responses import JSONResponse

from ..canonical.canonicalize import format_decimal, is_plain, scientific_decimal


MAX_SAFE_INTEGER = 2**53 - 1


def to_jsonable(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value if -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER else str(value)
    if isinstance(value, BaseModel):
        return to_jsonable(value.model_dump(mode="python"))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(dataclasses.asdict(value))
    if isinstance(value, Enum):
        return to_jsonable(value.value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            return str(value)
        return format_decimal(value) if is_plain(value) else scientific_decimal(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat()
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [to_jsonable(v) for v in value]
        return sorted(items, key=str) if isinstance(value, (set, frozenset)) else items
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, float):
        return format_decimal(Decimal(repr(value))) if value == value and value not in (float("inf"), float("-inf")) else str(value)
    return value


class ExactJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return json.dumps(to_jsonable(content), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def ok(content: Any, status_code: int = 200, headers: dict[str, str] | None = None) -> ExactJSONResponse:
    return ExactJSONResponse(content=content, status_code=status_code, headers=headers)
