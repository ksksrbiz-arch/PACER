"""Monetization strategy router.

After a domain is caught, decide how to monetize it AND where to point it
inside the 1Commerce multi-domain network.

Strategy tiers (from :class:`pacer.config.Settings`):
  - ``auction_bin``  -- yield_score >= ``score_threshold_auction``: top-tier,
    list BIN on Afternic + Sedo MLS, no redirect. Overrides 301/parking.
  - ``301_redirect`` -- score >= ``score_threshold_dropcatch``: high-authority
    domain, 301 to a specific category hub page with slug.
  - ``lease_to_own`` -- score >= ``lease_to_own_min_score`` AND commercial
    intent is high: list on DAN.com LTO while also parking for traffic
    revenue. Sets ``lease_to_own_enabled=True`` and
    ``lease_monthly_price_cents`` from the yield model.
  - ``parking``     -- score >= ``score_threshold_parking``: mid-tier, park
    on the category hub with ``?ref=<domain>`` tracking.
  - ``aftermarket`` -- below both: list on Afternic / Sedo / Dan, no target.

Yield / EPMV scoring:
  yield_score = epmv_authority_weight * authority_component
              + epmv_commercial_weight * commercial_component
  where authority_component = min(domain_rating, 100)
  and   commercial_component blends topical_relevance + CPC-derived intent.

Defaults: 40% authority, 60% commercial. Tunable via settings so we can
rebalance as we learn which side drives realized EPMV.

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

# Afternic BIN listing URL template — {domain} is substituted at route time.
AFTERNIC_BIN_URL = "https://www.afternic.com/domain/{domain}"
# DAN.com LTO landing URL.
DAN_LTO_URL = "https://dan.com/buy-domain/{domain}"

# Baseline monthly LTO multiplier on BIN price. DAN's data shows LTO converts
# 3–4× BIN; we price at 1/36th of est BIN so 36 payments ≈ BIN. Conservative.
_LTO_MONTHLY_DIVISOR = 36

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
        "saas",
        "platform",
        "cloud",
        "software",
        "api",
        "dashboard",
        "analytics",
        "crm",
        "erp",
    ],
    "tool_replacement": [
        "tool",
        "generator",
        "builder",
        "editor",
        "converter",
        "calculator",
        "tracker",
        "manager",
    ],
    "ecommerce": [
        "shop",
        "store",
        "commerce",
        "marketplace",
        "cart",
        "checkout",
        "merchant",
        "retail",
    ],
    "educational": [
        "learn",
        "course",
        "academy",
        "tutorial",
        "training",
        "bootcamp",
        "education",
        "university",
    ],
    "international": [
        "global",
        "world",
        "international",
        "europe",
        "asia",
        "uk",
        "eu",
        "trade",
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


def _commercial_component(candidate: DomainCandidate) -> float:
    """Blend topical relevance + CPC-derived commercial intent into 0–100.

    Weights:
        70% topical_relevance (from LLM, already 0–100)
        30% CPC signal — $20 CPC → 100, scaled linearly, capped.

    Missing inputs degrade to 0, not NaN — so a low-signal domain ranks low
    rather than poisoning the batch.
    """
    relevance = float(candidate.topical_relevance or 0.0)
    cpc = float(candidate.cpc_usd or 0.0)
    # Cap at $20 CPC for scoring — diminishing returns past that on yield.
    cpc_component = min(cpc / 20.0, 1.0) * 100.0
    return 0.70 * relevance + 0.30 * cpc_component


def yield_score(candidate: DomainCandidate) -> float:
    """Composite yield score for auction / LTO tiering.

    Authority weight * DR-based component + commercial weight * commercial.
    Returned value is 0–100 so it compares cleanly to score thresholds.
    """
    settings = get_settings()
    authority = float(candidate.domain_rating or 0.0)
    commercial = _commercial_component(candidate)
    return round(
        settings.epmv_authority_weight * authority + settings.epmv_commercial_weight * commercial,
        2,
    )


def _estimate_monthly_lto_cents(candidate: DomainCandidate) -> int | None:
    """Back-of-envelope LTO monthly price — cents. None if no signal.

    Formula: est_bin_cents / _LTO_MONTHLY_DIVISOR, where
        est_bin = (DR * $50) + (est_monthly_searches * CPC * 12 * 3)
    i.e. authority floor plus 3 years of organic commercial value.
    """
    dr = float(candidate.domain_rating or 0.0)
    searches = int(candidate.est_monthly_searches or 0)
    cpc = float(candidate.cpc_usd or 0.0)
    if dr == 0 and searches == 0:
        return None
    est_bin_usd = (dr * 50.0) + (searches * cpc * 12.0 * 3.0)
    if est_bin_usd <= 0:
        return None
    monthly_cents = int(round((est_bin_usd * 100.0) / _LTO_MONTHLY_DIVISOR))
    return max(monthly_cents, 999)  # $9.99 floor — protects brand perception


def _compute_target(
    strategy: str,
    category: str,
    candidate: DomainCandidate,
) -> str | None:
    """Derive the forwarding URL based on strategy + category."""
    base = TARGET_MAP.get(category, TARGET_MAP["default"])

    if strategy == "auction_bin":
        return AFTERNIC_BIN_URL.format(domain=candidate.domain)
    if strategy == "lease_to_own":
        # Park on the hub but surface the DAN.com offer via a CTA — we store
        # the DAN LTO URL in auction_listing_url; redirect stays on our hub
        # so we capture parking revenue while LTO is pending.
        return f"{base}?ref={quote(candidate.domain or 'unknown')}&lto=1"
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
        self._auction_threshold = settings.score_threshold_auction
        self._lto_threshold = settings.lease_to_own_min_score

    def choose_strategy(
        self,
        score: float | None,
        yield_s: float | None = None,
        commercial: float | None = None,
    ) -> str:
        """Pure, side-effect-free strategy resolver -- easy to unit test.

        Priority (highest yield wins):
            auction_bin > lease_to_own > 301_redirect > parking > aftermarket

        Auction/LTO tiers are only considered when ``yield_s`` is explicitly
        provided. Single-arg calls (``choose_strategy(score)``) use the
        simple dropcatch/parking/aftermarket ladder for backward compat with
        existing callers. :meth:`route` always supplies yield_s.
        """
        s = score or 0.0
        comm = commercial or 0.0

        if yield_s is not None:
            if yield_s >= self._auction_threshold:
                return "auction_bin"
            # LTO requires yield floor AND meaningful commercial intent,
            # otherwise we'd list boring domains nobody wants to lease.
            if yield_s >= self._lto_threshold and comm >= 50.0:
                return "lease_to_own"

        if s >= self._dropcatch_threshold:
            return "301_redirect"
        if s >= self._parking_threshold:
            return "parking"
        return "aftermarket"

    def route(self, candidate: DomainCandidate) -> DomainCandidate:
        """Tag ``candidate`` with its chosen strategy + target URL."""
        ys = yield_score(candidate)
        commercial = _commercial_component(candidate)
        strategy = self.choose_strategy(candidate.score, yield_s=ys, commercial=commercial)
        category = _categorize(candidate)
        target = _compute_target(strategy, category, candidate)

        candidate.monetization_strategy = strategy
        candidate.redirect_target = target

        if strategy == "auction_bin":
            candidate.auction_listing_url = AFTERNIC_BIN_URL.format(domain=candidate.domain)
            candidate.lease_to_own_enabled = False
        elif strategy == "lease_to_own":
            candidate.lease_to_own_enabled = True
            candidate.auction_listing_url = DAN_LTO_URL.format(domain=candidate.domain)
            candidate.lease_monthly_price_cents = _estimate_monthly_lto_cents(candidate)
        else:
            # Normalize LTO flag on non-LTO paths so callers can rely on it
            # without depending on the DB INSERT default having fired.
            candidate.lease_to_own_enabled = False

        candidate.status = Status.MONETIZED

        logger.info(
            "monetization.route domain={} score={} yield={} strategy={} " "category={} target={}",
            candidate.domain,
            candidate.score,
            ys,
            strategy,
            category,
            target or "aftermarket-listing",
        )
        return candidate

    def route_batch(self, candidates: list[DomainCandidate]) -> list[DomainCandidate]:
        """Route a batch -- no concurrency needed, pure CPU."""
        return [self.route(c) for c in candidates]

    async def route_and_list(self, candidate: DomainCandidate) -> DomainCandidate:
        """Route + actually POST aftermarket listings for auction/LTO tiers.

        The sync :meth:`route` is CPU-only (strategy + URL computation); this
        async variant is what the scheduler calls after route is chosen, so
        BIN/LTO listings land on the real exchanges. Errors on one provider
        don't revert the candidate's strategy — we record the failure and
        keep moving so tomorrow's retry can recover.

        Gated on ``Settings.aftermarket_listings_enabled`` — when False the
        listing clients log "dry_run" and return a stub :class:`ListingResult`
        without hitting the network.
        """
        # Defer import so unit tests that only exercise routing don't need
        # the aftermarket module loaded.
        from pacer.monetization.afternic import (
            post_auction_listing,
            post_lto_listing,
        )
        from pacer.monetization.cloudflare import configure_cloudflare_redirect

        self.route(candidate)

        # 301/parking tiers: write Cloudflare redirect rule for redirect_target.
        # auction_bin / lease_to_own skip this — they list instead of redirect.
        if candidate.redirect_target and candidate.monetization_strategy in {
            "301_redirect",
            "parking",
        }:
            cf = await configure_cloudflare_redirect(candidate.domain, candidate.redirect_target)
            logger.info(
                "router.cloudflare_redirect domain={} status={} ruleset={}",
                candidate.domain,
                cf.status,
                cf.ruleset_id,
            )

        if candidate.monetization_strategy == "auction_bin":
            # Use candidate-level BIN estimate if we have one, else settings default
            bin_price = (
                candidate.lease_monthly_price_cents * 36
                if candidate.lease_monthly_price_cents
                else get_settings().default_bin_price_cents
            )
            results = await post_auction_listing(candidate.domain, bin_price)
            logger.info(
                "router.auction_listings domain={} results={}",
                candidate.domain,
                [(r.provider, r.status) for r in results],
            )
        elif candidate.monetization_strategy == "lease_to_own":
            monthly = candidate.lease_monthly_price_cents or 999
            bin_price = monthly * 36
            result = await post_lto_listing(candidate.domain, bin_price, monthly)
            logger.info(
                "router.lto_listing domain={} provider={} status={}",
                candidate.domain,
                result.provider,
                result.status,
            )
        return candidate
