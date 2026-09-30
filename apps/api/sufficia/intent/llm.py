"""Optional LLM draft of a ParsedIntent through the Anthropic Messages API (plain httpx).

The LLM is *never* authoritative (INV-11). Its output is a draft that must survive the structural
preservation check against the deterministic parse (``sufficia.intent.preservation``) before it can
be used, and it is then validated exactly like the deterministic contract.

Any failure — no key, transport error, non-2xx status, refusal, no JSON, JSON outside the schema,
unknown claim — raises :class:`LLMUnavailable`; callers fall back to the deterministic compiler.
The API key is sent only in the ``x-api-key`` header and never logged or included in errors.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import httpx

from ..config import Settings
from ..domain.claims import CLAIMS
from ..domain.enums import ActionType, ConflictPolicy, Operator
from .parser import fold, plain
from .schema import USDC, ParsedCondition, ParsedIntent

log = logging.getLogger(__name__)

ANTHROPIC_VERSION = "2023-06-01"
MAX_TOKENS = 1024
MAX_CONDITIONS = 12


class LLMUnavailable(RuntimeError):
    """The LLM draft could not be obtained or did not conform to the schema.

    ``code`` is a short machine-readable cause (NO_API_KEY, TRANSPORT_ERROR, HTTP_<status>,
    BAD_RESPONSE, NO_JSON, INVALID_DRAFT). The message never contains the API key.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _claims_catalog() -> str:
    lines = []
    for name, spec in CLAIMS.items():
        ops = ", ".join(sorted(op.value for op in spec.allowed_operators))
        lines.append(f'- "{name}" ({spec.unit}): {spec.description} Allowed operators: {ops}.')
    return "\n".join(lines)


SYSTEM_PROMPT = f"""You compile a user's natural-language trading instruction into a STRICT JSON draft for an
Evidence Contract. The draft is not authoritative: it is checked against a deterministic parser and
rejected if it relaxes any constraint. Never invent, loosen, drop or reinterpret a constraint.

Reply with ONE JSON object and nothing else (no prose, no markdown fences), with exactly these keys:
{{
  "action": "BUY" | "SELL" | "TRANSFER" | null,
  "asset": string | null,              // ticker as written by the user, upper-case, e.g. "TOKEN-X", "MON", "WETH"
  "max_amount": string | null,         // decimal string, the maximum amount to spend/sell/transfer, e.g. "100"
  "currency": "USDC" | string | null,  // "USDC" for USDC, USD, $ or dollars
  "conditions": [
    {{
      "claim": string,                 // one of the known claims below, nothing else
      "operator": ">" | ">=" | "<" | "<=" | "==" | "!=" | "EXISTS" | "NOT_EXISTS" | "IN",
      "threshold": string | null,      // decimal string in the claim unit (USD): "$1M" -> "1000000", "$500K" -> "500000", "50 cents" -> "0.5"
      "max_age_seconds": integer | null  // only if the user gave a freshness limit for this condition
    }}
  ],
  "max_age_seconds": integer | null,   // global evidence age limit, e.g. "evidence under 30 seconds" -> 30
  "minimum_required_evidence": integer | null,  // "at least N sources" -> N
  "conflict_policy": "ABSTAIN" | "ESCALATE" | null,  // "abstain on conflict" / "escalate conflicts"
  "conflict_tolerance": string | null, // fraction, "sources must agree within 5%" -> "0.05"
  "evidence_budget_usdc": string | null,  // "evidence budget $0.10" -> "0.1"
  "unparsed": [string]                 // every part of the instruction you could not represent exactly
}}

Known claims (use ONLY these names):
{_claims_catalog()}

Rules:
- "exceeds", "above", "over", "more than", "greater than", "dépasse", "supérieur à" -> ">".
  "at least", "no less than", "minimum of" -> ">=". "below", "under", "less than", "inférieur à" -> "<".
  "at most", "no more than", "maximum of" -> "<=". Keep the user's exact threshold.
- "smart money inflows", "SM netflow", "net flow", "entrées smart money" -> "smart_money_inflow_24h".
  "liquidity", "pool liquidity", "TVL", "liquidité" -> "available_liquidity". "price", "prix" -> "asset_price".
- A constraint on anything that is not a known claim, a different time window, another asset, a
  negation or an "or" you cannot express exactly MUST be copied verbatim into "unparsed" and left out
  of "conditions". Do not add conditions the user did not state.
- The instruction may be in English or French.
"""


def _endpoint(settings: Settings) -> str:
    return settings.anthropic_base_url.rstrip("/") + "/v1/messages"


def request_body(text: str, settings: Settings) -> dict[str, Any]:
    return {
        "model": settings.llm_model,
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": f"Instruction:\n{text}"}],
    }


def extract_first_json_object(text: str) -> dict[str, Any]:
    """Returns the first *object* that decodes as JSON in ``text`` (fences and prose tolerated)."""
    decoder = json.JSONDecoder(parse_float=Decimal)
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            idx = text.find("{", idx + 1)
            continue
        if isinstance(obj, dict):
            return obj
        idx = text.find("{", idx + 1)  # pragma: no cover - raw_decode at "{" only yields dicts
    raise LLMUnavailable("NO_JSON", "the LLM response contained no JSON object")


# ---- schema coercion (strict: anything unexpected -> LLMUnavailable) ------------------------------


def _invalid(message: str) -> LLMUnavailable:
    return LLMUnavailable("INVALID_DRAFT", f"LLM draft rejected: {message}")


def _opt_str(obj: dict[str, Any], key: str) -> Optional[str]:
    v = obj.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise _invalid(f"'{key}' must be a string")
    v = v.strip()
    return v or None


def _decimal(v: Any, key: str) -> Optional[Decimal]:
    if v is None:
        return None
    if isinstance(v, bool):
        raise _invalid(f"'{key}' must be a number")
    if isinstance(v, (int, Decimal)):
        d = Decimal(v)
    elif isinstance(v, float):
        d = Decimal(repr(v))
    elif isinstance(v, str):
        try:
            d = Decimal(v.strip().replace(",", "").replace("$", "").replace("_", ""))
        except InvalidOperation:
            raise _invalid(f"'{key}' is not a decimal: {v[:40]!r}") from None
    else:
        raise _invalid(f"'{key}' must be a number")
    if not d.is_finite():
        raise _invalid(f"'{key}' must be finite")
    return plain(d)


def _int(v: Any, key: str, *, minimum: int = 0) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, bool):
        raise _invalid(f"'{key}' must be an integer")
    d = _decimal(v, key)
    assert d is not None
    if d != d.to_integral_value():
        raise _invalid(f"'{key}' must be an integer")
    i = int(d)
    if i < minimum:
        raise _invalid(f"'{key}' must be >= {minimum}")
    return i


def _fraction(v: Any, key: str) -> Optional[Decimal]:
    d = _decimal(v, key)
    if d is not None and not (Decimal(0) <= d <= Decimal(1)):
        raise _invalid(f"'{key}' must be a fraction within [0, 1]")
    return d


def _non_negative(v: Any, key: str) -> Optional[Decimal]:
    d = _decimal(v, key)
    if d is not None and d < 0:
        raise _invalid(f"'{key}' must be >= 0")
    return d


def _currency(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    if fold(v).strip() in ("usdc", "usd", "$", "us$", "dollar", "dollars"):
        return USDC
    return v.strip().upper()


def draft_from_json(obj: dict[str, Any], text: str) -> ParsedIntent:
    """Coerces the LLM JSON into a ParsedIntent, rejecting anything outside the documented schema."""
    if not isinstance(obj, dict):
        raise _invalid("top-level value must be an object")
    action_raw = _opt_str(obj, "action")
    action = None
    if action_raw is not None:
        try:
            action = ActionType(action_raw.upper())
        except ValueError:
            raise _invalid(f"unknown action {action_raw[:20]!r}") from None
    asset = _opt_str(obj, "asset")
    conditions_raw = obj.get("conditions") or []
    if not isinstance(conditions_raw, list):
        raise _invalid("'conditions' must be a list")
    if len(conditions_raw) > MAX_CONDITIONS:
        raise _invalid("too many conditions")
    conditions: list[ParsedCondition] = []
    for i, c in enumerate(conditions_raw):
        if not isinstance(c, dict):
            raise _invalid(f"conditions[{i}] must be an object")
        claim = _opt_str(c, "claim")
        if claim is None or claim not in CLAIMS:
            raise _invalid(f"conditions[{i}] uses an unknown claim {str(claim)[:40]!r}")
        op_raw = _opt_str(c, "operator")
        try:
            op = Operator(op_raw.upper() if op_raw and op_raw.isalpha() else op_raw)
        except ValueError:
            raise _invalid(f"conditions[{i}] has an unknown operator {str(op_raw)[:10]!r}") from None
        spec = CLAIMS[claim]
        if op not in spec.allowed_operators:
            raise _invalid(f"conditions[{i}] operator {op.value} is not allowed for {claim}")
        threshold = _decimal(c.get("threshold"), f"conditions[{i}].threshold")
        if op in (Operator.GT, Operator.GTE, Operator.LT, Operator.LTE) and threshold is None:
            raise _invalid(f"conditions[{i}] needs a threshold for {op.value}")
        if op in (Operator.EXISTS, Operator.NOT_EXISTS):
            threshold = None
        values = c.get("values")
        if values is not None and (not isinstance(values, list) or not all(isinstance(v, str) for v in values)):
            raise _invalid(f"conditions[{i}].values must be a list of strings")
        expected = c.get("expected")
        if expected is not None and not isinstance(expected, (bool, str)):
            raise _invalid(f"conditions[{i}].expected must be a boolean or a string")
        conditions.append(
            ParsedCondition(
                claim=claim,
                operator=op,
                threshold=threshold,
                max_age_seconds=_int(c.get("max_age_seconds"), f"conditions[{i}].max_age_seconds", minimum=1),
                values=values,
                expected=expected,
            )
        )
    policy_raw = _opt_str(obj, "conflict_policy")
    policy = None
    if policy_raw is not None:
        try:
            policy = ConflictPolicy(policy_raw.upper())
        except ValueError:
            raise _invalid(f"unknown conflict_policy {policy_raw[:20]!r}") from None
    unparsed_raw = obj.get("unparsed") or []
    if not isinstance(unparsed_raw, list) or not all(isinstance(u, str) for u in unparsed_raw):
        raise _invalid("'unparsed' must be a list of strings")
    return ParsedIntent(
        text=text,
        source="llm",
        action=action,
        asset=asset.upper() if asset else None,
        max_amount=_decimal(obj.get("max_amount"), "max_amount"),
        currency=_currency(_opt_str(obj, "currency")),
        conditions=conditions,
        max_age_seconds=_int(obj.get("max_age_seconds"), "max_age_seconds", minimum=1),
        minimum_required_evidence=_int(obj.get("minimum_required_evidence"), "minimum_required_evidence"),
        conflict_policy=policy,
        conflict_tolerance=_fraction(obj.get("conflict_tolerance"), "conflict_tolerance"),
        evidence_budget_usdc=_non_negative(obj.get("evidence_budget_usdc"), "evidence_budget_usdc"),
        unparsed=[u.strip() for u in unparsed_raw if u.strip()],
    )


def _response_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise LLMUnavailable("BAD_RESPONSE", "the LLM response is not a JSON object")
    content = payload.get("content")
    if not isinstance(content, list):
        raise LLMUnavailable("BAD_RESPONSE", "the LLM response has no content")
    texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    text = "".join(t for t in texts if isinstance(t, str))
    if not text.strip():
        raise LLMUnavailable("BAD_RESPONSE", "the LLM response has no text content")
    return text


def draft_intent(text: str, settings: Settings, *, http_client: httpx.Client | None = None) -> ParsedIntent:
    """Asks the configured model for a draft. Raises :class:`LLMUnavailable` on any failure."""
    api_key = settings.anthropic_api_key
    if not api_key:
        raise LLMUnavailable("NO_API_KEY", "no Anthropic API key is configured")
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    body = request_body(text, settings)
    url = _endpoint(settings)
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=settings.llm_timeout_seconds)
    try:
        try:
            response = client.post(url, headers=headers, json=body, timeout=settings.llm_timeout_seconds)
        except httpx.HTTPError as exc:
            log.warning("intent LLM request failed: %s", type(exc).__name__)
            raise LLMUnavailable("TRANSPORT_ERROR", f"LLM request failed ({type(exc).__name__})") from None
        if response.status_code // 100 != 2:
            log.warning("intent LLM returned HTTP %s", response.status_code)
            raise LLMUnavailable(f"HTTP_{response.status_code}", f"LLM returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            raise LLMUnavailable("BAD_RESPONSE", "the LLM response is not JSON") from None
    finally:
        if owns_client:
            client.close()
    obj = extract_first_json_object(_response_text(payload))
    return draft_from_json(obj, text)


__all__ = [
    "ANTHROPIC_VERSION",
    "LLMUnavailable",
    "SYSTEM_PROMPT",
    "draft_from_json",
    "draft_intent",
    "extract_first_json_object",
    "request_body",
]
