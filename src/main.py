"""
PACER platform — main entry point and daily pipeline orchestrator.

Pipeline:
  PACER PCL/RECAP → enrich → score → drop-catch → Doma RWA → Securitize → alert

Scheduled at 3 AM UTC daily via APScheduler.
Run manually: poetry run pacer-run
"""

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from src.alerts.slack_alert import alert_pipeline_complete, alert_pipeline_error
from src.config import Config
from src.dropcatch.dropcatch_client import DropCatchClient
from src.enrichment.domain_enricher import DomainEnricher
from src.pacer.pacer_client import PACERClient
from src.rwa.doma_client import DomaClient
from src.rwa.securitize_client import SecuritizeClient
from src.scoring.seo_scorer import SEOScorer
from src.utils.api_resilience import APIResilience


async def daily_pipeline() -> None:
    """
    Full daily pipeline for 1COMMERCE LLC.

    Step 1: Scrape PACER PCL + RECAP for yesterday's tech bankruptcies
    Step 2: Enrich debtor names → primary domains
    Step 3: Score (Ahrefs DR + GPT-4o topical relevance)
    Step 4: Queue high-value domains for drop-catch (Dynadot / DropCatch)
    Step 5: Tokenize via Doma (DOT/DST minting)
    Step 6: Settle via Securitize (DFR exemption path)
    Step 7: Alert + compliance log
    """
    logger.info(f"🚀 Starting PACER daily pipeline for {Config.LLC_ENTITY}")

    try:
        # Step 1 — PACER scrape
        candidates = await PACERClient().fetch_yesterday_bankruptcies()
        if not candidates:
            logger.info("No candidates found today — pipeline complete")
            await APIResilience.log_compliance(
                "daily_pipeline_no_candidates", {"entity": Config.LLC_ENTITY}
            )
            return

        # Step 2 — Domain enrichment
        candidates = await DomainEnricher().enrich_batch(candidates)

        # Step 3 — SEO + topical scoring (returns only qualified candidates)
        qualified = await SEOScorer().score_batch(candidates)

        if not qualified:
            logger.info(f"Enrichment done but no candidates scored ≥{Config.SCORE_THRESHOLD}")
            await alert_pipeline_complete(len(candidates), 0)
            return

        # Step 4 — Drop-catch
        qualified = await DropCatchClient().queue_batch(qualified)

        # Step 5 — Doma RWA tokenization
        qualified = await DomaClient().tokenize_batch(qualified)

        # Step 6 — Securitize settlement
        qualified = await SecuritizeClient().settle_batch(qualified)

        # Step 7 — Alerts + compliance
        await alert_pipeline_complete(len(candidates), len(qualified))
        await APIResilience.log_compliance(
            "daily_pipeline_complete",
            {
                "total_candidates": len(candidates),
                "qualified": len(qualified),
                "entity": Config.LLC_ENTITY,
            },
        )
        logger.info(
            f"✅ Pipeline complete: {len(candidates)} candidates, "
            f"{len(qualified)} qualified and processed"
        )

    except Exception as exc:
        logger.error(f"Pipeline failure: {exc}")
        await alert_pipeline_error(str(exc))
        await APIResilience.log_compliance(
            "pipeline_error", {"error": str(exc), "entity": Config.LLC_ENTITY}
        )
        raise


def main() -> None:
    """Entry point for `poetry run pacer-run`."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        daily_pipeline,
        "cron",
        hour=Config.PIPELINE_CRON_HOUR,
        minute=Config.PIPELINE_CRON_MINUTE,
    )
    scheduler.start()
    logger.info(
        f"PACER scheduler started — daily run at "
        f"{Config.PIPELINE_CRON_HOUR:02d}:{Config.PIPELINE_CRON_MINUTE:02d} UTC"
    )
    loop = asyncio.get_event_loop()
    try:
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        logger.info("PACER scheduler stopped")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
