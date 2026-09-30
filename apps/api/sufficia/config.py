"""Runtime configuration (12-factor, environment variables; see .env.example).

Private keys are server-side only and never exposed through the API.
"""

from __future__ import annotations

import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .domain.enums import ChainMode

API_ROOT = Path(__file__).resolve().parents[1]  # apps/api
REPO_ROOT = API_ROOT.parents[1]

# Development-only keys for the in-process LOCAL_EVM chain (well-known test keys, never funded on any
# public network). Real deployments MUST set their own keys through the environment.
LOCAL_DEV_KEYS = {
    "deployer": "0x" + "11" * 32,
    "authorizer": "0x" + "22" * 32,
    "executor": "0x" + "33" * 32,
    "pauser": "0x" + "44" * 32,
    "evidence_payer": "0x" + "55" * 32,
}

DEFAULT_SOURCES: dict[str, dict[str, list[str]]] = {
    # Grand Prize demo: deterministic scenario values (SIMULATED) + a genuinely paid x402 flow against the
    # local x402 reference server for liquidity. Every card is labelled with its mode.
    "demo": {
        "smart_money_inflow_24h": ["scenario"],
        "available_liquidity": ["x402_reference", "scenario"],
        "asset_price": ["scenario"],
    },
    # Real providers. Order = primary then fallbacks. A source without configuration is skipped.
    "live": {
        "smart_money_inflow_24h": ["nansen"],
        "available_liquidity": ["x402_paid", "dexscreener", "uniswap_v2_pool"],
        "asset_price": ["chainlink", "dexscreener"],
    },
    # Genuine previously captured responses, re-served and labelled RECORDED.
    "recorded": {
        "smart_money_inflow_24h": ["recorded"],
        "available_liquidity": ["recorded"],
        "asset_price": ["recorded"],
    },
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False)

    # ---- application --------------------------------------------------------------------------
    app_env: str = "development"
    demo_mode: bool = True
    log_level: str = "INFO"
    log_json: bool = True
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    database_url: str = f"sqlite:///{(API_ROOT / 'sufficia.db').as_posix()}"
    api_base_url: str = "http://127.0.0.1:8000"
    api_key: Optional[str] = None  # when set, every mutating request needs header X-API-Key
    rate_limit_per_minute: int = 240  # mutating requests per client IP (0 disables)
    max_request_bytes: int = 262_144

    # ---- policy defaults used by the intent compiler -----------------------------------------
    default_max_age_seconds: int = 30
    default_authorization_ttl_seconds: int = 30
    default_evidence_budget_usdc: Decimal = Decimal("0.25")
    default_conflict_tolerance: Decimal = Decimal("0.10")
    default_minimum_required_evidence: int = 3
    max_protected_amount_usdc: Decimal = Decimal("1000")

    # ---- intent compiler -----------------------------------------------------------------------
    intent_compiler: str = "auto"  # deterministic | llm | auto (llm when a key is configured)
    anthropic_api_key: Optional[str] = None
    anthropic_base_url: str = "https://api.anthropic.com"
    llm_model: str = "claude-sonnet-5-5"
    llm_timeout_seconds: float = 20.0

    # ---- chain ---------------------------------------------------------------------------------
    chain_mode: ChainMode = ChainMode.LOCAL_EVM
    monad_rpc_url: str = "https://testnet-rpc.monad.xyz"
    monad_rpc_fallback_url: Optional[str] = None
    monad_chain_id: int = 10143
    monad_explorer_url: str = "https://testnet.monadvision.com"
    deployment_file: Optional[str] = None  # deployments/<network>.json written by scripts/deploy.py
    evidence_gate_address: Optional[str] = None
    usdc_address: Optional[str] = None  # execution asset (6 decimals)
    token_x_address: Optional[str] = None
    swap_router_address: Optional[str] = None
    deployer_private_key: Optional[str] = None
    authorizer_private_key: Optional[str] = None
    executor_private_key: Optional[str] = None
    pauser_private_key: Optional[str] = None
    execution_adapter: str = "demo_swap"  # demo_swap | transfer
    execution_recipient: Optional[str] = None  # defaults to the gate itself (protected treasury)
    slippage_bps: int = 100
    tx_confirmation_timeout_seconds: float = 30.0
    commit_receipts_onchain: bool = True

    # ---- evidence sources ----------------------------------------------------------------------
    evidence_profile: str = "demo"  # demo | live | recorded
    evidence_sources_json: Optional[str] = None  # overrides DEFAULT_SOURCES[profile]
    http_timeout_seconds: float = 6.0
    asset_token_address: Optional[str] = None  # token queried at data providers
    nansen_api_key: Optional[str] = None
    nansen_base_url: str = "https://api.nansen.ai"
    nansen_chain: str = "monad"
    dexscreener_base_url: str = "https://api.dexscreener.com"
    dexscreener_chain_id: str = "monad"
    dexscreener_pair_address: Optional[str] = None
    chainlink_feed_address: Optional[str] = None
    chainlink_rpc_url: Optional[str] = None  # defaults to monad_rpc_url
    pool_address: Optional[str] = None  # Uniswap-V2-style pair for onchain liquidity
    pool_quote_token_index: int = 1  # which reserve is the USD stable (0 or 1)
    pool_quote_decimals: int = 6
    recorded_fixtures_dir: str = str(REPO_ROOT / "demo" / "fixtures" / "recorded")
    scenarios_dir: str = str(REPO_ROOT / "demo" / "scenarios")

    # ---- x402 paid evidence ----------------------------------------------------------------------
    x402_enabled: bool = True
    x402_network: str = "eip155:10143"
    x402_asset: str = "0x534b2f3A21130d7a60830c2Df862319e593943A3"  # Monad testnet USDC (x402 guide)
    x402_asset_name: str = "USDC"
    x402_asset_version: str = "2"
    x402_facilitator: str = "https://x402-facilitator.molandak.org"
    x402_max_per_request_usdc: Decimal = Decimal("0.10")
    x402_pay_to_allowlist: str = ""  # comma-separated; empty = accept any payTo that passes other checks
    x402_evidence_url: Optional[str] = None  # live paid liquidity endpoint (returns SUFFICIA evidence JSON)
    evidence_payment_private_key: Optional[str] = None
    x402_reference_enabled: bool = True  # mounts the local x402 reference server under /_dev/x402
    x402_reference_price_usdc: Decimal = Decimal("0.02")
    x402_reference_pay_to: str = "0x000000000000000000000000000000000000dEaD"

    # ---- reports -------------------------------------------------------------------------------
    evidence_reports_dir: str = str(REPO_ROOT / "docs" / "evidence")

    @field_validator("chain_mode", mode="before")
    @classmethod
    def _chain_mode(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip().upper().replace("-", "_")
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def x402_pay_to_list(self) -> list[str]:
        return [a.strip().lower() for a in self.x402_pay_to_allowlist.split(",") if a.strip()]

    def sources_for_profile(self) -> dict[str, list[str]]:
        if self.evidence_sources_json:
            data = json.loads(self.evidence_sources_json)
            return {k: list(v) for k, v in data.items()}
        return {k: list(v) for k, v in DEFAULT_SOURCES.get(self.evidence_profile, DEFAULT_SOURCES["demo"]).items()}

    def key(self, role: str) -> Optional[str]:
        """Private key for a signer role; LOCAL_EVM falls back to well-known dev keys."""
        value = {
            "deployer": self.deployer_private_key,
            "authorizer": self.authorizer_private_key,
            "executor": self.executor_private_key,
            "pauser": self.pauser_private_key,
            "evidence_payer": self.evidence_payment_private_key,
        }[role]
        if value:
            return value
        if self.chain_mode in (ChainMode.LOCAL_EVM, ChainMode.DISABLED):
            return LOCAL_DEV_KEYS[role]
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
