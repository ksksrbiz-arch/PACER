"""
Redirect manager — automated 301 redirect setup for caught domains.

After a domain is drop-caught, this module:
  1. Chooses the best 1commercesolutions.com landing page by topic (score-aware).
  2. Applies a Cloudflare page rule so the redirect is live immediately.
  3. Logs every decision to the compliance trail.

Cloudflare credentials (CLOUDFLARE_API_TOKEN) are optional; when absent the
target URL is still computed and logged so ops can apply rules manually.
"""

from __future__ import annotations

import httpx
from loguru import logger

from src.config import Config
from src.models.domain import DomainCandidate
from src.utils.api_resilience import APIResilience

PRIMARY_HUB = "https://1commercesolutions.com"

# Ordered topic rules: first match wins.
# Each entry is (keywords_tuple, hub_path).
TOPIC_RULES: list[tuple[tuple[str, ...], str]] = [
    (
        ("crm", "sales", "lead", "pipeline", "prospect"),
        "/resources/saas-alternatives/crm",
    ),
    (
        ("project", "task", "agile", "sprint", "kanban", "scrum"),
        "/resources/saas-alternatives/project-management",
    ),
    (
        ("hr", "payroll", "recruit", "talent", "hiring", "workforce"),
        "/resources/saas-alternatives/hr",
    ),
    (
        ("finance", "accounting", "invoice", "billing", "payment", "expense"),
        "/resources/saas-alternatives/finance",
    ),
    (
        ("shop", "store", "commerce", "cart", "merchant", "retail", "ecommerce"),
        "/marketplace",
    ),
    (
        ("learn", "course", "academy", "tutorial", "training", "bootcamp", "education"),
        "/learn",
    ),
    (
        ("saas", "platform", "cloud", "software", "api", "dashboard", "analytics", "erp"),
        "/alternatives",
    ),
    (
        ("tool", "generator", "builder", "editor", "converter", "calculator", "tracker"),
        "/tools",
    ),
    (
        ("global", "world", "international", "europe", "asia", "uk", "eu", "trade"),
        "/global",
    ),
]


class RedirectManager:
    """
    Automates 301 redirect rules for drop-caught domains.

    High-value domains (score ≥ SCORE_THRESHOLD) are directed to specific
    topic pages on the 1commercesolutions.com hub.  Lower-scoring domains
    fall back to /resources.
    """

    def _build_target_url(self, caught_domain: str, score: float) -> str:
        """
        Select the best hub landing page based on domain keywords and score.

        Topic matching uses ordered TOPIC_RULES; the first rule whose keywords
        appear anywhere in the caught domain string wins.  Unmatched domains
        fall back to /resources.
        """
        lower = caught_domain.lower()
        for keywords, path in TOPIC_RULES:
            if any(kw in lower for kw in keywords):
                return f"{PRIMARY_HUB}{path}"
        return f"{PRIMARY_HUB}/resources"

    @APIResilience.resilient_api_call(max_attempts=3)
    async def _apply_cloudflare_rule(self, domain: str, target: str) -> None:
        """
        Create (or update) a Cloudflare 301 page rule for ``domain → target``.

        Requires CLOUDFLARE_API_TOKEN in config.  The zone is looked up by the
        root domain (e.g. "example.com") so subdomains are covered automatically.

        If no matching Cloudflare zone is found the redirect is logged for
        manual setup instead of raising an error.
        """
        if not Config.CLOUDFLARE_API_TOKEN:
            logger.info(
                f"CLOUDFLARE_API_TOKEN not set — redirect logged for manual setup: "
                f"{domain} → {target}"
            )
            return

        headers = {
            "Authorization": f"Bearer {Config.CLOUDFLARE_API_TOKEN}",
            "Content-Type": "application/json",
        }

        # Derive root domain for zone lookup (handles sub-domains safely)
        parts = domain.rstrip(".").split(".")
        root_domain = ".".join(parts[-2:]) if len(parts) >= 2 else domain

        async with httpx.AsyncClient(timeout=20) as client:
            # 1. Locate the Cloudflare zone for this domain
            zone_resp = await client.get(
                "https://api.cloudflare.com/client/v4/zones",
                params={"name": root_domain},
                headers=headers,
            )
            zone_resp.raise_for_status()
            zones = zone_resp.json().get("result", [])
            if not zones:
                logger.warning(
                    f"No Cloudflare zone found for {root_domain!r} — "
                    f"redirect rule must be applied manually: {domain} → {target}"
                )
                return
            zone_id = zones[0]["id"]

            # 2. Create a forwarding page rule (301 permanent)
            rule_resp = await client.post(
                f"https://api.cloudflare.com/client/v4/zones/{zone_id}/pagerules",
                headers=headers,
                json={
                    "targets": [
                        {
                            "target": "url",
                            "constraint": {
                                "operator": "matches",
                                "value": f"*{domain}/*",
                            },
                        }
                    ],
                    "actions": [
                        {
                            "id": "forwarding_url",
                            "value": {"url": f"{target}/$2", "status_code": 301},
                        }
                    ],
                    "priority": 1,
                    "status": "active",
                },
            )
            rule_resp.raise_for_status()
            logger.success(f"✅ Cloudflare 301 rule applied: {domain} → {target}")

    async def setup_301_redirect(self, candidate: DomainCandidate) -> str | None:
        """
        Determine the best redirect target for a caught domain and apply the rule.

        Returns the target URL (for use by PortfolioManager), or None if the
        candidate has no domain.
        """
        domain = candidate.domain
        if not domain:
            logger.warning(
                f"RedirectManager: no domain on candidate {candidate.company_name!r} — skipped"
            )
            return None

        score = candidate.seo_score or 0.0
        target = self._build_target_url(domain, score)
        logger.info(f"🔄 301 redirect plan: {domain} → {target} (score={score:.1f})")

        try:
            await self._apply_cloudflare_rule(domain, target)
        except Exception as exc:
            logger.warning(f"Cloudflare rule setup failed for {domain!r}: {exc}")

        await APIResilience.log_compliance(
            "301_redirect_setup",
            {
                "caught_domain": domain,
                "target": target,
                "score": score,
                "llc": Config.LLC_ENTITY,
            },
        )
        return target

    async def setup_batch(
        self, candidates: list[DomainCandidate]
    ) -> dict[str, str | None]:
        """
        Apply redirect rules for a batch of caught candidates.

        Returns a mapping of domain → target URL (None if domain was missing).
        """
        results: dict[str, str | None] = {}
        for candidate in candidates:
            key = candidate.domain or candidate.company_name
            results[key] = await self.setup_301_redirect(candidate)
        return results
