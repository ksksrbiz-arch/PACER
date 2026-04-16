"""
WHOIS lookup client.

Checks domain registration status and expiry for drop-catch targeting.
"""

from loguru import logger

import whois
from src.models.domain import DomainCandidate


class WhoisClient:
    async def check_domain(self, candidate: DomainCandidate) -> dict | None:
        """Return WHOIS data for the candidate's domain, or None if unavailable."""
        domain = candidate.domain
        if not domain:
            return None
        try:
            data = whois.whois(domain)
            return {
                "registrar": data.registrar,
                "expiration_date": str(data.expiration_date),
                "status": data.status,
            }
        except Exception as exc:
            logger.warning(f"WHOIS lookup failed for {domain!r}: {exc}")
            return None
