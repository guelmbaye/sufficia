"""FastAPI application factory.

    uvicorn sufficia.api.app:app          # module-level app built from the environment
    create_app(settings=..., runtime=...) # tests / embedding
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__
from ..config import Settings, get_settings
from ..services.demo import DemoLab, ScenarioRunner
from ..services.errors import ApiError
from ..services.runtime import Runtime
from ..services.workflow import Sufficia
from .responses import ExactJSONResponse, to_jsonable
from .routes import core, demo, legacy, system
from .security import SecurityMiddleware, configure_logging

log = logging.getLogger("sufficia.api")

DESCRIPTION = """
**SUFFICIA — Evidence-Bound Execution for Autonomous Agents.**

Reasoning proposes. Evidence constrains. Policy decides. Authorization binds. Monad executes.

Flow: `POST /intents` → `POST /intents/{id}/compile` (frozen Evidence Contract) → `POST /contracts/{id}/acquire`
(x402-paid evidence) → `POST /contracts/{id}/decide` (AUTHORIZE / DENY / ABSTAIN / REFRESH / ESCALATE) →
`POST /decisions/{id}/execute` (403 unless AUTHORIZE; authorize + protected execution on the EvidenceGate) →
`GET /receipts/{id}` → `POST /receipts/{id}/replay`.
"""


def create_app(settings: Optional[Settings] = None, *, runtime: Optional[Runtime] = None) -> FastAPI:
    settings = settings or (runtime.settings if runtime else get_settings())
    configure_logging(settings.log_level, settings.log_json)
    rt = runtime or Runtime(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info("SUFFICIA API starting", extra={"path": settings.chain_mode.value})
        yield
        rt.close()

    app = FastAPI(
        title="SUFFICIA API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        default_response_class=ExactJSONResponse,
    )
    app.state.runtime = rt
    app.state.sufficia = Sufficia(rt)
    app.state.demo_lab = DemoLab(app.state.sufficia)
    app.state.scenarios = ScenarioRunner(app.state.sufficia, app.state.demo_lab, settings.scenarios_dir)

    app.add_middleware(
        SecurityMiddleware,
        api_key=settings.api_key,
        rate_per_minute=settings.rate_limit_per_minute,
        max_bytes=settings.max_request_bytes,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return ExactJSONResponse(exc.to_dict(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return ExactJSONResponse(
            {"error": {"code": "VALIDATION_ERROR", "message": "the request is malformed", "detail": {"errors": to_jsonable(errors)}}},
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return ExactJSONResponse({"error": {"code": code, "message": str(exc.detail), "detail": {}}}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error", extra={"request_id": getattr(request.state, "request_id", None), "path": request.url.path})
        return ExactJSONResponse({"error": {"code": "INTERNAL_ERROR", "message": "internal error", "detail": {}}}, status_code=500)

    app.include_router(system)
    app.include_router(core)
    app.include_router(demo)
    app.include_router(legacy)

    if settings.x402_reference_enabled and rt.reference_app is not None:
        # Local x402 reference resource server (tests/demos; settles nothing onchain — see its docstring).
        app.mount("/_dev/x402", rt.reference_app)

    return app


def _module_app() -> FastAPI:  # pragma: no cover - exercised by uvicorn
    return create_app()


def __getattr__(name: str):  # lazy `sufficia.api.app:app` so importing the module has no side effects
    if name == "app":
        global app
        app = _module_app()
        return app
    raise AttributeError(name)
