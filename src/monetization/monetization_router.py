"""
301 arbitrage / monetization module placeholder.

After a domain is caught, it can be:
  - Redirected (301) to a high-traffic affiliate target
  - Parked with ad revenue
  - Sold via aftermarket (Afternic, Sedo, etc.)
"""

from loguru import logger

from src.models.domain import DomainCandidate


class MonetizationRouter:
    async def route(self, candidate: DomainCandidate) -> DomainCandidate:
        """Determine and apply the best monetization strategy for a caught domain."""
        domain = candidate.domain
        score = candidate.seo_score or 0

        if score >= 80:
            strategy = "301_redirect"
        elif score >= 60:
            strategy = "parking"
        else:
            strategy = "aftermarket"

        logger.info(f"Monetization strategy for {domain!r}: {strategy} (score={score})")
        if candidate.notes:
            candidate.notes += f" | monetization={strategy}"
        else:
            candidate.notes = f"monetization={strategy}"
        return candidate
