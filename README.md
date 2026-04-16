# PACER

**1COMMERCE LLC DomainFi / RWA Platform**

Automated daily pipeline: PACER scraper → distressed SaaS/tech domain discovery → SEO scoring → drop-catch → Doma RWA tokenization → Securitize settlement → 301 arbitrage.

**Status**: Production-ready (April 2026) · Full API resilience · Compliance logging · Hostinger OpenClaw VPS deployment

---

## Architecture

```
PACER PCL / RECAP
      │
      ▼
Domain Enrichment  (Clearbit → Hunter → Google)
      │
      ▼
SEO Scoring        (Ahrefs DR + GPT-4o topical relevance)
      │  ≥ 60
      ▼
Drop-Catch         (Dynadot + DropCatch + NameJet)
      │
      ▼
RWA Tokenization   (Doma DOT/DST minting)
      │
      ▼
Settlement         (Securitize hybrid — DFR exemption)
      │
      ▼
301 Arbitrage / Parking / Aftermarket
```

All runs are compliance-logged under **1COMMERCE LLC** (Canby, Oregon) for DFR opinion letter, Koinly/8949 tax export, and business license renewal.

---

## Quick Start

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env with your PACER credentials, API keys, etc.

# 2. Install dependencies
make install          # or: poetry install

# 3. Apply database migrations
make migrate          # or: poetry run alembic upgrade head

# 4. Run once manually
make run

# 5. Start scheduled daemon (3 AM UTC daily)
poetry run pacer-run
```

### Docker (recommended for VPS)

```bash
docker-compose up -d        # dev stack (Postgres + app)
# or for production:
docker-compose -f docker-compose.prod.yml up -d
```

---

## Environment Variables

See [`.env.example`](.env.example) for the full list. Key variables:

| Variable | Description |
|---|---|
| `PACER_USERNAME` / `PACER_PASSWORD` | PACER PCL API credentials |
| `OPENAI_API_KEY` | GPT-4o topical scoring |
| `AHREFS_API_KEY` | Ahrefs domain rating |
| `DOMA_API_KEY` | RWA tokenization |
| `SECURITIZE_API_KEY` | DFR-exempt settlement |
| `DYNADOT_API_KEY` / `DROPCATCH_API_KEY` | Drop-catch registrars |
| `SLACK_WEBHOOK_URL` | Pipeline alerts |
| `DATABASE_URL` | Postgres (asyncpg) |
| `SCORE_THRESHOLD` | Minimum score for drop-catch/RWA (default: 60) |

---

## Module Overview

| Module | Purpose |
|---|---|
| `src/pacer/` | PCL + RECAP scraper (Chapter 7/11, tech keywords) |
| `src/enrichment/` | Company name → primary domain resolution |
| `src/scoring/` | Ahrefs DR + GPT-4o composite score |
| `src/dropcatch/` | Dynadot / DropCatch / NameJet backorder |
| `src/rwa/` | Doma tokenization + Securitize settlement |
| `src/compliance/` | Audit trail for 1COMMERCE LLC |
| `src/alerts/` | Slack pipeline notifications |
| `src/utils/` | Shared resilience (retry + circuit breaker) |

---

## Resilience & Compliance

- **Retry policy**: Exponential backoff + jitter (up to 5 attempts, max 60 s)
- **Circuit breaker**: 3 failures → 5-minute pause per endpoint
- **Fallback chain**: PCL → RECAP → empty list (pipeline never hard-fails)
- **Retry-After**: Respected for 429 responses
- **Auth drift**: 401 errors logged as critical alerts
- **Compliance logs**: Every run tagged `entity=1COMMERCE LLC` for DFR / tax / Canby license

---

## Development

```bash
make lint     # Ruff + Black checks
make fmt      # Auto-format
make test     # pytest
```

---

## License

MIT — © 2026 1COMMERCE LLC