"""Typed service errors, mapped 1:1 to HTTP responses by the API layer."""

from __future__ import annotations

from typing import Any, Optional


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, detail: Optional[dict[str, Any]] = None):
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "detail": self.detail}}


def not_found(kind: str, ident: str) -> ApiError:
    return ApiError(404, f"{kind.upper()}_NOT_FOUND", f"{kind} {ident} does not exist", {"id": ident})


def authorization_required(state: str, decision_id: str) -> ApiError:
    """Document 02 §23: execution against a non-AUTHORIZE decision is a 403, whatever the caller does."""
    return ApiError(
        403,
        "EVIDENCE_AUTHORIZATION_REQUIRED",
        f"Decision {decision_id} is {state}: no execution authority exists. NO_AUTHORIZATION was issued and "
        "nothing can be submitted to the EvidenceGate contract.",
        {"decision_id": decision_id, "decision_state": state, "authorization": "NO_AUTHORIZATION"},
    )
