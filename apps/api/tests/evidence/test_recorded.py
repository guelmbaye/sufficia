"""RECORDED fixtures: labelling, captured_at preservation, observed_at re-stamp, hashes of captured bytes."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from sufficia.canonical.hashes import RESPONSE_DOMAIN, raw_hash
from sufficia.decision_engine import evaluate
from sufficia.domain.enums import DecisionState, SourceMode, SourceType
from sufficia.domain.models import EvidenceSet
from sufficia.domain.reasons import ReasonCode
from sufficia.evidence.adapters import RecordedFixtureAdapter, build_registry, permitted_sources
from sufficia.evidence.base import AcquisitionError
from sufficia.evidence.normalizer import normalize

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
CAPTURED = "2026-07-01T09:30:00.000000Z"
BODY = b'{"data":[{"token_address":"0x1111111111111111111111111111111111111111","net_flow_24h_usd":1340000}]}'


def _write(directory: Path, file_claim: str, **overrides) -> None:
    fixture = {
        "provider": "nansen",
        "source_type": "API",
        "claim": file_claim,
        "value": 1340000,
        "unit": "USD",
        "captured_at": CAPTURED,
        "request_descriptor": {"method": "POST", "url": "https://api.nansen.ai/api/v1/smart-money/netflow"},
        "response_body_b64": base64.b64encode(BODY).decode(),
        "upstream_source": "nansen:smart-money-netflow",
        "source_family": "nansen",
        "endpoint_id": "nansen:/api/v1/smart-money/netflow",
    }
    fixture.update(overrides)
    (directory / f"{file_claim}.json").write_text(json.dumps(fixture))


def test_recorded_labels_mode_and_preserves_captured_at(h, tmp_path):
    _write(tmp_path, "smart_money_inflow_24h")
    ctx = h.make_ctx(settings=h.make_settings(recorded_fixtures_dir=str(tmp_path)), now=lambda: NOW)
    raw = h.run(RecordedFixtureAdapter().fetch(h.req("smart_money_inflow_24h"), ctx))
    assert raw.mode == SourceMode.RECORDED
    assert raw.provider == "nansen"  # original provider kept
    assert raw.captured_at == datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc)
    assert raw.observed_at == NOW  # explicitly re-stamped for demo continuity
    assert raw.response_body == BODY
    item = normalize(raw, requirement=h.req("smart_money_inflow_24h"), contract_id="ctr_test", acquisition_id="acq_x")
    assert item.source.mode == SourceMode.RECORDED
    assert item.source.captured_at == raw.captured_at
    assert item.integrity.response_hash == raw_hash(RESPONSE_DOMAIN, BODY)


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"claim": "asset_price"}, ReasonCode.INVALID_RESPONSE),
        ({"response_body_b64": "!!!not base64"}, ReasonCode.INVALID_RESPONSE),
        ({"captured_at": "2026-07-01T09:30:00"}, ReasonCode.INVALID_RESPONSE),  # naive
        ({"source_type": "TELEPATHY"}, ReasonCode.INVALID_RESPONSE),
    ],
)
def test_recorded_rejects_broken_fixtures(h, tmp_path, overrides, code):
    _write(tmp_path, "smart_money_inflow_24h", **overrides)
    ctx = h.make_ctx(settings=h.make_settings(recorded_fixtures_dir=str(tmp_path)))
    with pytest.raises(AcquisitionError) as e:
        h.run(RecordedFixtureAdapter().fetch(h.req("smart_money_inflow_24h"), ctx))
    assert e.value.code == code


def test_recorded_missing_fixture_and_missing_fields(h, tmp_path):
    ctx = h.make_ctx(settings=h.make_settings(recorded_fixtures_dir=str(tmp_path)))
    with pytest.raises(AcquisitionError) as e:
        h.run(RecordedFixtureAdapter().fetch(h.req("asset_price"), ctx))
    assert e.value.code == ReasonCode.SOURCE_NOT_CONFIGURED
    (tmp_path / "asset_price.json").write_text(json.dumps({"provider": "x", "claim": "asset_price"}))
    with pytest.raises(AcquisitionError) as e:
        h.run(RecordedFixtureAdapter().fetch(h.req("asset_price"), ctx))
    assert e.value.code == ReasonCode.MISSING_FIELD
    (tmp_path / "asset_price.json").write_text("{nope")
    with pytest.raises(AcquisitionError) as e:
        h.run(RecordedFixtureAdapter().fetch(h.req("asset_price"), ctx))
    assert e.value.code == ReasonCode.INVALID_RESPONSE


def test_recorded_string_value_kept_as_captured(h, tmp_path):
    _write(tmp_path, "asset_price", value="0.4281", provider="dexscreener", source_family="dexscreener")
    ctx = h.make_ctx(settings=h.make_settings(recorded_fixtures_dir=str(tmp_path)), now=lambda: NOW)
    raw = h.run(RecordedFixtureAdapter().fetch(h.req("asset_price"), ctx))
    assert raw.value == "0.4281"
    item = normalize(raw, requirement=h.req("asset_price"), contract_id="ctr_test", acquisition_id="a")
    assert item.claim.value == Decimal("0.4281")


def test_shipped_sample_fixtures_are_labelled_simulated_and_usable(h):
    """The repo's sample set is captured from the SIMULATED market and must say so."""
    settings = h.make_settings(evidence_profile="recorded")
    directory = Path(settings.recorded_fixtures_dir)
    for claim in ("smart_money_inflow_24h", "available_liquidity", "asset_price"):
        data = json.loads((directory / f"{claim}.json").read_text())
        assert data["provider"] == "simulated-market"
        assert data["original_mode"] == "SIMULATED" and "NOT a capture of a real provider" in data["note"]
    assert (directory / "README.md").is_file()

    registry = build_registry(settings)
    contract = h.make_contract()
    ctx = h.make_ctx(contract=contract, settings=settings)
    items = []
    for requirement in contract.requirements:
        raw = h.run(registry["recorded"].fetch(requirement, ctx))
        assert raw.mode == SourceMode.RECORDED and raw.source_type == SourceType.API
        items.append(normalize(raw, requirement=requirement, contract_id=contract.contract_id, acquisition_id=f"acq_{requirement.id}"))
    permitted = permitted_sources(registry, settings)
    assert "simulated-market" in permitted
    decision = evaluate(contract, EvidenceSet(contract_id=contract.contract_id, items=items), ctx.now() + timedelta(seconds=1), permitted_sources=permitted)
    assert decision.state == DecisionState.AUTHORIZE  # 1.34M > 1M, 92440 >= 50000, 0.428 > 0


def test_capture_script_round_trip_preserves_hashes(h, tmp_path):
    """capture_fixtures.py writes exactly the adapter's bytes: the RECORDED item commits to the same
    request/response hashes as the original observation."""
    import importlib.util

    script = Path(__file__).resolve().parents[2] / "scripts" / "capture_fixtures.py"
    spec = importlib.util.spec_from_file_location("capture_fixtures_under_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    settings = h.make_settings(recorded_fixtures_dir=str(tmp_path))
    report = h.run(module.capture(settings, out=tmp_path, claims=["asset_price"], sources=["scenario"], asset="TOKEN-X", allow_paid=False))
    assert report["asset_price"].startswith("captured from simulated-market (SIMULATED)")
    data = json.loads((tmp_path / "asset_price.json").read_text())
    assert data["original_mode"] == "SIMULATED" and "NOT a capture" in data["note"]
    assert json.loads((tmp_path / "asset_price.json").read_text(), parse_float=Decimal)["value"] == Decimal("0.428")

    ctx = h.make_ctx(settings=settings)
    requirement = h.req("asset_price")
    original = h.run(build_registry(settings)["scenario"].fetch(requirement, ctx))
    recorded = h.run(RecordedFixtureAdapter().fetch(requirement, ctx))
    a = normalize(original, requirement=requirement, contract_id="c", acquisition_id="a")
    b = normalize(recorded, requirement=requirement, contract_id="c", acquisition_id="a")
    assert a.integrity.request_hash == b.integrity.request_hash
    assert b.source.mode == SourceMode.RECORDED and b.claim.value == Decimal("0.428")


def test_capture_script_writes_nothing_for_failing_sources(h, tmp_path):
    import importlib.util

    script = Path(__file__).resolve().parents[2] / "scripts" / "capture_fixtures.py"
    spec = importlib.util.spec_from_file_location("capture_fixtures_under_test2", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    settings = h.make_settings(recorded_fixtures_dir=str(tmp_path))  # no live source configured
    report = h.run(module.capture(settings, out=tmp_path, claims=["asset_price", "available_liquidity"], sources=None, asset="TOKEN-X", allow_paid=False))
    assert all(not s.startswith("captured") for s in report.values())
    assert list(tmp_path.glob("*.json")) == []
