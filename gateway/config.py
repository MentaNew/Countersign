from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env and key files from the repo root, not from wherever the command is run
# (starting the gateway inside web/ once created a second, unused signing key there).
ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    paypal_webhook_id: str = ""

    database_url: str = "postgresql+psycopg://guardrail:guardrail@localhost:5433/guardrail"

    verifier: Literal["jev", "llm", "stub"] = "llm"
    groq_api_key: str = ""
    llm_verifier_model: str = "openai/gpt-oss-20b"
    injection_guard_model: str = "meta-llama/llama-prompt-guard-2-86m"
    anthropic_api_key: str = ""
    jev_api_key: str = ""
    jev_base_url: str = ""
    jev_model: str = "Jev-1.13.0"

    # Optional: a vaulted sandbox PayPal wallet (spike `vault`). When set, orders are paid
    # without a per-order buyer redirect; otherwise the approval step includes the PayPal link.
    paypal_vault_id: str = ""
    approval_ttl_minutes: int = 30
    test_database_url: str = "postgresql+psycopg://guardrail:guardrail@localhost:5433/guardrail_test"

    agent_api_key: str = "change-me-agent"
    user_password: str = "change-me-user"
    signing_key_path: str = "keys/gateway_ed25519.pem"
    public_base_url: str = "http://localhost:8000"
    web_base_url: str = "http://localhost:5173"
    merchant_base_url: str = "http://localhost:8100"

    @field_validator("signing_key_path")
    @classmethod
    def _from_root(cls, v: str) -> str:
        return v if Path(v).is_absolute() else str(ROOT / v)


@lru_cache
def get_settings() -> Settings:
    return Settings()
