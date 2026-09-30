"""RPC failover (fake providers only: no network) and transaction delivery edge cases."""

from __future__ import annotations

import secrets
from typing import Any, Callable, Optional

import pytest
import requests
from web3 import Web3
from web3.datastructures import NamedElementOnion
from web3.exceptions import Web3RPCError
from web3.providers.base import BaseProvider

from sufficia.blockchain.gateway import ChainUnavailable
from sufficia.blockchain.web3_gateway import (
    ConfigurationError,
    FailoverProvider,
    Web3Gateway,
    http_providers,
    is_connection_error,
    redact_url,
)
from sufficia.domain.enums import TxPurpose, TxStatus

CHAIN_ID = 10143


def http_error(status: int) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(f"{status} error", response=response)


class FakeProvider(BaseProvider):
    """Answers eth_chainId / eth_blockNumber, or raises the configured error."""

    def __init__(self, name: str, *, error: Optional[BaseException] = None, fail_times: Optional[int] = None) -> None:
        super().__init__()
        self.name = name
        self.error = error
        self.fail_times = fail_times  # None = always fail when error is set
        self.calls: list[str] = []

    def make_request(self, method: Any, params: Any) -> Any:
        self.calls.append(method)
        if self.error is not None and (self.fail_times is None or self.fail_times > 0):
            if self.fail_times is not None:
                self.fail_times -= 1
            raise self.error
        results = {"eth_chainId": hex(CHAIN_ID), "eth_blockNumber": hex(1234 if self.name == "primary" else 5678)}
        if method not in results:
            return {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": f"{method} not supported"}}
        return {"jsonrpc": "2.0", "id": 1, "result": results[method]}

    def is_connected(self, show_traceback: bool = False) -> bool:
        return self.error is None


def w3_for(*providers: BaseProvider, **kwargs: Any) -> tuple[Web3, FailoverProvider]:
    failover = FailoverProvider(list(providers), labels=[p.name for p in providers], **kwargs)
    return Web3(failover), failover


def test_primary_healthy_fallback_untouched():
    primary, fallback = FakeProvider("primary"), FakeProvider("fallback")
    w3, fp = w3_for(primary, fallback)
    assert w3.eth.chain_id == CHAIN_ID
    assert w3.eth.block_number == 1234
    assert fallback.calls == [] and fp.active_label == "primary" and fp.failovers == 0


@pytest.mark.parametrize(
    "error",
    [
        requests.exceptions.ConnectionError("refused"),
        requests.exceptions.ReadTimeout("slow"),
        http_error(502),
        http_error(503),
        http_error(429),
        ConnectionResetError("reset"),
        TimeoutError("timeout"),
    ],
    ids=["conn", "timeout", "502", "503", "429", "reset", "oserror-timeout"],
)
def test_connection_errors_retry_once_then_fail_over(error):
    primary, fallback = FakeProvider("primary", error=error), FakeProvider("fallback")
    w3, fp = w3_for(primary, fallback)
    assert w3.eth.block_number == 5678
    assert len(primary.calls) == 2  # first attempt + one retry on the primary
    assert fallback.calls == ["eth_blockNumber"]
    assert fp.active_label == "fallback" and fp.failovers == 1
    # Sticky: the next request goes straight to the fallback.
    w3.eth.block_number
    assert len(primary.calls) == 2 and len(fallback.calls) == 2


def test_transient_primary_error_recovers_on_retry():
    primary, fallback = FakeProvider("primary", error=requests.exceptions.ConnectionError("blip"), fail_times=1), FakeProvider("fallback")
    w3, fp = w3_for(primary, fallback)
    assert w3.eth.block_number == 1234
    assert len(primary.calls) == 2 and fallback.calls == [] and fp.failovers == 0


def test_all_endpoints_down_raises_chain_unavailable():
    primary = FakeProvider("primary", error=requests.exceptions.ConnectionError("down"))
    fallback = FakeProvider("fallback", error=http_error(500))
    w3, fp = w3_for(primary, fallback)
    with pytest.raises(ChainUnavailable) as exc:
        w3.eth.block_number
    assert len(primary.calls) == 2 and len(fallback.calls) == 2
    assert "primary" in str(exc.value) and "fallback" in str(exc.value)
    assert fp.last_error


def test_rpc_errors_and_client_errors_do_not_fail_over():
    # A node that answered with a JSON-RPC error is not a connection problem.
    primary, fallback = FakeProvider("primary"), FakeProvider("fallback")
    w3, _ = w3_for(primary, fallback)
    with pytest.raises(Web3RPCError):
        w3.eth.gas_price
    assert fallback.calls == []
    # HTTP 4xx (other than 408/429) is a request problem, not an outage.
    primary = FakeProvider("primary", error=http_error(400))
    fallback = FakeProvider("fallback")
    w3, _ = w3_for(primary, fallback)
    with pytest.raises(requests.exceptions.HTTPError):
        w3.eth.block_number
    assert fallback.calls == [] and len(primary.calls) == 1
    # Programming errors propagate untouched.
    primary = FakeProvider("primary", error=ValueError("bug"))
    w3, _ = w3_for(primary, FakeProvider("fallback"))
    with pytest.raises(ValueError):
        w3.eth.block_number


def test_primary_is_rechecked_after_cooldown():
    primary = FakeProvider("primary", error=requests.exceptions.ConnectionError("down"), fail_times=2)
    fallback = FakeProvider("fallback")
    w3, fp = w3_for(primary, fallback, recheck_primary_after=0.0)
    assert w3.eth.block_number == 5678 and fp.active_label == "fallback"
    # Cooldown elapsed (0 s): the primary, now healthy, is tried first again.
    assert w3.eth.block_number == 1234 and fp.active_label == "primary"


def test_is_connection_error_classification():
    assert is_connection_error(requests.exceptions.ConnectionError())
    assert is_connection_error(http_error(500)) and is_connection_error(http_error(408))
    assert not is_connection_error(http_error(401))
    assert not is_connection_error(ValueError("x"))
    assert not is_connection_error(Web3RPCError("execution reverted"))


def test_http_provider_construction_is_offline_and_redacted():
    providers, labels = http_providers(
        ["https://rpc.example.org/v1/SECRET_KEY?apikey=abc", None, "https://rpc.example.org/v1/SECRET_KEY?apikey=abc", "http://127.0.0.1:8545"]
    )
    assert len(providers) == 2  # empty and duplicate URLs dropped
    assert labels == ["https://rpc.example.org", "http://127.0.0.1:8545"]
    assert all("SECRET" not in label for label in labels)
    assert providers[0].exception_retry_configuration is None  # FailoverProvider owns retries
    assert redact_url("not a url") == "<rpc>"
    with pytest.raises(ConfigurationError):
        http_providers([None, ""])
    with pytest.raises(ConfigurationError):
        FailoverProvider([])


# ---------------------------------------------------------------------------------------------
# Gateway over forwarding providers (in-process chain behind fake "endpoints")
# ---------------------------------------------------------------------------------------------


class ForwardingProvider(BaseProvider):
    """A fake RPC endpoint that forwards to an in-process chain; can go down per method."""

    def __init__(self, target: Web3, name: str) -> None:
        super().__init__()
        self.target = target
        self.name = name
        self.down = False
        self.fail_methods: set[str] = set()
        self.after_forward: Optional[Callable[[str], None]] = None
        self.calls: list[str] = []

    def make_request(self, method: Any, params: Any) -> Any:
        self.calls.append(method)
        if self.down or method in self.fail_methods:
            raise requests.exceptions.ConnectionError(f"{self.name} unreachable")
        fn = self.target.provider.request_func(self.target, NamedElementOnion([]))
        response = fn(method, params)
        if self.after_forward is not None:
            self.after_forward(method)
        return response


def gateway_over(chain: Any, *providers: ForwardingProvider) -> Web3Gateway:
    return Web3Gateway(
        providers=list(providers),
        provider_labels=[p.name for p in providers],
        info=chain.gateway.info,
        signer_keys=chain.keys,
        clock_sync=chain.sync_clock,
        view_block="pending",
        simulate_block="pending",
        poll_interval=0.01,
        tx_timeout=5.0,
    )


@pytest.mark.evm
def test_gateway_transacts_through_fallback_when_primary_is_down(local_chain):
    primary, fallback = ForwardingProvider(local_chain.w3, "primary"), ForwardingProvider(local_chain.w3, "fallback")
    primary.down = True
    gateway = gateway_over(local_chain, primary, fallback)
    health = gateway.health()
    assert health["ok"] and health["rpc"]["active"] == "fallback" and health["rpc"]["failovers"] == 1
    result = gateway.register_intent("0x" + secrets.token_hex(32), "0x" + secrets.token_hex(32))
    assert result.status == TxStatus.CONFIRMED and result.tx_hash
    assert [e["event"] for e in result.events] == ["IntentRegistered"]
    assert primary.calls.count("eth_chainId") <= 2  # tried (+1 retry) once, then sticky on the fallback


@pytest.mark.evm
def test_gateway_all_rpcs_down_fails_closed(local_chain):
    primary, fallback = ForwardingProvider(local_chain.w3, "primary"), ForwardingProvider(local_chain.w3, "fallback")
    primary.down = fallback.down = True
    gateway = gateway_over(local_chain, primary, fallback)
    health = gateway.health()
    assert health["ok"] is False and health["error"].startswith("ChainUnavailable")
    result = gateway.register_intent("0x" + secrets.token_hex(32), "0x" + secrets.token_hex(32))
    assert result.status == TxStatus.FAILED and result.tx_hash is None and result.error_name == "ChainUnavailable"
    with pytest.raises(ChainUnavailable):
        gateway.get_authorization("0x" + "00" * 32)


@pytest.mark.evm
def test_broadcast_lost_is_submitted_not_failed(local_chain):
    """sendRawTransaction unreachable everywhere: delivery unknown -> SUBMITTED + hash; reconcile finds nothing."""
    primary, fallback = ForwardingProvider(local_chain.w3, "primary"), ForwardingProvider(local_chain.w3, "fallback")
    primary.fail_methods = fallback.fail_methods = {"eth_sendRawTransaction"}
    gateway = gateway_over(local_chain, primary, fallback)
    result = gateway.register_intent("0x" + secrets.token_hex(32), "0x" + secrets.token_hex(32))
    assert result.status == TxStatus.SUBMITTED and result.tx_hash and not result.ok
    assert result.error_name == "ChainUnavailable"
    # Reconciliation: the node never saw it (safe to rebuild; never assume success).
    assert gateway.find_transaction(result.tx_hash) is None


@pytest.mark.evm
def test_broadcast_delivered_but_not_acknowledged_is_reconciled(local_chain):
    """The node accepted the tx but the response was lost; the retry is rejected as a duplicate. The
    pipeline sees the node knows the exact signed tx and waits for it instead of reporting a failure."""
    primary, fallback = ForwardingProvider(local_chain.w3, "primary"), ForwardingProvider(local_chain.w3, "fallback")
    state = {"dropped": False}

    def drop_first_ack(method: str) -> None:
        if method == "eth_sendRawTransaction" and not state["dropped"]:
            state["dropped"] = True
            raise requests.exceptions.ConnectionError("response lost")

    primary.after_forward = drop_first_ack
    gateway = gateway_over(local_chain, primary, fallback)
    result = gateway.register_intent("0x" + secrets.token_hex(32), "0x" + secrets.token_hex(32))
    assert state["dropped"]
    assert result.status == TxStatus.CONFIRMED, (result.error_name, result.error)
    assert [e["event"] for e in result.events] == ["IntentRegistered"]
    found = gateway.find_transaction(result.tx_hash)
    assert found is not None and found.status == TxStatus.CONFIRMED and found.purpose == TxPurpose.INTENT_REGISTER
