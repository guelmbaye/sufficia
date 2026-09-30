"""Process-wide runtime: settings, database, chain gateway, evidence registry, x402 reference server.

Everything that is expensive or stateful is created once and shared by the request handlers:
  * the chain gateway is built lazily (the LOCAL_EVM chain deploys the real contracts on first use);
  * the local x402 reference server is ONE app instance, mounted under /_dev/x402 and reached
    in-process by the acquisition layer, so its replay-protection state (used nonces) is shared.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from ..blockchain.gateway import ChainGateway
from ..config import Settings, get_settings
from ..db import Database
from ..domain.enums import ChainMode
from ..evidence.adapters.registry import build_registry, permitted_sources
from ..evidence.simulated_market import MARKET, SimulatedMarket
from .errors import ApiError


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Runtime:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        database: Optional[Database] = None,
        gateway: Optional[ChainGateway] = None,
        clock: Callable[[], datetime] = utcnow,
        market: SimulatedMarket = MARKET,
    ):
        self.settings = settings or get_settings()
        self.db = database or Database(self.settings.database_url)
        self.clock = clock
        self.market = market
        self.registry = build_registry(self.settings)
        self.permitted = permitted_sources(self.registry, self.settings)
        if self.settings.demo_mode:
            # Demo Lab injections are attributed to their own provider (never to a real one).
            from ..domain.claims import CLAIMS

            self.permitted["demo-lab"] = set(CLAIMS)
        self._gateway = gateway
        self._gateway_error: Optional[str] = None
        self._gateway_lock = threading.Lock()
        self._reference_app: Any = None
        self.audit_lock = threading.Lock()

    # ---- time ---------------------------------------------------------------------------------
    def now(self) -> datetime:
        return self.clock()

    # ---- chain --------------------------------------------------------------------------------
    @property
    def chain_enabled(self) -> bool:
        return self.settings.chain_mode != ChainMode.DISABLED or self._gateway is not None

    def gateway(self, *, required: bool = True) -> Optional[ChainGateway]:
        if self._gateway is not None:
            return self._gateway
        if self.settings.chain_mode == ChainMode.DISABLED:
            if required:
                raise ApiError(503, "CHAIN_DISABLED", "CHAIN_MODE=DISABLED: protected execution is unavailable")
            return None
        with self._gateway_lock:
            if self._gateway is None:
                try:
                    from ..blockchain.factory import build_gateway

                    self._gateway = build_gateway(self.settings)
                    self._gateway_error = None
                except Exception as exc:  # configuration / connectivity: fail closed, never fake a chain
                    self._gateway_error = f"{type(exc).__name__}: {exc}"
                    if required:
                        raise ApiError(
                            503, "CHAIN_UNAVAILABLE", "the chain gateway could not be initialised", {"error": self._gateway_error}
                        ) from exc
                    return None
        return self._gateway

    @property
    def gateway_error(self) -> Optional[str]:
        return self._gateway_error

    # ---- x402 reference server --------------------------------------------------------------------
    @property
    def reference_app(self) -> Any:
        if self._reference_app is None and self.settings.x402_reference_enabled:
            from ..devtools.x402_reference import create_x402_reference_app

            self._reference_app = create_x402_reference_app(self.settings, market=self.market)
        return self._reference_app

    def close(self) -> None:
        self.db.dispose()
