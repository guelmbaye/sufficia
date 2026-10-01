"""HTTP routes (Document 02 §22-23, Document 04 §63-66, Document 05 §114).

Every handler is a thin adapter over ``Sufficia`` / ``DemoLab`` / ``ScenarioRunner``: validation of the
request shape here, all authority logic in the services and the engine. Handlers are synchronous and
run in the threadpool (chain calls and acquisitions block).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import __version__
from ..decision_engine import ENGINE_VERSION
from ..domain.models import Intent
from ..services.demo import CONDITIONS, DemoLab, ScenarioRunner
from ..services.errors import ApiError
from ..services.workflow import Sufficia, new_id
from .responses import ok


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntentCreate(_Body):
    text: str = Field(min_length=1, max_length=2000)
    network: str = Field(default="monad", max_length=32)
    protected_amount: Optional[Decimal] = None
    evidence_budget: Optional[Decimal] = None


class IntentPreview(_Body):
    text: str = Field(min_length=1, max_length=2000)
    mode: Optional[Literal["deterministic", "llm", "auto"]] = None


class CompileRequest(_Body):
    mode: Optional[Literal["deterministic", "llm", "auto"]] = None
    override: Optional[dict[str, Any]] = None


class AcquireRequest(_Body):
    sources_per_requirement: int = Field(default=1, ge=1, le=3)


class DecideRequest(_Body):
    evidence_set_id: Optional[str] = Field(default=None, max_length=64)


class MarketUpdate(_Body):
    asset: str = Field(default="TOKEN-X", max_length=32)
    values: dict[str, Any]


class ScenarioRun(_Body):
    execute: bool = True
    stop_before: Optional[str] = Field(default=None, max_length=32)


def _app(request: Request) -> Sufficia:
    return request.app.state.sufficia


def _lab(request: Request) -> DemoLab:
    return request.app.state.demo_lab


def _runner(request: Request) -> ScenarioRunner:
    return request.app.state.scenarios


# =============================================================================================
# System
# =============================================================================================

system = APIRouter(tags=["system"])


@system.get("/health")
def health(request: Request):
    return ok({"status": "ok", "service": "sufficia-api", "version": __version__, "engine_version": ENGINE_VERSION})


@system.get("/health/ready")
def ready(request: Request):
    app = _app(request)
    checks: dict[str, Any] = {}
    try:
        app.stats()
        checks["database"] = "ok"
    except Exception as exc:  # pragma: no cover
        checks["database"] = f"error: {type(exc).__name__}"
    gw = app.rt.gateway(required=False)
    checks["chain"] = "ok" if gw is not None else ("disabled" if not app.rt.chain_enabled else f"unavailable: {app.rt.gateway_error}")
    status = 200 if checks["database"] == "ok" else 503
    return ok({"status": "ready" if status == 200 else "degraded", "checks": checks}, status_code=status)


@system.get("/api/v1/system")
def system_info(request: Request):
    app = _app(request)
    s = app.settings
    gw = app.rt.gateway(required=False)
    return ok(
        {
            "version": __version__,
            "engine_version": ENGINE_VERSION,
            "demo_mode": s.demo_mode,
            "environment": s.app_env,
            "intent_compiler": s.intent_compiler if s.anthropic_api_key or s.intent_compiler == "deterministic" else "deterministic (no LLM key)",
            "evidence": {"profile": s.evidence_profile, "sources": s.sources_for_profile(),
                         "configured_adapters": sorted(n for n, a in app.rt.registry.items() if a.is_configured(s))},
            "x402": {"enabled": s.x402_enabled, "network": s.x402_network, "asset": s.x402_asset, "facilitator": s.x402_facilitator,
                     "max_per_request_usdc": s.x402_max_per_request_usdc, "reference_server": "/_dev/x402" if s.x402_reference_enabled else None,
                     "live_evidence_url_configured": bool(s.x402_evidence_url)},
            "chain": gw.info.public() if gw is not None else {"mode": s.chain_mode.value, "error": app.rt.gateway_error},
            "execution_adapter": s.execution_adapter,
            "database": s.database_url.split(":", 1)[0],
            "policy_defaults": {"max_age_seconds": s.default_max_age_seconds, "authorization_ttl_seconds": s.default_authorization_ttl_seconds,
                                "evidence_budget_usdc": s.default_evidence_budget_usdc, "conflict_tolerance": s.default_conflict_tolerance,
                                "max_protected_amount_usdc": s.max_protected_amount_usdc},
        }
    )


@system.get("/api/v1/chain")
def chain(request: Request):
    gw = _app(request).gateway()
    return ok({"info": gw.info.public(), "health": gw.health()})


@system.get("/api/v1/chain/transactions")
def chain_transactions(request: Request, limit: int = Query(100, ge=1, le=500)):
    return ok({"transactions": _app(request).chain_transactions(limit)})


@system.get("/api/v1/chain/artifacts/{name}")
def chain_artifact(name: str):
    from ..blockchain.artifacts import load_artifact

    if name not in ("EvidenceGate", "DemoSwapRouter", "DemoERC20"):
        raise ApiError(404, "ARTIFACT_NOT_FOUND", f"unknown artifact {name}")
    art = load_artifact(name)
    return ok({"contractName": art.get("contractName", name), "abi": art["abi"], "compiler": art.get("compiler")})


@system.get("/api/v1/audit")
def audit_events(request: Request, subject_id: Optional[str] = Query(None, max_length=80), limit: int = Query(200, ge=1, le=1000)):
    return ok({"events": _app(request).audit_events(subject_id=subject_id, limit=limit)})


@system.get("/api/v1/audit/verify")
def audit_verify(request: Request):
    return ok(_app(request).audit_verify())


@system.get("/api/v1/stats")
def stats(request: Request):
    return ok(_app(request).stats())


@system.get("/api/v1/benchmark")
def benchmark_report(request: Request):
    import json
    from pathlib import Path

    p = Path(_app(request).settings.evidence_reports_dir) / "benchmark.json"
    if not p.is_file():
        raise ApiError(404, "BENCHMARK_NOT_RUN", "no benchmark report yet: run `python -m sufficia.benchmark` or POST /api/v1/benchmark/run")
    return ok(json.loads(p.read_text(encoding="utf-8")))


@system.post("/api/v1/benchmark/run")
def benchmark_run(request: Request):
    from ..benchmark.report import benchmark_json
    from ..benchmark.runner import run_benchmark

    run = run_benchmark(cross_process=False)
    data = benchmark_json(run, datetime.now(timezone.utc))
    return ok({"ok": run.ok, "report": data})


# =============================================================================================
# Intents & contracts
# =============================================================================================

core = APIRouter(prefix="/api/v1", tags=["authority"])


@core.post("/intents", status_code=201)
def create_intent(request: Request, body: IntentCreate):
    return ok(
        _app(request).create_intent(body.text, network=body.network, protected_amount=body.protected_amount, evidence_budget=body.evidence_budget),
        status_code=201,
    )


@core.get("/intents")
def list_intents(request: Request, limit: int = Query(50, ge=1, le=200)):
    return ok({"intents": _app(request).list_intents(limit)})


@core.post("/intents/preview")
def preview_intent(request: Request, body: IntentPreview):
    """Compiles without persisting anything: the 'What must be true before money can move?' preview."""
    from ..intent import IntentCompilationError, compile_intent

    app = _app(request)
    intent = Intent(intent_id="preview", text=body.text.strip(), created_at=app.rt.now())
    try:
        compiled = compile_intent(intent, contract_id="preview", settings=app.settings, mode=body.mode)
    except IntentCompilationError as exc:
        raise ApiError(422, exc.code, str(exc), {"missing": exc.missing, "problems": exc.problems, "unparsed": exc.unparsed}) from exc
    return ok({"preview": True, "contract": compiled.contract, "contract_hash": compiled.contract_hash, "compiler": compiled.compiler,
               "summary": compiled.summary, "preservation": compiled.preservation})


@core.get("/intents/{intent_id}")
def get_intent(request: Request, intent_id: str):
    return ok(_app(request).get_intent(intent_id))


@core.post("/intents/{intent_id}/compile", status_code=201)
def compile_intent_route(request: Request, intent_id: str, body: Optional[CompileRequest] = None):
    body = body or CompileRequest()
    return ok(_app(request).compile(intent_id, mode=body.mode, override=body.override), status_code=201)


@core.get("/contracts/{contract_id}")
def get_contract(request: Request, contract_id: str):
    return ok(_app(request).get_contract(contract_id))


@core.post("/contracts/{contract_id}/acquire", status_code=201)
def acquire(request: Request, contract_id: str, body: Optional[AcquireRequest] = None):
    body = body or AcquireRequest()
    return ok(_app(request).acquire(contract_id, sources_per_requirement=body.sources_per_requirement), status_code=201)


@core.get("/contracts/{contract_id}/evidence")
def contract_evidence(request: Request, contract_id: str):
    return ok(_app(request).current_evidence(contract_id))


@core.get("/evidence-sets/{evidence_set_id}")
def evidence_set(request: Request, evidence_set_id: str):
    return ok(_app(request).get_evidence_set(evidence_set_id))


@core.post("/contracts/{contract_id}/verify")
def verify(request: Request, contract_id: str, body: Optional[DecideRequest] = None):
    body = body or DecideRequest()
    return ok(_app(request).verify(contract_id, evidence_set_id=body.evidence_set_id))


@core.post("/contracts/{contract_id}/decide", status_code=201)
def decide(request: Request, contract_id: str, body: Optional[DecideRequest] = None):
    body = body or DecideRequest()
    return ok(_app(request).decide(contract_id, evidence_set_id=body.evidence_set_id), status_code=201)


# =============================================================================================
# Decisions, authorizations, execution
# =============================================================================================


@core.get("/decisions/{decision_id}")
def get_decision(request: Request, decision_id: str):
    return ok(_app(request).get_decision(decision_id))


@core.post("/decisions/{decision_id}/authorize", status_code=201)
def authorize(request: Request, decision_id: str):
    return ok(_app(request).authorize(decision_id), status_code=201)


@core.post("/decisions/{decision_id}/execute")
def execute_decision(request: Request, decision_id: str):
    """403 EVIDENCE_AUTHORIZATION_REQUIRED unless the decision is AUTHORIZE (the button is not the guard)."""
    return ok(_app(request).execute_decision(decision_id))


@core.get("/decisions/{decision_id}/receipt")
def decision_receipt(request: Request, decision_id: str):
    return ok(_app(request).decision_receipt(decision_id))


@core.get("/authorizations/{authorization_id}")
def get_authorization(request: Request, authorization_id: str):
    return ok(_app(request).get_authorization(authorization_id))


@core.post("/authorizations/{authorization_id}/execute")
def execute_authorization(request: Request, authorization_id: str):
    return ok(_app(request).execute_authorization(authorization_id))


# =============================================================================================
# Receipts & replay
# =============================================================================================


@core.get("/receipts")
def list_receipts(request: Request, limit: int = Query(50, ge=1, le=200)):
    return ok({"receipts": _app(request).list_receipts(limit)})


@core.get("/receipts/{receipt_id}")
def get_receipt(request: Request, receipt_id: str):
    return ok(_app(request).get_receipt(receipt_id))


@core.post("/receipts/{receipt_id}/replay")
def replay(request: Request, receipt_id: str):
    return ok(_app(request).replay_receipt(receipt_id))


@core.post("/receipts/{receipt_id}/tamper-test")
def tamper_test(request: Request, receipt_id: str):
    """Changes one evidence value by the smallest unit, in memory only, and shows the integrity break."""
    return ok(_app(request).tamper_test(receipt_id))


# =============================================================================================
# Demo Lab & scenarios (demo mode only)
# =============================================================================================

demo = APIRouter(prefix="/api/v1/demo", tags=["demo"])


def _condition(condition: str) -> str:
    c = condition.lower()
    if c not in CONDITIONS and c != "missing":
        raise ApiError(404, "UNKNOWN_CONDITION", f"condition must be one of {', '.join(CONDITIONS)}")
    return c


@demo.post("/evidence/{evidence_id}/{condition}", status_code=201)
def inject_on_evidence(request: Request, evidence_id: str, condition: str):
    return ok(_lab(request).inject(_condition(condition), evidence_id=evidence_id), status_code=201)


@demo.post("/contracts/{contract_id}/conditions/{condition}", status_code=201)
def inject_on_contract(request: Request, contract_id: str, condition: str, requirement_id: Optional[str] = Query(None, max_length=64)):
    return ok(_lab(request).inject(_condition(condition), contract_id=contract_id, requirement_id=requirement_id), status_code=201)


@demo.post("/contracts/{contract_id}/test/{condition}", status_code=201)
def test_condition(request: Request, contract_id: str, condition: str):
    """'Test the Gate' (Document 05 §24-26): valid | threshold | missing | stale | conflict -> fresh evidence,
    one mutation, a new decision."""
    return ok(_lab(request).test_condition(contract_id, condition.lower()), status_code=201)


@demo.get("/market")
def market(request: Request):
    return ok(_lab(request).market())


@demo.put("/market")
def set_market(request: Request, body: MarketUpdate):
    return ok(_lab(request).set_market(body.asset, body.values))


@demo.post("/market/reset")
def reset_market(request: Request, asset: Optional[str] = Query(None, max_length=32)):
    return ok(_lab(request).reset_market(asset))


@demo.get("/scenarios")
def scenarios(request: Request):
    _lab(request)._require_demo()
    return ok({"scenarios": _runner(request).list()})


@demo.get("/scenarios/{scenario_id}")
def scenario(request: Request, scenario_id: str):
    _lab(request)._require_demo()
    return ok(_runner(request).get(scenario_id))


def _run_scenario(request: Request, scenario_id: str, body: Optional[ScenarioRun]):
    body = body or ScenarioRun()
    return ok(_runner(request).run(scenario_id, execute=body.execute, stop_before=body.stop_before))


@demo.post("/scenarios/{scenario_id}/run")
def run_scenario(request: Request, scenario_id: str, body: Optional[ScenarioRun] = None):
    return _run_scenario(request, scenario_id, body)


# Document 05 §114 spells the orchestration endpoint without the /api/v1 prefix.
legacy = APIRouter(tags=["demo"])


@legacy.post("/demo/scenarios/{scenario_id}/run", include_in_schema=False)
def run_scenario_legacy(request: Request, scenario_id: str, body: Optional[ScenarioRun] = None):
    return _run_scenario(request, scenario_id, body)


__all__ = ["core", "demo", "legacy", "new_id", "system"]
