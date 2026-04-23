"""Tests for spam filter + scoring weights."""

from __future__ import annotations

import math

import pytest
from pacer.models.domain_candidate import DomainCandidate, PipelineSource, Status
from pacer.scoring.ahrefs import AhrefsMetrics
from pacer.scoring.engine import _score_one
from pacer.scoring.spam_filter import is_likely_spam, spam_score


# ─────────────────────── spam filter ───────────────────────────
@pytest.mark.parametrize(
    "domain,expected",
    [
        ("legitcorp.com", False),
        ("example.io", False),
        ("sketchy-offer.tk", True),  # bad TLD
        ("promo123456.com", True),  # long digit run
        ("a-b-c-d.com", True),  # heavy hyphenation
        ("best-casino.com", True),  # bad keyword
        ("clean-domain.ai", False),
    ],
)
def test_is_likely_spam(domain, expected):
    assert is_likely_spam(domain) is expected


def test_spam_score_is_bounded_0_to_1():
    # Worst-case compound: bad TLD + digits + hyphens + keyword
    worst = "casino-1234-loan--viagra.tk"
    assert 0.0 <= spam_score(worst) <= 1.0
    assert spam_score("cleandomain.com") == 0.0


# ─────────────────────── scoring weights ───────────────────────
@pytest.mark.asyncio
async def test_score_one_weights_match_spec(monkeypatch):
    """Validate the documented blend: 40% DR + 20% refdom + 20% rel + 10% intent + 10% inv-spam."""
    # Stub the LLM call so tests don't hit OpenAI
    from pacer.scoring import engine

    async def fake_llm(domain, company_name):
        return {"relevance": 80, "commercial_intent": 70, "vertical": "saas", "notes": ""}

    monkeypatch.setattr(engine, "llm_relevance", fake_llm)

    c = DomainCandidate(
        domain="acme.com",
        source=PipelineSource.EDGAR,
        status=Status.DISCOVERED,
        company_name="Acme Corp",
    )

    metrics = {
        "acme.com": AhrefsMetrics(
            domain="acme.com", domain_rating=50, backlinks=200, referring_domains=100
        )
    }
    scored = await _score_one(c, metrics)

    # Expected:
    #   0.40 * 50                     = 20.0
    #   0.20 * min(log10(101)*20,100) ≈ 0.20 * 40.09 = 8.02
    #   0.20 * 80                     = 16.0
    #   0.10 * 70                     = 7.0
    #   0.10 * (1 - 0.0) * 100        = 10.0
    expected = (
        0.40 * 50 + 0.20 * min(math.log10(101) * 20, 100) + 0.20 * 80 + 0.10 * 70 + 0.10 * 100
    )
    assert scored.score == round(expected, 2)
    assert scored.status == Status.SCORED
    assert scored.domain_rating == 50
    assert scored.referring_domains == 100


@pytest.mark.asyncio
async def test_score_one_skips_llm_when_dr_below_10(monkeypatch):
    """Weak domains (DR<10) should not consume OpenAI tokens."""
    from pacer.scoring import engine

    calls = {"n": 0}

    async def fake_llm(domain, company_name):
        calls["n"] += 1
        return {"relevance": 99, "commercial_intent": 99}

    monkeypatch.setattr(engine, "llm_relevance", fake_llm)

    c = DomainCandidate(domain="weak.com", source=PipelineSource.EDGAR, status=Status.DISCOVERED)
    metrics = {
        "weak.com": AhrefsMetrics(
            domain="weak.com", domain_rating=3, backlinks=0, referring_domains=0
        )
    }
    scored = await _score_one(c, metrics)

    assert calls["n"] == 0
    assert scored.topical_relevance == 0.0
