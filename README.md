# PACER — Distressed-Domain Arbitrage & RWA Tokenization

**Operating entity:** 1COMMERCE LLC (Canby, OR)
**Status:** Production-grade scaffold, deployment-ready.

PACER runs a daily pipeline that:

1. Discovers distressed SaaS/tech domains from PACER/RECAP, state SOS dissolutions, EDGAR filings, USPTO abandonments, UCC liens, and probate asset sales.
2. Enriches company records → resolves active domains.
3. Scores SEO equity via Ahrefs + topical relevance via GPT-4o.
4. Catches premium domains during the pending-delete window via multi-registrar backorder (Dynadot primary, DropCatch/NameJet/GoDaddy fallbacks).
5. Tokenizes qualifying domains as RWAs (Doma DOT/DST) with Securitize hybrid settlement to preserve Oregon DFR money-transmitter exemption.
6. Monetizes via 301 redirects, competitor arbitrage outreach, parking/affiliate, yield stacking, and fractional sales.

All records are tagged `llc_entity="1COMMERCE LLC"` for audit compliance.

---

## Quick start

```bash
git clone git@github.com:ksksrbiz-arch/PACER.git pacer && cd pacer
cp .env.example .env              # fill in secrets
make deploy-prep                  # builds, runs migrations
make docker-up                    # starts postgres + redis + pacer daemon
make docker-logs                  # tails the scheduler
```

## Architecture

```
src/pacer/
├── main.py               # APScheduler entrypoint + resilience hooks
├── config.py             # Pydantic settings with LLC tagging
├── db.py                 # Async + sync engines
├── models/               # DomainCandidate, ComplianceLog
├── pipelines/            # 6 discovery pipelines, AsyncIO parallel
├── pacer/pacer_client.py # PCL + RECAP with deep error handling
├── utils/api_resilience.py
├── scoring/              # Ahrefs batch + spam filter + GPT-4o relevance
├── enrichment/           # company → domain resolution
├── dropcatch/            # multi-registrar orchestrator
├── rwa/                  # Doma + Securitize hybrid settlement
├── monetization/         # 301, parking, outreach
└── compliance/           # audit log, LLC tagging, KYC hooks
```

## Pipelines

| # | Source                                      | Cadence | Module                              |
|---|---------------------------------------------|---------|-------------------------------------|
| 1 | PACER PCL + CourtListener RECAP             | Daily   | `pipelines/pacer_recap.py`          |
| 2 | State SOS Dissolutions (OR, CA, DE, NY, TX) | Daily   | `pipelines/sos_dissolutions.py`     |
| 3 | EDGAR Distressed Public Companies           | Daily   | `pipelines/edgar.py`                |
| 4 | USPTO Abandoned Trademarks                  | Daily   | `pipelines/uspto.py`                |
| 5 | UCC Liens & Judgment Distress               | Daily   | `pipelines/ucc_liens.py`            |
| 6 | Probate / Estate Asset Sales                | Daily   | `pipelines/probate.py`              |

All run inside a single `asyncio.gather` call. Failures are isolated per pipeline via the resilience decorator — one broken source does not halt the others.

## Scoring thresholds

- **≥ 60** → drop-catch + RWA candidate
- **40–59** → parking / instant-flip candidate
- **< 40** → discarded (log only)

## Compliance posture

- Every API call logged to `compliance_log` with LLC tag, timestamp, endpoint, and status.
- RWA settlement routed through Securitize (or Doma custody) → Oregon DFR money-transmitter exemption path.
- KYC/AML hooks wired for Securitize integration before first fractional sale.
- Attorney opinion letter template available under `docs/dfr-exemption-opinion.md` (add before first tokenization).

## Operating runbook

See `SETUP.md` for VPS deploy, Alembic, Prometheus, and DFR exemption steps.

## License

Proprietary — all rights reserved, 1COMMERCE LLC.
