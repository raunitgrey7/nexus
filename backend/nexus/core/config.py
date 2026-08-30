"""Runtime configuration for NEXUS.

Every setting can be overridden with an environment variable prefixed ``NEXUS_``
(for example ``NEXUS_LLM_MODEL=qwen2.5:3b``) or a ``.env`` file in the working directory.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NEXUS_", env_file=".env", extra="ignore")

    # ---- general -------------------------------------------------------------------------------
    env: str = "dev"
    log_level: str = "INFO"
    log_json: bool = False

    # ---- API -----------------------------------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )
    cors_origin_regex: str | None = (
        r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"  # any localhost port (dev-friendly; front with a gateway in production)
    )

    # ---- persistence ---------------------------------------------------------------------------
    database_url: str | None = None  # e.g. postgresql+asyncpg://nexus:nexus@localhost:5432/nexus
    redis_url: str | None = None  # e.g. redis://localhost:6379/0
    snapshot_every_ticks: int = 600

    # ---- LLM -----------------------------------------------------------------------------------
    llm_enabled: bool = True
    ollama_url: str = "http://localhost:11434"
    llm_model: str = "qwen2.5:7b"
    llm_fallback_model: str | None = (
        "qwen2.5:3b"  # used when the primary model cannot be loaded (e.g. low memory)
    )
    llm_embed_model: str = "qwen2.5:3b"
    llm_timeout_s: float = 90.0
    llm_temperature: float = 0.2

    # ---- simulation ----------------------------------------------------------------------------
    default_seed: int = 42
    default_scale: str = "small"
    tick_seconds: int = 1
    live_ticks_per_second: float = 10.0

    # ---- agents --------------------------------------------------------------------------------
    candidate_plans: int = 8
    sim_horizon_ticks: int = 5400  # 90 simulated minutes
    risk_seeds: int = 2  # extra seeds used by the Risk agent to test plan stability
    auto_approve_max_risk: str = "LOW"
    auto_approve_min_gain: float = 0.02  # ≥2 percentage points of SLA improvement
    decision_workers: int = 4

    # ---- observability -------------------------------------------------------------------------
    otel_enabled: bool = False
    otel_endpoint: str | None = None
    prometheus_enabled: bool = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
