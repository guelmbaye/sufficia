"""Uniswap-V2-style pair reserves (LIVE, ONCHAIN) -> ``available_liquidity`` (USD).

Reads ``getReserves()`` on ``pool_address`` at the latest block.

Valuation assumption (documented, deliberately simple): the reserve at ``pool_quote_token_index`` is a
USD stablecoin worth exactly 1 USD per unit with ``pool_quote_decimals`` decimals, and a constant-
product pool holds equal value on both sides, so::

    liquidity_usd = 2 * stable_reserve / 10**pool_quote_decimals

This does not price the non-stable side independently and ignores stablecoin de-pegs; it is an
on-chain *lower-trust-assumption* complement to API liquidity figures, not a replacement.

* observed_at = timestamp of the block the reserves were read at (the state is exact as of that block);
* response    = ``getReserves()`` return data (96 bytes) exactly as returned by ``eth_call``;
* chain_ref   = {chain_id, block_number, block_hash, contract=pool}.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from eth_abi import decode as abi_decode

from ...domain.enums import SourceMode, SourceType
from ...domain.models import ChainReference, Requirement
from ...domain.reasons import ReasonCode
from ..base import AcquisitionContext, AcquisitionError, RawEvidence, SourceAdapter
from ._evm import Web3Factory, default_web3_factory, eth_call, latest_block, map_rpc_exception

if TYPE_CHECKING:  # pragma: no cover
    from ...config import Settings

GET_RESERVES_SELECTOR = "0x0902f1ac"  # getReserves()
GET_RESERVES_TYPES = ["uint112", "uint112", "uint32"]


class UniswapV2PoolLiquidityAdapter(SourceAdapter):
    name = "uniswap-v2-pool"
    source_type = SourceType.ONCHAIN
    mode = SourceMode.LIVE
    claims = frozenset({"available_liquidity"})
    family = "onchain-pool"

    def __init__(self, web3_factory: Web3Factory = default_web3_factory):
        self.web3_factory = web3_factory

    def is_configured(self, settings: "Settings") -> bool:
        return bool(settings.pool_address and settings.monad_rpc_url and settings.pool_quote_token_index in (0, 1))

    def _read(self, rpc_url: str, pool: str, timeout: float) -> dict:
        w3 = self.web3_factory(rpc_url, timeout)
        block = latest_block(w3)
        raw = eth_call(w3, pool, GET_RESERVES_SELECTOR, block.number)
        return {"block": block, "raw": raw}

    async def fetch(self, requirement: Requirement, ctx: AcquisitionContext) -> RawEvidence:
        settings = ctx.settings
        if not self.is_configured(settings):
            raise AcquisitionError(ReasonCode.SOURCE_NOT_CONFIGURED, "uniswap-v2-pool: pool_address not configured")
        pool = str(settings.pool_address)
        try:
            out = await asyncio.to_thread(self._read, settings.monad_rpc_url, pool, settings.http_timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            raise map_rpc_exception(exc, self.name) from exc
        received_at = ctx.now()
        block = out["block"]
        try:
            reserve0, reserve1, _ts_last = abi_decode(GET_RESERVES_TYPES, out["raw"])
        except Exception as exc:  # noqa: BLE001
            raise AcquisitionError(ReasonCode.INVALID_RESPONSE, f"getReserves returned undecodable data: {type(exc).__name__}") from exc
        stable_reserve = (reserve0, reserve1)[settings.pool_quote_token_index]
        value = 2 * Decimal(stable_reserve).scaleb(-int(settings.pool_quote_decimals))
        return RawEvidence(
            provider=self.name,
            source_type=self.source_type,
            mode=self.mode,
            claim="available_liquidity",
            value=value,
            unit="USD",
            observed_at=datetime.fromtimestamp(block.timestamp, tz=timezone.utc),
            received_at=received_at,
            request_descriptor={
                "method": "eth_call",
                "chain_id": block.chain_id,
                "pool": pool,
                "selector": GET_RESERVES_SELECTOR,
                "block_number": block.number,
                "valuation": {
                    "method": "2x_stable_reserve",
                    "quote_index": settings.pool_quote_token_index,
                    "quote_decimals": settings.pool_quote_decimals,
                },
            },
            response_body=out["raw"],
            endpoint_id=f"uniswap-v2:{pool.lower()}",
            upstream_source=f"onchain-pool:{pool.lower()}",
            source_family=self.family,
            network=f"eip155:{block.chain_id}",
            chain_ref=ChainReference(chain_id=block.chain_id, block_number=block.number, block_hash=block.hash, contract=pool),
        )
