"""Application configuration loaded from environment variables.

Use ``get_settings()`` (cached) anywhere the app needs config — avoids re-parsing
the environment on every call and makes dependency injection explicit.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- App ----
    app_env: Environment = Field(default="development", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    app_secret_key: SecretStr = Field(alias="APP_SECRET_KEY")
    app_log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")

    # ---- Database ----
    database_url: str = Field(alias="DATABASE_URL")
    database_pool_size: int = Field(default=20, alias="DATABASE_POOL_SIZE")
    database_max_overflow: int = Field(default=10, alias="DATABASE_MAX_OVERFLOW")
    database_echo: bool = Field(default=False, alias="DATABASE_ECHO")

    # ---- Redis ----
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # ---- Anthropic ----
    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model_normalize: str = Field(
        default="claude-sonnet-4-6", alias="ANTHROPIC_MODEL_NORMALIZE"
    )
    anthropic_model_resolver: str = Field(
        default="claude-opus-4-7", alias="ANTHROPIC_MODEL_RESOLVER"
    )

    # ---- Adapters ----
    google_places_api_key: SecretStr | None = Field(default=None, alias="GOOGLE_PLACES_API_KEY")
    vietmap_api_key: SecretStr | None = Field(default=None, alias="VIETMAP_API_KEY")
    osm_overpass_url: str = Field(
        default="https://overpass-api.de/api/interpreter", alias="OSM_OVERPASS_URL"
    )
    gosom_scraper_url: str = Field(
        default="http://gosom-scraper:8080", alias="GOSOM_SCRAPER_URL"
    )
    foody_rate_limit_per_sec: float = Field(default=2.0, alias="FOODY_RATE_LIMIT_PER_SEC")

    # ---- Crawl planner ----
    crawl_rate_per_hour: int = Field(default=200, alias="CRAWL_RATE_PER_HOUR")
    crawl_planner_minutes: int = Field(default=10, alias="CRAWL_PLANNER_MINUTES")
    crawl_batch_size: int = Field(default=50, alias="CRAWL_BATCH_SIZE")
    crawl_cell_size_m: int = Field(default=5000, alias="CRAWL_CELL_SIZE_M")
    crawl_priority_provinces: str = Field(
        default="01,79,48,31,92,22,75,74,68",
        alias="CRAWL_PRIORITY_PROVINCES",
    )
    crawl_recrawl_days: int = Field(default=30, alias="CRAWL_RECRAWL_DAYS")

    # ---- Pipeline ----
    dedupe_cluster_eps_meters: float = Field(default=55.0, alias="DEDUPE_CLUSTER_EPS_METERS")
    dedupe_auto_merge_threshold: float = Field(
        default=0.85, alias="DEDUPE_AUTO_MERGE_THRESHOLD"
    )
    dedupe_llm_threshold: float = Field(default=0.65, alias="DEDUPE_LLM_THRESHOLD")
    dedupe_schedule_minutes: int = Field(default=15, alias="DEDUPE_SCHEDULE_MINUTES")

    # ---- Embedding ----
    embedding_model: str = Field(
        default="paraphrase-multilingual-MiniLM-L12-v2", alias="EMBEDDING_MODEL"
    )
    embedding_cache_dir: str = Field(default="/app/models", alias="EMBEDDING_CACHE_DIR")

    # ---- Webhooks ----
    webhook_timeout_seconds: int = Field(default=10, alias="WEBHOOK_TIMEOUT_SECONDS")
    webhook_max_retries: int = Field(default=5, alias="WEBHOOK_MAX_RETRIES")

    # ---- Encryption ----
    fernet_key: SecretStr | None = Field(default=None, alias="FERNET_KEY")

    @field_validator("app_secret_key")
    @classmethod
    def _secret_key_length(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError("APP_SECRET_KEY must be at least 32 characters")
        return v

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def sync_database_url(self) -> str:
        """Alembic and some tools need a sync (psycopg) URL."""
        return self.database_url.replace("+asyncpg", "+psycopg").replace(
            "postgresql+psycopg://", "postgresql://"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
