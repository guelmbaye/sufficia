"""Intent compiler test configuration: makes ``sufficia`` and ``intent_helpers`` importable
regardless of pytest's import mode or whether the package is installed."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
API_ROOT = HERE.parents[1]
for p in (str(API_ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from intent_helpers import make_settings  # noqa: E402


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def llm_settings():
    return make_settings(anthropic_api_key="sk-test-SECRET-KEY-123", intent_compiler="llm")
