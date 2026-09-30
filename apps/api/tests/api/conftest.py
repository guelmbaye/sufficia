"""API test harness: a fresh app per test module with an in-memory database and the in-process
LOCAL_EVM chain (real EvidenceGate bytecode on py-evm). No network access is needed."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api_helpers import Flow, make_settings
from sufficia.api.app import create_app
from sufficia.evidence.simulated_market import MARKET


@pytest.fixture(scope="module")
def client():
    MARKET.reset()
    app = create_app(make_settings())
    with TestClient(app) as c:
        yield c
    MARKET.reset()


@pytest.fixture(autouse=True)
def _reset_market():
    MARKET.reset()
    yield
    MARKET.reset()


@pytest.fixture
def flow(client) -> Flow:
    return Flow(client)
