"""Tests for Cloudflare auto-301 client."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from pacer.config import Settings, get_settings
from pacer.monetization.cloudflare import (
    CLOUDFLARE_API_BASE,
    REDIRECT_PHASE,
    CloudflareRedirectClient,
    _build_redirect_payload,
    configure_cloudflare_redirect,
)
from pydantic import SecretStr

# ─── Payload shape ───────────────────────────────────────────────────────


def test_build_redirect_payload_shape():
    p = _build_redirect_payload("example.com", "https://1commercesolutions.com/resources")
    assert p["rules"][0]["action"] == "redirect"
    params = p["rules"][0]["action_parameters"]["from_value"]
    assert params["status_code"] == 301
    assert params["target_url"]["value"] == "https://1commercesolutions.com/resources"
    assert params["preserve_query_string"] is True
    assert p["rules"][0]["expression"] == "true"
    assert "example.com" in p["rules"][0]["description"]
    assert p["rules"][0]["enabled"] is True


# ─── Client (real HTTP via respx) ────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_client_installs_redirect_rule():
    zone = "zone-abc"
    route = respx.put(
        f"{CLOUDFLARE_API_BASE}/zones/{zone}/rulesets/phases/" f"{REDIRECT_PHASE}/entrypoint"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "errors": [],
                "messages": [],
                "result": {"id": "ruleset-xyz", "version": "1"},
            },
        )
    )

    client = CloudflareRedirectClient(api_token="tok-123", default_zone_id=zone)
    result = await client.set_single_redirect("example.com", "https://1commercesolutions.com/tools")

    assert route.called
    req = route.calls[0].request
    assert req.headers["Authorization"] == "Bearer tok-123"
    body = json.loads(req.content)
    assert (
        body["rules"][0]["action_parameters"]["from_value"]["target_url"]["value"]
        == "https://1commercesolutions.com/tools"
    )
    assert result.status == "ok"
    assert result.zone_id == zone
    assert result.ruleset_id == "ruleset-xyz"
    assert result.provider == "cloudflare"


@pytest.mark.asyncio
@respx.mock
async def test_client_returns_error_on_4xx():
    zone = "zone-abc"
    respx.put(
        f"{CLOUDFLARE_API_BASE}/zones/{zone}/rulesets/phases/" f"{REDIRECT_PHASE}/entrypoint"
    ).mock(
        return_value=httpx.Response(
            403,
            json={
                "success": False,
                "errors": [{"code": 10000, "message": "Authentication error"}],
            },
        )
    )

    client = CloudflareRedirectClient(api_token="bad-token", default_zone_id=zone)
    result = await client.set_single_redirect(
        "example.com", "https://1commercesolutions.com/resources"
    )

    assert result.status == "error"
    assert "403" in (result.error or "")
    assert result.ruleset_id is None


@pytest.mark.asyncio
async def test_client_errors_when_no_zone():
    client = CloudflareRedirectClient(api_token="tok", default_zone_id="")
    result = await client.set_single_redirect("example.com", "https://1commercesolutions.com/")
    assert result.status == "error"
    assert "zone" in (result.error or "").lower()


@pytest.mark.asyncio
@respx.mock
async def test_client_uses_explicit_zone_override():
    """Per-call zone_id wins over default."""
    respx.put(
        f"{CLOUDFLARE_API_BASE}/zones/override-zone/rulesets/phases/" f"{REDIRECT_PHASE}/entrypoint"
    ).mock(return_value=httpx.Response(200, json={"result": {"id": "rs-1"}, "success": True}))

    client = CloudflareRedirectClient(api_token="tok", default_zone_id="default-zone")
    result = await client.set_single_redirect(
        "example.com",
        "https://1commercesolutions.com/",
        zone_id="override-zone",
    )
    assert result.status == "ok"
    assert result.zone_id == "override-zone"


# ─── Facade + settings gating ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_facade_dry_run_when_no_token(monkeypatch):
    """Empty cloudflare_api_token → dry-run, no HTTP calls."""
    get_settings.cache_clear()

    def _stub():
        return Settings(
            cloudflare_api_token=SecretStr(""),
            cloudflare_zone_id="zone-abc",
        )

    monkeypatch.setattr("pacer.monetization.cloudflare.get_settings", _stub)

    with respx.mock() as router:
        # Nothing should be mocked because nothing should be called.
        result = await configure_cloudflare_redirect(
            "example.com", "https://1commercesolutions.com/resources"
        )
        assert len(router.calls) == 0

    assert result.status == "dry_run"
    assert result.zone_id == "zone-abc"
    assert result.target_url == "https://1commercesolutions.com/resources"


@pytest.mark.asyncio
@respx.mock
async def test_facade_hits_api_when_token_present(monkeypatch):
    get_settings.cache_clear()

    def _stub():
        return Settings(
            cloudflare_api_token=SecretStr("live-token"),
            cloudflare_zone_id="zone-live",
        )

    monkeypatch.setattr("pacer.monetization.cloudflare.get_settings", _stub)

    route = respx.put(
        f"{CLOUDFLARE_API_BASE}/zones/zone-live/rulesets/phases/" f"{REDIRECT_PHASE}/entrypoint"
    ).mock(return_value=httpx.Response(200, json={"result": {"id": "rs-live"}, "success": True}))

    result = await configure_cloudflare_redirect(
        "example.com", "https://1commercesolutions.com/alternatives"
    )

    assert route.called
    assert route.calls[0].request.headers["Authorization"] == "Bearer live-token"
    assert result.status == "ok"
    assert result.ruleset_id == "rs-live"


# ─── Router wiring ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_router_triggers_cloudflare_for_301_tier(monkeypatch):
    """When router assigns 301_redirect, route_and_list must call CF."""
    from pacer.models.domain_candidate import (
        DomainCandidate,
        PipelineSource,
        Status,
    )
    from pacer.monetization import cloudflare as cf_mod
    from pacer.monetization.router import MonetizationRouter

    calls: list[tuple[str, str]] = []

    async def fake_configure(domain, target, zone_id=None):
        calls.append((domain, target))
        return cf_mod.RedirectResult(
            provider="cloudflare",
            domain=domain,
            zone_id="z",
            target_url=target,
            status="ok",
            ruleset_id="rs-fake",
        )

    monkeypatch.setattr(cf_mod, "configure_cloudflare_redirect", fake_configure)

    c = DomainCandidate(
        domain="saastool.com",
        company_name="Saas Tool Co",
        source=PipelineSource.SOS_DISSOLUTION,
        llc_entity="1COMMERCE LLC",
        status=Status.CAUGHT,
        score=80.0,  # above dropcatch threshold, below auction
        domain_rating=40.0,
        topical_relevance=40.0,
        cpc_usd=2.0,
    )
    router = MonetizationRouter()
    await router.route_and_list(c)

    assert c.monetization_strategy == "301_redirect"
    assert len(calls) == 1
    assert calls[0][0] == "saastool.com"
    assert calls[0][1] == c.redirect_target


@pytest.mark.asyncio
async def test_router_skips_cloudflare_for_auction_tier(monkeypatch):
    """auction_bin tier lists — doesn't redirect — so CF must NOT be called."""
    from pacer.models.domain_candidate import (
        DomainCandidate,
        PipelineSource,
        Status,
    )
    from pacer.monetization import afternic as afternic_mod
    from pacer.monetization import cloudflare as cf_mod
    from pacer.monetization.afternic import ListingResult
    from pacer.monetization.router import MonetizationRouter

    cf_calls: list[str] = []

    async def fake_configure(domain, target, zone_id=None):
        cf_calls.append(domain)
        return cf_mod.RedirectResult(
            provider="cloudflare",
            domain=domain,
            zone_id="z",
            target_url=target,
            status="ok",
        )

    async def fake_auction(domain, bin_price_cents, **_kwargs):
        return [
            ListingResult(
                provider="afternic",
                domain=domain,
                listing_id="stub",
                listing_url=f"https://www.afternic.com/domain/{domain}",
                bin_price_cents=bin_price_cents,
                status="listed",
            )
        ]

    monkeypatch.setattr(cf_mod, "configure_cloudflare_redirect", fake_configure)
    monkeypatch.setattr(afternic_mod, "post_auction_listing", fake_auction)

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
    assert cf_calls == []  # no Cloudflare redirect for BIN listings


@pytest.mark.asyncio
@respx.mock
async def test_facade_respects_zone_override(monkeypatch):
    get_settings.cache_clear()

    def _stub():
        return Settings(
            cloudflare_api_token=SecretStr("live-token"),
            cloudflare_zone_id="zone-default",
        )

    monkeypatch.setattr("pacer.monetization.cloudflare.get_settings", _stub)

    route = respx.put(
        f"{CLOUDFLARE_API_BASE}/zones/zone-custom/rulesets/phases/" f"{REDIRECT_PHASE}/entrypoint"
    ).mock(return_value=httpx.Response(200, json={"result": {"id": "rs"}, "success": True}))

    result = await configure_cloudflare_redirect(
        "example.com",
        "https://1commercesolutions.com/",
        zone_id="zone-custom",
    )
    assert route.called
    assert result.zone_id == "zone-custom"
