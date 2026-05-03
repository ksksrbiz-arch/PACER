"""Unit tests for the monetization strategy router."""

from __future__ import annotations

import pytest
from pacer.models.domain_candidate import (
    DomainCandidate,
    PipelineSource,
    Status,
)
from pacer.monetization.router import (
    PRIMARY_HUB,
    MonetizationRouter,
    _categorize,
    _slugify,
)


@pytest.fixture
def router() -> MonetizationRouter:
    return MonetizationRouter()


@pytest.fixture
def candidate() -> DomainCandidate:
    return DomainCandidate(
        domain="example.com",
        source=PipelineSource.SOS_DISSOLUTION,
        llc_entity="1COMMERCE LLC",
        status=Status.CAUGHT,
    )


# --- Strategy tiers --------------------------------------------------
@pytest.mark.parametrize(
    "score,expected",
    [
        (95, "301_redirect"),
        (75, "301_redirect"),
        (60, "301_redirect"),  # >= dropcatch threshold
        (59, "parking"),
        (40, "parking"),  # >= parking threshold
        (39, "aftermarket"),
        (0, "aftermarket"),
        (None, "aftermarket"),
    ],
)
def test_choose_strategy_tiers(
    router: MonetizationRouter,
    score: float | None,
    expected: str,
) -> None:
    assert router.choose_strategy(score) == expected


# --- Mutation contract -----------------------------------------------
def test_route_mutates_candidate(
    router: MonetizationRouter,
    candidate: DomainCandidate,
) -> None:
    candidate.score = 82.0
    result = router.route(candidate)

    assert result is candidate  # mutates in place, returns same ref
    assert candidate.monetization_strategy == "301_redirect"
    assert candidate.status == Status.MONETIZED


def test_route_handles_none_score(
    router: MonetizationRouter,
    candidate: DomainCandidate,
) -> None:
    candidate.score = None
    router.route(candidate)
    assert candidate.monetization_strategy == "aftermarket"
    assert candidate.redirect_target is None


# --- Category inference ----------------------------------------------
@pytest.mark.parametrize(
    "company_name,domain,expected_category",
    [
        ("CloudCRM Platform", "cloudcrm.io", "saas_alternative"),
        ("PDF Converter Tool", "pdfconverter.com", "tool_replacement"),
        ("ShopWidget Store", "shopwidget.com", "ecommerce"),
        ("BrainBoost Academy", "brainboost.edu", "educational"),
        ("Global Trade Hub", "globaltradehub.com", "international"),
        ("Random Biz Co", "randombiz.xyz", "default"),
    ],
)
def test_categorize_by_keywords(
    company_name: str,
    domain: str,
    expected_category: str,
) -> None:
    c = DomainCandidate(
        domain=domain,
        company_name=company_name,
        source=PipelineSource.SOS_DISSOLUTION,
        llc_entity="1COMMERCE LLC",
    )
    assert _categorize(c) == expected_category


# --- Slugify ---------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("CloudCRM Platform", "cloudcrm-platform"),
        ("  Spaced   Name  ", "spaced-name"),
        ("Acme, Inc.!", "acme-inc"),
        ("Already-slug", "already-slug"),
    ],
)
def test_slugify(raw: str, expected: str) -> None:
    assert _slugify(raw) == expected


# --- Target URL resolution -------------------------------------------
def test_301_redirects_to_category_slug(
    router: MonetizationRouter,
) -> None:
    c = DomainCandidate(
        domain="cloudcrm.io",
        company_name="CloudCRM Platform",
        source=PipelineSource.SOS_DISSOLUTION,
        llc_entity="1COMMERCE LLC",
        score=85.0,
    )
    router.route(c)
    assert c.monetization_strategy == "301_redirect"
    assert c.redirect_target == f"{PRIMARY_HUB}/alternatives/cloudcrm-platform"


def test_ecommerce_routes_to_marketplace(
    router: MonetizationRouter,
) -> None:
    c = DomainCandidate(
        domain="shopwidget.com",
        company_name="ShopWidget Store",
        source=PipelineSource.SOS_DISSOLUTION,
        llc_entity="1COMMERCE LLC",
        score=82.0,
    )
    router.route(c)
    assert c.redirect_target == f"{PRIMARY_HUB}/marketplace/shopwidget-store"


def test_parking_uses_hub_with_ref(router: MonetizationRouter) -> None:
    c = DomainCandidate(
        domain="generic.io",
        company_name="Generic Biz",
        source=PipelineSource.SOS_DISSOLUTION,
        llc_entity="1COMMERCE LLC",
        score=50.0,
    )
    router.route(c)
    assert c.monetization_strategy == "parking"
    assert c.redirect_target is not None
    assert "?ref=generic.io" in c.redirect_target


def test_aftermarket_has_no_target(router: MonetizationRouter) -> None:
    c = DomainCandidate(
        domain="junk.xyz",
        company_name="Junk Domain Co",
        source=PipelineSource.SOS_DISSOLUTION,
        llc_entity="1COMMERCE LLC",
        score=15.0,
    )
    router.route(c)
    assert c.monetization_strategy == "aftermarket"
    assert c.redirect_target is None


def test_301_without_company_name_falls_back_to_hub_root(
    router: MonetizationRouter,
) -> None:
    c = DomainCandidate(
        domain="cloudapi.dev",
        company_name=None,
        source=PipelineSource.SOS_DISSOLUTION,
        llc_entity="1COMMERCE LLC",
        score=90.0,
    )
    router.route(c)
    # domain contains "cloud" -> saas_alternative
    assert c.redirect_target == f"{PRIMARY_HUB}/alternatives"


# --- Batch -----------------------------------------------------------
def test_route_batch(router: MonetizationRouter) -> None:
    candidates = [
        DomainCandidate(
            domain=f"site{i}.com",
            company_name=f"Site {i}",
            source=PipelineSource.SOS_DISSOLUTION,
            llc_entity="1COMMERCE LLC",
            score=float(score),
        )
        for i, score in enumerate([95, 50, 10])
    ]
    results = router.route_batch(candidates)
    assert len(results) == 3
    assert results[0].monetization_strategy == "301_redirect"
    assert results[1].monetization_strategy == "parking"
    assert results[2].monetization_strategy == "aftermarket"
