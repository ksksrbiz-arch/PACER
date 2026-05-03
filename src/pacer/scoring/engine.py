"""Unified scoring engine.

Weighted blend:
    40% Ahrefs Domain Rating (normalized 0–100)
    20% Referring domains (log-scaled)
    20% GPT-4o topical relevance
    10% Commercial intent
    10% Inverse spam score

Pipeline order:
    spam filter → USPTO TM screen → Ahrefs → LLM relevance → weighted blend.

USPTO conflicts hard-stop the candidate (status=DISCARDED, score=0) so we
don't waste Ahrefs credits or LLM tokens on a domain we can't legally catch.
"""

from __future__ import annotations

import math

from loguru import logger
from sqlalchemy import select

from pacer.db import session_scope
from pacer.models.domain_candidate import DomainCandidate, Status
from pacer.monetization.router import _categorize
from pacer.scoring.ahrefs import AhrefsMetrics, batch_metrics
from pacer.scoring.relevance import llm_relevance
from pacer.scoring.spam_filter import is_likely_spam, spam_score
from pacer.scoring.trademark import USPTOTrademarkScreener

# Module-level singleton — honors settings.uspto_tmscreen_enabled internally.
_tm_screener = USPTOTrademarkScreener()


async def _tm_check(candidate: DomainCandidate) -> bool:
    """Run USPTO screen; mutate candidate with verdict. Return True on conflict."""
    try:
        verdict = await _tm_screener.check(candidate.domain, category=_categorize(candidate))
    except Exception as exc:  # defensive — screener already degrades internally
        logger.warning("tm_screen_unexpected_error domain={} err={}", candidate.domain, exc)
        candidate.tm_conflict = None
        candidate.tm_reason = "error"
        return False

    candidate.tm_conflict = verdict.conflict
    candidate.tm_reason = verdict.reason
    if verdict.conflict:
        candidate.score = 0.0
        candidate.status = Status.DISCARDED
        logger.info(
            "tm_conflict_discarded domain={} reason={}",
            candidate.domain,
            verdict.reason,
        )
    return verdict.conflict


async def _score_one(
    candidate: DomainCandidate,
    ahrefs_map: dict[str, AhrefsMetrics],
) -> DomainCandidate:
    metrics = ahrefs_map.get(candidate.domain)

    dr = float(metrics.domain_rating) if metrics else 0.0
    backlinks = int(metrics.backlinks) if metrics else 0
    refdomains = int(metrics.referring_domains) if metrics else 0
    refdomain_component = min(math.log10(refdomains + 1) * 20, 100)

    llm = await llm_relevance(candidate.domain, candidate.company_name) if dr >= 10 else {}
    relevance = float(llm.get("relevance") or 0)
    commercial = float(llm.get("commercial_intent") or 0)

    spam = spam_score(candidate.domain)

    score = (
        0.40 * dr
        + 0.20 * refdomain_component
        + 0.20 * relevance
        + 0.10 * commercial
        + 0.10 * (1.0 - spam) * 100
    )

    candidate.domain_rating = dr
    candidate.backlinks = backlinks
    candidate.referring_domains = refdomains
    candidate.topical_relevance = relevance
    candidate.spam_score = spam
    candidate.score = round(score, 2)
    candidate.status = Status.SCORED

    return candidate


async def score_candidate(candidate: DomainCandidate) -> DomainCandidate:
    if is_likely_spam(candidate.domain):
        candidate.spam_score = 1.0
        candidate.score = 0.0
        candidate.status = Status.DISCARDED
        return candidate

    if await _tm_check(candidate):
        return candidate

    metrics = await batch_metrics([candidate.domain])
    return await _score_one(candidate, metrics)


async def score_candidates(candidates: list[DomainCandidate]) -> list[DomainCandidate]:
    """Batch scoring — one Ahrefs call for all domains, individual LLM calls."""
    clean = [c for c in candidates if not is_likely_spam(c.domain)]
    discarded = [c for c in candidates if is_likely_spam(c.domain)]
    for c in discarded:
        c.spam_score = 1.0
        c.score = 0.0
        c.status = Status.DISCARDED

    # TM screen clean candidates; conflicts join the discard pile.
    tm_clean: list[DomainCandidate] = []
    for c in clean:
        if await _tm_check(c):
            discarded.append(c)
        else:
            tm_clean.append(c)

    if not tm_clean:
        return discarded

    metrics = await batch_metrics([c.domain for c in tm_clean])
    scored = [await _score_one(c, metrics) for c in tm_clean]

    # persist updates
    async with session_scope() as sess:
        for c in scored + discarded:
            existing = (
                await sess.execute(
                    select(DomainCandidate).where(DomainCandidate.domain == c.domain)
                )
            ).scalar_one_or_none()
            if existing is None:
                continue
            existing.score = c.score
            existing.domain_rating = c.domain_rating
            existing.backlinks = c.backlinks
            existing.referring_domains = c.referring_domains
            existing.topical_relevance = c.topical_relevance
            existing.spam_score = c.spam_score
            existing.tm_conflict = c.tm_conflict
            existing.tm_reason = c.tm_reason
            existing.status = c.status

    logger.info(
        "scoring_done scored={} discarded={} avg_score={:.1f}",
        len(scored),
        len(discarded),
        sum((c.score or 0) for c in scored) / max(len(scored), 1),
    )
    return scored + discarded
