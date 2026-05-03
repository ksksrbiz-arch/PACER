"""301 redirect configuration — topic routing + Cloudflare Page Rules API.

For every caught domain we:
  1. Determine the best 1commercesolutions.com landing page by topic keyword
     matching against the domain name.
  2. Call the Cloudflare API to attach a permanent (301) forwarding Page Rule
     so the redirect is live immediately after registration.
  3. Fall back to logging the rule for manual setup when no API token is
     configured, so the pipeline never hard-fails.

The resolved URL is written back to ``candidate.redirect_target`` and
``candidate.monetization_strategy`` by the :func:`configure_redirect` helper
so the existing pipeline interface is unchanged.
"""

from __future__ import annotations

import httpx
from loguru import logger

from pacer.compliance.audit import record_event
from pacer.config import get_settings
from pacer.models.domain_candidate import DomainCandidate, Status

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


def build_redirect_target(domain: str) -> str:
    """
    Select the best hub landing page based on domain keywords.

    Topic matching uses ordered TOPIC_RULES; the first rule whose keywords
    appear anywhere in the caught domain string wins.  Unmatched domains
    fall back to /resources.
    """
    lower = domain.lower()
    for keywords, path in TOPIC_RULES:
        if any(kw in lower for kw in keywords):
            return f"{PRIMARY_HUB}{path}"
    return f"{PRIMARY_HUB}/resources"


async def _apply_cloudflare_rule(domain: str, target: str) -> None:
    """
    Create (or update) a Cloudflare 301 page rule for ``domain → target``.

    Requires ``cloudflare_api_token`` in settings. The zone is looked up by
    root domain so subdomains are covered automatically.  If no matching zone
    is found the redirect is logged for manual setup instead of raising.
    """
    settings = get_settings()
    token = settings.cloudflare_api_token.get_secret_value()
    if not token:
        logger.info(
            "cloudflare_redirect_skipped domain={} target={} reason=no_token",
            domain,
            target,
        )
        return

    headers = {
        "Authorization": f"Bearer {token}",
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
                "cloudflare_zone_not_found domain={} root={} target={}",
                domain,
                root_domain,
                target,
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
        logger.success("cloudflare_rule_applied domain={} target={}", domain, target)


async def configure_redirect(
    candidate: DomainCandidate, target_url: str | None = None
) -> DomainCandidate:
    """Attach a 301 redirect to the caught domain.

    If ``target_url`` is omitted, the topic-routing logic selects the best
    hub page automatically.  The Cloudflare Page Rule is applied if
    ``CLOUDFLARE_API_TOKEN`` is set; otherwise the decision is logged for
    manual setup.

    The candidate's ``redirect_target``, ``monetization_strategy``, and
    ``status`` fields are updated in place.
    """
    domain = candidate.domain or ""
    if not target_url:
        target_url = build_redirect_target(domain)

    try:
        await _apply_cloudflare_rule(domain, target_url)
    except Exception as exc:
        logger.warning(
            "cloudflare_rule_failed domain={} err={} — continuing without CF rule",
            domain,
            exc,
        )

    candidate.redirect_target = target_url
    candidate.monetization_strategy = "301_redirect"
    candidate.status = Status.MONETIZED

    logger.info("redirect_configured domain={} target={}", domain, target_url)

    await record_event(
        event_type="redirect_configured",
        endpoint="monetization.configure_redirect",
        domain=domain,
        message=f"target={target_url}",
    )
    return candidate
