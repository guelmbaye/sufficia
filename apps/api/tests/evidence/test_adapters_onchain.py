"""Chainlink (ORACLE) and Uniswap-V2 pool (ONCHAIN) adapters against an in-memory web3 provider."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
import requests
from web3.exceptions import ContractLogicError

from sufficia.domain.enums import SourceMode, SourceType
from sufficia.domain.reasons import ReasonCode
from sufficia.evidence.adapters import ChainlinkPriceAdapter, UniswapV2PoolLiquidityAdapter
from sufficia.evidence.adapters.chainlink import DECIMALS_SELECTOR, LATEST_ROUND_DATA_SELECTOR
from sufficia.evidence.adapters.uniswap_v2_pool import GET_RESERVES_SELECTOR
from sufficia.evidence.base import AcquisitionError

UPDATED_AT = 1_790_000_100


def _feed(h, answer=42_812_345, decimals=8, updated_at=UPDATED_AT):
    p = h.FakeEVMProvider(block_number=777, block_hash="0x" + "cd" * 32)
    p.calls[DECIMALS_SELECTOR] = h.abi_encode(["uint8"], [decimals])
    p.calls[LATEST_ROUND_DATA_SELECTOR] = h.abi_encode(
        ["uint80", "int256", "uint256", "uint256", "uint80"], [18446744073709551999, answer, updated_at - 5, updated_at, 18446744073709551999]
    )
    return p


def _cl_ctx(h, **kw):
    return h.make_ctx(settings=h.make_settings(chainlink_feed_address=h.FEED, **kw))


def test_chainlink_price_value_timestamp_and_chain_ref(h):
    provider = _feed(h)
    factory = h.web3_factory_for(provider)
    raw = h.run(ChainlinkPriceAdapter(factory).fetch(h.req("asset_price"), _cl_ctx(h)))
    assert raw.value == Decimal("0.42812345") and isinstance(raw.value, Decimal)
    assert raw.unit == "USD" and raw.source_type == SourceType.ORACLE and raw.mode == SourceMode.LIVE
    assert raw.observed_at == datetime.fromtimestamp(UPDATED_AT, tz=timezone.utc)  # oracle time, not read time
    assert raw.chain_ref.chain_id == 10143 and raw.chain_ref.block_number == 777
    assert raw.chain_ref.block_hash == "0x" + "cd" * 32 and raw.chain_ref.contract == h.FEED
    assert raw.request_descriptor["selectors"] == {"decimals": "0x313ce567", "latestRoundData": "0xfeaf968c"}
    assert raw.request_descriptor["chain_id"] == 10143 and raw.request_descriptor["feed"] == h.FEED
    assert raw.response_body == provider.calls[DECIMALS_SELECTOR] + provider.calls[LATEST_ROUND_DATA_SELECTOR]
    assert raw.source_family == "chainlink"
    # both reads pinned to the same block
    calls = [p for m, p in provider.requests if m == "eth_call"]
    assert {c[1] for c in calls} == {hex(777)}
    assert factory.seen == ["http://rpc.test"]  # defaults to monad_rpc_url


def test_chainlink_uses_dedicated_rpc_when_set(h):
    factory = h.web3_factory_for(_feed(h))
    h.run(ChainlinkPriceAdapter(factory).fetch(h.req("asset_price"), _cl_ctx(h, chainlink_rpc_url="http://oracle-rpc.test")))
    assert factory.seen == ["http://oracle-rpc.test"]


@pytest.mark.parametrize("answer", [0, -5])
def test_chainlink_rejects_non_positive_answer(h, answer):
    with pytest.raises(AcquisitionError) as e:
        h.run(ChainlinkPriceAdapter(h.web3_factory_for(_feed(h, answer=answer))).fetch(h.req("asset_price"), _cl_ctx(h)))
    assert e.value.code == ReasonCode.INVALID_RESPONSE


def test_chainlink_error_mapping(h):
    p = _feed(h)
    p.calls[LATEST_ROUND_DATA_SELECTOR] = b""  # not a contract / empty return
    with pytest.raises(AcquisitionError) as e:
        h.run(ChainlinkPriceAdapter(h.web3_factory_for(p)).fetch(h.req("asset_price"), _cl_ctx(h)))
    assert e.value.code == ReasonCode.INVALID_RESPONSE

    p = _feed(h)
    p.fail_with = requests.exceptions.ConnectionError("down")
    with pytest.raises(AcquisitionError) as e:
        h.run(ChainlinkPriceAdapter(h.web3_factory_for(p)).fetch(h.req("asset_price"), _cl_ctx(h)))
    assert e.value.code == ReasonCode.SOURCE_UNAVAILABLE

    p = _feed(h)
    p.calls[LATEST_ROUND_DATA_SELECTOR] = ContractLogicError("execution reverted")
    with pytest.raises(AcquisitionError) as e:
        h.run(ChainlinkPriceAdapter(h.web3_factory_for(p)).fetch(h.req("asset_price"), _cl_ctx(h)))
    assert e.value.code == ReasonCode.INVALID_RESPONSE


def test_chainlink_not_configured(h):
    assert not ChainlinkPriceAdapter().is_configured(h.make_settings())
    with pytest.raises(AcquisitionError) as e:
        h.run(ChainlinkPriceAdapter().fetch(h.req("asset_price"), h.make_ctx()))
    assert e.value.code == ReasonCode.SOURCE_NOT_CONFIGURED


# ---- Uniswap V2 pool -----------------------------------------------------------------------------


def _pool(h, r0, r1):
    p = h.FakeEVMProvider(block_number=4242, block_hash="0x" + "ef" * 32, timestamp=1_790_000_200)
    p.calls[GET_RESERVES_SELECTOR] = h.abi_encode(["uint112", "uint112", "uint32"], [r0, r1, 1_790_000_000])
    return p


def test_pool_liquidity_is_twice_the_stable_reserve(h):
    provider = _pool(h, 5 * 10**23, 46_220_123_456)  # token1 = USDC (6 decimals): 46,220.123456
    ctx = h.make_ctx(settings=h.make_settings(pool_address=h.POOL))
    raw = h.run(UniswapV2PoolLiquidityAdapter(h.web3_factory_for(provider)).fetch(h.req("available_liquidity"), ctx))
    assert raw.value == Decimal("92440.246912")
    assert raw.source_type == SourceType.ONCHAIN and raw.source_family == "onchain-pool"
    assert raw.observed_at == datetime.fromtimestamp(1_790_000_200, tz=timezone.utc)  # block timestamp
    assert raw.chain_ref.block_number == 4242 and raw.chain_ref.block_hash == "0x" + "ef" * 32
    assert raw.response_body == provider.calls[GET_RESERVES_SELECTOR]
    assert raw.request_descriptor["valuation"]["method"] == "2x_stable_reserve"


def test_pool_quote_index_zero(h):
    provider = _pool(h, 1_000_000, 7)
    ctx = h.make_ctx(settings=h.make_settings(pool_address=h.POOL, pool_quote_token_index=0))
    raw = h.run(UniswapV2PoolLiquidityAdapter(h.web3_factory_for(provider)).fetch(h.req("available_liquidity"), ctx))
    assert raw.value == Decimal(2)


def test_pool_errors(h):
    p = _pool(h, 1, 1)
    p.calls[GET_RESERVES_SELECTOR] = b"\x00" * 10
    ctx = h.make_ctx(settings=h.make_settings(pool_address=h.POOL))
    with pytest.raises(AcquisitionError) as e:
        h.run(UniswapV2PoolLiquidityAdapter(h.web3_factory_for(p)).fetch(h.req("available_liquidity"), ctx))
    assert e.value.code == ReasonCode.INVALID_RESPONSE
    p = _pool(h, 1, 1)
    p.fail_with = requests.exceptions.ReadTimeout("slow")
    with pytest.raises(AcquisitionError) as e:
        h.run(UniswapV2PoolLiquidityAdapter(h.web3_factory_for(p)).fetch(h.req("available_liquidity"), ctx))
    assert e.value.code == ReasonCode.SOURCE_UNAVAILABLE
    assert not UniswapV2PoolLiquidityAdapter().is_configured(h.make_settings())
