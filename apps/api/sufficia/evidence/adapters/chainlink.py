"""Chainlink price feed (LIVE, ORACLE) -> ``asset_price`` (USD).

Reads ``AggregatorV3Interface.decimals()`` and ``latestRoundData()`` on ``chainlink_feed_address``
through ``chainlink_rpc_url`` (default ``monad_rpc_url``), both pinned to the same block.

* value        = ``answer / 10**decimals`` as an exact Decimal (``answer <= 0`` -> INVALID_RESPONSE);
* observed_at  = ``updatedAt`` (UTC) — the oracle's own update time, *not* the read time, so a feed
                 that stopped updating is correctly seen as stale by the engine;
* response     = ``decimals()`` return data (32 bytes) || ``latestRoundData()`` return data (160
                 bytes), exactly as returned by ``eth_call``;
* request      = chain id, feed address, selectors and the pinned block number;
* chain_ref    = {chain_id, block_number, block_hash, contract=feed}.

web3's HTTP provider is blocking, so the read runs in ``asyncio.to_thread``.
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

DECIMALS_SELECTOR = "0x313ce567"  # decimals()
LATEST_ROUND_DATA_SELECTOR = "0xfeaf968c"  # latestRoundData()
LATEST_ROUND_TYPES = ["uint80", "int256", "uint256", "uint256", "uint80"]


class ChainlinkPriceAdapter(SourceAdapter):
    name = "chainlink"
    source_type = SourceType.ORACLE
    mode = SourceMode.LIVE
    claims = frozenset({"asset_price"})
    family = "chainlink"

    def __init__(self, web3_factory: Web3Factory = default_web3_factory):
        self.web3_factory = web3_factory

    @staticmethod
    def rpc_url(settings: "Settings") -> str | None:
        return settings.chainlink_rpc_url or settings.monad_rpc_url

    def is_configured(self, settings: "Settings") -> bool:
        return bool(settings.chainlink_feed_address and self.rpc_url(settings))

    def _read(self, rpc_url: str, feed: str, timeout: float) -> dict:
        w3 = self.web3_factory(rpc_url, timeout)
        block = latest_block(w3)
        decimals_raw = eth_call(w3, feed, DECIMALS_SELECTOR, block.number)
        round_raw = eth_call(w3, feed, LATEST_ROUND_DATA_SELECTOR, block.number)
        return {"block": block, "decimals_raw": decimals_raw, "round_raw": round_raw}

    async def fetch(self, requirement: Requirement, ctx: AcquisitionContext) -> RawEvidence:
        settings = ctx.settings
        if not self.is_configured(settings):
            raise AcquisitionError(ReasonCode.SOURCE_NOT_CONFIGURED, "chainlink: chainlink_feed_address not configured")
        feed = str(settings.chainlink_feed_address)
        rpc = str(self.rpc_url(settings))
        try:
            out = await asyncio.to_thread(self._read, rpc, feed, settings.http_timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - mapped to an acquisition code, fail closed
            raise map_rpc_exception(exc, self.name) from exc
        received_at = ctx.now()
        block = out["block"]
        try:
            (decimals,) = abi_decode(["uint8"], out["decimals_raw"])
            round_id, answer, _started_at, updated_at, _answered_in = abi_decode(LATEST_ROUND_TYPES, out["round_raw"])
        except Exception as exc:  # noqa: BLE001
            raise AcquisitionError(ReasonCode.INVALID_RESPONSE, f"chainlink returned undecodable data: {type(exc).__name__}") from exc
        if answer <= 0:
            raise AcquisitionError(ReasonCode.INVALID_RESPONSE, f"chainlink answer {answer} is not a positive price")
        value = Decimal(answer).scaleb(-int(decimals))
        try:
            observed_at: datetime | str = datetime.fromtimestamp(int(updated_at), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            observed_at = str(updated_at)  # engine flags INVALID_TIMESTAMP
        return RawEvidence(
            provider=self.name,
            source_type=self.source_type,
            mode=self.mode,
            claim="asset_price",
            value=value,
            unit="USD",
            observed_at=observed_at,
            received_at=received_at,
            request_descriptor={
                "method": "eth_call",
                "chain_id": block.chain_id,
                "feed": feed,
                "selectors": {"decimals": DECIMALS_SELECTOR, "latestRoundData": LATEST_ROUND_DATA_SELECTOR},
                "block_number": block.number,
            },
            response_body=out["decimals_raw"] + out["round_raw"],
            endpoint_id=f"chainlink:{feed.lower()}",
            upstream_source=f"chainlink:{feed.lower()}:round:{round_id}",
            source_family=self.family,
            network=f"eip155:{block.chain_id}",
            chain_ref=ChainReference(chain_id=block.chain_id, block_number=block.number, block_hash=block.hash, contract=feed),
        )
