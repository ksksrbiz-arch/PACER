"""
SEO / topical relevance scorer.

Combines:
  - Ahrefs domain rating (DR) and traffic estimate
  - GPT-4o topical relevance score (SaaS/tech fit)

Returns a composite score 0–100. Candidates scoring ≥ SCORE_THRESHOLD
(default 60) are queued for drop-catch + RWA.
"""

import httpx
from loguru import logger
from openai import AsyncOpenAI

from src.config import Config
from src.models.domain import DomainCandidate
from src.utils.api_resilience import APIResilience

_openai = AsyncOpenAI(api_key=Config.OPENAI_API_KEY)


class SEOScorer:
    AHREFS_BASE = "https://api.ahrefs.com/v3/site-explorer/domain-rating"

    @APIResilience.resilient_api_call(max_attempts=3)
    async def _ahrefs_score(self, domain: str) -> float:
        """Fetch Ahrefs domain rating (0–100)."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                self.AHREFS_BASE,
                params={"target": domain, "output": "json"},
                headers={"Authorization": f"Bearer {Config.AHREFS_API_KEY}"},
            )
            resp.raise_for_status()
            data = resp.json()
            return float(data.get("domain_rating", {}).get("domain_rating", 0))

    async def _topical_score(self, company_name: str, domain: str | None) -> float:
        """
        GPT-4o relevance score: how well does this company/domain fit
        the SaaS/tech distressed-domain thesis? Returns 0.0–1.0.
        """
        prompt = (
            f"Rate how well the company '{company_name}' "
            f"(domain: {domain or 'unknown'}) fits the profile of a distressed "
            f"SaaS or tech startup with valuable domain equity. "
            f"Reply with a single float between 0.0 (no fit) and 1.0 (perfect fit). "
            f"No explanation."
        )
        try:
            response = await _openai.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=10,
                temperature=0,
            )
            raw = response.choices[0].message.content or "0"
            return min(1.0, max(0.0, float(raw.strip())))
        except Exception as exc:
            logger.warning(f"GPT-4o topical scoring failed for {company_name!r}: {exc}")
            return 0.0

    async def score(self, candidate: DomainCandidate) -> DomainCandidate:
        """Score a single candidate and update its seo_score and topical_score fields."""
        domain = candidate.domain

        ahrefs_dr: float = 0.0
        if domain and Config.AHREFS_API_KEY:
            try:
                ahrefs_dr = await self._ahrefs_score(domain)
            except Exception as exc:
                logger.warning(f"Ahrefs scoring failed for {domain!r}: {exc}")

        topical = await self._topical_score(candidate.company_name, domain)
        candidate.topical_score = topical

        # Composite: 60% Ahrefs DR + 40% topical relevance (both normalised to 0–100)
        candidate.seo_score = round(ahrefs_dr * 0.6 + topical * 100 * 0.4, 2)

        logger.debug(
            f"Scored {candidate.company_name!r}: "
            f"DR={ahrefs_dr} topical={topical:.2f} composite={candidate.seo_score}"
        )
        return candidate

    async def score_batch(self, candidates: list[DomainCandidate]) -> list[DomainCandidate]:
        """Score all candidates, return only those meeting the threshold."""
        scored = []
        for candidate in candidates:
            scored.append(await self.score(candidate))
        qualified = [c for c in scored if (c.seo_score or 0) >= Config.SCORE_THRESHOLD]
        logger.info(
            f"Scoring complete: {len(scored)} total, "
            f"{len(qualified)} qualified (≥{Config.SCORE_THRESHOLD})"
        )
        return qualified
