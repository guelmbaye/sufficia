"""LLM draft client (Anthropic Messages API over plain httpx, mocked with httpx.MockTransport)."""

from __future__ import annotations

import json
import logging
from decimal import Decimal

import httpx
import pytest

from intent_helpers import API_KEY, GRAND_PRIZE, GRAND_PRIZE_DRAFT, LLM_BASE_URL, MockAnthropic, cond, draft_json, forbidden_client, make_settings
from sufficia.domain.claims import CLAIMS
from sufficia.domain.enums import ActionType, ConflictPolicy, Operator
from sufficia.intent import LLMUnavailable, draft_intent
from sufficia.intent.llm import ANTHROPIC_VERSION, SYSTEM_PROMPT, extract_first_json_object


def test_request_shape(llm_settings):
    mock = MockAnthropic(reply=GRAND_PRIZE_DRAFT)
    draft_intent(GRAND_PRIZE, llm_settings, http_client=mock.client())
    assert mock.calls == 1
    req = mock.requests[0]
    assert req.method == "POST"
    assert str(req.url) == f"{LLM_BASE_URL}/v1/messages"
    assert req.headers["x-api-key"] == API_KEY
    assert req.headers["anthropic-version"] == ANTHROPIC_VERSION == "2023-06-01"
    assert req.headers["content-type"] == "application/json"
    body = mock.body()
    assert {"model", "max_tokens", "system", "messages"} <= set(body)
    assert body["model"] == "claude-test-model"
    assert body["messages"] == [{"role": "user", "content": f"Instruction:\n{GRAND_PRIZE}"}]
    assert body["system"] == SYSTEM_PROMPT


def test_base_url_trailing_slash(llm_settings):
    mock = MockAnthropic(reply=GRAND_PRIZE_DRAFT)
    s = make_settings(anthropic_api_key=API_KEY, anthropic_base_url=LLM_BASE_URL + "/")
    draft_intent(GRAND_PRIZE, s, http_client=mock.client())
    assert str(mock.requests[0].url) == f"{LLM_BASE_URL}/v1/messages"


def test_system_prompt_documents_schema_and_only_known_claims():
    for name in CLAIMS:
        assert f'"{name}"' in SYSTEM_PROMPT
    for key in ("action", "asset", "max_amount", "currency", "conditions", "claim", "operator", "threshold",
                "max_age_seconds", "minimum_required_evidence", "unparsed"):
        assert f'"{key}"' in SYSTEM_PROMPT
    assert "STRICT JSON" in SYSTEM_PROMPT


def test_valid_draft_is_parsed(llm_settings):
    mock = MockAnthropic(reply=GRAND_PRIZE_DRAFT)
    d = draft_intent(GRAND_PRIZE, llm_settings, http_client=mock.client())
    assert (d.source, d.action, d.asset, d.max_amount, d.currency) == ("llm", ActionType.BUY, "TOKEN-X", Decimal("100"), "USDC")
    assert [(c.claim, c.operator, c.threshold) for c in d.conditions] == [
        ("smart_money_inflow_24h", Operator.GT, Decimal("1000000")),
        ("available_liquidity", Operator.GT, Decimal("500000")),
    ]


def test_numbers_and_policy_fields(llm_settings):
    body = draft_json(
        max_amount=100,
        currency="usd",
        conditions=[{"claim": "asset_price", "operator": "<", "threshold": 0.1, "max_age_seconds": 15}, {"claim": "asset_price", "operator": "exists"}],
        max_age_seconds=20,
        minimum_required_evidence=2,
        conflict_policy="abstain",
        conflict_tolerance="0.05",
        evidence_budget_usdc=0.1,
        unparsed=["  whales are accumulating "],
    )
    d = draft_intent("x", llm_settings, http_client=MockAnthropic(reply=body).client())
    assert d.currency == "USDC"
    assert str(d.conditions[0].threshold) == "0.1"  # exact decimal, not a float artefact
    assert d.conditions[0].max_age_seconds == 15
    assert d.conditions[1].operator == Operator.EXISTS and d.conditions[1].threshold is None
    assert (d.max_age_seconds, d.minimum_required_evidence, d.conflict_policy) == (20, 2, ConflictPolicy.ABSTAIN)
    assert (d.conflict_tolerance, d.evidence_budget_usdc) == (Decimal("0.05"), Decimal("0.1"))
    assert d.unparsed == ["whales are accumulating"]


@pytest.mark.parametrize(
    "text",
    [
        "Here is the draft:\n```json\n" + json.dumps(GRAND_PRIZE_DRAFT) + "\n```\nDone.",
        "{not json} then " + json.dumps(GRAND_PRIZE_DRAFT) + " and {\"second\": 1}",
        json.dumps(GRAND_PRIZE_DRAFT, indent=2),
    ],
)
def test_first_json_object_is_extracted(text, llm_settings):
    d = draft_intent(GRAND_PRIZE, llm_settings, http_client=MockAnthropic(reply=text).client())
    assert len(d.conditions) == 2


def test_extract_first_json_object_ignores_arrays():
    assert extract_first_json_object('[1, 2] {"a": 1}') == {"a": 1}


def test_no_api_key_makes_no_request():
    with pytest.raises(LLMUnavailable) as exc:
        draft_intent(GRAND_PRIZE, make_settings(anthropic_api_key=None), http_client=forbidden_client())
    assert exc.value.code == "NO_API_KEY"


@pytest.mark.parametrize(
    "mock,code",
    [
        (MockAnthropic(status=500, reply=GRAND_PRIZE_DRAFT), "HTTP_500"),
        (MockAnthropic(status=429, reply=GRAND_PRIZE_DRAFT), "HTTP_429"),
        (MockAnthropic(status=401, raw_body=b'{"error": {"type": "authentication_error"}}'), "HTTP_401"),
        (MockAnthropic(reply="I cannot help with that."), "NO_JSON"),
        (MockAnthropic(reply="{\"action\": \"BUY\", "), "NO_JSON"),
        (MockAnthropic(raw_body=b"<html>gateway</html>"), "BAD_RESPONSE"),
        (MockAnthropic(raw_body=b'{"content": []}'), "BAD_RESPONSE"),
        (MockAnthropic(raw_body=b'{"type": "message"}'), "BAD_RESPONSE"),
        (MockAnthropic(raise_exc=httpx.ConnectError("boom")), "TRANSPORT_ERROR"),
        (MockAnthropic(raise_exc=httpx.ReadTimeout("slow")), "TRANSPORT_ERROR"),
    ],
)
def test_failures_raise_llm_unavailable(mock, code, llm_settings):
    with pytest.raises(LLMUnavailable) as exc:
        draft_intent(GRAND_PRIZE, llm_settings, http_client=mock.client())
    assert exc.value.code == code
    assert API_KEY not in str(exc.value)


@pytest.mark.parametrize(
    "body",
    [
        draft_json(conditions=[cond("whale_accumulation", ">", 0)]),  # unknown claim
        draft_json(conditions=[cond("available_liquidity", "~", 1)]),  # unknown operator
        draft_json(conditions=[cond("available_liquidity", ">")]),  # missing threshold
        draft_json(conditions=[cond("asset_price", "IN", 1)]),  # operator not allowed for the claim
        draft_json(conditions=[cond("available_liquidity", ">", "lots")]),  # not a decimal
        draft_json(conditions=[cond("available_liquidity", ">", "NaN")]),  # not finite
        draft_json(conditions={"claim": "available_liquidity"}),  # not a list
        draft_json(conditions=["liquidity > 500K"]),  # not an object
        draft_json(action="HODL"),
        draft_json(max_amount=True),
        draft_json(conflict_policy="IGNORE"),
        draft_json(conflict_tolerance="2"),
        draft_json(unparsed="nothing"),
        draft_json(minimum_required_evidence=1.5),
        draft_json(conditions=[{**cond("available_liquidity", ">", 1), "max_age_seconds": 0}]),
    ],
)
def test_schema_violations_are_rejected(body, llm_settings):
    with pytest.raises(LLMUnavailable) as exc:
        draft_intent(GRAND_PRIZE, llm_settings, http_client=MockAnthropic(reply=body).client())
    assert exc.value.code == "INVALID_DRAFT"


def test_api_key_is_never_logged(llm_settings, caplog):
    caplog.set_level(logging.DEBUG)
    for mock in (MockAnthropic(reply=GRAND_PRIZE_DRAFT), MockAnthropic(status=500), MockAnthropic(raise_exc=httpx.ConnectError("x"))):
        try:
            draft_intent(GRAND_PRIZE, llm_settings, http_client=mock.client())
        except LLMUnavailable:
            pass
    assert API_KEY not in caplog.text


def test_client_owned_when_not_provided(monkeypatch, llm_settings):
    """Without an injected client a fresh httpx.Client is created (and closed) — mocked here."""
    mock = MockAnthropic(reply=GRAND_PRIZE_DRAFT)
    real = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(mock.handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    d = draft_intent(GRAND_PRIZE, llm_settings)
    assert mock.calls == 1 and len(d.conditions) == 2
