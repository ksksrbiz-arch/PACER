"""Auction / LTO / yield-score coverage for the monetization router."""

from __future__ import annotations

import pytest
from pacer.models.domain_candidate import (
    DomainCandidate,
    PipelineSource,
    Status,
)
from pacer.monetization.router import (
    AFTERNIC_BIN_URL,
    DAN_LTO_URL,
    MonetizationRouter,
    _commercial_component,
    _estimate_monthly_lto_cents,
    yield_score,
)


def _mk(**kwargs) -> DomainCandidate:
    defaults = dict(
        domain="widget.com",
        company_name="Widget Inc",
        source=PipelineSource.SOS_DISSOLUTION,
        llc_entity="1COMMERCE LLC",
        status=Status.CAUGHT,
    )
    defaults.update(kwargs)
    return DomainCandidate(**defaults)


@pytest.fixture
def router() -> MonetizationRouter:
    return MonetizationRouter()


# --- yield_score ---------------------------------------------------------
def test_yield_score_all_zeros_is_zero() -> None:
    assert yield_score(_mk()) == 0.0


def test_yield_score_weights_default_40_60() -> None:
    # DR=100, relevance=100, cpc=20 → commercial=70+30=100
    c = _mk(domain_rating=100.0, topical_relevance=100.0, cpc_usd=20.0)
    # 0.40*100 + 0.60*100 = 100
    assert yield_score(c) == 100.0


def test_yield_score_pure_authority() -> None:
    c = _mk(domain_rating=100.0)
    # 0.40*100 + 0.60*0 = 40.0
    assert yield_score(c) == 40.0


def test_yield_score_pure_commercial_relevance() -> None:
    c = _mk(topical_relevance=100.0)
    # 0.60 * (0.70 * 100) = 42.0
    assert yield_score(c) == 42.0


# --- commercial component ------------------------------------------------
def test_commercial_component_caps_cpc_at_20() -> None:
    low = _mk(topical_relevance=0.0, cpc_usd=20.0)
    insane = _mk(topical_relevance=0.0, cpc_usd=500.0)
    # Both cap at 100 for CPC component.
    assert _commercial_component(low) == _commercial_component(insane) == 30.0


def test_commercial_component_handles_nulls() -> None:
    assert _commercial_component(_mk()) == 0.0


# --- LTO price estimation ------------------------------------------------
def test_lto_price_none_without_signal() -> None:
    assert _estimate_monthly_lto_cents(_mk()) is None


def test_lto_price_has_floor() -> None:
    # Tiny DR, no commercial → should floor at $9.99 (999c)
    c = _mk(domain_rating=1.0)
    price = _estimate_monthly_lto_cents(c)
    assert price is not None
    assert price >= 999


def test_lto_price_scales_with_commercial() -> None:
    low = _mk(domain_rating=50.0, est_monthly_searches=100, cpc_usd=1.0)
    high = _mk(domain_rating=50.0, est_monthly_searches=10000, cpc_usd=10.0)
    assert _estimate_monthly_lto_cents(high) > _estimate_monthly_lto_cents(low)


# --- choose_strategy: auction tier --------------------------------------
def test_auction_tier_wins_above_threshold(router: MonetizationRouter) -> None:
    # yield_s=90 ≥ 85 auction threshold
    assert router.choose_strategy(score=60, yield_s=90.0) == "auction_bin"


def test_auction_tier_ignored_without_yield_arg(router: MonetizationRouter) -> None:
    # Single-arg form — yield_s defaults to None, auction gate skipped.
    assert router.choose_strategy(95) == "301_redirect"


# --- choose_strategy: LTO tier ------------------------------------------
def test_lto_requires_yield_and_commercial(router: MonetizationRouter) -> None:
    # yield ≥ 70 but commercial < 50 → falls through to 301/parking
    assert router.choose_strategy(score=75, yield_s=75.0, commercial=20.0) == "301_redirect"
    # yield ≥ 70 AND commercial ≥ 50 → LTO
    assert router.choose_strategy(score=75, yield_s=75.0, commercial=60.0) == "lease_to_own"


def test_auction_beats_lto(router: MonetizationRouter) -> None:
    # Both thresholds met — auction wins
    assert router.choose_strategy(score=99, yield_s=95.0, commercial=99.0) == "auction_bin"


# --- route() wiring ------------------------------------------------------
def test_route_auction_sets_afternic_url(router: MonetizationRouter) -> None:
    c = _mk(
        domain="premium.com",
        score=99.0,
        domain_rating=100.0,
        topical_relevance=100.0,
        cpc_usd=20.0,
    )
    router.route(c)
    assert c.monetization_strategy == "auction_bin"
    assert c.auction_listing_url == AFTERNIC_BIN_URL.format(domain="premium.com")
    # Redirect target for auction is the BIN listing itself
    assert c.redirect_target == AFTERNIC_BIN_URL.format(domain="premium.com")


def test_route_lto_enables_and_prices(router: MonetizationRouter) -> None:
    # DR=80, relevance=90, CPC=5 → yield well above 70 but below 85
    # commercial = 0.70*90 + 0.30*(5/20*100) = 63 + 7.5 = 70.5 → ≥ 50
    # yield = 0.40*80 + 0.60*70.5 = 32 + 42.3 = 74.3
    c = _mk(
        domain="midtier.io",
        score=75.0,
        domain_rating=80.0,
        topical_relevance=90.0,
        cpc_usd=5.0,
        est_monthly_searches=2000,
    )
    router.route(c)
    assert c.monetization_strategy == "lease_to_own"
    assert c.lease_to_own_enabled is True
    assert c.auction_listing_url == DAN_LTO_URL.format(domain="midtier.io")
    assert c.lease_monthly_price_cents is not None
    assert c.lease_monthly_price_cents > 0
    # Redirect still goes to hub with lto flag so we capture parking revenue
    assert c.redirect_target is not None
    assert "lto=1" in c.redirect_target


def test_route_preserves_existing_301_path(router: MonetizationRouter) -> None:
    # Classic 301 — no DR/commercial signal, score 82 → 301 redirect.
    c = _mk(domain="cloudcrm.io", company_name="CloudCRM Platform", score=82.0)
    router.route(c)
    assert c.monetization_strategy == "301_redirect"
    assert c.auction_listing_url is None
    assert c.lease_to_own_enabled is False
