# PACER Setup Guide

## Requirements

- Python 3.11+
- Poetry 1.8+
- PostgreSQL 16+
- Docker & Docker Compose (recommended for VPS)

## For 1COMMERCE LLC (Canby, Oregon)

- All compliance logs are automatically tagged `entity="1COMMERCE LLC"`
- Pipeline runs daily at **3 AM UTC**
- DFR exemption path uses Securitize hybrid settlement
- Hostinger OpenClaw VPS (2 vCPU / 8 GB RAM) ready

## Local Development Setup

```bash
# 1. Copy and fill environment variables
cp .env.example .env

# 2. Install Python dependencies
poetry install

# 3. Start Postgres (Docker)
docker-compose up -d db

# 4. Apply migrations
poetry run alembic upgrade head

# 5. Run once manually to test
poetry run python -c "
import asyncio
from src.pacer.pacer_client import PACERClient
asyncio.run(PACERClient().fetch_yesterday_bankruptcies())
"
```

## Hostinger VPS Deployment

```bash
# SSH into VPS
ssh user@your-vps-ip

# Clone repo
git clone https://github.com/ksksrbiz-arch/PACER.git
cd PACER

# Configure environment
cp .env.example .env
nano .env   # fill in all API keys and credentials

# Start with Docker Compose (production)
docker-compose -f docker-compose.prod.yml up -d

# Run migrations
docker-compose -f docker-compose.prod.yml exec app poetry run alembic upgrade head

# Check logs
docker-compose -f docker-compose.prod.yml logs -f app
```

## Key Environment Variables

| Variable | Required | Description |
|---|---|---|
| `PACER_USERNAME` | ✅ | PACER NextGenCSO username |
| `PACER_PASSWORD` | ✅ | PACER NextGenCSO password |
| `OPENAI_API_KEY` | ✅ | GPT-4o topical relevance scoring |
| `DATABASE_URL` | ✅ | PostgreSQL asyncpg connection string |
| `AHREFS_API_KEY` | ⚠️ | Ahrefs DR scoring (optional but recommended) |
| `DOMA_API_KEY` | ⚠️ | Doma RWA tokenization |
| `SECURITIZE_API_KEY` | ⚠️ | Securitize DFR settlement |
| `SLACK_WEBHOOK_URL` | ⚠️ | Pipeline alerts |

## PACER API Notes

- **Free tier**: Filtered daily queries stay well under the quarterly $30 waiver
- **Scheduled outage**: April 26, 2026 (7 AM – 9 PM ET) — pipeline falls back to RECAP automatically
- **Authentication**: PACER NextGenCSO username/password (same login as PACER website)

## Compliance

All pipeline runs are logged to `compliance_logs` with:
- `llc_entity = "1COMMERCE LLC"`
- `source = "PACER"`
- Full timestamp and candidate count

These logs support:
- Oregon DFR exemption opinion letter
- Koinly / Form 8949 tax export
- Canby business license annual renewal
