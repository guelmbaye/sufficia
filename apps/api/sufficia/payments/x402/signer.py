"""EIP-3009 ``TransferWithAuthorization`` signing for the x402 "exact" scheme on EVM networks.

The signature authorizes a single USDC transfer ``from -> to`` of ``value`` atomic units, valid in the
window ``(validAfter, validBefore)`` and bound to a random 32-byte ``nonce``. USDC marks each nonce as
used on-chain, so one signed authorization can be settled at most once: re-sending the *same* payload
can never cause a double payment, which is what makes the client's retry-after-timeout safe.

The EIP-712 domain is ``{name, version, chainId, verifyingContract=asset}`` where name/version come
from the requirement's ``extra`` (Monad testnet USDC: name "USDC", version "2").
"""

from __future__ import annotations

import secrets
from typing import Any, Callable, Optional

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import is_address, to_checksum_address

from .models import TransferAuthorization

EIP712_DOMAIN_TYPE = [
    {"name": "name", "type": "string"},
    {"name": "version", "type": "string"},
    {"name": "chainId", "type": "uint256"},
    {"name": "verifyingContract", "type": "address"},
]

TRANSFER_WITH_AUTHORIZATION_TYPE = [
    {"name": "from", "type": "address"},
    {"name": "to", "type": "address"},
    {"name": "value", "type": "uint256"},
    {"name": "validAfter", "type": "uint256"},
    {"name": "validBefore", "type": "uint256"},
    {"name": "nonce", "type": "bytes32"},
]


def random_nonce() -> str:
    return "0x" + secrets.token_hex(32)


def transfer_typed_data(
    *, name: str, version: str, chain_id: int, verifying_contract: str, authorization: TransferAuthorization
) -> dict[str, Any]:
    return {
        "types": {"EIP712Domain": EIP712_DOMAIN_TYPE, "TransferWithAuthorization": TRANSFER_WITH_AUTHORIZATION_TYPE},
        "primaryType": "TransferWithAuthorization",
        "domain": {
            "name": name,
            "version": version,
            "chainId": int(chain_id),
            "verifyingContract": to_checksum_address(verifying_contract),
        },
        "message": {
            "from": to_checksum_address(authorization.from_address),
            "to": to_checksum_address(authorization.to),
            "value": int(authorization.value),
            "validAfter": int(authorization.valid_after),
            "validBefore": int(authorization.valid_before),
            "nonce": bytes.fromhex(authorization.nonce[2:]),
        },
    }


class EvidencePayer:
    """Holds the evidence-payment key (server-side only) and signs EIP-3009 authorizations.

    ``sign_count`` exists so tests (and the idempotency invariant) can assert that one acquisition
    produces at most one signature.
    """

    def __init__(self, private_key: str, *, nonce_factory: Callable[[], str] = random_nonce):
        self._account = Account.from_key(private_key)
        self._nonce_factory = nonce_factory
        self.sign_count = 0

    @property
    def address(self) -> str:
        return self._account.address

    def authorize(
        self,
        *,
        to: str,
        value: int,
        valid_after: int,
        valid_before: int,
        name: str,
        version: str,
        chain_id: int,
        asset: str,
        nonce: Optional[str] = None,
    ) -> tuple[TransferAuthorization, str]:
        """Builds and signs one authorization. Returns (authorization, 0x-signature)."""
        if not is_address(to) or not is_address(asset):
            raise ValueError("recipient and asset must be EVM addresses")
        authorization = TransferAuthorization(
            from_address=self.address,
            to=to_checksum_address(to),
            value=int(value),
            valid_after=int(valid_after),
            valid_before=int(valid_before),
            nonce=nonce or self._nonce_factory(),
        )
        typed = transfer_typed_data(
            name=name, version=version, chain_id=chain_id, verifying_contract=asset, authorization=authorization
        )
        signed = Account.sign_typed_data(self._account.key, full_message=typed)
        self.sign_count += 1
        signature = signed.signature.hex()
        if not signature.startswith("0x"):
            signature = "0x" + signature
        return authorization, signature


def recover_authorizer(
    *, name: str, version: str, chain_id: int, verifying_contract: str, authorization: TransferAuthorization, signature: str
) -> str:
    """Recovers the signer of an EIP-3009 authorization (checksummed). Raises on a malformed signature."""
    typed = transfer_typed_data(
        name=name, version=version, chain_id=chain_id, verifying_contract=verifying_contract, authorization=authorization
    )
    return Account.recover_message(encode_typed_data(full_message=typed), signature=signature)
