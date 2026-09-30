from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sufficia.canonical.canonicalize import canonical_bytes
from sufficia.canonical.hashes import REQUEST_DOMAIN, RESPONSE_DOMAIN, raw_hash
from sufficia.decision_engine import evaluate
from sufficia.domain.enums import DecisionState, EvidenceStatus, Operator, PaymentStatus, SourceMode
from sufficia.domain.models import EvidencePayment, EvidenceSet, Requirement
from sufficia.domain.reasons import ReasonCode
from sufficia.evidence.normalizer import make_evidence_id, normalize, parse_numeric

LIQ = Requirement(id="r_liq", claim="available_liquidity", operator=Operator.GTE, threshold=Decimal("500000"))


def _norm(h, **raw_kw):
    raw = h.raw_evidence(**raw_kw)
    return raw, normalize(raw, requirement=LIQ, contract_id="ctr_test", evidence_id=None, acquisition_id="acq_1")


def test_hashes_are_domain_separated_over_descriptor_and_exact_bytes(h):
    raw, item = _norm(h, response_body=b'{"liquidity": 600000}', request_descriptor={"url": "https://x.test", "method": "GET"})
    assert item.integrity.request_hash == raw_hash(REQUEST_DOMAIN, canonical_bytes(raw.request_descriptor))
    assert item.integrity.response_hash == raw_hash(RESPONSE_DOMAIN, b'{"liquidity": 600000}')
    # key order in the descriptor does not matter (canonical JSON), a byte of the body does
    _, same = _norm(h, response_body=b'{"liquidity": 600000}', request_descriptor={"method": "GET", "url": "https://x.test"})
    _, other = _norm(h, response_body=b'{"liquidity": 600001}', request_descriptor={"method": "GET", "url": "https://x.test"})
    assert same.integrity.request_hash == item.integrity.request_hash
    assert other.integrity.response_hash != item.integrity.response_hash


def test_evidence_id_is_deterministic_from_acquisition_and_response(h):
    raw, item = _norm(h)
    assert item.evidence_id.startswith("ev_") and len(item.evidence_id) == 3 + 16
    int(item.evidence_id[3:], 16)
    assert item.evidence_id == make_evidence_id("acq_1", item.integrity.response_hash)
    again = normalize(raw, requirement=LIQ, contract_id="ctr_test", evidence_id=None, acquisition_id="acq_1")
    other_acq = normalize(raw, requirement=LIQ, contract_id="ctr_test", evidence_id=None, acquisition_id="acq_2")
    assert again.evidence_id == item.evidence_id
    assert other_acq.evidence_id != item.evidence_id
    explicit = normalize(raw, requirement=LIQ, contract_id="ctr_test", evidence_id="ev_explicit", acquisition_id="acq_1")
    assert explicit.evidence_id == "ev_explicit"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("92440", Decimal("92440")),
        ("0.428", Decimal("0.428")),
        ("-1340000.50", Decimal("-1340000.50")),
        (92440, 92440),
        (Decimal("1.5"), Decimal("1.5")),
        (0.1, Decimal("0.1")),  # repr, not binary expansion
        (1e20, Decimal("1E+20")),
    ],
)
def test_numeric_parsing(value, expected):
    out = parse_numeric(value)
    assert out == expected
    assert type(out) is type(expected)


@pytest.mark.parametrize("value", ["1,340,000", "1e6", "N/A", "", " 12", "12 ", "+5", "0x10", "١٢٣", "12.", ".5", "123\n", None, True, {"usd": 1}, [1]])
def test_malformed_values_are_kept_raw(value):
    assert parse_numeric(value) is value


def test_float_nan_becomes_non_finite_decimal():
    assert parse_numeric(float("nan")).is_nan()
    assert parse_numeric(float("inf")) == Decimal("Infinity")


@given(st.decimals(allow_nan=False, allow_infinity=False, places=6, min_value=-10**12, max_value=10**12))
def test_numeric_strings_round_trip(d):
    text = format(d, "f")
    if "E" in text or "e" in text:
        return
    assert parse_numeric(text) == d


def test_normalized_item_keeps_provenance_payment_and_cost(h):
    payment = EvidencePayment(network="eip155:10143", scheme="exact", quoted_amount=Decimal("0.02"), status=PaymentStatus.SIMULATED)
    cap = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _, item = _norm(h, mode=SourceMode.RECORDED, captured_at=cap, cost=Decimal("0.02"), payment=payment, upstream_source="u", network="monad")
    assert item.source.mode == SourceMode.RECORDED
    assert item.source.captured_at == cap
    assert item.economic.cost == Decimal("0.02")
    assert item.economic.payment == payment
    assert item.acquisition_id == "acq_1"
    assert item.claim.unit == "USD"


def test_malformed_timestamp_string_is_not_coerced(h):
    _, item = _norm(h, observed_at="1700000000")
    assert item.temporal.observed_at == "1700000000"


def test_malformed_value_reaches_engine_as_invalid_never_pass(h):
    """'1,340,000' must stay visible and be INVALID; the requirement is then missing -> ABSTAIN."""
    contract = h.make_contract(LIQ)
    _, item = _norm(h, value="1,340,000")
    assert item.claim.value == "1,340,000"
    decision = evaluate(contract, EvidenceSet(contract_id=contract.contract_id, items=[item]), datetime.now(timezone.utc) + timedelta(seconds=1))
    assert decision.state == DecisionState.ABSTAIN
    assessment = decision.evidence_assessments[0]
    assert assessment.status == EvidenceStatus.INVALID
    assert ReasonCode.INVALID_EVIDENCE_TYPE in assessment.reason_codes


def test_numeric_string_value_is_usable_by_engine(h):
    contract = h.make_contract(LIQ)
    _, item = _norm(h, value="600000.25")
    decision = evaluate(contract, EvidenceSet(contract_id=contract.contract_id, items=[item]), datetime.now(timezone.utc) + timedelta(seconds=1))
    assert decision.state == DecisionState.AUTHORIZE
