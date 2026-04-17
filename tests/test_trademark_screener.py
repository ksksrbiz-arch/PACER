"""USPTOTrademarkScreener tests — fully mocked, no network."""
from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import SecretStr

from pacer.scoring.trademark import (
    USPTOTrademarkScreener,
    _normalize_brand,
)

_USPTO = "https://tsdrapi.uspto.gov/ts/cd/casestatus/search"


def test_normalize_brand_strips_tld_and_punct():
    assert _normalize_brand("Widget-Co.com") == "widgetco"
    assert _normalize_brand("FOO.BAR.NET") == "foo"
    assert _normalize_brand("a1-b2.io") == "a1b2"


@pytest.mark.asyncio
async def test_disabled_returns_clear():
    screener = USPTOTrademarkScreener(enabled=False)
    verdict = await screener.check("anything.com", "tech")
    assert verdict.conflict is False
    assert verdict.reason == "disabled"


@pytest.mark.asyncio
async def test_too_short_brand_skipped():
    screener = USPTOTrademarkScreener(api_key=SecretStr("k"), enabled=True)
    verdict = await screener.check("a.com", "tech")
    assert verdict.conflict is False
    assert verdict.reason == "too_short"


@pytest.mark.asyncio
@respx.mock
async def test_clear_when_no_records():
    respx.get(_USPTO).mock(return_value=httpx.Response(200, json={"results": []}))
    async with httpx.AsyncClient() as client:
        screener = USPTOTrademarkScreener(
            api_key=SecretStr("k"), client=client, enabled=True
        )
        verdict = await screener.check("novelwidgetbrand.com", "tech")
    assert verdict.conflict is False
    assert verdict.reason == "clear"


@pytest.mark.asyncio
@respx.mock
async def test_exact_match_is_conflict():
    respx.get(_USPTO).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "markIdentification": "WIDGETCO",
                        "markCurrentStatusCategory": "registered",
                        "internationalClassNumbers": [42],
                    }
                ]
            },
        )
    )
    async with httpx.AsyncClient() as client:
        screener = USPTOTrademarkScreener(
            api_key=SecretStr("k"), client=client, enabled=True
        )
        verdict = await screener.check("widgetco.com", "tech")
    assert verdict.conflict is True
    assert verdict.reason == "exact_match"


@pytest.mark.asyncio
@respx.mock
async def test_fuzzy_class_overlap_is_conflict():
    # live mark "widget" vs our brand "widgetx" — fuzzy + nice class 42 (tech)
    respx.get(_USPTO).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "markIdentification": "WIDGET",
                        "markCurrentStatusCategory": "live",
                        "internationalClassNumbers": ["42"],
                    }
                ]
            },
        )
    )
    async with httpx.AsyncClient() as client:
        screener = USPTOTrademarkScreener(
            api_key=SecretStr("k"), client=client, enabled=True
        )
        verdict = await screener.check("widgetx.com", "tech")
    assert verdict.conflict is True
    assert verdict.reason == "fuzzy_class_overlap"


@pytest.mark.asyncio
@respx.mock
async def test_fuzzy_no_class_overlap_is_clear():
    # live mark "widget" in class 25 (clothing), our domain is tech (42) → no overlap
    respx.get(_USPTO).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "markIdentification": "WIDGET",
                        "markCurrentStatusCategory": "live",
                        "internationalClassNumbers": [25],
                    }
                ]
            },
        )
    )
    async with httpx.AsyncClient() as client:
        screener = USPTOTrademarkScreener(
            api_key=SecretStr("k"), client=client, enabled=True
        )
        verdict = await screener.check("widgetx.com", "tech")
    assert verdict.conflict is False
    assert verdict.reason == "clear"


@pytest.mark.asyncio
@respx.mock
async def test_dead_marks_ignored():
    respx.get(_USPTO).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "markIdentification": "WIDGETCO",
                        "markCurrentStatusCategory": "dead",
                        "internationalClassNumbers": [42],
                    }
                ]
            },
        )
    )
    async with httpx.AsyncClient() as client:
        screener = USPTOTrademarkScreener(
            api_key=SecretStr("k"), client=client, enabled=True
        )
        verdict = await screener.check("widgetco.com", "tech")
    assert verdict.conflict is False
    assert verdict.reason == "clear"


@pytest.mark.asyncio
@respx.mock
async def test_network_error_degrades_to_unknown():
    respx.get(_USPTO).mock(side_effect=httpx.ConnectError("boom"))
    async with httpx.AsyncClient() as client:
        screener = USPTOTrademarkScreener(
            api_key=SecretStr("k"), client=client, enabled=True
        )
        verdict = await screener.check("widgetco.com", "tech")
    assert verdict.conflict is False
    assert verdict.reason == "unknown"
