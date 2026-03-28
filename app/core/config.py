"""
Application settings management.
Uses pydantic-settings for environment variable loading.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    app_name: str = Field(
        default="PMTCT Triple Elimination Tool",
        validation_alias=AliasChoices("APP_NAME", "APP_TITLE"),
    )
    app_version: str = Field(
        default="1.0.0",
        validation_alias=AliasChoices("APP_VERSION"),
    )
    debug: bool = Field(default=False, validation_alias=AliasChoices("APP_DEBUG"))
    secret_key: str = "change-me-to-a-random-secret-key"

    # DHIS2 defaults (can be overridden per-session)
    dhis2_base_url: str = "https://hmis.health.go.ug"
    dhis2_api_version: str = "2.39"

    # Session
    session_timeout_minutes: int = Field(
        default=60,
        validation_alias=AliasChoices("SESSION_TIMEOUT_MINUTES", "SESSION_EXPIRE_MINUTES"),
    )

    # Timeouts
    dhis2_timeout_seconds: int = 30
    dhis2_max_retries: int = 3

    # LLM (vendor-neutral)
    llm_provider: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("LLM_PROVIDER"),
    )
    llm_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("LLM_API_KEY"),
    )
    llm_model: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("LLM_MODEL"),
    )
    llm_max_tokens: int = Field(
        default=1024,
        validation_alias=AliasChoices("LLM_MAX_TOKENS"),
    )
    llm_temperature: float = Field(
        default=0.3,
        validation_alias=AliasChoices("LLM_TEMPERATURE"),
    )
    llm_timeout_seconds: int = Field(
        default=30,
        validation_alias=AliasChoices("LLM_TIMEOUT", "LLM_TIMEOUT_SECONDS"),
    )
    llm_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("LLM_ENABLED"),
    )
    llm_fallback_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("LLM_FALLBACK_ENABLED"),
    )
    llm_max_content_length: int = Field(
        default=5000,
        validation_alias=AliasChoices("LLM_MAX_CONTENT_LENGTH"),
    )
    llm_azure_endpoint: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("LLM_AZURE_ENDPOINT"),
    )

    # Audit and security
    audit_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("AUDIT_ENABLED"),
    )
    audit_log_file: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("AUDIT_LOG_FILE"),
    )
    rate_limit_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("RATE_LIMIT_ENABLED"),
    )
    csrf_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("CSRF_ENABLED"),
    )
    csrf_exempt_paths: list[str] = Field(
        default_factory=lambda: ["/auth/login", "/auth/refresh", "/health", "/health/ready", "/health/live"],
    )

    # Cache and performance
    cache_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("CACHE_ENABLED"),
    )
    cache_max_size: int = Field(
        default=5000,
        validation_alias=AliasChoices("CACHE_MAX_SIZE"),
    )
    cache_default_ttl: int = Field(
        default=300,
        validation_alias=AliasChoices("CACHE_DEFAULT_TTL"),
    )
    cache_metadata_ttl: int = Field(
        default=3600,
        validation_alias=AliasChoices("CACHE_METADATA_TTL"),
    )
    cache_hierarchy_ttl: int = Field(
        default=3600,
        validation_alias=AliasChoices("CACHE_HIERARCHY_TTL"),
    )
    cache_aggregate_ttl: int = Field(
        default=300,
        validation_alias=AliasChoices("CACHE_AGGREGATE_TTL"),
    )
    cache_indicator_ttl: int = Field(
        default=300,
        validation_alias=AliasChoices("CACHE_INDICATOR_TTL"),
    )
    cache_trend_ttl: int = Field(
        default=600,
        validation_alias=AliasChoices("CACHE_TREND_TTL"),
    )
    cache_insight_ttl: int = Field(
        default=900,
        validation_alias=AliasChoices("CACHE_INSIGHT_TTL"),
    )
    cache_data_quality_ttl: int = Field(
        default=300,
        validation_alias=AliasChoices("CACHE_DATA_QUALITY_TTL"),
    )
    cache_alert_ttl: int = Field(
        default=300,
        validation_alias=AliasChoices("CACHE_ALERT_TTL"),
    )
    cache_warming_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("CACHE_WARMING_ENABLED"),
    )

    # Shared httpx pool
    http_max_connections: int = Field(
        default=100,
        validation_alias=AliasChoices("HTTP_MAX_CONNECTIONS"),
    )
    http_max_keepalive: int = Field(
        default=20,
        validation_alias=AliasChoices("HTTP_MAX_KEEPALIVE"),
    )
    http_keepalive_expiry: float = Field(
        default=30.0,
        validation_alias=AliasChoices("HTTP_KEEPALIVE_EXPIRY"),
    )
    http_connect_timeout: float = Field(
        default=10.0,
        validation_alias=AliasChoices("HTTP_CONNECT_TIMEOUT"),
    )
    http_read_timeout: float = Field(
        default=60.0,
        validation_alias=AliasChoices("HTTP_READ_TIMEOUT"),
    )
    http_write_timeout: float = Field(
        default=30.0,
        validation_alias=AliasChoices("HTTP_WRITE_TIMEOUT"),
    )
    http_pool_timeout: float = Field(
        default=30.0,
        validation_alias=AliasChoices("HTTP_POOL_TIMEOUT"),
    )

    # Compatibility and deployment helpers used by the existing scaffold
    app_env: str = Field(
        default="development",
        validation_alias=AliasChoices("APP_ENV"),
    )
    app_port: int = Field(
        default=8000,
        validation_alias=AliasChoices("APP_PORT", "PORT"),
    )
    app_host: str = Field(
        default="0.0.0.0",
        validation_alias=AliasChoices("APP_HOST", "HOST"),
    )
    llm_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("LLM_BASE_URL"),
    )
    log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices("LOG_LEVEL"),
    )
    log_format: str = Field(
        default="console",
        validation_alias=AliasChoices("LOG_FORMAT"),
    )
    temp_dir: str = "/tmp/pmtct_reports"
    max_temp_file_age_hours: int = 24

    @property
    def app_title(self) -> str:
        """Backward-compatible title accessor for the current FastAPI bootstrap."""
        return self.app_name


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()


CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def load_yaml_config(filename: str) -> Dict[str, Any]:
    """Load a YAML configuration file from the repository config directory."""
    path = CONFIG_DIR / filename
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)
