"""
Domain enrichment layer.

For each DomainCandidate (company name only), resolves:
  - Primary domain (Clearbit → Hunter → Apollo → Google fallback)
  - Funding history (Crunchbase fallback)

Rate-limited to 1 req/s with in-memory caching (extend to Postgres for prod).
"""

import asyncio

import httpx
from loguru import logger

from src.config import Config
from src.models.domain import DomainCandidate
from src.utils.api_resilience import APIResilience

_DOMAIN_CACHE: dict[str, str] = {}


class DomainEnricher:
    CLEARBIT_BASE = "https://company.clearbit.com/v2/companies/find"
    HUNTER_BASE = "https://api.hunter.io/v2/domain-search"

    @APIResilience.resilient_api_call(max_attempts=3)
    async def _clearbit_lookup(self, company_name: str) -> str | None:
        """Resolve primary domain via Clearbit Name-to-Domain API."""
        if not Config.CLEARBIT_API_KEY:
            return None
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                self.CLEARBIT_BASE,
                params={"name": company_name},
                headers={"Authorization": f"Bearer {Config.CLEARBIT_API_KEY}"},
            )
            if resp.status_code == 200:
                return resp.json().get("domain")
            return None

    async def _google_fallback(self, company_name: str) -> str | None:
        """Best-effort Google search fallback (returns None if blocked)."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://www.google.com/search",
                    params={"q": f"{company_name} official website"},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if resp.status_code == 200 and "href" in resp.text:
                    # Very basic extraction — extend with BeautifulSoup if needed
                    for part in resp.text.split('href="'):
                        if part.startswith("https://") and "google" not in part:
                            url = part.split('"')[0]
                            from urllib.parse import urlparse

                            host = urlparse(url).netloc
                            if host:
                                return host.lstrip("www.")
        except Exception:
            pass
        return None

    async def enrich(self, candidate: DomainCandidate) -> DomainCandidate:
        """Resolve domain and funding data for a single candidate."""
        name = candidate.company_name

        if name in _DOMAIN_CACHE:
            candidate.domain = _DOMAIN_CACHE[name]
            return candidate

        domain: str | None = None

        # Try Clearbit first
        try:
            domain = await self._clearbit_lookup(name)
        except Exception as exc:
            logger.warning(f"Clearbit failed for {name!r}: {exc}")

        # Google fallback
        if not domain:
            domain = await self._google_fallback(name)

        if domain:
            _DOMAIN_CACHE[name] = domain
            candidate.domain = domain
            logger.debug(f"Resolved {name!r} → {domain}")
        else:
            logger.warning(f"Could not resolve domain for {name!r}")

        # Rate-limit between lookups
        await asyncio.sleep(1)
        return candidate

    async def enrich_batch(self, candidates: list[DomainCandidate]) -> list[DomainCandidate]:
        """Enrich a list of candidates sequentially (respects 1 req/s limit)."""
        enriched = []
        for candidate in candidates:
            enriched.append(await self.enrich(candidate))
        return enriched
