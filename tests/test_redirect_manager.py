"""
Tests for src/monetization/redirect_manager.py
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.models.domain import DomainCandidate
from src.monetization.redirect_manager import PRIMARY_HUB, RedirectManager

# ---------------------------------------------------------------------------
# _build_target_url — topic routing
# ---------------------------------------------------------------------------


def test_redirect_crm_keyword():
    mgr = RedirectManager()
    url = mgr._build_target_url("bestcrm.io", 85.0)
    assert url == f"{PRIMARY_HUB}/resources/saas-alternatives/crm"


def test_redirect_sales_keyword():
    mgr = RedirectManager()
    url = mgr._build_target_url("salesboost.com", 80.0)
    assert url == f"{PRIMARY_HUB}/resources/saas-alternatives/crm"


def test_redirect_project_keyword():
    mgr = RedirectManager()
    url = mgr._build_target_url("projecthub.io", 70.0)
    assert url == f"{PRIMARY_HUB}/resources/saas-alternatives/project-management"


def test_redirect_ecommerce_keyword():
    mgr = RedirectManager()
    url = mgr._build_target_url("mystore.com", 75.0)
    assert url == f"{PRIMARY_HUB}/marketplace"


def test_redirect_saas_keyword():
    mgr = RedirectManager()
    url = mgr._build_target_url("cloudplatform.io", 80.0)
    assert url == f"{PRIMARY_HUB}/alternatives"


def test_redirect_educational_keyword():
    mgr = RedirectManager()
    url = mgr._build_target_url("learnfast.io", 65.0)
    assert url == f"{PRIMARY_HUB}/learn"


def test_redirect_finance_keyword():
    mgr = RedirectManager()
    url = mgr._build_target_url("invoicemaster.com", 70.0)
    assert url == f"{PRIMARY_HUB}/resources/saas-alternatives/finance"


def test_redirect_tool_keyword():
    mgr = RedirectManager()
    url = mgr._build_target_url("builderapp.io", 62.0)
    assert url == f"{PRIMARY_HUB}/tools"


def test_redirect_global_keyword():
    mgr = RedirectManager()
    url = mgr._build_target_url("globaltrading.com", 65.0)
    assert url == f"{PRIMARY_HUB}/global"


def test_redirect_unmatched_falls_back_to_resources():
    mgr = RedirectManager()
    url = mgr._build_target_url("xyzqwerty123.com", 70.0)
    assert url == f"{PRIMARY_HUB}/resources"


# ---------------------------------------------------------------------------
# setup_301_redirect — full method
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_redirect_returns_none_for_no_domain():
    mgr = RedirectManager()
    candidate = DomainCandidate(company_name="NoDomainCo", domain=None, seo_score=80.0)
    result = await mgr.setup_301_redirect(candidate)
    assert result is None


@pytest.mark.asyncio
async def test_setup_redirect_returns_target_url():
    mgr = RedirectManager()
    candidate = DomainCandidate(
        company_name="CRM Corp", domain="crmcorp.io", seo_score=85.0
    )
    with patch.object(mgr, "_apply_cloudflare_rule", new_callable=AsyncMock) as mock_cf:
        result = await mgr.setup_301_redirect(candidate)

    assert result is not None
    assert result.startswith("https://1commercesolutions.com")
    mock_cf.assert_called_once()


@pytest.mark.asyncio
async def test_setup_redirect_survives_cloudflare_failure():
    """A Cloudflare error should not raise — redirect target is still returned."""
    mgr = RedirectManager()
    candidate = DomainCandidate(
        company_name="SomeSaaS", domain="somesaas.io", seo_score=75.0
    )
    with patch.object(
        mgr, "_apply_cloudflare_rule", new_callable=AsyncMock, side_effect=Exception("CF error")
    ):
        result = await mgr.setup_301_redirect(candidate)

    assert result is not None


@pytest.mark.asyncio
async def test_setup_batch_returns_mapping():
    mgr = RedirectManager()
    candidates = [
        DomainCandidate(company_name="CRM Co", domain="crmco.io", seo_score=85.0),
        DomainCandidate(company_name="No Domain", domain=None, seo_score=70.0),
    ]
    with patch.object(mgr, "_apply_cloudflare_rule", new_callable=AsyncMock):
        results = await mgr.setup_batch(candidates)

    assert "crmco.io" in results
    assert results["crmco.io"] is not None
    assert results["crmco.io"].startswith("https://1commercesolutions.com")
    # candidate with no domain falls back to company_name as key
    assert results.get("No Domain") is None


# ---------------------------------------------------------------------------
# _apply_cloudflare_rule — skips when token absent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_cloudflare_rule_skips_without_token(monkeypatch):
    """When CLOUDFLARE_API_TOKEN is empty, the method logs and returns without HTTP call."""
    from src import config as cfg_module

    original = cfg_module.Config.CLOUDFLARE_API_TOKEN
    monkeypatch.setattr(cfg_module.Config, "CLOUDFLARE_API_TOKEN", "")

    mgr = RedirectManager()
    # Should not raise even with no HTTP client
    await mgr._apply_cloudflare_rule.__wrapped__(mgr, "example.com", "https://target.com")

    monkeypatch.setattr(cfg_module.Config, "CLOUDFLARE_API_TOKEN", original)
