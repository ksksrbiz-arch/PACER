from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", extra="ignore")

    ENV: str = "production"
    LLC_ENTITY: str = "1COMMERCE LLC"

    # PACER PCL API
    PACER_USERNAME: str = ""
    PACER_PASSWORD: str = ""

    # CourtListener / RECAP
    COURTLISTENER_API_KEY: str = ""

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://pacer:pacer@localhost:5432/pacer"
    DATABASE_URL_SYNC: str = "postgresql+psycopg2://pacer:pacer@localhost:5432/pacer"

    # OpenAI
    OPENAI_API_KEY: str = ""

    # Clearbit
    CLEARBIT_API_KEY: str = ""

    # Ahrefs
    AHREFS_API_KEY: str = ""

    # Doma RWA
    DOMA_API_KEY: str = ""
    DOMA_API_BASE: str = "https://api.doma.com/v1"

    # Securitize
    SECURITIZE_API_KEY: str = ""
    SECURITIZE_API_BASE: str = "https://api.securitize.io/v1"

    # Drop-catch registrars
    DYNADOT_API_KEY: str = ""
    DROPCATCH_API_KEY: str = ""
    NAMEJET_API_KEY: str = ""

    # Cloudflare (for automated 301 redirect rule creation)
    CLOUDFLARE_API_TOKEN: str = ""
    CLOUDFLARE_ZONE_ID: str = ""  # default zone; per-domain lookup is also attempted

    # Slack
    SLACK_WEBHOOK_URL: str = ""

    # WhoisXML
    WHOISXML_API_KEY: str = ""

    # Scheduler
    PIPELINE_CRON_HOUR: int = 3
    PIPELINE_CRON_MINUTE: int = 0

    # Scoring
    SCORE_THRESHOLD: int = 60


Config = Settings()
