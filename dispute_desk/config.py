"""Seller-side settings. The desk shares the .env file in this repo for convenience, but uses
its own login and reaches the buyer's gateway only through its public receipt endpoint."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class DeskSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The seller's PayPal app. In the sandbox the same business account plays every seller.
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    paypal_webhook_id: str = ""

    database_url: str = "postgresql+psycopg://guardrail:guardrail@localhost:5433/guardrail"

    # Where buyers' authorization receipts can be verified (public, no login).
    gateway_public_url: str = "http://localhost:8000"
    # Optional: pin the gateway's signing key so a swapped key is detected.
    gateway_public_key: str = ""

    seller_password: str = "change-me-seller"
    desk_public_url: str = "http://localhost:8200"
    web_base_url: str = "http://localhost:5173"
    # Accept webhooks whose PayPal signature cannot be verified (PayPal's simulate-event, or
    # PAYPAL_WEBHOOK_ID not set). Such disputes are labelled unverified everywhere.
    desk_allow_unverified_webhooks: bool = False


@lru_cache
def get_desk_settings() -> DeskSettings:
    return DeskSettings()
