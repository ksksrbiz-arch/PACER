"""Monetization strategy router.

After a domain is caught, decide how to monetize it AND where to point it
inside the 1Commerce multi-domain network.

Strategy tiers (from :class:`pacer.config.Settings`):
  - ``301_redirect`` -- score >= ``score_threshold_dropcatch``: high-authority
    domain, 301 to a specific category hub page with slug.
  - ``parking``     -- score >= ``score_threshold_parking``: mid-tier, park
    on the category hub with ``?ref=<domain>`` tracking.
  - ``aftermarket`` -- below both: list on Afternic / Sedo / Dan, no target.

Category inference (keyword-driven, cheap; upgrade to LLM later if needed):
  saas_alternative / tool_replacement / ecommerce / educational /
  international / informational / default.

The resolved URL is written to ``candidate.redirect_target`` so the
drop-catch registrar can configure forwarding once the domain registers.
"""
from __future__ import annotations

import re
from urllib.parse import quote

from loguru import logger

from pacer.config import get_settings
from pacer.models.domain_candidate import DomainCandidate, Status

# Hub root -- override via Settings.primary_hub_url if/when we add it.
PRIMARY_HUB = "https://1commercesolutions.com"

TARGET_MAP: dict[str, str] = {
    "saas_alternative": f"{PRIMARY_HUB}/alternatives",
    "tool_replacement": f"{PRIMARY_HUB}/tools",
    "ecommerce": f"{PRIMARY_HUB}/marketplace",
    "informational": f"{PRIMARY_HUB}/resources",
    "educational": f"{PRIMARY_HUB}/learn",
    "international": f"{PRIMARY_HUB}/global",
    "default": f"{PRIMARY_HUB}/resources",
}

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "saas_alternative": [
        "saas", "platform", "cloud", "software", "api",
        "dashboard", "analytics", "crm", "erp",
    ],
    "tool_replacement": [
        "tool", "generator", "builder", "editor", "converter",
        "calculator", "tracker", "manager",
    ],
    "ecommerce": [
        "shop", "store", "commerce", "marketplace", "cart",
        "checkout", "merchant", "retail",
    ],
    "educational": [
        "learn", "course", "academy", "tutorial", "training",
        "bootcamp", "education", "university",
    ],
    "international": [
        "global", "world", "international", "europe", "asia",
        "uk", "eu", "trade",
    ],
}


def _slugify(value: str) -> str:
    """Lowercase, strip non-word chars, collapse whitespace to ``-``."""
    slug = re.sub(r"[^\w\s-]", "", value.lower())
    slug = re.sub(r"[-\s]+", "-", slug).strip("-")
    return slug


def _categorize(candidate: DomainCandidate) -> str:
    """Keyword-match ``company_name`` + ``domain`` to a hub category."""
    haystack = " ".join(
        filter(
            None,
            [
                candidate.company_name or "",
                candidate.domain or "",
            ],
        )
    ).lower()

    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in haystack for kw in keywords):
            return category

    return "default"


def _compute_target(
    strategy: str,
    category: str,
    candidate: DomainCandidate,
) -> str | None:
    """Derive the forwarding URL based on strategy + category."""
    base = TARGET_MAP.get(category, TARGET_MAP["default"])

    if strategy == "301_redirect" and candidate.company_name:
        return f"{base}/{_slugify(candidate.company_name)}"
    if strategy == "301_redirect":
        # No company name -- fall back to hub root for the category.
        return base
    if strategy == "parking":
        return f"{base}?ref={quote(candidate.domain or 'unknown')}"
    return None  # aftermarket: no hub target


class MonetizationRouter:
    """Dispatches caught domains to strategy + topical target URL."""

    def __init__(self) -> None:
        settings = get_settings()
        self._dropcatch_threshold = settings.score_threshold_dropcatch
        self._parking_threshold = settings.score_threshold_parking

    def choose_strategy(self, score: float | None) -> str:
        """Pure, side-effect-free strategy resolver -- easy to unit test."""
        s = score or 0.0
        if s >= self._dropcatch_threshold:
            return "301_redirect"
        if s >= self._parking_threshold:
            return "parking"
        return "aftermarket"

    def route(self, candidate: DomainCandidate) -> DomainCandidate:
        """Tag ``candidate`` with its chosen strategy + target URL."""
        strategy = self.choose_strategy(candidate.score)
        category = _categorize(candidate)
        target = _compute_target(strategy, category, candidate)

        candidate.monetization_strategy = strategy
        candidate.redirect_target = target
        candidate.status = Status.MONETIZED

        logger.info(
            "monetization.route domain={} score={} strategy={} "
            "category={} target={}",
            candidate.domain,
            candidate.score,
            strategy,
            category,
            target or "aftermarket-listing",
        )
        return candidate

    def route_batch(
        self, candidates: list[DomainCandidate]
    ) -> list[DomainCandidate]:
        """Route a batch -- no concurrency needed, pure CPU."""
        return [self.route(c) for c in candidates]
