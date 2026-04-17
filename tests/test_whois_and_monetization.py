"""
Tests for src/whois/whois_client.py and src/monetization/monetization_router.py
"""

from unittest.mock import MagicMock, patch

import pytest

from src.models.domain import DomainCandidate
from src.monetization.monetization_router import MonetizationRouter
from src.whois.whois_client import WhoisClient

# ---------------------------------------------------------------------------
# WhoisClient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_whois_check_domain_returns_none_for_missing_domain():
    """Candidates without a domain should return None immediately."""
    client = WhoisClient()
    candidate = DomainCandidate(company_name="Acme", domain=None)
    result = await client.check_domain(candidate)
    assert result is None


@pytest.mark.asyncio
async def test_whois_check_domain_returns_dict_on_success():
    """A successful WHOIS lookup should return a dict with registrar/expiry/status keys."""
    mock_data = MagicMock()
    mock_data.registrar = "GoDaddy"
    mock_data.expiration_date = "2027-01-01"
    mock_data.status = "active"

    with patch("src.whois.whois_client.whois.whois", return_value=mock_data):
        client = WhoisClient()
        candidate = DomainCandidate(company_name="Acme", domain="acme.com")
        result = await client.check_domain(candidate)

    assert result is not None
    assert result["registrar"] == "GoDaddy"
    assert "2027-01-01" in result["expiration_date"]
    assert result["status"] == "active"


@pytest.mark.asyncio
async def test_whois_check_domain_returns_none_on_error():
    """WHOIS lookup failures should return None, not raise."""
    with patch("src.whois.whois_client.whois.whois", side_effect=Exception("WHOIS timeout")):
        client = WhoisClient()
        candidate = DomainCandidate(company_name="Acme", domain="acme.com")
        result = await client.check_domain(candidate)

    assert result is None


# ---------------------------------------------------------------------------
# MonetizationRouter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monetization_high_score_uses_301_redirect():
    """Domains with score ≥ 80 should be routed to 301_redirect."""
    router = MonetizationRouter()
    candidate = DomainCandidate(company_name="BigCo", domain="bigco.io", seo_score=85.0)
    result = await router.route(candidate)
    assert result.notes is not None
    assert "301_redirect" in result.notes


@pytest.mark.asyncio
async def test_monetization_mid_score_uses_parking():
    """Domains with score 60–79 should be routed to parking."""
    router = MonetizationRouter()
    candidate = DomainCandidate(company_name="MidCo", domain="midco.io", seo_score=65.0)
    result = await router.route(candidate)
    assert result.notes is not None
    assert "parking" in result.notes


@pytest.mark.asyncio
async def test_monetization_low_score_uses_aftermarket():
    """Domains with score < 60 should be routed to aftermarket."""
    router = MonetizationRouter()
    candidate = DomainCandidate(company_name="LowCo", domain="lowco.io", seo_score=40.0)
    result = await router.route(candidate)
    assert result.notes is not None
    assert "aftermarket" in result.notes


@pytest.mark.asyncio
async def test_monetization_appends_to_existing_notes():
    """MonetizationRouter should append to existing notes, not overwrite."""
    router = MonetizationRouter()
    candidate = DomainCandidate(
        company_name="NotedCo", domain="noted.io", seo_score=70.0, notes="case_id=24-999"
    )
    result = await router.route(candidate)
    assert result.notes is not None
    assert "case_id=24-999" in result.notes
    assert "parking" in result.notes


@pytest.mark.asyncio
async def test_monetization_categorizes_saas_to_alternatives():
    """SaaS keywords should route to /alternatives."""
    router = MonetizationRouter()
    candidate = DomainCandidate(
        company_name="CloudCRM Platform", domain="cloudcrm.io", seo_score=85.0
    )
    result = await router.route(candidate)
    assert (
        "target=https://1commercesolutions.com/alternatives/cloudcrm-platform"
        in result.notes
    )


@pytest.mark.asyncio
async def test_monetization_ecommerce_routes_to_marketplace():
    """E-commerce keywords should route to /marketplace."""
    router = MonetizationRouter()
    candidate = DomainCandidate(
        company_name="ShopWidget Store", domain="shopwidget.com", seo_score=82.0
    )
    result = await router.route(candidate)
    assert "/marketplace/" in result.notes


@pytest.mark.asyncio
async def test_monetization_parking_uses_hub_with_ref():
    """Mid-score domains park on hub with ?ref= tracking."""
    router = MonetizationRouter()
    candidate = DomainCandidate(
        company_name="Generic Biz", domain="generic.io", seo_score=65.0
    )
    result = await router.route(candidate)
    assert "?ref=generic.io" in result.notes


@pytest.mark.asyncio
async def test_monetization_aftermarket_no_target():
    """Low-score domains get no hub target (listed on aftermarket)."""
    router = MonetizationRouter()
    candidate = DomainCandidate(
        company_name="Junk Domain Co", domain="junk.xyz", seo_score=35.0
    )
    result = await router.route(candidate)
    assert "aftermarket" in result.notes
    assert "target=" not in result.notes
