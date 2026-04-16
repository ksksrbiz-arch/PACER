"""
Tests for src/pacer/pacer_client.py

Uses pytest-mock to avoid real HTTP calls.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.pacer.pacer_client import PACERClient


@pytest.fixture
def client():
    return PACERClient()


@pytest.fixture
def mock_pcl_cases():
    return [
        {"debtorName": "Acme SaaS Inc", "caseNumber": "24-12345", "dateFiled": "2026-04-15"},
        {"debtorName": "TechCo Platform LLC", "caseNumber": "24-12346", "dateFiled": "2026-04-15"},
    ]


@pytest.fixture
def mock_recap_cases():
    return [
        {"partyName": "CloudStack Corp", "caseNumber": "24-12347", "dateFiled": "2026-04-15"},
        # Duplicate of first PCL case — should be de-duplicated
        {"debtorName": "Acme SaaS Inc", "caseNumber": "24-12345", "dateFiled": "2026-04-15"},
    ]


@pytest.mark.asyncio
async def test_fetch_yesterday_bankruptcies_merges_and_deduplicates(
    client, mock_pcl_cases, mock_recap_cases
):
    """Candidates from PCL and RECAP should be merged and deduplicated by debtor name."""
    with (
        patch.object(client, "_call_pcl", new=AsyncMock(return_value=mock_pcl_cases)),
        patch.object(client, "_call_recap", new=AsyncMock(return_value=mock_recap_cases)),
        patch("src.pacer.pacer_client.APIResilience.log_compliance", new=AsyncMock()),
    ):
        candidates = await client.fetch_yesterday_bankruptcies()

    # Acme SaaS Inc appears in both — should only appear once
    names = [c.company_name for c in candidates]
    assert len(candidates) == 3
    assert names.count("Acme SaaS Inc") == 1
    assert "TechCo Platform LLC" in names
    assert "CloudStack Corp" in names


@pytest.mark.asyncio
async def test_fetch_yesterday_pcl_failure_falls_back_to_recap(client, mock_recap_cases):
    """If PCL fails, pipeline continues with RECAP results only (no PCL deduplication)."""
    with (
        patch.object(client, "_call_pcl", new=AsyncMock(side_effect=Exception("PCL down"))),
        patch.object(client, "_call_recap", new=AsyncMock(return_value=mock_recap_cases)),
        patch("src.pacer.pacer_client.APIResilience.log_compliance", new=AsyncMock()),
    ):
        candidates = await client.fetch_yesterday_bankruptcies()

    # PCL failed so pcl_cases=[] — both RECAP entries are unique from each other
    # mock_recap_cases has CloudStack Corp + Acme SaaS Inc (the "duplicate" only applies
    # when the same name appears in BOTH pcl_cases AND recap_cases)
    names = {c.company_name for c in candidates}
    assert "CloudStack Corp" in names
    # All candidates sourced from recap
    assert all(c.source == "recap" for c in candidates)


@pytest.mark.asyncio
async def test_fetch_yesterday_both_fail_returns_empty(client):
    """If both APIs fail, return empty list without raising."""
    with (
        patch.object(client, "_call_pcl", new=AsyncMock(side_effect=Exception("PCL down"))),
        patch.object(client, "_call_recap", new=AsyncMock(side_effect=Exception("RECAP down"))),
        patch("src.pacer.pacer_client.APIResilience.log_compliance", new=AsyncMock()),
    ):
        candidates = await client.fetch_yesterday_bankruptcies()

    assert candidates == []


@pytest.mark.asyncio
async def test_source_labelling(client, mock_pcl_cases, mock_recap_cases):
    """PCL cases should be labelled 'pacer_pcl', RECAP-only cases 'recap'."""
    with (
        patch.object(client, "_call_pcl", new=AsyncMock(return_value=mock_pcl_cases)),
        patch.object(client, "_call_recap", new=AsyncMock(return_value=mock_recap_cases)),
        patch("src.pacer.pacer_client.APIResilience.log_compliance", new=AsyncMock()),
    ):
        candidates = await client.fetch_yesterday_bankruptcies()

    sources = {c.company_name: c.source for c in candidates}
    assert sources["Acme SaaS Inc"] == "pacer_pcl"
    assert sources["TechCo Platform LLC"] == "pacer_pcl"
    assert sources["CloudStack Corp"] == "recap"


@pytest.mark.asyncio
async def test_empty_debtor_name_skipped(client):
    """Cases with empty or None debtor names should be silently skipped."""
    cases = [
        {"debtorName": "", "caseNumber": "24-99999"},
        {"debtorName": None, "caseNumber": "24-99998"},
        {"debtorName": "Valid Corp", "caseNumber": "24-99997"},
    ]
    with (
        patch.object(client, "_call_pcl", new=AsyncMock(return_value=cases)),
        patch.object(client, "_call_recap", new=AsyncMock(return_value=[])),
        patch("src.pacer.pacer_client.APIResilience.log_compliance", new=AsyncMock()),
    ):
        candidates = await client.fetch_yesterday_bankruptcies()

    assert len(candidates) == 1
    assert candidates[0].company_name == "Valid Corp"


@pytest.mark.asyncio
async def test_fetch_by_date_range(client, mock_pcl_cases):
    """fetch_by_date_range should call PCL with the provided date range."""
    mock_pcl = AsyncMock(return_value=mock_pcl_cases)
    with (
        patch.object(client, "_call_pcl", new=mock_pcl),
        patch("src.pacer.pacer_client.APIResilience.log_compliance", new=AsyncMock()),
    ):
        candidates = await client.fetch_by_date_range("2026-04-01", "2026-04-15")
        call_args = mock_pcl.call_args[0][0]

    assert len(candidates) == 2
    assert call_args["dateFiledFrom"] == "2026-04-01"
    assert call_args["dateFiledTo"] == "2026-04-15"
