"""Fixtures for the evidence acquisition tests (helpers live in tests/evidence/_helpers.py)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from sufficia.evidence.simulated_market import MARKET


def _load_helpers() -> types.ModuleType:
    name = "sufficia_test_helpers"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).resolve().parents[1] / "evidence" / "_helpers.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def h() -> types.SimpleNamespace:
    return _load_helpers().namespace()


@pytest.fixture(autouse=True)
def _reset_market():
    MARKET.reset()
    yield
    MARKET.reset()
