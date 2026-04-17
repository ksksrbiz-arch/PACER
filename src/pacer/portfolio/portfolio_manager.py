"""
Domain portfolio manager.

Maintains 1COMMERCE LLC's owned domain portfolio: records acquisitions,
tracks renewal dates and valuations, and surfaces expiry/analytics summaries.

Database persistence is available when an AsyncSession is injected;
the in-memory helpers work independently for lightweight pipeline use.
"""
from __future__ import annotations

from datetime import date, timedelta

from loguru import logger

from pacer.compliance.audit import record_event
from pacer.models.domain_candidate import DomainCandidate
from pacer.models.domain_portfolio import DomainPortfolio


class PortfolioManager:
    """
    Manages the 1COMMERCE LLC domain portfolio.

    Core responsibilities:
      - Record newly caught/purchased domains (add_from_candidate).
      - Compute aggregate portfolio valuation and statistics.
      - Surface domains nearing renewal so action can be taken in time.
    """

    # Days before renewal_date to flag a domain as "expiring soon"
    EXPIRY_ALERT_DAYS: int = 30

    # ---------------------------------------------------------------------------
    # Acquisition helpers
    # ---------------------------------------------------------------------------

    async def add_from_candidate(
        self,
        candidate: DomainCandidate,
        *,
        redirect_target: str | None = None,
        monetization_strategy: str | None = None,
        purchase_price_usd: float | None = None,
        registrar: str | None = None,
    ) -> DomainPortfolio:
        """
        Create a DomainPortfolio entry from a qualified DomainCandidate.

        In a full deployment, pass an AsyncSession and call
        ``session.add(entry); await session.commit()`` after this method.
        """
        domain = candidate.domain or ""
        valuation = self._estimate_valuation(candidate)
        entry = DomainPortfolio(
            domain=domain,
            registrar=registrar,
            purchase_date=date.today().isoformat(),
            purchase_price_usd=purchase_price_usd,
            current_valuation_usd=valuation,
            seo_score=candidate.score,
            redirect_target=redirect_target,
            monetization_strategy=monetization_strategy,
            status="pending",  # upgraded to "active" once registrar confirms transfer
        )
        logger.info(
            "portfolio.add domain={} score={} strategy={} valuation={}",
            domain,
            candidate.score,
            monetization_strategy,
            valuation,
        )
        await record_event(
            event_type="portfolio_entry_created",
            endpoint="portfolio.add_from_candidate",
            domain=domain,
            message=f"strategy={monetization_strategy} valuation={valuation}",
            payload={
                "seo_score": candidate.score,
                "monetization_strategy": monetization_strategy,
                "redirect_target": redirect_target,
                "estimated_valuation_usd": valuation,
            },
        )
        return entry

    # ---------------------------------------------------------------------------
    # Analytics
    # ---------------------------------------------------------------------------

    def compute_portfolio_summary(self, entries: list[DomainPortfolio]) -> dict:
        """
        Compute aggregate statistics across portfolio entries.

        Returns a dict with:
          - total_domains
          - status_breakdown (counts by status string)
          - total_valuation_usd
          - avg_seo_score
          - expiring_soon_count (within EXPIRY_ALERT_DAYS)
          - expiring_soon (list of domain names)
        """
        total = len(entries)
        status_counts: dict[str, int] = {}
        for entry in entries:
            status_counts[entry.status] = status_counts.get(entry.status, 0) + 1

        total_valuation = sum((e.current_valuation_usd or 0.0) for e in entries)
        scores = [e.seo_score for e in entries if e.seo_score is not None]
        avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0

        expiring = self.find_expiring_soon(entries, days=self.EXPIRY_ALERT_DAYS)

        summary = {
            "total_domains": total,
            "status_breakdown": status_counts,
            "total_valuation_usd": round(total_valuation, 2),
            "avg_seo_score": avg_score,
            "expiring_soon_count": len(expiring),
            "expiring_soon": [e.domain for e in expiring],
        }
        logger.info("portfolio.summary {}", summary)
        return summary

    def find_expiring_soon(
        self, entries: list[DomainPortfolio], *, days: int = 30
    ) -> list[DomainPortfolio]:
        """
        Return portfolio entries whose renewal_date falls within the next ``days`` days.

        Entries without a renewal_date are excluded.
        """
        cutoff = date.today() + timedelta(days=days)
        expiring = []
        for entry in entries:
            if not entry.renewal_date:
                continue
            try:
                renewal = date.fromisoformat(entry.renewal_date)
            except ValueError:
                logger.warning(
                    "portfolio.invalid_renewal_date domain={} renewal_date={}",
                    entry.domain,
                    entry.renewal_date,
                )
                continue
            if date.today() <= renewal <= cutoff:
                expiring.append(entry)
        return expiring

    def update_valuation(
        self, entry: DomainPortfolio, new_valuation_usd: float
    ) -> DomainPortfolio:
        """Update the current valuation for a portfolio entry."""
        old = entry.current_valuation_usd
        entry.current_valuation_usd = new_valuation_usd
        logger.info(
            "portfolio.valuation_updated domain={} old={} new={}",
            entry.domain,
            old,
            new_valuation_usd,
        )
        return entry

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    @staticmethod
    def _estimate_valuation(candidate: DomainCandidate) -> float:
        """
        Rough valuation estimate based on composite score.

        Formula: $100 × score (linear proxy until Doma/Ahrefs data is available).
        Capped at $50,000 for sanity.
        """
        score = candidate.score or 0.0
        return min(round(score * 100, 2), 50_000.0)
