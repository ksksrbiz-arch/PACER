"""Engine × trademark screener integration.

Verifies a TM conflict discards a candidate before Ahrefs/LLM are called.
We patch the module-level screener and the batch_metrics / llm_relevance
functions so nothing hits the network.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pacer.models.domain_candidate import (
    DomainCandidate,
    PipelineSource,
    Status,
)
from pacer.scoring import engine as engine_mod
from pacer.scoring.trademark import TrademarkVerdict


def _mk(domain: str = "widget.com") -> DomainCandidate:
    return DomainCandidate(
        domain=domain,
        company_name="Widget Inc",
        source=PipelineSource.SOS_DISSOLUTION,
        llc_entity="1COMMERCE LLC",
        status=Status.CAUGHT,
    )


@pytest.mark.asyncio
async def test_score_candidate_tm_conflict_discards(monkeypatch) -> None:
    # Screener reports a live exact-match conflict.
    async def fake_check(domain: str, category: str = "default"):
        return TrademarkVerdict(True, "exact_match", [{"m": domain}])

    monkeypatch.setattr(engine_mod._tm_screener, "check", fake_check)

    ahrefs_spy = AsyncMock()
    llm_spy = AsyncMock()
    monkeypatch.setattr(engine_mod, "batch_metrics", ahrefs_spy)
    monkeypatch.setattr(engine_mod, "llm_relevance", llm_spy)

    c = _mk()
    result = await engine_mod.score_candidate(c)

    assert result.status == Status.DISCARDED
    assert result.score == 0.0
    assert result.tm_conflict is True
    assert result.tm_reason == "exact_match"
    # Critically, no Ahrefs/LLM calls were made for this candidate.
    ahrefs_spy.assert_not_called()
    llm_spy.assert_not_called()


@pytest.mark.asyncio
async def test_score_candidate_tm_clear_proceeds(monkeypatch) -> None:
    async def fake_check(domain: str, category: str = "default"):
        return TrademarkVerdict(False, "clear", [])

    monkeypatch.setattr(engine_mod._tm_screener, "check", fake_check)

    # batch_metrics needs to return a dict mapping domain -> AhrefsMetrics
    from pacer.scoring.ahrefs import AhrefsMetrics

    async def fake_metrics(domains):
        return {
            d: AhrefsMetrics(domain=d, domain_rating=5.0, backlinks=0, referring_domains=0)
            for d in domains
        }

    async def fake_llm(domain, company_name):
        return {"relevance": 0, "commercial_intent": 0}

    monkeypatch.setattr(engine_mod, "batch_metrics", fake_metrics)
    monkeypatch.setattr(engine_mod, "llm_relevance", fake_llm)

    c = _mk("novel-widget-brand.io")
    result = await engine_mod.score_candidate(c)

    assert result.tm_conflict is False
    assert result.tm_reason == "clear"
    assert result.status == Status.SCORED
