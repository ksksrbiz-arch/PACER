"""
PACER platform — main entry point and daily pipeline orchestrator.

Pipeline:
  PACER PCL/RECAP → enrich → score → drop-catch → monetize/redirect → portfolio → Doma RWA → Securitize → alert

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
from src.monetization.monetization_router import MonetizationRouter
from src.monetization.redirect_manager import RedirectManager
from src.pacer.pacer_client import PACERClient
from src.portfolio.portfolio_manager import PortfolioManager
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
    Step 5: Route monetization strategy + set up 301 redirects
    Step 6: Record acquisitions in domain portfolio
    Step 7: Tokenize via Doma (DOT/DST minting)
    Step 8: Settle via Securitize (DFR exemption path)
    Step 9: Alert + compliance log
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

        # Step 5 — Monetization routing + 301 redirect setup
        router = MonetizationRouter()
        redirect_mgr = RedirectManager()
        qualified = await router.route_batch(qualified)
        redirect_map = await redirect_mgr.setup_batch(qualified)
        logger.info(f"Redirects configured for {len(redirect_map)} domains")

        # Step 6 — Record acquisitions in portfolio
        portfolio_mgr = PortfolioManager()
        portfolio_entries = []
        for candidate in qualified:
            domain_key = candidate.domain or candidate.company_name
            redirect_target = redirect_map.get(domain_key)
            # Parse monetization strategy from notes (format: "monetization=<strategy>|...")
            strategy: str | None = None
            if candidate.notes:
                for part in candidate.notes.split("|"):
                    part = part.strip()
                    if part.startswith("monetization="):
                        strategy = part.split("=", 1)[1]
                        break
            entry = await portfolio_mgr.add_from_candidate(
                candidate,
                redirect_target=redirect_target,
                monetization_strategy=strategy,
            )
            portfolio_entries.append(entry)

        summary = portfolio_mgr.compute_portfolio_summary(portfolio_entries)
        logger.info(f"Portfolio batch summary: {summary}")

        # Step 7 — Doma RWA tokenization
        qualified = await DomaClient().tokenize_batch(qualified)

        # Step 8 — Securitize settlement
        qualified = await SecuritizeClient().settle_batch(qualified)

        # Step 9 — Alerts + compliance
        await alert_pipeline_complete(len(candidates), len(qualified))
        await APIResilience.log_compliance(
            "daily_pipeline_complete",
            {
                "total_candidates": len(candidates),
                "qualified": len(qualified),
                "portfolio_entries": len(portfolio_entries),
                "total_valuation_usd": summary.get("total_valuation_usd"),
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
