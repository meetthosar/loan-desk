"""Settings from environment variables (prefix LOANDESK_) and config/models.yaml."""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOANDESK_", env_file=ROOT / ".env",
                                      extra="ignore")

    openrouter_api_key: str = Field(default="", validation_alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    budget_usd: float = 10.0
    state_dir: Path = ROOT / ".loandesk"

    prompt_version: str = "v1"
    models_config: Path = ROOT / "config" / "models.yaml"

    database_url: str = "postgresql+psycopg://loandesk:loandesk@localhost:5432/loandesk"
    vector_store: str = "pgvector"
    qdrant_url: str = "http://localhost:6333"
    chroma_url: str = "localhost:8001"
    pinecone_api_key: str = ""
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    retrieval_k: int = 4

    checkpointer: str = "memory"

    zendesk_subdomain: str = ""
    zendesk_email: str = ""
    zendesk_api_token: str = ""
    webhook_secret: str = "change-me"
    otp_mode: str = "fixed"
    phone_hash_salt: str = "udaan-synthetic-salt"

    admin_jwks_url: str = ""
    admin_audience: str = ""
    admin_issuer: str = ""
    # Dev only: open the admin routes without a token (used by docker compose).
    admin_dev_open: bool = False

    # Business date. Fixed in evals so due dates do not drift between runs.
    as_of: date = Field(default_factory=date.today)

    @property
    def models(self) -> dict[str, Any]:
        return load_models_config(str(self.models_config))

    @property
    def prompts_dir(self) -> Path:
        return ROOT / "prompts" / self.prompt_version


@lru_cache
def load_models_config(path: str) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


@lru_cache
def get_settings() -> Settings:
    return Settings()
