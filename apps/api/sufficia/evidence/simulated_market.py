"""In-memory simulated market used by SIMULATED sources (demo profile, local x402 reference server).

This is *not* market data: every observation read from here is labelled SIMULATED. The Demo Lab
changes these values to produce reproducible PASS/FAIL conditions (Document 05 §92).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from decimal import Decimal

DEFAULT_ASSET = "TOKEN-X"

# Grand Prize scenario baseline: fresh and complete, but liquidity fails the $500K requirement.
DEFAULT_VALUES: dict[str, Decimal] = {
    "smart_money_inflow_24h": Decimal("1340000"),
    "available_liquidity": Decimal("92440"),
    "asset_price": Decimal("0.428"),
}


@dataclass(frozen=True)
class SimulatedObservation:
    asset: str
    claim: str
    value: Decimal
    version: int


class SimulatedMarket:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[tuple[str, str], Decimal] = {}
        self._version = 0

    def get(self, asset: str, claim: str) -> SimulatedObservation | None:
        with self._lock:
            key = (asset.upper(), claim)
            if key in self._values:
                return SimulatedObservation(asset.upper(), claim, self._values[key], self._version)
            if claim in DEFAULT_VALUES:
                return SimulatedObservation(asset.upper(), claim, DEFAULT_VALUES[claim], self._version)
            return None

    def set(self, asset: str, claim: str, value: Decimal) -> None:
        with self._lock:
            self._values[(asset.upper(), claim)] = Decimal(value)
            self._version += 1

    def reset(self, asset: str | None = None) -> None:
        with self._lock:
            if asset is None:
                self._values.clear()
            else:
                for key in [k for k in self._values if k[0] == asset.upper()]:
                    del self._values[key]
            self._version += 1

    def snapshot(self) -> dict[str, dict[str, str]]:
        with self._lock:
            out: dict[str, dict[str, str]] = {}
            for (asset, claim), value in self._values.items():
                out.setdefault(asset, {})[claim] = str(value)
            return out


MARKET = SimulatedMarket()
