"""Blocking EVM read helpers shared by ONCHAIN / ORACLE adapters.

All reads of one observation are pinned to a single block (``latest`` resolved once), so the
value, the block number/hash and the block timestamp are mutually consistent. Adapters run these
helpers in ``asyncio.to_thread`` because web3's HTTP provider is blocking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from web3 import HTTPProvider, Web3

from ...domain.reasons import ReasonCode
from ..base import AcquisitionError

Web3Factory = Callable[[str, float], Web3]


def default_web3_factory(rpc_url: str, timeout: float) -> Web3:
    return Web3(HTTPProvider(rpc_url, request_kwargs={"timeout": timeout}))


@dataclass
class BlockSnapshot:
    chain_id: int
    number: int
    hash: str
    timestamp: int


def _hex(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        text = bytes(value).hex()
    else:
        text = value.hex() if hasattr(value, "hex") else str(value)
    return text if text.startswith("0x") else "0x" + text


def latest_block(w3: Web3) -> BlockSnapshot:
    chain_id = int(w3.eth.chain_id)
    block = w3.eth.get_block("latest")
    return BlockSnapshot(chain_id=chain_id, number=int(block["number"]), hash=_hex(block["hash"]), timestamp=int(block["timestamp"]))


def eth_call(w3: Web3, to: str, data: str, block_number: int) -> bytes:
    return bytes(w3.eth.call({"to": Web3.to_checksum_address(to), "data": data}, block_identifier=block_number))


def map_rpc_exception(exc: BaseException, provider: str) -> AcquisitionError:
    """Maps web3 / transport / ABI errors to acquisition codes (fail closed, never a value)."""
    if isinstance(exc, AcquisitionError):
        return exc
    name = type(exc).__name__
    module = type(exc).__module__ or ""
    try:
        import requests

        if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
            return AcquisitionError(ReasonCode.SOURCE_UNAVAILABLE, f"{provider} RPC unreachable: {name}")
        if isinstance(exc, requests.exceptions.HTTPError):
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 429:
                return AcquisitionError(ReasonCode.RATE_LIMITED, f"{provider} RPC rate limited")
            return AcquisitionError(ReasonCode.SOURCE_UNAVAILABLE, f"{provider} RPC HTTP error {status}")
    except ImportError:  # pragma: no cover
        pass
    from web3 import exceptions as w3e

    if isinstance(exc, (w3e.ProviderConnectionError, w3e.TimeExhausted, w3e.RequestTimedOut, TimeoutError, ConnectionError)):
        return AcquisitionError(ReasonCode.SOURCE_UNAVAILABLE, f"{provider} RPC unreachable: {name}")
    if isinstance(exc, w3e.TooManyRequests):
        return AcquisitionError(ReasonCode.RATE_LIMITED, f"{provider} RPC rate limited")
    if isinstance(exc, (w3e.ContractLogicError, w3e.BadFunctionCallOutput)) or module.startswith("eth_abi"):
        return AcquisitionError(ReasonCode.INVALID_RESPONSE, f"{provider} call returned unusable data: {name}")
    if isinstance(exc, (w3e.Web3RPCError, w3e.BadResponseFormat, w3e.BlockNotFound)):
        return AcquisitionError(ReasonCode.SOURCE_UNAVAILABLE, f"{provider} RPC error: {name}")
    return AcquisitionError(ReasonCode.INVALID_RESPONSE, f"{provider} read failed: {name}")
