"""Tests for Cloudflare auto-301 client."""
from __future__ import annotations

import json

import httpx
import pytest
import respx
from pydantic import SecretStr

from pacer.config import Settings, get_settings
from pacer.monetization.cloudflare import (
    CLOUDFLARE_API_BASE,
    REDIRECT_PHASE,
    CloudflareRedirectClient,
    _build_redirect_payload,
    _build_redirect_rule,
    _hostname_expression,
    _merge_rules,
    _rule_description,
    configure_cloudflare_redirect,
)


def _entrypoint_url(zone: str) -> str:
    return (
        f"{CLOUDFLARE_API_BASE}/zones/{zone}/rulesets/phases/"
        f"{REDIRECT_PHASE}/entrypoint"
    )


def _ruleset_response(rules: list[dict], ruleset_id: str = "rs-abc") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "success": True,
            "errors": [],
            "messages": [],
            "result": {"id": ruleset_id, "rules": rules, "version": "1"},
        },
    )


# ─── Unit helpers ─────────────────────────────────────────────────────────


def test_hostname_expression_scopes_to_apex_and_www():
    expr = _hostname_expression("example.com")
    assert expr == 'http.host eq "example.com" or http.host eq "www.example.com"'


def test_build_redirect_rule_shape():
    r = _build_redirect_rule(
        "example.com", "https://1commercesolutions.com/resources"
    )
    assert r["action"] == "redirect"
    params = r["action_parameters"]["from_value"]
    assert params["status_code"] == 301
    assert params["target_url"]["value"] == "https://1commercesolutions.com/resources"
    assert params["preserve_query_string"] is True
    # Hostname-scoped, never "true" (prevents all-traffic redirect bug)
    assert r["expression"] != "true"
    assert '"example.com"' in r["expression"]
    assert r["description"] == "PACER auto-301 for example.com"
    assert r["enabled"] is True


def test_build_redirect_payload_backward_compat_shape():
    """Legacy single-rule shape still wraps into {'rules': [rule]}."""
    p = _build_redirect_payload("example.com", "https://1commercesolutions.com/")
    assert list(p.keys()) == ["rules"]
    assert len(p["rules"]) == 1
    assert p["rules"][0]["description"] == _rule_description("example.com")


def test_merge_rules_preserves_other_domains():
    """A new catch must not wipe out rules for other domains in the zone."""
    existing = [
        _build_redirect_rule("first.com", "https://hub.example/first"),
        _build_redirect_rule("second.com", "https://hub.example/second"),
    ]
    merged = _merge_rules(existing, "third.com", "https://hub.example/third")
    descriptions = [r["description"] for r in merged]
    assert "PACER auto-301 for first.com" in descriptions
    assert "PACER auto-301 for second.com" in descriptions
    assert "PACER auto-301 for third.com" in descriptions
    assert len(merged) == 3


def test_merge_rules_deduplicates_same_domain():
    """Re-catching a domain replaces its prior rule in-place (no dupes)."""
    existing = [
        _build_redirect_rule("first.com", "https://old-target/first"),
        _build_redirect_rule("second.com", "https://hub.example/second"),
    ]
    merged = _merge_rules(existing, "first.com", "https://new-target/first")
    first_rules = [r for r in merged if r["description"] == "PACER auto-301 for first.com"]
    assert len(first_rules) == 1
    assert (
        first_rules[0]["action_parameters"]["from_value"]["target_url"]["value"]
        == "https://new-target/first"
    )
    assert len(merged) == 2  # not 3


def test_merge_rules_preserves_operator_configured_rules():
    """Rules without PACER's description prefix must pass through untouched."""
    operator_rule = {
        "action": "redirect",
        "expression": 'http.host eq "manual.com"',
        "description": "operator-configured vanity redirect",
        "enabled": True,
    }
    merged = _merge_rules([operator_rule], "example.com", "https://hub.example/")
    assert operator_rule in merged
    assert len(merged) == 2


# ─── Client (real HTTP via respx) ─────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_client_installs_first_rule_when_zone_empty():
    """Fresh zone (no existing ruleset -> 404) installs a single rule."""
    zone = "zone-abc"
    respx.get(_entrypoint_url(zone)).mock(return_value=httpx.Response(404))
    put_route = respx.put(_entrypoint_url(zone)).mock(
        return_value=_ruleset_response([], ruleset_id="ruleset-xyz")
    )

    client = CloudflareRedirectClient(api_token="tok-123", default_zone_id=zone)
    result = await client.set_single_redirect(
        "example.com", "https://1commercesolutions.com/tools"
    )

    assert put_route.called
    req = put_route.calls[0].request
    assert req.headers["Authorization"] == "Bearer tok-123"
    body = json.loads(req.content)
    assert len(body["rules"]) == 1
    assert '"example.com"' in body["rules"][0]["expression"]
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
async def test_client_appends_to_existing_ruleset_without_wiping_others():
    """Second domain catch must PUT both rules, not just the new one."""
    zone = "zone-multi"
    existing_rule = _build_redirect_rule("first.com", "https://hub/first")
    respx.get(_entrypoint_url(zone)).mock(
        return_value=_ruleset_response([existing_rule])
    )
    put_route = respx.put(_entrypoint_url(zone)).mock(
        return_value=_ruleset_response(
            [existing_rule, _build_redirect_rule("second.com", "https://hub/second")]
        )
    )

    client = CloudflareRedirectClient(api_token="tok", default_zone_id=zone)
    result = await client.set_single_redirect("second.com", "https://hub/second")

    assert put_route.called
    body = json.loads(put_route.calls[0].request.content)
    descriptions = [r["description"] for r in body["rules"]]
    assert "PACER auto-301 for first.com" in descriptions
    assert "PACER auto-301 for second.com" in descriptions
    assert len(body["rules"]) == 2
    assert result.status == "ok"


@pytest.mark.asyncio
@respx.mock
async def test_client_replaces_same_domain_rule_on_rerun():
    """Re-catching the same domain replaces the existing rule in-place."""
    zone = "zone-rerun"
    old_rule = _build_redirect_rule("example.com", "https://old-target/")
    respx.get(_entrypoint_url(zone)).mock(return_value=_ruleset_response([old_rule]))
    put_route = respx.put(_entrypoint_url(zone)).mock(
        return_value=_ruleset_response([])
    )

    client = CloudflareRedirectClient(api_token="tok", default_zone_id=zone)
    await client.set_single_redirect("example.com", "https://new-target/")

    body = json.loads(put_route.calls[0].request.content)
    assert len(body["rules"]) == 1
    assert (
        body["rules"][0]["action_parameters"]["from_value"]["target_url"]["value"]
        == "https://new-target/"
    )


@pytest.mark.asyncio
@respx.mock
async def test_client_returns_error_on_put_4xx():
    zone = "zone-abc"
    respx.get(_entrypoint_url(zone)).mock(return_value=httpx.Response(404))
    respx.put(_entrypoint_url(zone)).mock(
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
@respx.mock
async def test_client_returns_error_on_get_5xx():
    """GET failure aborts the install so we don't PUT with stale data."""
    zone = "zone-abc"
    respx.get(_entrypoint_url(zone)).mock(
        return_value=httpx.Response(500, json={"success": False})
    )
    put_route = respx.put(_entrypoint_url(zone))

    client = CloudflareRedirectClient(api_token="tok", default_zone_id=zone)
    result = await client.set_single_redirect("example.com", "https://hub/")

    assert result.status == "error"
    assert "get failed" in (result.error or "").lower()
    assert not put_route.called


@pytest.mark.asyncio
async def test_client_errors_when_no_zone():
    client = CloudflareRedirectClient(api_token="tok", default_zone_id="")
    result = await client.set_single_redirect(
        "example.com", "https://1commercesolutions.com/"
    )
    assert result.status == "error"
    assert "zone" in (result.error or "").lower()


@pytest.mark.asyncio
@respx.mock
async def test_client_uses_explicit_zone_override():
    """Per-call zone_id wins over default."""
    respx.get(_entrypoint_url("override-zone")).mock(
        return_value=httpx.Response(404)
    )
    respx.put(_entrypoint_url("override-zone")).mock(
        return_value=_ruleset_response([], ruleset_id="rs-1")
    )

    client = CloudflareRedirectClient(api_token="tok", default_zone_id="default-zone")
    result = await client.set_single_redirect(
        "example.com",
        "https://1commercesolutions.com/",
        zone_id="override-zone",
    )
    assert result.status == "ok"
    assert result.zone_id == "override-zone"


# ─── Facade + settings gating ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_facade_dry_run_when_no_token(monkeypatch):
    """Empty cloudflare_api_token -> dry-run, no HTTP calls."""
    get_settings.cache_clear()

    def _stub():
        return Settings(
            cloudflare_api_token=SecretStr(""),
            cloudflare_zone_id="zone-abc",
        )

    monkeypatch.setattr("pacer.monetization.cloudflare.get_settings", _stub)

    with respx.mock() as router:
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

    respx.get(_entrypoint_url("zone-live")).mock(return_value=httpx.Response(404))
    put_route = respx.put(_entrypoint_url("zone-live")).mock(
        return_value=_ruleset_response([], ruleset_id="rs-live")
    )

    result = await configure_cloudflare_redirect(
        "example.com", "https://1commercesolutions.com/alternatives"
    )

    assert put_route.called
    assert put_route.calls[0].request.headers["Authorization"] == "Bearer live-token"
    assert result.status == "ok"
    assert result.ruleset_id == "rs-live"


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

    respx.get(_entrypoint_url("zone-custom")).mock(return_value=httpx.Response(404))
    put_route = respx.put(_entrypoint_url("zone-custom")).mock(
        return_value=_ruleset_response([], ruleset_id="rs")
    )

    result = await configure_cloudflare_redirect(
        "example.com",
        "https://1commercesolutions.com/",
        zone_id="zone-custom",
    )
    assert put_route.called
    assert result.zone_id == "zone-custom"


# ─── Router wiring ────────────────────────────────────────────────────────


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
    """auction_bin tier lists -- doesn't redirect -- so CF must NOT be called."""
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
