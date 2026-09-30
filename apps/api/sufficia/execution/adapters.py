"""Execution adapters: turn an authorized Evidence Contract into the exact onchain action.

An adapter is pure (no network, no clock): given the frozen contract, the decision, the evidence the
decision used, the chain addresses, a nonce and a deadline, it returns the :class:`CanonicalAction` whose
hash the authorization binds and that ``EvidenceGate.executeAuthorized`` will run byte for byte.

  * ``demo_swap`` (:class:`DemoSwapAdapter`): the gate calls
    ``DemoSwapRouter.swapExactIn(usdc, tokenX, amountIn, minOut, recipient)`` with its own demo USDC.
    ``minOut`` is derived from the FRESH ``asset_price`` evidence the decision used (highest price when
    several: the lowest, most conservative output expectation)::

        expected = amount_usd / price * 10**token_x_decimals
        minOut   = floor(expected * (10000 - slippage_bps) / 10000)

    Without usable price evidence ``minOut = 1`` (and the description says so).
  * ``transfer`` (:class:`TransferAdapter`): the gate calls ``usdc.transfer(recipient, amount)``;
    the gate's output check verifies the recipient's USDC balance grew by ``amount``.

Adapters refuse (``AdapterError``) what they cannot represent faithfully -- they never approximate an
action into a different one. :func:`verify_spend` independently decodes the calldata of a prepared action
and checks it against the declared ``spend_raw`` / ``max_amount_in`` / ``min_amount_out`` / ``recipient``.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from decimal import ROUND_DOWN, Decimal, localcontext
from typing import Any, Iterable, Optional, Sequence

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import is_address, keccak, to_checksum_address

from ..blockchain.gateway import ChainInfo
from ..canonical.canonicalize import format_decimal
from ..canonical.hashes import authorization_nonce
from ..domain.enums import ActionType, DecisionState, EvidenceStatus
from ..domain.models import CanonicalAction, Decision, EvidenceContract, EvidenceItem, Money

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
PRICE_CLAIM = "asset_price"
BPS = 10_000
SWAP_EXACT_IN_SIGNATURE = "swapExactIn(address,address,uint256,uint256,address)"
TRANSFER_SIGNATURE = "transfer(address,uint256)"
TOKEN_X_ALIASES = frozenset({"TOKEN-X", "TOKENX", "TOKEN_X"})
USDC_ALIASES = frozenset({"USDC", "DUSDC"})


class AdapterError(ValueError):
    """The adapter cannot prepare an exact action for this contract / chain configuration."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


def selector(signature: str) -> bytes:
    return keccak(text=signature)[:4]


def encode_call(signature: str, types: Sequence[str], args: Sequence[Any]) -> str:
    return "0x" + (selector(signature) + abi_encode(list(types), list(args))).hex()


def to_base_units(amount: Decimal, decimals: int) -> int:
    """Whole-token Decimal -> integer base units, rounded DOWN (never exceeds the contract amount)."""
    with localcontext() as ctx:
        ctx.prec = 80
        return int((Decimal(amount) * (Decimal(10) ** decimals)).to_integral_value(rounding=ROUND_DOWN))


def format_units(value: int, decimals: int, places: int = 2) -> str:
    with localcontext() as ctx:
        ctx.prec = 80
        whole = (Decimal(int(value)) / (Decimal(10) ** decimals)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_DOWN)
    return format_decimal(whole)


def action_nonce(decision: Decision) -> str:
    """The nonce build_authorization expects for this decision."""
    return authorization_nonce(decision.fingerprint)


def action_deadline(decision: Decision) -> int:
    """Default action deadline: the end of the evidence-bound authority window (unix seconds, floor)."""
    if decision.authority_window is None:
        raise AdapterError("NO_AUTHORITY_WINDOW", f"decision {decision.decision_id} is {decision.state.value}")
    return math.floor(decision.authority_window.expires_at.timestamp())


def _checksum(value: Optional[str], what: str) -> str:
    if not value or not is_address(value):
        raise AdapterError("CHAIN_NOT_CONFIGURED", f"{what} address is not configured")
    return to_checksum_address(value)


def price_observations(
    contract: EvidenceContract, decision: Decision, evidence_items: Iterable[EvidenceItem]
) -> list[tuple[str, Decimal]]:
    """``(evidence_id, price)`` of every FRESH, VALID ``asset_price`` observation the decision assessed.

    Uses the engine's normalized value; stale, invalid, conflicted or duplicate items are ignored.
    """
    price_requirements = {r.id for r in contract.requirements if r.claim == PRICE_CLAIM}
    if not price_requirements:
        return []
    valid = {
        a.evidence_id: a
        for a in decision.evidence_assessments
        if a.status == EvidenceStatus.VALID and a.requirement_id in price_requirements
    }
    out: list[tuple[str, Decimal]] = []
    seen: set[str] = set()
    for item in evidence_items:
        if item.evidence_id in seen or item.requirement_id not in price_requirements:
            continue
        if item.claim.name != PRICE_CLAIM or item.evidence_id not in valid:
            continue
        assessment = valid[item.evidence_id]
        raw = assessment.normalized_value
        try:
            price = Decimal(raw) if raw is not None else None
        except Exception:  # noqa: BLE001
            price = None
        if price is None or not price.is_finite() or price <= 0:
            continue
        seen.add(item.evidence_id)
        out.append((item.evidence_id, price))
    return sorted(out)


# ---------------------------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------------------------


class ExecutionAdapter(ABC):
    name: str = ""
    label: str = ""

    @abstractmethod
    def prepare(
        self,
        *,
        contract: EvidenceContract,
        decision: Decision,
        evidence_items: list[EvidenceItem],
        chain: ChainInfo,
        nonce: str,
        deadline: int,
        settings: Any,
    ) -> CanonicalAction: ...

    # -- shared checks ----------------------------------------------------------------------------

    @staticmethod
    def _common_checks(contract: EvidenceContract, decision: Decision, chain: ChainInfo, nonce: str, deadline: int) -> None:
        if decision.contract_id != contract.contract_id:
            raise AdapterError("CONTRACT_MISMATCH", "decision was produced for another Evidence Contract")
        if contract.action.max_amount.currency.upper() != "USDC":
            raise AdapterError(
                "UNSUPPORTED_CURRENCY", f"execution is denominated in USDC, got {contract.action.max_amount.currency}"
            )
        if not isinstance(deadline, int) or deadline <= 0 or deadline >= 2**64:
            raise AdapterError("INVALID_DEADLINE", f"deadline must be a uint64 unix timestamp, got {deadline!r}")
        text = nonce[2:] if nonce.startswith("0x") else nonce
        if len(text) != 64:
            raise AdapterError("INVALID_NONCE", "nonce must be 32 bytes (0x + 64 hex)")
        try:
            bytes.fromhex(text)
        except ValueError:
            raise AdapterError("INVALID_NONCE", "nonce is not hex") from None
        _checksum(chain.gate_address, "EvidenceGate")

    @staticmethod
    def _amount_units(contract: EvidenceContract, chain: ChainInfo) -> int:
        units = to_base_units(contract.action.max_amount.value, chain.usdc_decimals)
        if units <= 0:
            raise AdapterError("INVALID_AMOUNT", "amount rounds to zero USDC base units")
        return units


class DemoSwapAdapter(ExecutionAdapter):
    """USDC -> TOKEN-X through DemoSwapRouter, paid from the gate's own demo USDC."""

    name = "demo_swap"
    label = "DemoSwapRouter"

    def __init__(self, supported_assets: Iterable[str] = TOKEN_X_ALIASES) -> None:
        self.supported_assets = frozenset(a.upper() for a in supported_assets)

    def prepare(
        self,
        *,
        contract: EvidenceContract,
        decision: Decision,
        evidence_items: list[EvidenceItem],
        chain: ChainInfo,
        nonce: str,
        deadline: int,
        settings: Any,
    ) -> CanonicalAction:
        self._common_checks(contract, decision, chain, nonce, deadline)
        intent = contract.action
        if intent.type != ActionType.BUY:
            raise AdapterError("UNSUPPORTED_ACTION", f"demo_swap only executes BUY actions, got {intent.type.value}")
        if intent.asset.upper() not in self.supported_assets:
            raise AdapterError(
                "UNSUPPORTED_ASSET",
                f"demo_swap can only buy {', '.join(sorted(self.supported_assets))}, got {intent.asset}",
            )
        gate = _checksum(chain.gate_address, "EvidenceGate")
        usdc = _checksum(chain.usdc_address, "USDC")
        token_x = _checksum(chain.token_x_address, "TOKEN-X")
        router = _checksum(chain.router_address, "DemoSwapRouter")
        recipient = _checksum(getattr(settings, "execution_recipient", None) or gate, "execution recipient")
        slippage_bps = int(getattr(settings, "slippage_bps", 100))
        if not 0 <= slippage_bps < BPS:
            raise AdapterError("INVALID_SLIPPAGE", f"slippage_bps must be within 0..9999, got {slippage_bps}")

        amount_in = self._amount_units(contract, chain)
        prices = price_observations(contract, decision, evidence_items)
        amount_text = format_decimal(intent.max_amount.value)
        if prices:
            evidence_id, price = max(prices, key=lambda p: (p[1], p[0]))
            with localcontext() as ctx:
                ctx.prec = 80
                expected = Decimal(intent.max_amount.value) / price * (Decimal(10) ** chain.token_x_decimals)
                min_out = int(
                    (expected * Decimal(BPS - slippage_bps) / Decimal(BPS)).to_integral_value(rounding=ROUND_DOWN)
                )
            min_out = max(min_out, 1)
            description = (
                f"Swap {amount_text} USDC → ≥{format_units(min_out, chain.token_x_decimals)} {intent.asset} "
                f"via {self.label}"
            )
        else:
            min_out = 1
            description = (
                f"Swap {amount_text} USDC → {intent.asset} via {self.label} "
                "(no fresh asset_price evidence: minimum output is 1 base unit)"
            )

        data = encode_call(
            SWAP_EXACT_IN_SIGNATURE,
            ["address", "address", "uint256", "uint256", "address"],
            [usdc, token_x, amount_in, min_out, recipient],
        )
        return CanonicalAction(
            action_type=intent.type,
            asset=intent.asset,
            amount=Money(value=intent.max_amount.value, currency=intent.max_amount.currency),
            chain_id=int(chain.chain_id),
            gate_address=gate,
            target=router,
            value=0,
            data=data,
            asset_in=usdc,
            max_amount_in=amount_in,
            spend_raw=amount_in,
            amount_decimals=chain.usdc_decimals,
            asset_out=token_x,
            min_amount_out=min_out,
            recipient=recipient,
            deadline=int(deadline),
            nonce=nonce.lower() if nonce.startswith("0x") else "0x" + nonce.lower(),
            adapter=self.name,
            description=description,
        )


class TransferAdapter(ExecutionAdapter):
    """``usdc.transfer(recipient, amount)`` from the gate (no approval; output floor = amount)."""

    name = "transfer"
    label = "USDC transfer"

    def prepare(
        self,
        *,
        contract: EvidenceContract,
        decision: Decision,
        evidence_items: list[EvidenceItem],
        chain: ChainInfo,
        nonce: str,
        deadline: int,
        settings: Any,
    ) -> CanonicalAction:
        self._common_checks(contract, decision, chain, nonce, deadline)
        intent = contract.action
        gate = _checksum(chain.gate_address, "EvidenceGate")
        usdc = _checksum(chain.usdc_address, "USDC")
        configured = getattr(settings, "execution_recipient", None)
        if not configured:
            raise AdapterError("RECIPIENT_REQUIRED", "the transfer adapter needs EXECUTION_RECIPIENT")
        recipient = _checksum(configured, "execution recipient")
        if recipient.lower() == gate.lower():
            raise AdapterError("RECIPIENT_REQUIRED", "a transfer to the gate itself cannot move funds")
        amount = self._amount_units(contract, chain)
        data = encode_call(TRANSFER_SIGNATURE, ["address", "uint256"], [recipient, amount])
        return CanonicalAction(
            action_type=intent.type,
            asset=intent.asset,
            amount=Money(value=intent.max_amount.value, currency=intent.max_amount.currency),
            chain_id=int(chain.chain_id),
            gate_address=gate,
            target=usdc,
            value=0,
            data=data,
            asset_in=ZERO_ADDRESS,
            max_amount_in=0,
            spend_raw=amount,
            amount_decimals=chain.usdc_decimals,
            asset_out=usdc,
            min_amount_out=amount,
            recipient=recipient,
            deadline=int(deadline),
            nonce=nonce.lower() if nonce.startswith("0x") else "0x" + nonce.lower(),
            adapter=self.name,
            description=f"Transfer {format_decimal(intent.max_amount.value)} USDC to {recipient}",
        )


ADAPTERS: dict[str, type[ExecutionAdapter]] = {
    DemoSwapAdapter.name: DemoSwapAdapter,
    TransferAdapter.name: TransferAdapter,
}


def get_adapter(name: str) -> ExecutionAdapter:
    key = (name or "").strip().lower().replace("-", "_")
    cls = ADAPTERS.get(key)
    if cls is None:
        raise AdapterError("UNKNOWN_ADAPTER", f"unknown execution adapter '{name}' (known: {', '.join(sorted(ADAPTERS))})")
    return cls()


def prepare_action(
    adapter: ExecutionAdapter | str,
    *,
    contract: EvidenceContract,
    decision: Decision,
    evidence_items: list[EvidenceItem],
    chain: ChainInfo,
    settings: Any,
    nonce: Optional[str] = None,
    deadline: Optional[int] = None,
) -> CanonicalAction:
    """Convenience: nonce/deadline default to the decision's (authorization nonce, authority window end).
    Only an AUTHORIZE decision has an authority window."""
    if decision.state != DecisionState.AUTHORIZE and deadline is None:
        raise AdapterError("NO_AUTHORITY_WINDOW", f"decision {decision.decision_id} is {decision.state.value}")
    if isinstance(adapter, str):
        adapter = get_adapter(adapter)
    return adapter.prepare(
        contract=contract,
        decision=decision,
        evidence_items=evidence_items,
        chain=chain,
        nonce=nonce or action_nonce(decision),
        deadline=deadline if deadline is not None else action_deadline(decision),
        settings=settings,
    )


# ---------------------------------------------------------------------------------------------
# Calldata verification
# ---------------------------------------------------------------------------------------------


def _same_address(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b or not is_address(a) or not is_address(b):
        return False
    return a.lower() == b.lower()


def _decode_call(data: str, signature: str, types: Sequence[str]) -> Optional[tuple]:
    """Decodes ``data`` as a call of ``signature``; None if the selector differs or the payload is malformed.
    The payload must be exactly the canonical encoding (no trailing bytes, no non-canonical padding)."""
    try:
        raw = bytes.fromhex(data[2:] if data.startswith(("0x", "0X")) else data)
    except (ValueError, AttributeError):
        return None
    if raw[:4] != selector(signature):
        return None
    try:
        values = abi_decode(list(types), raw[4:], strict=True)
    except Exception:  # noqa: BLE001 - any decoding failure == not the call we expect
        return None
    if abi_encode(list(types), list(values)) != raw[4:]:
        return None
    return tuple(values)


def verify_spend(action: CanonicalAction, chain: ChainInfo) -> list[str]:
    """Proves a prepared action against its own calldata and the configured chain.

    Decodes the calldata of the known adapters (``swapExactIn`` / ``transfer``) and returns the list of
    mismatches between what the calldata really does and what the action declares (``spend_raw``,
    ``max_amount_in``, ``min_amount_out``, ``recipient``, assets, target, gate, chain). An empty list means
    the action is internally consistent. An unknown adapter gives ``["unknown_adapter"]`` (fail closed).
    """
    problems: list[str] = []

    def check(ok: bool, name: str) -> None:
        if not ok and name not in problems:
            problems.append(name)

    adapter = (action.adapter or "").strip().lower()
    if adapter not in ADAPTERS:
        return ["unknown_adapter"]

    check(int(action.chain_id) == int(chain.chain_id), "chain_id")
    check(_same_address(action.gate_address, chain.gate_address), "gate_address")
    check(int(action.value) == 0, "value")
    check(int(action.amount_decimals) == int(chain.usdc_decimals), "amount_decimals")
    check(action.spend_raw is not None and int(action.spend_raw) > 0, "spend_raw")

    if adapter == DemoSwapAdapter.name:
        check(_same_address(action.target, chain.router_address), "target")
        check(_same_address(action.asset_in, chain.usdc_address), "asset_in")
        check(_same_address(action.asset_out, chain.token_x_address), "asset_out")
        decoded = _decode_call(action.data, SWAP_EXACT_IN_SIGNATURE, ["address", "address", "uint256", "uint256", "address"])
        if decoded is None:
            check(False, "calldata")
            return problems
        token_in, token_out, amount_in, min_out, recipient = decoded
        check(_same_address(token_in, action.asset_in), "calldata_token_in")
        check(_same_address(token_out, action.asset_out), "calldata_token_out")
        # The router pulls exactly amount_in with the approval the gate grants (max_amount_in).
        check(int(amount_in) == int(action.max_amount_in), "max_amount_in")
        check(action.spend_raw is not None and int(amount_in) == int(action.spend_raw), "spend_raw")
        check(int(min_out) == int(action.min_amount_out) and int(min_out) > 0, "min_amount_out")
        check(_same_address(recipient, action.recipient), "recipient")
        return problems

    # transfer
    check(_same_address(action.target, chain.usdc_address), "target")
    check(_same_address(action.asset_in, ZERO_ADDRESS) and int(action.max_amount_in) == 0, "asset_in")
    check(_same_address(action.asset_out, chain.usdc_address), "asset_out")
    decoded = _decode_call(action.data, TRANSFER_SIGNATURE, ["address", "uint256"])
    if decoded is None:
        check(False, "calldata")
        return problems
    recipient, amount = decoded
    check(_same_address(recipient, action.recipient), "recipient")
    check(not _same_address(recipient, action.gate_address), "recipient")
    check(action.spend_raw is not None and int(amount) == int(action.spend_raw), "spend_raw")
    check(int(amount) == int(action.min_amount_out), "min_amount_out")
    return problems
