"""Claim registry: the canonical claims an Evidence Contract may reference.

Each claim has exactly one canonical unit (Document 03 §84). Adapters must normalize into it
before an Evidence Item is accepted; the engine rejects any other unit with INVALID_UNIT.
Physical bounds reject impossible values (negative liquidity, overflow-sized numbers).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .enums import Operator

MAX_ABS_VALUE = Decimal("1e18")


@dataclass(frozen=True)
class ClaimSpec:
    name: str
    unit: str
    description: str
    label: str
    min_value: Decimal | None = None
    max_value: Decimal | None = MAX_ABS_VALUE
    numeric: bool = True
    allowed_operators: frozenset[Operator] = field(
        default_factory=lambda: frozenset(
            {Operator.GT, Operator.GTE, Operator.LT, Operator.LTE, Operator.EQ, Operator.NEQ, Operator.EXISTS}
        )
    )


CLAIMS: dict[str, ClaimSpec] = {
    "smart_money_inflow_24h": ClaimSpec(
        name="smart_money_inflow_24h",
        unit="USD",
        label="Smart-money inflow (24h)",
        description="Net USD flow of smart-money wallets into the asset over 24h. May be negative (outflow).",
        min_value=-MAX_ABS_VALUE,
    ),
    "available_liquidity": ClaimSpec(
        name="available_liquidity",
        unit="USD",
        label="Available liquidity",
        description="USD liquidity available on the execution venue for the asset.",
        min_value=Decimal(0),
    ),
    "asset_price": ClaimSpec(
        name="asset_price",
        unit="USD",
        label="Price",
        description="Reference USD price of one unit of the asset.",
        min_value=Decimal(0),
        max_value=Decimal("1e12"),
    ),
    "asset_listed": ClaimSpec(
        name="asset_listed",
        unit="BOOL",
        label="Asset listed on venue",
        description="Whether the asset is tradable on the execution venue.",
        numeric=False,
        min_value=None,
        max_value=None,
        allowed_operators=frozenset({Operator.EQ, Operator.EXISTS, Operator.NOT_EXISTS}),
    ),
    "sanctions_flag": ClaimSpec(
        name="sanctions_flag",
        unit="BOOL",
        label="Counterparty sanctions flag",
        description="Presence of a sanctions flag for the counterparty (used with NOT_EXISTS).",
        numeric=False,
        min_value=None,
        max_value=None,
        allowed_operators=frozenset({Operator.EQ, Operator.EXISTS, Operator.NOT_EXISTS}),
    ),
    "venue_id": ClaimSpec(
        name="venue_id",
        unit="ID",
        label="Execution venue",
        description="Identifier of the execution venue (used with IN).",
        numeric=False,
        min_value=None,
        max_value=None,
        allowed_operators=frozenset({Operator.IN, Operator.EQ, Operator.NEQ, Operator.EXISTS}),
    ),
}


def get_claim(name: str) -> ClaimSpec | None:
    return CLAIMS.get(name)
