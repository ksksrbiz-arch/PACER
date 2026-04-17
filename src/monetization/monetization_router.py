"""
Monetization router.

After a domain is caught, decides:
  1. Monetization strategy (301 redirect / parking / aftermarket)
  2. Target URL across the 1Commerce multi-domain network
  3. Specific landing page based on caught-domain topic

The target URL is written to candidate.notes for the drop-catch registrar
to configure as the forwarding destination once the domain registers.
"""

import re
from urllib.parse import quote

from loguru import logger

from src.models.domain import DomainCandidate

PRIMARY_HUB = "https://1commercesolutions.com"

TARGET_MAP = {
    "saas_alternative": f"{PRIMARY_HUB}/alternatives",
    "tool_replacement": f"{PRIMARY_HUB}/tools",
    "ecommerce": f"{PRIMARY_HUB}/marketplace",
    "informational": f"{PRIMARY_HUB}/resources",
    "educational": f"{PRIMARY_HUB}/learn",
    "international": f"{PRIMARY_HUB}/global",
    "default": f"{PRIMARY_HUB}/resources",
}

CATEGORY_KEYWORDS = {
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


def _slugify(company_name: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", company_name.lower())
    slug = re.sub(r"[-\s]+", "-", slug).strip("-")
    return slug


def _categorize(candidate: DomainCandidate) -> str:
    haystack = " ".join(
        filter(
            None,
            [
                candidate.company_name or "",
                candidate.domain or "",
                candidate.notes or "",
            ],
        )
    ).lower()

    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in haystack for kw in keywords):
            return category

    return "default"


class MonetizationRouter:
    """Decides monetization strategy + target URL for caught domains."""

    async def route(self, candidate: DomainCandidate) -> DomainCandidate:
        domain = candidate.domain
        score = candidate.seo_score or 0

        if score >= 80:
            strategy = "301_redirect"
        elif score >= 60:
            strategy = "parking"
        else:
            strategy = "aftermarket"

        category = _categorize(candidate)
        base_target = TARGET_MAP.get(category, TARGET_MAP["default"])

        if strategy == "301_redirect" and candidate.company_name:
            slug = _slugify(candidate.company_name)
            target_url = f"{base_target}/{slug}"
        elif strategy == "parking":
            target_url = f"{base_target}?ref={quote(domain or 'unknown')}"
        else:
            target_url = None

        decision = f"monetization={strategy}|category={category}"
        if target_url:
            decision += f"|target={target_url}"

        if candidate.notes:
            candidate.notes += f" | {decision}"
        else:
            candidate.notes = decision

        logger.info(
            f"Monetization for {domain!r}: "
            f"strategy={strategy} category={category} "
            f"target={target_url or 'aftermarket-listing'} score={score}"
        )
        return candidate

    async def route_batch(
        self, candidates: list[DomainCandidate]
    ) -> list[DomainCandidate]:
        return [await self.route(c) for c in candidates]
