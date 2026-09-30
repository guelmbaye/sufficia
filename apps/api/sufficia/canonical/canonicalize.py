"""Canonical serialization — the basis of every commitment (Document 03 §76).

Rules:
  * object keys sorted, no insignificant whitespace, ASCII-only output;
  * Decimal / int emitted as normalized *number* tokens (``1340000``, ``0.428``) — never floats;
    a numeric string stays a string, so "92440" and 92440 do not collide;
  * non-finite numbers are tagged objects (``{"$nonfinite":"NaN"}``) so malformed evidence is still
    hashable and cannot masquerade as a number;
  * datetimes are UTC ``YYYY-MM-DDTHH:MM:SS.ffffffZ``; naive datetimes are tagged ``{"$naive": ...}``;
  * strings are emitted byte-exact (no case folding): the encoding is injective. Address
    normalization belongs to the typed boundary (e.g. checksum addresses in the action hash);
  * numbers whose adjusted exponent lies outside [-PLAIN_EXP_LIMIT, PLAIN_EXP_LIMIT] are tagged
    ``{"$decimal": "<coefficient>E<exponent>"}`` so pathological magnitudes stay hashable in O(digits)
    without ever being expanded (malformed evidence must be *rejected*, never crash the engine);
  * enums emit their value; pydantic models emit their python dump.

``format_decimal`` never performs context arithmetic: it rewrites the digit tuple, so values with any
number of significant digits are encoded exactly (no silent rounding to 28 digits).

``loads`` is the inverse used for immutable snapshots: numbers come back as ``int``/``Decimal``,
so hash(snapshot) is stable across a database round-trip (replay fidelity).
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel

PLAIN_EXP_LIMIT = 64


def _stripped(d: Decimal) -> tuple[int, str, int]:
    """(sign, significant digits without trailing zeros, exponent) — exact, no context rounding."""
    sign, digits, exponent = d.as_tuple()
    text = "".join(map(str, digits)).lstrip("0")
    if not text:
        return 0, "0", 0
    stripped = text.rstrip("0")
    return sign, stripped, int(exponent) + (len(text) - len(stripped))


def decimal_shape(d: Decimal) -> tuple[int, str, int]:
    """Public exact shape of a finite Decimal: (sign, significant digits, exponent)."""
    return _stripped(d)


def is_plain(d: Decimal) -> bool:
    if not d.is_finite():
        return False
    _, digits, exp = _stripped(d)
    adjusted = exp + len(digits) - 1
    return -PLAIN_EXP_LIMIT <= adjusted <= PLAIN_EXP_LIMIT


def format_decimal(d: Decimal) -> str:
    """Normalized plain-notation decimal: no exponent, no trailing zeros, no '-0'. Exact."""
    if not d.is_finite():
        raise ValueError("non-finite decimal")
    sign, digits, exp = _stripped(d)
    if digits == "0":
        return "0"
    if exp >= 0:
        text = digits + "0" * exp
    elif -exp >= len(digits):
        text = "0." + "0" * (-exp - len(digits)) + digits
    else:
        text = digits[:exp] + "." + digits[exp:]
    return ("-" if sign else "") + text


def scientific_decimal(d: Decimal) -> str:
    """Unique exact scientific form '<digits>E<exp>' (coefficient without trailing zeros)."""
    sign, digits, exp = _stripped(d)
    return ("-" if sign else "") + digits + "E" + ("+" if exp >= 0 else "") + str(exp)


def _number(d: Decimal) -> Any:
    if is_plain(d):
        return _Number(format_decimal(d))
    return {"$decimal": scientific_decimal(d)}


def format_datetime(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _to_plain(value: Any) -> Any:
    """Converts supported python values into a JSON-able structure with tagged number tokens."""
    if isinstance(value, BaseModel):
        return _to_plain(value.model_dump(mode="python"))
    if isinstance(value, Enum):
        return _to_plain(value.value)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if -(10**18) <= value <= 10**18:
            return _Number(str(value))
        return _number(Decimal(value))  # Decimal(int) is exact and not subject to the str() digit limit
    if isinstance(value, Decimal):
        if not value.is_finite():
            return {"$nonfinite": "NaN" if value.is_nan() else ("Infinity" if value > 0 else "-Infinity")}
        return _number(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return {"$nonfinite": "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")}
        return _number(Decimal(repr(value)))
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return {"$naive": value.isoformat()}
        return format_datetime(value)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return {str(k): _to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    raise TypeError(f"cannot canonicalize {type(value).__name__}")


class _Number(str):
    """Marker: emitted as a bare JSON number token."""


def _emit(value: Any) -> str:
    if isinstance(value, _Number):
        return str.__str__(value)
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: kv[0])
        return "{" + ",".join(json.dumps(k, ensure_ascii=True) + ":" + _emit(v) for k, v in items) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_emit(v) for v in value) + "]"
    raise TypeError(f"cannot emit {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return _emit(_to_plain(value))


def canonical_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("ascii")


def _object_hook(obj: dict) -> Any:
    if len(obj) == 1:
        if "$nonfinite" in obj:
            return Decimal(obj["$nonfinite"])
        if "$naive" in obj:
            return datetime.fromisoformat(obj["$naive"])
        if "$decimal" in obj:
            return Decimal(obj["$decimal"])
    return obj


def loads(text: str) -> Any:
    """Parses canonical JSON back into python values with exact numbers (int / Decimal)."""
    return json.loads(text, parse_float=Decimal, parse_int=int, object_hook=_object_hook)


def dumps_pretty(value: Any) -> str:
    """Human-friendly JSON for API responses/reports (numbers as strings where Decimal)."""
    return json.dumps(loads(canonical_json(value)), indent=2, default=_default, sort_keys=True)


def _default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return format_decimal(o) if is_plain(o) else str(o)
    raise TypeError
