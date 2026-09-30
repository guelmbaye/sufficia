"""Canonicalization and commitment tests (Document 06 §117-118, Document 03 §76-77)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from eth_utils import keccak

import engine_builders as b
from sufficia.canonical import canonicalize as cz
from sufficia.canonical import hashes as hs
from sufficia.canonical.canonicalize import canonical_json, format_decimal, loads
from sufficia.domain.enums import DecisionState, Operator
from sufficia.domain.models import CanonicalAction, EvidenceContract

# ---------------------------------------------------------------------------------------------
# canonical_json
# ---------------------------------------------------------------------------------------------


def test_key_order_does_not_matter():
    a = {"b": 1, "a": {"y": 2, "x": [3, {"q": 1, "p": 2}]}}
    b_ = {"a": {"x": [3, {"p": 2, "q": 1}], "y": 2}, "b": 1}
    assert canonical_json(a) == canonical_json(b_) == '{"a":{"x":[3,{"p":2,"q":1}],"y":2},"b":1}'


def test_list_order_is_significant():
    assert canonical_json([1, 2]) != canonical_json([2, 1])


def test_no_insignificant_whitespace_and_ascii_only():
    out = canonical_json({"k": "héllo wörld", "n": None, "t": True, "f": False})
    assert out == '{"f":false,"k":"h\\u00e9llo w\\u00f6rld","n":null,"t":true}'
    out.encode("ascii")  # does not raise
    assert canonical_json({"k": " x "}) == '{"k":" x "}'  # whitespace inside strings is preserved


@pytest.mark.parametrize(
    "value, token",
    [
        (Decimal("1340000"), "1340000"),
        (Decimal("1.34E+6"), "1340000"),
        (Decimal("1340000.000"), "1340000"),
        (Decimal("0.4280"), "0.428"),
        (Decimal("4.28E-1"), "0.428"),
        (Decimal("-0"), "0"),
        (Decimal("0E-10"), "0"),
        (Decimal("-250000.50"), "-250000.5"),
        (Decimal("1E-7"), "0.0000001"),
        (1340000, "1340000"),
        (-5, "-5"),
        (0.1, "0.1"),
    ],
)
def test_decimal_normalization(value, token):
    assert canonical_json(value) == token
    assert canonical_json({"v": value}) == '{"v":' + token + "}"


def test_equal_decimals_share_one_encoding():
    assert canonical_json(Decimal("718000")) == canonical_json(Decimal("718000.00")) == canonical_json(718000)


def test_number_and_numeric_string_are_distinct():
    assert canonical_json(Decimal(92440)) == "92440"
    assert canonical_json("92440") == '"92440"'
    assert canonical_json({"v": "92440"}) != canonical_json({"v": 92440})
    item_num = b.make_item("req_liquidity", Decimal(92440))
    item_str = b.make_item("req_liquidity", "92440")
    assert hs.evidence_item_hash(item_num) != hs.evidence_item_hash(item_str)


def test_bool_is_not_a_number():
    assert canonical_json(True) == "true"
    assert canonical_json(1) == "1"
    assert canonical_json({"v": True}) != canonical_json({"v": 1})


@pytest.mark.parametrize(
    "value, tag",
    [
        (Decimal("NaN"), "NaN"),
        (Decimal("sNaN"), "NaN"),
        (Decimal("Infinity"), "Infinity"),
        (Decimal("-Infinity"), "-Infinity"),
        (float("nan"), "NaN"),
        (float("inf"), "Infinity"),
        (float("-inf"), "-Infinity"),
    ],
)
def test_non_finite_numbers_are_tagged(value, tag):
    assert canonical_json(value) == '{"$nonfinite":"' + tag + '"}'


def test_nan_tag_round_trips_and_is_distinct_from_string():
    text = canonical_json({"v": Decimal("NaN")})
    back = loads(text)
    assert isinstance(back["v"], Decimal) and back["v"].is_nan()
    assert canonical_json(back) == text
    assert canonical_json({"v": "NaN"}) != text


def test_format_decimal_rejects_non_finite():
    with pytest.raises(ValueError):
        format_decimal(Decimal("NaN"))


def test_timestamps_normalize_to_utc_microseconds():
    utc = datetime(2026, 10, 5, 18, 42, 15, tzinfo=timezone.utc)
    paris = datetime(2026, 10, 5, 20, 42, 15, tzinfo=timezone(timedelta(hours=2)))
    assert canonical_json(utc) == canonical_json(paris) == '"2026-10-05T18:42:15.000000Z"'
    assert canonical_json(utc + timedelta(microseconds=1)) == '"2026-10-05T18:42:15.000001Z"'


def test_naive_timestamp_is_tagged_not_assumed_utc():
    naive = datetime(2026, 10, 5, 18, 42, 15)
    out = canonical_json(naive)
    assert out == '{"$naive":"2026-10-05T18:42:15"}'
    assert out != canonical_json(naive.replace(tzinfo=timezone.utc))
    back = loads(out)
    assert back == naive and back.tzinfo is None
    with pytest.raises(ValueError):
        cz.format_datetime(naive)


def test_strings_are_byte_exact_so_the_encoding_is_injective():
    """No case folding: two different strings never share a commitment. Address normalization happens at
    typed boundaries instead (the action hash checksums every address)."""
    checksum = "0xAbCdEf0123456789aBcDeF0123456789AbCdEf01"
    assert canonical_json(checksum) == '"' + checksum + '"'
    assert canonical_json(checksum) != canonical_json(checksum.lower())
    h32 = "0x" + "AB" * 32
    assert canonical_json(h32) == '"' + h32 + '"'


def test_enums_emit_values_and_bytes_emit_hex():
    assert canonical_json(DecisionState.AUTHORIZE) == '"AUTHORIZE"'
    assert canonical_json(Operator.GTE) == '">="'
    assert canonical_json(b"\x01\xff") == '"0x01ff"'


def test_unsupported_types_raise():
    with pytest.raises(TypeError):
        canonical_json({1, 2})
    with pytest.raises(TypeError):
        canonical_json(object())


def test_loads_round_trip_is_stable_for_models():
    contract = b.make_contract()
    items = b.good_items()
    evs = b.make_set(items)
    for obj in (contract, evs, b.decide(items)):
        text = canonical_json(obj)
        once = loads(text)
        assert canonical_json(once) == text
        assert canonical_json(loads(canonical_json(once))) == text


def test_loads_returns_exact_numbers():
    back = loads('{"a":1340000,"b":0.428,"c":-0.5}')
    assert back == {"a": 1340000, "b": Decimal("0.428"), "c": Decimal("-0.5")}
    assert isinstance(back["a"], int) and isinstance(back["b"], Decimal)


def test_model_round_trip_preserves_contract_and_evidence_hashes():
    contract = b.make_contract()
    items = b.good_items()
    contract2 = EvidenceContract.model_validate(loads(canonical_json(contract)))
    assert hs.contract_hash(contract2) == hs.contract_hash(contract)
    for it in items:
        it2 = type(it).model_validate(loads(canonical_json(it)))
        assert hs.evidence_item_hash(it2) == hs.evidence_item_hash(it)


def test_dumps_pretty_is_human_readable_json():
    out = cz.dumps_pretty({"v": Decimal("1.50"), "t": b.T0})
    assert '"v": "1.5"' in out and "2026-10-05T18:42:15.000000Z" in out


# ---------------------------------------------------------------------------------------------
# domain separation
# ---------------------------------------------------------------------------------------------

OFFCHAIN_DOMAINS = [
    hs.CONTRACT_DOMAIN,
    hs.EVIDENCE_DOMAIN,
    hs.EVIDENCE_SET_DOMAIN,
    hs.INTENT_DOMAIN,
    hs.DECISION_DOMAIN,
    hs.RECEIPT_DOMAIN,
    hs.NONCE_DOMAIN,
    hs.AUDIT_DOMAIN,
    hs.REQUEST_DOMAIN,
    hs.RESPONSE_DOMAIN,
]


def test_same_payload_under_different_domains_differs():
    payload = {"threshold": 500000, "claim": "available_liquidity"}
    digests = {hs.domain_hash(d, payload) for d in OFFCHAIN_DOMAINS}
    assert len(digests) == len(OFFCHAIN_DOMAINS)
    raw = canonical_json(payload).encode()
    assert len({hs.raw_hash(d, raw) for d in OFFCHAIN_DOMAINS}) == len(OFFCHAIN_DOMAINS)
    assert hs.domain_hash(hs.CONTRACT_DOMAIN, payload) != "0x" + keccak(raw).hex()


def test_contract_domain_vs_action_domain():
    contract = b.make_contract()
    contract_digest = hs.contract_hash(contract)
    as_action_domain = "0x" + keccak(hs.ACTION_DOMAIN_ONCHAIN + b"\x00" + cz.canonical_bytes(hs.contract_semantics(contract))).hex()
    assert contract_digest != as_action_domain
    assert hs.ACTION_DOMAIN_ONCHAIN != hs.AUTH_DOMAIN_ONCHAIN


def test_receipt_intent_and_item_hash_helpers_are_domain_bound():
    obj = {"x": 1}
    assert hs.receipt_hash(obj) == hs.domain_hash(hs.RECEIPT_DOMAIN, obj)
    assert hs.intent_hash(obj) == hs.domain_hash(hs.INTENT_DOMAIN, obj)
    assert hs.receipt_hash(obj) != hs.intent_hash(obj)
    assert hs.onchain_intent_id(hs.intent_hash(obj)) == hs.intent_hash(obj)


def test_unhex32_validates_length():
    assert len(hs.unhex32("0x" + "00" * 32)) == 32
    with pytest.raises(ValueError):
        hs.unhex32("0xdeadbeef")
    assert hs.unhex("0x") == b"" and hs.unhex("") == b""


# ---------------------------------------------------------------------------------------------
# evidence_root
# ---------------------------------------------------------------------------------------------


def test_evidence_root_is_order_independent_and_case_insensitive():
    items = b.good_items()
    hashes = [hs.evidence_item_hash(i) for i in items]
    root = hs.evidence_root(hashes)
    assert hs.evidence_root(reversed(hashes)) == root
    assert hs.evidence_root([h.upper().replace("0X", "0x") for h in hashes]) == root
    assert hs.evidence_root(hashes + hashes[:1]) == root  # duplicates collapse (set commitment)


def test_evidence_root_distinguishes_sets_and_sizes():
    items = b.good_items()
    hashes = [hs.evidence_item_hash(i) for i in items]
    assert hs.evidence_root(hashes) != hs.evidence_root(hashes[:2])
    assert hs.evidence_root([]) != hs.evidence_root(hashes[:1])


def test_decision_evidence_root_ignores_input_order():
    items = b.good_items()
    d1 = b.decide(items)
    d2 = b.decide(list(reversed(items)))
    assert d1.evidence_root == d2.evidence_root
    assert d1.fingerprint == d2.fingerprint


def test_any_item_mutation_changes_evidence_root():
    base = b.decide().evidence_root
    mutations = [
        {"value": Decimal("718001")},
        {"age": 6},
        {"provider": "other-provider"},
        {"response_hash": "0x" + "c3" * 32},
        {"cost": "0.01"},
        {"evidence_id": "ev_other"},
    ]
    for m in mutations:
        items = b.good_items(req_liquidity=m)
        assert b.decide(items).evidence_root != base, m


# ---------------------------------------------------------------------------------------------
# contract_hash sensitivity
# ---------------------------------------------------------------------------------------------


def _hash(**overrides):
    return hs.contract_hash(b.make_contract(**overrides))


BASE_HASH = None


def test_contract_hash_ignores_identifiers_label_and_requirement_order():
    base = b.make_contract()
    h = hs.contract_hash(base)
    assert _hash(contract_id="another") == h
    assert _hash(intent_id="another") == h
    assert hs.contract_hash(b.with_requirement(base, "req_liquidity", label="Pool depth")) == h
    d = base.model_dump(mode="python")
    d["requirements"] = list(reversed(d["requirements"]))
    assert hs.contract_hash(EvidenceContract.model_validate(d)) == h


def test_contract_hash_ignores_in_value_order():
    c1 = b.make_contract(requirements=[{"id": "r", "claim": "venue_id", "operator": "IN", "values": ["a", "b"], "unit": "ID"}])
    c2 = b.make_contract(requirements=[{"id": "r", "claim": "venue_id", "operator": "IN", "values": ["b", "a"], "unit": "ID"}])
    assert hs.contract_hash(c1) == hs.contract_hash(c2)


@pytest.mark.parametrize(
    "rid, change",
    [
        ("req_liquidity", {"threshold": Decimal(500_001)}),
        ("req_liquidity", {"threshold": Decimal(50_000)}),
        ("req_liquidity", {"operator": Operator.GTE}),
        ("req_liquidity", {"max_age_seconds": 31}),
        ("req_liquidity", {"required": False}),
        ("req_liquidity", {"minimum_items": 2}),
        ("req_liquidity", {"conflict_tolerance": Decimal("0.2")}),
        ("req_liquidity", {"conflict_policy": "ABSTAIN"}),
        ("req_liquidity", {"id": "req_liq"}),
        ("req_inflow", {"threshold": Decimal(999_999)}),
        ("req_price", {"max_age_seconds": 60}),
    ],
)
def test_contract_hash_changes_with_requirement_semantics(rid, change):
    base = b.make_contract()
    assert hs.contract_hash(b.with_requirement(base, rid, **change)) != hs.contract_hash(base)


@pytest.mark.parametrize(
    "overrides",
    [
        {"authorization": {"ttl_seconds": 31}},
        {"policy": {"minimum_required_evidence": 2}},
        {"policy": {"minimum_required_evidence": 3, "missing_required": "DENY"}},
        {"policy": {"minimum_required_evidence": 3, "clock_skew_seconds": 5}},
        {"economic_limits": {"max_execution_value_usd": Decimal(100), "max_evidence_cost_usd": Decimal("0.30")}},
        {"economic_limits": {"max_execution_value_usd": Decimal(200), "max_evidence_cost_usd": Decimal("0.25")}},
        {"action": {"type": "BUY", "asset": "TOKEN-Y", "max_amount": {"value": Decimal(100), "currency": "USDC"}}},
        {"action": {"type": "BUY", "asset": "TOKEN-X", "max_amount": {"value": Decimal(99), "currency": "USDC"}}},
        {"action": {"type": "SELL", "asset": "TOKEN-X", "max_amount": {"value": Decimal(100), "currency": "USDC"}}},
        {"policy_version": 2},
        {"schema_version": 2},
    ],
)
def test_contract_hash_changes_with_policy_limits_ttl_and_action(overrides):
    assert _hash(**overrides) != _hash()


def test_contract_hash_equal_for_equivalent_numeric_forms():
    base = b.make_contract()
    same = b.with_requirement(base, "req_liquidity", threshold=Decimal("500000.00"))
    assert hs.contract_hash(same) == hs.contract_hash(base)


# ---------------------------------------------------------------------------------------------
# action_hash / authorization_id / nonce determinism
# ---------------------------------------------------------------------------------------------


def _action(**overrides):
    return CanonicalAction.model_validate(b.action_dict(**overrides))


def test_action_hash_is_deterministic_and_address_case_insensitive():
    a = _action()
    assert hs.canonical_action_hash(a) == hs.canonical_action_hash(_action())
    upper = _action(target="0x2222222222222222222222222222222222222222".upper().replace("0X", "0x"),
                    recipient="0x5555555555555555555555555555555555555555")
    assert hs.canonical_action_hash(upper) == hs.canonical_action_hash(a)


def test_action_hash_matches_manual_abi_encoding():
    from eth_abi import encode

    a = _action()
    expected = keccak(
        encode(
            hs.ACTION_HASH_TYPES,
            [
                keccak(text="EVIDENCE_GATE_ACTION_V1"), a.chain_id, a.gate_address, a.target, a.value, keccak(b""),
                a.asset_in, a.max_amount_in, a.asset_out, a.min_amount_out, a.recipient, a.deadline, bytes(32),
            ],
        )
    )
    assert hs.canonical_action_hash(a) == "0x" + expected.hex()


def test_action_hash_ignores_presentation_fields():
    a = _action()
    assert hs.canonical_action_hash(_action(description="different text", adapter="x")) == hs.canonical_action_hash(a)


def test_authorization_id_and_nonce_are_deterministic_and_bound():
    ch, er, ah, nonce = "0x" + "11" * 32, "0x" + "22" * 32, "0x" + "33" * 32, "0x" + "44" * 32
    aid = hs.authorization_id(ch, er, ah, b.EXECUTOR, nonce)
    assert aid == hs.authorization_id(ch, er, ah, b.EXECUTOR.upper().replace("0X", "0x"), nonce)
    variants = [
        ("0x" + "12" * 32, er, ah, b.EXECUTOR, nonce),
        (ch, "0x" + "23" * 32, ah, b.EXECUTOR, nonce),
        (ch, er, "0x" + "34" * 32, b.EXECUTOR, nonce),
        (ch, er, ah, "0x" + "67" * 20, nonce),
        (ch, er, ah, b.EXECUTOR, "0x" + "45" * 32),
    ]
    assert len({hs.authorization_id(*v) for v in variants} | {aid}) == len(variants) + 1
    fp = "0x" + "99" * 32
    assert hs.authorization_nonce(fp) == hs.authorization_nonce(fp)
    assert hs.authorization_nonce(fp) != hs.authorization_nonce("0x" + "98" * 32)


def test_decision_fingerprint_binds_every_input():
    ch, er = "0x" + "11" * 32, "0x" + "22" * 32
    ic = hs.decision_inputs_commitment([], None)
    t = "2026-10-05T18:42:15.000000Z"
    base = hs.decision_fingerprint(ch, er, t, "1.0.0", ic)
    assert base == hs.decision_fingerprint(ch, er, t, "1.0.0", ic)
    assert base != hs.decision_fingerprint("0x" + "12" * 32, er, t, "1.0.0", ic)
    assert base != hs.decision_fingerprint(ch, "0x" + "23" * 32, t, "1.0.0", ic)
    assert base != hs.decision_fingerprint(ch, er, "2026-10-05T18:42:15.000001Z", "1.0.0", ic)
    assert base != hs.decision_fingerprint(ch, er, t, "1.0.1", ic)
    assert base != hs.decision_fingerprint(ch, er, t, "1.0.0", hs.decision_inputs_commitment([], {}))
