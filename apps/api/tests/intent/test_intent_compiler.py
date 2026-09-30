"""compile_intent: modes (deterministic / llm / auto), LLM fallback on relaxation or failure, the
override path, structured limits, the human summary and determinism of contract_hash."""

from __future__ import annotations

import json
import logging
from decimal import Decimal

import httpx
import pytest

from intent_helpers import (
    API_KEY,
    GRAND_PRIZE,
    GRAND_PRIZE_DRAFT,
    GRAND_PRIZE_FR,
    MockAnthropic,
    cond,
    draft_json,
    forbidden_client,
    make_intent,
    make_settings,
    requirement_view,
    usdc,
)
from sufficia.canonical.hashes import contract_hash
from sufficia.decision_engine.validator import validate_contract
from sufficia.domain.enums import CompilerKind
from sufficia.domain.models import CompiledContract
from sufficia.intent import IntentCompilationError, compile_intent, resolve_mode

IN, LIQ, PRICE = "smart_money_inflow_24h", "available_liquidity", "asset_price"


def compile_text(text, settings, **kw):
    return compile_intent(make_intent(text), contract_id=kw.pop("contract_id", "contract_001"), settings=settings, **kw)


# ---- deterministic ----------------------------------------------------------------------------------------------------


def test_deterministic_grand_prize(settings):
    cc = compile_text(GRAND_PRIZE, settings, mode="deterministic", http_client=forbidden_client())
    assert isinstance(cc, CompiledContract)
    assert cc.compiler == CompilerKind.DETERMINISTIC
    validate_contract(cc.contract)
    assert cc.contract_hash == contract_hash(cc.contract)
    assert cc.contract_hash.startswith("0x") and len(cc.contract_hash) == 66
    assert requirement_view(cc.contract) == [
        ("req_inflow", IN, ">", "1000000", 30, "USER"),
        ("req_liquidity", LIQ, ">", "500000", 30, "USER"),
        ("req_price", PRICE, "EXISTS", None, 30, "IMPLICIT"),
    ]
    p = cc.preservation
    assert p["status"] == "PRESERVED" and p["accepted"] is True and p["fallback"] is False
    assert p["compiler"] == "DETERMINISTIC" and p["mode"] == "deterministic"
    assert p["intent_preserved"] is True
    json.dumps(p)  # JSON-serializable for the API


def test_summary_what_must_be_true(settings):
    s = compile_text(GRAND_PRIZE, settings).summary
    assert s["action"] == "BUY TOKEN-X"
    assert s["maximum"] == "100 USDC"
    assert [{k: r[k] for k in ("id", "label", "expression", "origin", "max_age_seconds")} for r in s["requirements"]] == [
        {"id": "req_inflow", "label": "Smart-money inflow (24h)", "expression": "> $1,000,000", "origin": "USER", "max_age_seconds": 30},
        {"id": "req_liquidity", "label": "Available liquidity", "expression": "> $500,000", "origin": "USER", "max_age_seconds": 30},
        {"id": "req_price", "label": "Price", "expression": "EXISTS", "origin": "IMPLICIT", "max_age_seconds": 30},
    ]
    assert "slippage" in s["requirements"][2]["note"]
    assert s["time"] == "Maximum evidence age 30 seconds"
    assert s["authority"] == "Authorization expires when supporting evidence expires (max 30 s)"
    assert s["evidence_budget"] == "0.25 USDC"
    assert s["intent_preserved"] is True
    assert s["mandatory_requirements"] == 3
    assert s["unparsed"] == []
    json.dumps(s)


def test_summary_with_mixed_ages_and_unparsed(settings):
    s = compile_text(
        "Buy up to 100 USDC of TOKEN-X if inflows exceed $1M (max age 10s) and liquidity exceeds $500K and the moon is full",
        settings,
    ).summary
    assert s["time"] == "Maximum evidence age 10 to 30 seconds (per requirement)"
    assert s["authority"] == "Authorization expires when supporting evidence expires (max 10 s)"
    assert s["unparsed"] == ["moon is full"]
    assert s["intent_preserved"] is False  # something in the instruction is not represented


def test_same_text_same_hash_and_ids_do_not_matter(settings):
    a = compile_text(GRAND_PRIZE, settings, contract_id="c1")
    b = compile_intent(make_intent(GRAND_PRIZE, intent_id="other"), contract_id="c2", settings=settings)
    assert a.contract_hash == b.contract_hash
    assert a.contract.model_dump() != b.contract.model_dump()  # ids differ, semantics do not


def test_english_and_french_grand_prize_share_contract_hash(settings):
    assert compile_text(GRAND_PRIZE, settings).contract_hash == compile_text(GRAND_PRIZE_FR, settings).contract_hash


@pytest.mark.parametrize(
    "variant",
    [
        GRAND_PRIZE.replace("$500K", "$400K"),
        GRAND_PRIZE.replace("exceeds $500K", "is at least $500K"),
        GRAND_PRIZE.replace("100 USDC", "90 USDC"),
        GRAND_PRIZE.replace("TOKEN-X", "WETH"),
        GRAND_PRIZE + " Evidence under 20 seconds.",
    ],
)
def test_any_semantic_change_changes_hash(variant, settings):
    assert compile_text(variant, settings).contract_hash != compile_text(GRAND_PRIZE, settings).contract_hash


def test_compilation_is_deterministic_across_runs(settings):
    hashes = {compile_text(GRAND_PRIZE, settings).contract_hash for _ in range(5)}
    assert len(hashes) == 1


def test_missing_parts_raise_helpful_error(settings):
    with pytest.raises(IntentCompilationError) as exc:
        compile_text("whenever liquidity exceeds $500K", settings)
    msg = str(exc.value)
    assert exc.value.code == "INTENT_INCOMPLETE"
    assert "action" in msg and "asset" in msg and "maximum amount" in msg
    assert exc.value.missing
    with pytest.raises(IntentCompilationError) as exc2:
        compile_text("   ", settings)
    assert exc2.value.code == "EMPTY_INTENT"


def test_amount_cap_raises_before_any_llm_call(llm_settings):
    with pytest.raises(IntentCompilationError) as exc:
        compile_text("Buy up to 5000 USDC of TOKEN-X if liquidity exceeds $500K", llm_settings, http_client=forbidden_client())
    assert exc.value.code == "AMOUNT_CAP_EXCEEDED"


def test_unknown_mode(settings):
    with pytest.raises(ValueError):
        compile_text(GRAND_PRIZE, settings, mode="magic")
    with pytest.raises(ValueError):
        compile_text(GRAND_PRIZE, settings, mode="override")


# ---- mode resolution -----------------------------------------------------------------------------------------------


def test_resolve_mode():
    assert resolve_mode("auto", make_settings()) == "deterministic"
    assert resolve_mode("auto", make_settings(anthropic_api_key="k")) == "llm"
    assert resolve_mode(None, make_settings(intent_compiler="llm")) == "llm"
    assert resolve_mode(" Deterministic ", make_settings(anthropic_api_key="k")) == "deterministic"


def test_auto_without_key_is_deterministic_and_makes_no_request(settings):
    cc = compile_text(GRAND_PRIZE, make_settings(intent_compiler="auto"), http_client=forbidden_client())
    assert cc.compiler == CompilerKind.DETERMINISTIC
    assert cc.preservation["mode"] == "deterministic"


def test_auto_with_key_uses_llm():
    mock = MockAnthropic(reply=GRAND_PRIZE_DRAFT)
    cc = compile_text(GRAND_PRIZE, make_settings(intent_compiler="auto", anthropic_api_key=API_KEY), http_client=mock.client())
    assert cc.compiler == CompilerKind.LLM and mock.calls == 1


def test_deterministic_mode_ignores_configured_key(llm_settings):
    cc = compile_text(GRAND_PRIZE, llm_settings, mode="deterministic", http_client=forbidden_client())
    assert cc.compiler == CompilerKind.DETERMINISTIC


# ---- LLM path ------------------------------------------------------------------------------------------------------


def test_llm_valid_draft_is_used(llm_settings, settings):
    mock = MockAnthropic(reply=GRAND_PRIZE_DRAFT)
    cc = compile_text(GRAND_PRIZE, llm_settings, http_client=mock.client())
    assert cc.compiler == CompilerKind.LLM
    p = cc.preservation
    assert p["status"] == "PRESERVED" and p["accepted"] is True and p["fallback"] is False
    assert p["draft"]["source"] == "llm" and p["reference"]["source"] == "deterministic"
    assert cc.summary["intent_preserved"] is True and cc.summary["compiler"] == "LLM"
    # same semantics as the deterministic compiler -> same commitment
    assert cc.contract_hash == compile_text(GRAND_PRIZE, settings).contract_hash


def test_llm_stricter_draft_is_used(llm_settings):
    body = draft_json(conditions=[cond(IN, ">", 1000000), cond(LIQ, ">", 600000)], max_age_seconds=15)
    cc = compile_text(GRAND_PRIZE, llm_settings, http_client=MockAnthropic(reply=body).client())
    assert cc.compiler == CompilerKind.LLM
    assert str(cc.contract.requirement("req_liquidity").threshold) == "600000"
    assert {r.max_age_seconds for r in cc.contract.requirements} == {15}
    assert cc.preservation["status"] == "PRESERVED"


def test_llm_extra_restrictive_condition_is_reported(llm_settings):
    body = draft_json(conditions=[cond(IN, ">", 1000000), cond(LIQ, ">", 500000), cond(PRICE, "<", "0.5")])
    cc = compile_text(GRAND_PRIZE, llm_settings, http_client=MockAnthropic(reply=body).client())
    assert cc.compiler == CompilerKind.LLM
    extras = [c for c in cc.preservation["checks"] if c["kind"] == "EXTRA"]
    assert [c["check"] for c in extras] == [f"extra_condition:{PRICE} < 0.5"]
    assert "req_price" in {r.id for r in cc.contract.requirements}
    assert cc.contract.requirement("req_price").origin == "USER"


@pytest.mark.parametrize(
    "body,failing_check",
    [
        (draft_json(conditions=[cond(IN, ">", 1000000), cond(LIQ, ">", 50000)]), f"condition:{LIQ} > 500000"),  # §25
        (draft_json(conditions=[cond(IN, ">=", 1000000), cond(LIQ, ">", 500000)]), f"condition:{IN} > 1000000"),
        (draft_json(conditions=[cond(IN, ">", 1000000)]), f"condition:{LIQ} > 500000"),
        (draft_json(max_amount="1000", conditions=GRAND_PRIZE_DRAFT["conditions"]), "max_amount"),
        (draft_json(asset="WETH", conditions=GRAND_PRIZE_DRAFT["conditions"]), "asset"),
        (draft_json(action="SELL", conditions=GRAND_PRIZE_DRAFT["conditions"]), "action"),
        (draft_json(max_age_seconds=300, conditions=GRAND_PRIZE_DRAFT["conditions"]), "max_age_seconds"),
    ],
)
def test_llm_relaxation_falls_back_to_deterministic(body, failing_check, llm_settings, settings):
    mock = MockAnthropic(reply=body)
    cc = compile_text(GRAND_PRIZE, llm_settings, http_client=mock.client())
    assert mock.calls == 1
    assert cc.compiler == CompilerKind.DETERMINISTIC
    assert cc.contract_hash == compile_text(GRAND_PRIZE, settings).contract_hash  # the reference contract is in force
    p = cc.preservation
    assert p["status"] == "RELAXATION_DETECTED"
    assert p["fallback"] is True and p["fallback_reason"] == "RELAXATION_DETECTED"
    assert p["accepted"] is True  # the deterministic contract in force is accepted
    assert failing_check in {c["check"] for c in p["checks"] if not c["ok"]}
    assert p["draft"] is not None and p["explanation"]
    assert str(cc.contract.requirement("req_liquidity").threshold) == "500000"
    assert cc.summary["fallback_reason"] == "RELAXATION_DETECTED"
    assert cc.summary["intent_preserved"] is True  # describes the (deterministic) contract in force


@pytest.mark.parametrize(
    "mock,reason",
    [
        (MockAnthropic(reply="Sure! I think you should buy."), "LLM_UNAVAILABLE:NO_JSON"),
        (MockAnthropic(reply="{{{ garbage"), "LLM_UNAVAILABLE:NO_JSON"),
        (MockAnthropic(status=500, reply=GRAND_PRIZE_DRAFT), "LLM_UNAVAILABLE:HTTP_500"),
        (MockAnthropic(raw_body=b"not json at all"), "LLM_UNAVAILABLE:BAD_RESPONSE"),
        (MockAnthropic(raise_exc=httpx.ConnectTimeout("t")), "LLM_UNAVAILABLE:TRANSPORT_ERROR"),
        (MockAnthropic(reply=draft_json(conditions=[cond("rsi", "<", 30)])), "LLM_UNAVAILABLE:INVALID_DRAFT"),
    ],
)
def test_llm_failure_falls_back_to_deterministic(mock, reason, llm_settings, settings):
    cc = compile_text(GRAND_PRIZE, llm_settings, http_client=mock.client())
    assert cc.compiler == CompilerKind.DETERMINISTIC
    assert cc.contract_hash == compile_text(GRAND_PRIZE, settings).contract_hash
    p = cc.preservation
    assert p["fallback"] is True and p["fallback_reason"] == reason
    assert p["llm_error"]["code"] == reason.split(":", 1)[1]
    assert p["status"] == "PRESERVED"  # the deterministic contract vs its own parse
    assert API_KEY not in json.dumps(p)


def test_llm_draft_in_other_currency_falls_back(llm_settings):
    # a draft in another currency cannot be compared with the reference: MISMATCH -> fallback
    body = draft_json(currency="EUR", conditions=GRAND_PRIZE_DRAFT["conditions"])
    cc = compile_text(GRAND_PRIZE, llm_settings, http_client=MockAnthropic(reply=body).client())
    assert cc.compiler == CompilerKind.DETERMINISTIC
    assert cc.preservation["fallback_reason"] == "MISMATCH"


def test_llm_reference_only_draft_is_accepted_but_flagged(llm_settings):
    text = "Buy up to 100 USDC of TOKEN-X if whales are accumulating"
    body = draft_json(conditions=[cond(IN, ">", 0)], unparsed=[])
    cc = compile_text(text, llm_settings, http_client=MockAnthropic(reply=body).client())
    assert cc.compiler == CompilerKind.LLM
    p = cc.preservation
    assert p["status"] == "REFERENCE_ONLY" and p["reference_verified"] is False and p["accepted"] is True
    assert any(c["check"] == "reference_verification" for c in p["checks"])
    assert cc.summary["intent_preserved"] is False  # never shown as verified
    assert cc.summary["unparsed"] == ["whales are accumulating"]  # the reference's gap stays visible
    assert requirement_view(cc.contract) == [
        ("req_inflow", IN, ">", "0", 30, "USER"),
        ("req_price", PRICE, "EXISTS", None, 30, "IMPLICIT"),
    ]


def test_llm_reference_only_with_unknown_claim_falls_back(llm_settings):
    text = "Buy up to 100 USDC of TOKEN-X if whales are accumulating"
    body = draft_json(conditions=[cond("whale_accumulation", ">", 0)])
    cc = compile_text(text, llm_settings, http_client=MockAnthropic(reply=body).client())
    assert cc.compiler == CompilerKind.DETERMINISTIC
    assert requirement_view(cc.contract) == [("req_price", PRICE, "EXISTS", None, 30, "IMPLICIT")]
    assert cc.summary["unparsed"] == ["whales are accumulating"]


def test_llm_transfer_without_reference_conditions(llm_settings):
    text = "Transfer 25 USDC when the treasury is healthy"
    body = draft_json(action="TRANSFER", asset="USDC", max_amount="25", conditions=[cond(LIQ, ">", 1000000)])
    cc = compile_text(text, llm_settings, http_client=MockAnthropic(reply=body).client())
    assert cc.compiler == CompilerKind.LLM and cc.preservation["status"] == "REFERENCE_ONLY"
    with pytest.raises(IntentCompilationError) as exc:
        compile_text(text, llm_settings, http_client=MockAnthropic(status=500).client())
    assert exc.value.code == "NO_CONDITIONS"


def test_llm_path_never_logs_the_key(llm_settings, caplog):
    caplog.set_level(logging.DEBUG)
    compile_text(GRAND_PRIZE, llm_settings, http_client=MockAnthropic(reply=GRAND_PRIZE_DRAFT).client())
    compile_text(GRAND_PRIZE, llm_settings, http_client=MockAnthropic(status=503).client())
    assert API_KEY not in caplog.text


# ---- structured limits from the Intent ----------------------------------------------------------------------------------


def test_protected_amount_fills_missing_amount(settings):
    cc = compile_intent(
        make_intent("Buy TOKEN-X if liquidity exceeds $500K", protected_amount=usdc("50")), contract_id="c", settings=settings
    )
    assert cc.contract.action.max_amount.value == Decimal("50")


def test_protected_amount_can_only_tighten(settings):
    lower = compile_intent(make_intent(GRAND_PRIZE, protected_amount=usdc("40")), contract_id="c", settings=settings)
    assert lower.contract.action.max_amount.value == Decimal("40")
    assert lower.summary["warnings"]
    higher = compile_intent(make_intent(GRAND_PRIZE, protected_amount=usdc("400")), contract_id="c", settings=settings)
    assert higher.contract.action.max_amount.value == Decimal("100")


def test_evidence_budget_can_only_tighten(settings):
    cc = compile_intent(make_intent(GRAND_PRIZE, evidence_budget=usdc("0.07")), contract_id="c", settings=settings)
    assert cc.contract.economic_limits.max_evidence_cost_usd == Decimal("0.07")
    assert cc.summary["evidence_budget"] == "0.07 USDC"


def test_protected_amount_applies_to_llm_draft_too(llm_settings):
    cc = compile_intent(
        make_intent(GRAND_PRIZE, protected_amount=usdc("40")),
        contract_id="c",
        settings=llm_settings,
        http_client=MockAnthropic(reply=GRAND_PRIZE_DRAFT).client(),
    )
    assert cc.compiler == CompilerKind.LLM
    assert cc.contract.action.max_amount.value == Decimal("40")


# ---- override path ---------------------------------------------------------------------------------------------------


@pytest.fixture
def det(settings):
    return compile_text(GRAND_PRIZE, settings)


def edited(det, **changes):
    data = det.contract.model_dump(mode="json")
    for path, value in changes.items():
        target = data
        keys = path.split("__")
        for k in keys[:-1]:
            target = target[int(k)] if k.isdigit() else target[k]
        target[keys[-1]] = value
    return data


def test_override_unchanged_is_accepted(det, settings):
    cc = compile_text(GRAND_PRIZE, settings, override=edited(det))
    assert cc.compiler == CompilerKind.OVERRIDE
    assert cc.preservation["accepted"] is True and cc.preservation["status"] == "PRESERVED"
    assert cc.contract_hash == det.contract_hash
    assert cc.summary["compiler"] == "OVERRIDE"


def test_override_stricter_is_accepted(det, settings):
    data = edited(det, requirements__1__threshold="750000", authorization__ttl_seconds=15)
    cc = compile_text(GRAND_PRIZE, settings, override=data)
    assert cc.preservation["accepted"] is True
    assert cc.contract_hash != det.contract_hash
    assert str(cc.contract.requirement("req_liquidity").threshold) == "750000"


@pytest.mark.parametrize(
    "changes",
    [
        {"requirements__1__threshold": "50000"},
        {"requirements__0__operator": ">="},
        {"requirements__1__max_age_seconds": 300},
        {"requirements__1__required": False},
        {"authorization__ttl_seconds": 120},
        {"economic_limits__max_evidence_cost_usd": "2"},
        {"policy__minimum_required_evidence": 1},
        {"policy__stale_required": "ESCALATE"},
        {"action__max_amount__value": "500", "economic_limits__max_execution_value_usd": "500"},
    ],
)
def test_override_relaxation_is_flagged_not_accepted(det, settings, changes):
    cc = compile_text(GRAND_PRIZE, settings, override=edited(det, **changes))
    assert cc.compiler == CompilerKind.OVERRIDE
    p = cc.preservation
    assert p["status"] == "RELAXATION_DETECTED"
    assert p["accepted"] is False and "rejection_reason" in p
    assert cc.summary["intent_preserved"] is False


def test_override_removing_implicit_price_is_a_relaxation(det, settings):
    data = edited(det)
    data["requirements"] = [r for r in data["requirements"] if r["id"] != "req_price"]
    cc = compile_text(GRAND_PRIZE, settings, override=data)
    assert cc.preservation["status"] == "RELAXATION_DETECTED" and cc.preservation["accepted"] is False


def test_override_binds_intent_and_contract_ids(det, settings):
    data = edited(det, contract_id="attacker", intent_id="someone_else")
    cc = compile_intent(make_intent(GRAND_PRIZE, intent_id="intent_real"), contract_id="contract_real", settings=settings, override=data)
    assert (cc.contract.contract_id, cc.contract.intent_id) == ("contract_real", "intent_real")


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"policy__failed_threshold": "AUTHORIZE"}, "INVALID_OVERRIDE"),
        ({"requirements__0__claim": "rsi"}, "INVALID_OVERRIDE"),
        ({"requirements__0__operator": "~"}, "INVALID_OVERRIDE"),
        ({"surprise": 1}, "INVALID_OVERRIDE"),
        ({"action__max_amount__value": "5000", "economic_limits__max_execution_value_usd": "5000"}, "AMOUNT_CAP_EXCEEDED"),
    ],
)
def test_invalid_override_raises(det, settings, changes, code):
    with pytest.raises(IntentCompilationError) as exc:
        compile_text(GRAND_PRIZE, settings, override=edited(det, **changes))
    assert exc.value.code == code


def test_override_cannot_be_verified_without_a_complete_reference(det, settings):
    cc = compile_text("Buy TOKEN-X if liquidity exceeds $500K", settings, override=edited(det))
    assert cc.preservation["status"] == "MISMATCH" and cc.preservation["accepted"] is False


def test_override_ignores_llm(det, llm_settings):
    cc = compile_text(GRAND_PRIZE, llm_settings, override=edited(det), http_client=forbidden_client())
    assert cc.compiler == CompilerKind.OVERRIDE


# ---- the compiled contract is what the Decision Engine consumes ------------------------------------------------------


def _evidence(contract, values, observed_at):
    from sufficia.domain.enums import SourceMode, SourceType
    from sufficia.domain.models import EvidenceClaim, EvidenceIntegrity, EvidenceItem, EvidenceSet, EvidenceSource, EvidenceTemporal

    items = [
        EvidenceItem(
            evidence_id=f"ev_{rid}",
            contract_id=contract.contract_id,
            requirement_id=rid,
            claim=EvidenceClaim(name=contract.requirement(rid).claim, value=value, unit="USD"),
            temporal=EvidenceTemporal(observed_at=observed_at, received_at=observed_at),
            source=EvidenceSource(provider=f"provider_{rid}", source_type=SourceType.API, mode=SourceMode.SIMULATED),
            integrity=EvidenceIntegrity(request_hash="0x" + "a1" * 32, response_hash="0x" + "b2" * 32),
        )
        for rid, value in values.items()
    ]
    return EvidenceSet(contract_id=contract.contract_id, items=items)


@pytest.mark.parametrize(
    "liquidity,state,reason",
    [(Decimal(718_000), "AUTHORIZE", "ALL_REQUIREMENTS_SATISFIED"), (Decimal(450_000), "DENY", "THRESHOLD_NOT_MET")],
)
def test_compiled_contract_drives_the_engine(settings, liquidity, state, reason):
    from datetime import datetime, timedelta, timezone

    from sufficia.decision_engine import evaluate

    contract = compile_text(GRAND_PRIZE, settings).contract
    t0 = datetime(2026, 10, 5, 18, 42, 15, tzinfo=timezone.utc)
    values = {"req_inflow": Decimal(1_340_000), "req_liquidity": liquidity, "req_price": Decimal("0.428")}
    decision = evaluate(contract, _evidence(contract, values, t0 - timedelta(seconds=5)), t0)
    assert decision.state.value == state
    assert reason in [c.value for c in decision.reason_codes]
    assert decision.contract_hash == contract_hash(contract)
