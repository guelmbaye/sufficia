"""Helpers shared by the API tests (unique module name: test dirs have no __init__.py)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sufficia.config import Settings

GRAND_PRIZE = "Buy up to 100 USDC of TOKEN-X only if smart-money inflows exceed $1M and liquidity exceeds $500K."


def make_settings(**overrides) -> Settings:
    base = dict(
        database_url="sqlite://",
        anthropic_api_key=None,
        intent_compiler="deterministic",
        chain_mode="LOCAL_EVM",
        evidence_profile="demo",
        demo_mode=True,
        log_json=False,
        log_level="WARNING",
        rate_limit_per_minute=0,
        _env_file=None,
    )
    base.update(overrides)
    return Settings(**base)


class Flow:
    """Drives the public HTTP API exactly like the web app does."""

    def __init__(self, client: TestClient):
        self.c = client

    def post(self, path: str, json=None, expect: int | None = None):
        r = self.c.post(path, json=json)
        if expect is not None:
            assert r.status_code == expect, (r.status_code, r.text)
        return r

    def contract(self, text: str = GRAND_PRIZE) -> str:
        intent = self.post("/api/v1/intents", {"text": text}, 201).json()
        c = self.post(f"/api/v1/intents/{intent['intent']['intent_id']}/compile", None, 201).json()
        return c["contract"]["contract_id"]

    def decide(self, contract_id: str, *, acquire: bool = True) -> dict:
        if acquire:
            self.post(f"/api/v1/contracts/{contract_id}/acquire", None, 201)
        return self.post(f"/api/v1/contracts/{contract_id}/decide", None, 201).json()
