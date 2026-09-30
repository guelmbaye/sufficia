"""Shared helpers for the intent compiler tests (no network: Anthropic is mocked with httpx.MockTransport)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

import httpx

from sufficia.config import Settings
from sufficia.domain.models import Intent, Money

GRAND_PRIZE = "Buy up to 100 USDC of TOKEN-X only if smart-money inflows exceed $1M and liquidity exceeds $500K."
GRAND_PRIZE_FR = (
    "Achète jusqu'à 100 USDC de TOKEN-X seulement si les entrées smart money dépassent 1M$ et la liquidité dépasse 500K$"
)
LLM_BASE_URL = "https://llm.test"
API_KEY = "sk-test-SECRET-KEY-123"


def make_settings(**overrides: Any) -> Settings:
    """Settings independent of the process environment / .env (every field the compiler reads is explicit)."""
    values: dict[str, Any] = {
        "_env_file": None,
        "intent_compiler": "deterministic",
        "anthropic_api_key": None,
        "anthropic_base_url": LLM_BASE_URL,
        "llm_model": "claude-test-model",
        "llm_timeout_seconds": 5.0,
        "default_max_age_seconds": 30,
        "default_authorization_ttl_seconds": 30,
        "default_evidence_budget_usdc": Decimal("0.25"),
        "default_conflict_tolerance": Decimal("0.10"),
        "default_minimum_required_evidence": 3,
        "max_protected_amount_usdc": Decimal("1000"),
    }
    values.update(overrides)
    return Settings(**values)


def make_intent(text: str, *, intent_id: str = "intent_test_001", **kwargs: Any) -> Intent:
    return Intent(intent_id=intent_id, text=text, created_at=datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc), **kwargs)


def usdc(value: str) -> Money:
    return Money(value=Decimal(value), currency="USDC")


def cond(claim: str, operator: str, threshold: Any = None, max_age_seconds: int | None = None) -> dict[str, Any]:
    return {
        "claim": claim,
        "operator": operator,
        "threshold": None if threshold is None else str(threshold),
        "max_age_seconds": max_age_seconds,
    }


def draft_json(
    *,
    action: str | None = "BUY",
    asset: str | None = "TOKEN-X",
    max_amount: Any = "100",
    currency: str | None = "USDC",
    conditions: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "action": action,
        "asset": asset,
        "max_amount": max_amount,
        "currency": currency,
        "conditions": conditions if conditions is not None else [],
        "max_age_seconds": None,
        "minimum_required_evidence": None,
        "conflict_policy": None,
        "unparsed": [],
    }
    body.update(extra)
    return body


GRAND_PRIZE_DRAFT = draft_json(
    conditions=[cond("smart_money_inflow_24h", ">", 1000000), cond("available_liquidity", ">", 500000)]
)


def anthropic_message(text: str) -> dict[str, Any]:
    return {
        "id": "msg_test_01",
        "type": "message",
        "role": "assistant",
        "model": "claude-test-model",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


@dataclass
class MockAnthropic:
    """Records requests and replies with a canned Messages API response (or raises)."""

    reply: Any = None  # dict -> JSON object in a text block; str -> raw text block
    status: int = 200
    raw_body: bytes | None = None  # overrides the whole HTTP body
    raise_exc: Exception | None = None
    requests: list[httpx.Request] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.raw_body is not None:
            return httpx.Response(self.status, content=self.raw_body, headers={"content-type": "application/json"})
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return httpx.Response(self.status, json=anthropic_message(text))

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    @property
    def calls(self) -> int:
        return len(self.requests)

    def body(self, index: int = -1) -> dict[str, Any]:
        return json.loads(self.requests[index].content)


def forbidden_client() -> httpx.Client:
    """A client that fails the test if any request is made."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not be called
        raise AssertionError(f"unexpected HTTP request to {request.url}")

    return httpx.Client(transport=httpx.MockTransport(handler))


def conditions_of(parsed: Any) -> list[tuple[str, str, str | None, int | None]]:
    return [
        (c.claim, c.operator.value, None if c.threshold is None else str(c.threshold), c.max_age_seconds)
        for c in parsed.conditions
    ]


def requirement_view(contract: Any) -> list[tuple[str, str, str, str | None, int, str]]:
    return [
        (r.id, r.claim, r.operator.value, None if r.threshold is None else str(r.threshold), r.max_age_seconds, r.origin)
        for r in contract.requirements
    ]


Handler = Callable[[httpx.Request], httpx.Response]
