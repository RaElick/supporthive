from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./supporthive.db"
    secret_key: str = "dev-secret-change-me"
    token_ttl_hours: int = 24

    # OpenAI-compatible Chat Completions API. Empty base URL => heuristic mode (no LLM).
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout_s: float = 30.0
    llm_context_budget_tokens: int = 3000

    telegram_bot_token: str = ""

    # Default routing strategy until an admin changes it (stored in DB settings table).
    routing_strategy: str = "skills"
    # A low-priority ticket open longer than this (minutes) is flagged as an anomaly.
    anomaly_minutes_low_priority: int = 30

    cors_origins: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
