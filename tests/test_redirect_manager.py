"""
Tests for pacer/monetization/redirect_engine.py — topic routing + 301 redirect configuration.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pacer.models.domain_candidate import DomainCandidate, PipelineSource, Status
from pacer.monetization.redirect_engine import (
    PRIMARY_HUB,
    build_redirect_target,
    configure_redirect,
)


def _candidate(domain: str, score: float = 80.0) -> DomainCandidate:
    return DomainCandidate(
        domain=domain,
        source=PipelineSource.SOS_DISSOLUTION,
        status=Status.CAUGHT,
        score=score,
    )


# ---------------------------------------------------------------------------
# build_redirect_target — topic routing
# ---------------------------------------------------------------------------


def test_redirect_crm_keyword():
    assert build_redirect_target("bestcrm.io") == f"{PRIMARY_HUB}/resources/saas-alternatives/crm"


def test_redirect_sales_keyword():
    assert (
        build_redirect_target("salesboost.com") == f"{PRIMARY_HUB}/resources/saas-alternatives/crm"
    )


def test_redirect_project_keyword():
    url = build_redirect_target("projecthub.io")
    assert url == f"{PRIMARY_HUB}/resources/saas-alternatives/project-management"


def test_redirect_ecommerce_keyword():
    assert build_redirect_target("mystore.com") == f"{PRIMARY_HUB}/marketplace"


def test_redirect_saas_keyword():
    assert build_redirect_target("cloudplatform.io") == f"{PRIMARY_HUB}/alternatives"


def test_redirect_educational_keyword():
    assert build_redirect_target("learnfast.io") == f"{PRIMARY_HUB}/learn"


def test_redirect_finance_keyword():
    url = build_redirect_target("invoicemaster.com")
    assert url == f"{PRIMARY_HUB}/resources/saas-alternatives/finance"


def test_redirect_tool_keyword():
    assert build_redirect_target("builderapp.io") == f"{PRIMARY_HUB}/tools"


def test_redirect_global_keyword():
    assert build_redirect_target("globaltrading.com") == f"{PRIMARY_HUB}/global"


def test_redirect_unmatched_falls_back_to_resources():
    assert build_redirect_target("xyzqwerty123.com") == f"{PRIMARY_HUB}/resources"


# ---------------------------------------------------------------------------
# configure_redirect — full function
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_configure_redirect_sets_fields():
    """configure_redirect should set redirect_target, monetization_strategy, status."""
    c = _candidate("crmcorp.io")

    async def noop_cf(domain, target):
        return None

    async def noop_audit(**kwargs):
        return None

    with (
        patch("pacer.monetization.redirect_engine._apply_cloudflare_rule", new=noop_cf),
        patch("pacer.monetization.redirect_engine.record_event", new=noop_audit),
    ):
        result = await configure_redirect(c)

    assert result is c
    assert result.redirect_target is not None
    assert result.redirect_target.startswith("https://1commercesolutions.com")
    assert result.monetization_strategy == "301_redirect"
    assert result.status == Status.MONETIZED


@pytest.mark.asyncio
async def test_configure_redirect_explicit_target():
    """Explicit target_url must be used instead of auto-routing."""
    c = _candidate("anything.com")
    explicit = "https://1commercesolutions.com/specials"

    async def noop_cf(domain, target):
        return None

    async def noop_audit(**kwargs):
        return None

    with (
        patch("pacer.monetization.redirect_engine._apply_cloudflare_rule", new=noop_cf),
        patch("pacer.monetization.redirect_engine.record_event", new=noop_audit),
    ):
        result = await configure_redirect(c, target_url=explicit)

    assert result.redirect_target == explicit


@pytest.mark.asyncio
async def test_configure_redirect_survives_cloudflare_failure():
    """Cloudflare errors must not propagate — candidate is still updated."""
    c = _candidate("somesaas.io")

    async def noop_audit(**kwargs):
        return None

    with (
        patch(
            "pacer.monetization.redirect_engine._apply_cloudflare_rule",
            new=AsyncMock(side_effect=Exception("CF error")),
        ),
        patch("pacer.monetization.redirect_engine.record_event", new=noop_audit),
    ):
        result = await configure_redirect(c)

    assert result.redirect_target is not None
    assert result.status == Status.MONETIZED


# ---------------------------------------------------------------------------
# _apply_cloudflare_rule — skips when token absent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_cloudflare_rule_skips_without_token(monkeypatch):
    """When cloudflare_api_token is empty, the function returns without HTTP calls."""
    from pacer.config import get_settings
    from pacer.monetization.redirect_engine import _apply_cloudflare_rule

    settings = get_settings()
    monkeypatch.setattr(settings, "cloudflare_api_token", __import__("pydantic").SecretStr(""))

    # Should not raise
    await _apply_cloudflare_rule("example.com", "https://1commercesolutions.com/resources")
