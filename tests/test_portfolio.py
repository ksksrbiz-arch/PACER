"""
Tests for pacer/portfolio/portfolio_manager.py and pacer/models/domain_portfolio.py
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from pacer.models.domain_candidate import DomainCandidate, PipelineSource, Status
from pacer.models.domain_portfolio import DomainPortfolio
from pacer.portfolio.portfolio_manager import PortfolioManager


def _candidate(
    domain: str,
    score: float = 70.0,
    company_name: str = "Test Co",
) -> DomainCandidate:
    return DomainCandidate(
        domain=domain,
        company_name=company_name,
        source=PipelineSource.SOS_DISSOLUTION,
        status=Status.CAUGHT,
        score=score,
    )


# ---------------------------------------------------------------------------
# add_from_candidate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_from_candidate_basic():
    mgr = PortfolioManager()
    candidate = _candidate("acmesaas.io", score=72.0, company_name="Acme SaaS")

    async def noop(**kwargs):
        return None

    with patch("pacer.portfolio.portfolio_manager.record_event", new=noop):
        entry = await mgr.add_from_candidate(candidate)

    assert entry.domain == "acmesaas.io"
    assert entry.status == "pending"
    assert entry.purchase_date == date.today().isoformat()
    assert entry.seo_score == 72.0


@pytest.mark.asyncio
async def test_add_from_candidate_valuation_estimate():
    mgr = PortfolioManager()
    candidate = _candidate("highscore.io", score=90.0, company_name="HighScore Corp")

    async def noop(**kwargs):
        return None

    with patch("pacer.portfolio.portfolio_manager.record_event", new=noop):
        entry = await mgr.add_from_candidate(candidate)

    # Valuation = score * 100, capped at 50_000
    assert entry.current_valuation_usd == 9000.0


@pytest.mark.asyncio
async def test_add_from_candidate_valuation_capped():
    """Domains with very high synthetic scores should not exceed the cap."""
    mgr = PortfolioManager()
    candidate = _candidate("mega.io", score=999.0, company_name="Mega Corp")

    async def noop(**kwargs):
        return None

    with patch("pacer.portfolio.portfolio_manager.record_event", new=noop):
        entry = await mgr.add_from_candidate(candidate)

    assert entry.current_valuation_usd == 50_000.0


@pytest.mark.asyncio
async def test_add_from_candidate_records_redirect_and_strategy():
    mgr = PortfolioManager()
    candidate = _candidate("crm.io", score=80.0, company_name="CRM Co")

    async def noop(**kwargs):
        return None

    with patch("pacer.portfolio.portfolio_manager.record_event", new=noop):
        entry = await mgr.add_from_candidate(
            candidate,
            redirect_target="https://1commercesolutions.com/resources/saas-alternatives/crm",
            monetization_strategy="301_redirect",
            purchase_price_usd=299.0,
            registrar="Dynadot",
        )

    assert entry.redirect_target is not None
    assert "crm" in entry.redirect_target
    assert entry.monetization_strategy == "301_redirect"
    assert entry.purchase_price_usd == 299.0
    assert entry.registrar == "Dynadot"


# ---------------------------------------------------------------------------
# compute_portfolio_summary
# ---------------------------------------------------------------------------


def _make_entry(
    domain: str,
    status: str = "active",
    valuation: float = 1000.0,
    score: float = 70.0,
) -> DomainPortfolio:
    return DomainPortfolio(
        domain=domain, status=status, current_valuation_usd=valuation, seo_score=score
    )


def test_compute_portfolio_summary_empty():
    mgr = PortfolioManager()
    summary = mgr.compute_portfolio_summary([])
    assert summary["total_domains"] == 0
    assert summary["total_valuation_usd"] == 0.0
    assert summary["avg_seo_score"] == 0.0


def test_compute_portfolio_summary_aggregation():
    mgr = PortfolioManager()
    entries = [
        _make_entry("a.com", "active", 1000.0, 60.0),
        _make_entry("b.com", "active", 2000.0, 80.0),
        _make_entry("c.com", "sold", 500.0, 50.0),
    ]
    summary = mgr.compute_portfolio_summary(entries)
    assert summary["total_domains"] == 3
    assert summary["total_valuation_usd"] == 3500.0
    assert summary["avg_seo_score"] == round((60 + 80 + 50) / 3, 2)
    assert summary["status_breakdown"]["active"] == 2
    assert summary["status_breakdown"]["sold"] == 1


# ---------------------------------------------------------------------------
# find_expiring_soon
# ---------------------------------------------------------------------------


def test_find_expiring_soon_returns_entries_in_window():
    mgr = PortfolioManager()
    soon = (date.today() + timedelta(days=10)).isoformat()
    far = (date.today() + timedelta(days=60)).isoformat()
    entries = [
        DomainPortfolio(domain="expiring.io", renewal_date=soon),
        DomainPortfolio(domain="safe.io", renewal_date=far),
    ]
    result = mgr.find_expiring_soon(entries, days=30)
    assert len(result) == 1
    assert result[0].domain == "expiring.io"


def test_find_expiring_soon_excludes_past_renewals():
    mgr = PortfolioManager()
    past = (date.today() - timedelta(days=5)).isoformat()
    entries = [DomainPortfolio(domain="expired.io", renewal_date=past)]
    result = mgr.find_expiring_soon(entries, days=30)
    assert len(result) == 0


def test_find_expiring_soon_skips_missing_renewal_date():
    mgr = PortfolioManager()
    entries = [DomainPortfolio(domain="nodateio", renewal_date=None)]
    result = mgr.find_expiring_soon(entries, days=30)
    assert len(result) == 0


def test_find_expiring_soon_handles_invalid_date():
    mgr = PortfolioManager()
    entries = [DomainPortfolio(domain="bad.io", renewal_date="not-a-date")]
    result = mgr.find_expiring_soon(entries, days=30)
    assert len(result) == 0


# ---------------------------------------------------------------------------
# update_valuation
# ---------------------------------------------------------------------------


def test_update_valuation():
    mgr = PortfolioManager()
    entry = DomainPortfolio(domain="test.io", current_valuation_usd=1000.0)
    updated = mgr.update_valuation(entry, 2500.0)
    assert updated.current_valuation_usd == 2500.0


# ---------------------------------------------------------------------------
# DomainPortfolio model
# ---------------------------------------------------------------------------


def test_domain_portfolio_defaults():
    entry = DomainPortfolio(domain="example.io")
    assert entry.domain == "example.io"
    assert entry.status == "active"
    assert entry.registrar is None
    assert entry.redirect_target is None


def test_domain_portfolio_repr():
    entry = DomainPortfolio(domain="repr.io", status="active", current_valuation_usd=5000.0)
    r = repr(entry)
    assert entry.domain in r
    assert entry.status in r
