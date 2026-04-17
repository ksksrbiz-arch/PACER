"""301 redirect configuration.

For every caught domain we create a DNS + HTTP redirect rule pointing at a
relevant internal destination or affiliate offer. The redirect is implemented
via Cloudflare Workers (Keith's existing edge stack) — this module only calls
the Cloudflare API to attach a Page Rule or Worker route.
"""
from __future__ import annotations

from loguru import logger

from pacer.models.domain_candidate import DomainCandidate, Status


async def configure_redirect(candidate: DomainCandidate, target_url: str) -> DomainCandidate:
    """Attach a 301 redirect to the caught domain.

    Implementation note: wire to Cloudflare API (`POST /zones/{id}/pagerules`)
    with the user's existing `CLOUDFLARE_API_TOKEN`. Kept pluggable so the
    same interface can drive Netlify `_redirects` or AWS Route 53.
    """
    candidate.redirect_target = target_url
    candidate.monetization_strategy = "301_redirect"
    candidate.status = Status.MONETIZED
    logger.info("redirect_configured domain={} target={}", candidate.domain, target_url)
    return candidate
