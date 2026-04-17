"""Unit tests for the monetization strategy router."""
from __future__ import annotations

import pytest

from pacer.models.domain_candidate import (
    DomainCandidate,
    PipelineSource,
    Status,
)
from pacer.monetization.router import MonetizationRouter


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


@pytest.mark.parametrize(
    "score,expected",
    [
        (95, "301_redirect"),
        (75, "301_redirect"),
        (60, "301_redirect"),  # >= dropcatch threshold
        (59, "parking"),
        (40, "parking"),       # >= parking threshold
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
