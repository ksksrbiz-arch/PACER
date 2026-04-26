"""Pydantic settings with LLC compliance tagging."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ─── LLC compliance tags ─────────────────────────────────────────
    llc_entity: str = "1COMMERCE LLC"
    llc_state: str = "OR"
    llc_city: str = "Canby"
    llc_ein: str = ""

    # ─── Database ────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://pacer:pacer@postgres:5432/pacer"
    sync_database_url: str = "postgresql://pacer:pacer@postgres:5432/pacer"
    redis_url: str = "redis://redis:6379/0"

    # ─── PACER + RECAP ──────────────────────────────────────────────
    pacer_username: str = ""
    pacer_password: SecretStr = SecretStr("")
    pacer_client_code: str = ""
    courtlistener_api_token: SecretStr = SecretStr("")

    # ─── EDGAR / SEC ─────────────────────────────────────────────────
    sec_user_agent: str = "1COMMERCE LLC pacer-ops skdev@1commercesolutions.com"

    # ─── USPTO ───────────────────────────────────────────────────────
    uspto_api_key: SecretStr = SecretStr("")

    # ─── Scoring ─────────────────────────────────────────────────────
    ahrefs_api_token: SecretStr = SecretStr("")
    moz_access_id: str = ""
    moz_secret_key: SecretStr = SecretStr("")
    semrush_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4o"

    # ─── LLM provider (claude | groq | openai) ────────────────────
    # "claude" is the primary; "groq" is the free-tier fallback.
    # The engine tries the configured provider first, then auto-falls
    # back through the chain: claude → groq → openai → {} (empty).
    llm_provider: Literal["claude", "groq", "openai"] = "claude"
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = "claude-opus-4-5"
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = "llama-3.3-70b-versatile"

    # ─── WHOIS ───────────────────────────────────────────────────────
    whoisxml_api_key: SecretStr = SecretStr("")

    # ─── Enrichment ──────────────────────────────────────────────────
    clearbit_api_key: SecretStr = SecretStr("")
    hunter_api_key: SecretStr = SecretStr("")
    apollo_api_key: SecretStr = SecretStr("")
    crunchbase_api_key: SecretStr = SecretStr("")

    # ─── Registrars ──────────────────────────────────────────────────
    dynadot_api_key: SecretStr = SecretStr("")
    dropcatch_user: str = ""
    dropcatch_key: SecretStr = SecretStr("")
    namejet_user: str = ""
    namejet_key: SecretStr = SecretStr("")
    godaddy_api_key: SecretStr = SecretStr("")
    godaddy_api_secret: SecretStr = SecretStr("")

    # ─── RWA ─────────────────────────────────────────────────────────
    doma_api_url: str = "https://api.doma.xyz"
    doma_api_key: SecretStr = SecretStr("")
    doma_wallet_private_key: SecretStr = SecretStr("")
    doma_chain_id: int = 1

    securitize_api_url: str = "https://api.securitize.io/v2"
    securitize_api_key: SecretStr = SecretStr("")
    securitize_issuer_id: str = ""
    rwa_fractional_sales_enabled: bool = False

    # ─── Monetization ────────────────────────────────────────────────
    parking_provider: Literal["sedo", "bodis", "dan"] = "sedo"
    parking_api_key: SecretStr = SecretStr("")
    affiliate_default_tag: str = "1commerce-20"

    # ─── Aftermarket listing APIs (auction_bin tier) ─────────────────
    afternic_api_url: str = "https://api.afternic.com/v2"
    afternic_api_key: SecretStr = SecretStr("")
    afternic_partner_id: str = ""  # seller/partner account ID at GoDaddy
    sedo_api_url: str = "https://api.sedo.com/api/v1"
    sedo_username: str = ""
    sedo_signkey: SecretStr = SecretStr("")
    sedo_partnerid: str = ""
    dan_api_url: str = "https://api.dan.com/v1"
    dan_api_key: SecretStr = SecretStr("")
    # Default BIN price multiplier applied when router doesn't supply one.
    default_bin_price_cents: int = 299_000  # $2,990
    # Whether to actually POST to Afternic/Sedo. When False, we log
    # what we WOULD do (good for staging / shadow-mode).
    aftermarket_listings_enabled: bool = False

    # ─── Cloudflare (automated 301 redirect rules) ────────────────────
    cloudflare_api_token: SecretStr = SecretStr("")
    cloudflare_zone_id: str = ""  # default zone; per-domain lookup is also attempted

    # ─── Alerts ──────────────────────────────────────────────────────
    slack_webhook_url: SecretStr = SecretStr("")
    alert_channel: str = "#pacer-ops"

    # ─── Scheduler ───────────────────────────────────────────────────
    schedule_cron_hour: int = 3
    schedule_cron_minute: int = 0
    score_threshold_dropcatch: int = Field(60, ge=0, le=100)
    score_threshold_parking: int = Field(40, ge=0, le=100)
    score_threshold_auction: int = Field(85, ge=0, le=100)
    lease_to_own_min_score: int = Field(70, ge=0, le=100)

    # ─── Yield / EPMV weighting ──────────────────────────────────────
    epmv_authority_weight: float = Field(0.40, ge=0.0, le=1.0)
    epmv_commercial_weight: float = Field(0.60, ge=0.0, le=1.0)

    # ─── Trademark screening ────────────────────────────────────────
    uspto_tmscreen_enabled: bool = True

    # ─── Partner / profit-share ─────────────────────────────────────
    # Hard cap keeps partners under CTA/BOI beneficial-ownership threshold (25%).
    partner_max_rev_share_pct: float = Field(24.9, ge=0.0, le=24.9)
    partner_default_rev_share_pct: float = Field(20.0, ge=0.0, le=24.9)

    # ─── API server ──────────────────────────────────────────────────
    # Shared secret sent in `X-API-Key` header by data-feed API callers.
    # Leave blank to disable key enforcement (useful in development).
    api_key: SecretStr = SecretStr("")
    api_host: str = "0.0.0.0"  # noqa: S104 — bind addr, not user-visible
    api_port: int = 8000

    # ─── Env ─────────────────────────────────────────────────────────
    environment: Literal["development", "ci", "staging", "production"] = "production"
    log_level: str = "INFO"

    @property
    def compliance_tags(self) -> dict[str, str]:
        """Attach to every audit event."""
        return {
            "llc_entity": self.llc_entity,
            "llc_state": self.llc_state,
            "llc_city": self.llc_city,
            "environment": self.environment,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
