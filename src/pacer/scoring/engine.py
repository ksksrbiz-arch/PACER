"""Unified scoring engine.

Weighted blend:
    40% Ahrefs Domain Rating (normalized 0–100)
    20% Referring domains (log-scaled)
    20% GPT-4o topical relevance
    10% Commercial intent
    10% Inverse spam score
"""
from __future__ import annotations

import math

from loguru import logger
from sqlalchemy import select

from pacer.db import session_scope
from pacer.models.domain_candidate import DomainCandidate, Status
from pacer.scoring.ahrefs import AhrefsMetrics, batch_metrics
from pacer.scoring.relevance import llm_relevance
from pacer.scoring.spam_filter import is_likely_spam, spam_score


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

    if not clean:
        return discarded

    metrics = await batch_metrics([c.domain for c in clean])
    scored = [await _score_one(c, metrics) for c in clean]

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
            existing.status = c.status

    logger.info(
        "scoring_done scored={} discarded={} avg_score={:.1f}",
        len(scored),
        len(discarded),
        sum((c.score or 0) for c in scored) / max(len(scored), 1),
    )
    return scored + discarded
