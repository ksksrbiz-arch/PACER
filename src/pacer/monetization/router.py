"""Monetization strategy router.

After a domain is caught, decide how to monetize it:
  - ``301_redirect`` — high-authority domains → redirect to affiliate target
  - ``parking``     — mid-tier domains → ad-revenue parking
  - ``aftermarket`` — low-tier domains → list on Afternic / Sedo / Dan

Thresholds come from :class:`pacer.config.Settings` so they can be tuned
per environment without a code change.
"""
from __future__ import annotations

from loguru import logger

from pacer.config import get_settings
from pacer.models.domain_candidate import DomainCandidate, Status


class MonetizationRouter:
    """Dispatches caught domains to the appropriate monetization track."""

    def __init__(self) -> None:
        settings = get_settings()
        self._dropcatch_threshold = settings.score_threshold_dropcatch
        self._parking_threshold = settings.score_threshold_parking

    def choose_strategy(self, score: float | None) -> str:
        """Pure, side-effect-free strategy resolver — easy to unit test."""
        s = score or 0.0
        if s >= self._dropcatch_threshold:
            return "301_redirect"
        if s >= self._parking_threshold:
            return "parking"
        return "aftermarket"

    def route(self, candidate: DomainCandidate) -> DomainCandidate:
        """Tag ``candidate`` with its chosen monetization strategy."""
        strategy = self.choose_strategy(candidate.score)
        candidate.monetization_strategy = strategy
        candidate.status = Status.MONETIZED
        logger.info(
            "monetization.route domain={} score={} strategy={}",
            candidate.domain,
            candidate.score,
            strategy,
        )
        return candidate
