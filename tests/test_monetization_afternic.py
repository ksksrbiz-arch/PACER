"""Aftermarket listing clients — Afternic / Sedo / DAN.

Respx-backed HTTP stubs; no real network calls. We also verify the
``aftermarket_listings_enabled=False`` + missing-key dry-run paths since
that's how CI + staging run by default.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from pacer.monetization.afternic import (
    AfternicClient,
    DanClient,
    ListingResult,
    SedoClient,
    post_auction_listing,
    post_lto_listing,
)


# ─────────────────────────── dry-run paths ──────────────────────────
@pytest.mark.asyncio
async def test_afternic_dry_run_when_no_key(monkeypatch):
    # Explicitly zero out key
    monkeypatch.setenv("AFTERNIC_API_KEY", "")
    monkeypatch.setenv("AFTERMARKET_LISTINGS_ENABLED", "false")
    from pacer.config import get_settings

    get_settings.cache_clear()  # invalidate lru_cache
    c = AfternicClient()
    res = await c.list_for_sale("widget.com", 299_000)
    assert res.status == "dry_run"
    assert res.provider == "afternic"
    assert res.listing_id is None
    assert res.listing_url == "https://www.afternic.com/domain/widget.com"


@pytest.mark.asyncio
async def test_sedo_dry_run_when_no_key(monkeypatch):
    monkeypatch.setenv("SEDO_SIGNKEY", "")
    monkeypatch.setenv("AFTERMARKET_LISTINGS_ENABLED", "false")
    from pacer.config import get_settings

    get_settings.cache_clear()
    c = SedoClient()
    res = await c.list_for_sale("widget.com", 299_000)
    assert res.status == "dry_run"
    assert res.provider == "sedo"


@pytest.mark.asyncio
async def test_dan_dry_run_when_no_key(monkeypatch):
    monkeypatch.setenv("DAN_API_KEY", "")
    monkeypatch.setenv("AFTERMARKET_LISTINGS_ENABLED", "false")
    from pacer.config import get_settings

    get_settings.cache_clear()
    c = DanClient()
    res = await c.list_lease_to_own("widget.com", 299_000, 9999)
    assert res.status == "dry_run"
    assert res.provider == "dan"


# ─────────────────────────── live HTTP paths ────────────────────────
@pytest.mark.asyncio
@respx.mock
async def test_afternic_posts_listing_with_proper_auth(monkeypatch):
    monkeypatch.setenv("AFTERNIC_API_KEY", "key-123")
    monkeypatch.setenv("AFTERNIC_PARTNER_ID", "partner-42")
    monkeypatch.setenv("AFTERMARKET_LISTINGS_ENABLED", "true")
    from pacer.config import get_settings

    get_settings.cache_clear()

    route = respx.post("https://api.afternic.com/v2/listings").mock(
        return_value=httpx.Response(200, json={"id": "af-7777"})
    )
    c = AfternicClient()
    res = await c.list_for_sale("widget.com", 299_000)

    assert route.called
    call = route.calls[0]
    sent = call.request
    assert sent.headers["Authorization"] == "sso-key key-123"
    assert sent.headers["X-Partner-Id"] == "partner-42"
    body = call.request.content.decode()
    assert '"domain":"widget.com"' in body
    assert '"price":2990.0' in body  # cents → USD conversion
    assert res.status == "listed"
    assert res.listing_id == "af-7777"


@pytest.mark.asyncio
@respx.mock
async def test_afternic_http_error_returns_error_result(monkeypatch):
    monkeypatch.setenv("AFTERNIC_API_KEY", "key-123")
    monkeypatch.setenv("AFTERMARKET_LISTINGS_ENABLED", "true")
    from pacer.config import get_settings

    get_settings.cache_clear()

    respx.post("https://api.afternic.com/v2/listings").mock(
        return_value=httpx.Response(422, json={"error": "invalid domain"})
    )
    c = AfternicClient()
    res = await c.list_for_sale("bad.com", 299_000)
    assert res.status == "error"
    assert res.error == "http_422"
    assert res.listing_id is None


@pytest.mark.asyncio
@respx.mock
async def test_sedo_posts_listing(monkeypatch):
    monkeypatch.setenv("SEDO_SIGNKEY", "sk-abc")
    monkeypatch.setenv("SEDO_USERNAME", "me")
    monkeypatch.setenv("SEDO_PARTNERID", "99")
    monkeypatch.setenv("AFTERMARKET_LISTINGS_ENABLED", "true")
    from pacer.config import get_settings

    get_settings.cache_clear()

    route = respx.post("https://api.sedo.com/api/v1/domainInsert").mock(
        return_value=httpx.Response(200, json={"domainid": "sedo-444"})
    )
    c = SedoClient()
    res = await c.list_for_sale("gadget.io", 150_000)
    assert route.called
    assert res.status == "listed"
    assert res.listing_id == "sedo-444"
    assert "partnerid=99" in res.listing_url


@pytest.mark.asyncio
@respx.mock
async def test_dan_lto_body_has_monthly(monkeypatch):
    monkeypatch.setenv("DAN_API_KEY", "dan-xyz")
    monkeypatch.setenv("AFTERMARKET_LISTINGS_ENABLED", "true")
    from pacer.config import get_settings

    get_settings.cache_clear()

    route = respx.post("https://api.dan.com/v1/domains").mock(
        return_value=httpx.Response(201, json={"id": "dan-9001"})
    )
    c = DanClient()
    res = await c.list_lease_to_own("midtier.io", 299_000, 8_300)
    body = route.calls[0].request.content.decode()
    assert '"lease_to_own_enabled":true' in body
    assert '"lease_monthly_price":83.0' in body
    assert res.status == "listed"
    assert res.listing_id == "dan-9001"


# ─────────────────────────── facade fan-out ─────────────────────────
@pytest.mark.asyncio
async def test_post_auction_listing_fans_out_to_both(monkeypatch):
    """The composite facade calls Afternic AND Sedo in parallel."""
    calls: list[tuple[str, str]] = []

    class StubAfternic:
        async def list_for_sale(self, domain, bin_price_cents):
            calls.append(("afternic", domain))
            return ListingResult(
                provider="afternic",
                domain=domain,
                listing_id="af-1",
                listing_url=f"https://www.afternic.com/domain/{domain}",
                bin_price_cents=bin_price_cents,
                status="listed",
            )

    class StubSedo:
        async def list_for_sale(self, domain, bin_price_cents):
            calls.append(("sedo", domain))
            return ListingResult(
                provider="sedo",
                domain=domain,
                listing_id="sd-1",
                listing_url=f"https://sedo.com/search/details/?domain={domain}",
                bin_price_cents=bin_price_cents,
                status="listed",
            )

    results = await post_auction_listing(
        "widget.com", 299_000, afternic=StubAfternic(), sedo=StubSedo()
    )
    assert [r.provider for r in results] == ["afternic", "sedo"]
    assert {c[0] for c in calls} == {"afternic", "sedo"}


@pytest.mark.asyncio
async def test_post_lto_listing_calls_dan_only():
    calls: list[tuple] = []

    class StubDan:
        async def list_lease_to_own(self, domain, bin_price_cents, monthly_cents):
            calls.append((domain, bin_price_cents, monthly_cents))
            return ListingResult(
                provider="dan",
                domain=domain,
                listing_id="dan-1",
                listing_url=f"https://dan.com/buy-domain/{domain}",
                bin_price_cents=bin_price_cents,
                status="listed",
            )

    res = await post_lto_listing("midtier.io", 299_000, 8_300, dan=StubDan())
    assert calls == [("midtier.io", 299_000, 8_300)]
    assert res.provider == "dan"


# ─────────────────────────── router.route_and_list wiring ──────────
@pytest.mark.asyncio
async def test_router_route_and_list_posts_auction(monkeypatch):
    """route_and_list() on an auction-tier candidate calls the listing API."""
    from pacer.models.domain_candidate import (
        DomainCandidate,
        PipelineSource,
        Status,
    )
    from pacer.monetization import afternic as afternic_mod
    from pacer.monetization.router import MonetizationRouter

    called: list[tuple[str, int]] = []

    async def fake_post_auction(domain, bin_price_cents, **_kwargs):
        called.append((domain, bin_price_cents))
        return [
            ListingResult(
                provider="afternic",
                domain=domain,
                listing_id="af-stub",
                listing_url=f"https://www.afternic.com/domain/{domain}",
                bin_price_cents=bin_price_cents,
                status="listed",
            )
        ]

    monkeypatch.setattr(afternic_mod, "post_auction_listing", fake_post_auction)

    c = DomainCandidate(
        domain="premium.com",
        company_name="Premium Co",
        source=PipelineSource.SOS_DISSOLUTION,
        llc_entity="1COMMERCE LLC",
        status=Status.CAUGHT,
        score=99.0,
        domain_rating=100.0,
        topical_relevance=100.0,
        cpc_usd=20.0,
    )
    router = MonetizationRouter()
    await router.route_and_list(c)

    assert c.monetization_strategy == "auction_bin"
    assert len(called) == 1
    assert called[0][0] == "premium.com"
