"""Cloudflare auto-301 redirect client.

Automates the final hop of the redirect pipeline: once
:class:`MonetizationRouter` assigns a ``redirect_target`` to a caught
domain, this client installs a Single Redirect rule at the Cloudflare
edge so traffic hitting that domain lands on the target URL with a 301.

Cloudflare model (Rulesets API, modern replacement for Page Rules):
  GET /zones/{zone_id}/rulesets/phases/http_request_dynamic_redirect/entrypoint
  PUT /zones/{zone_id}/rulesets/phases/http_request_dynamic_redirect/entrypoint
  body: {"rules": [... all rules in the zone ...]}

Important: PUT replaces the ENTIRE entrypoint ruleset. If we naively
overwrote with a single rule every time we caught a new domain, the
previous catch's rule would be wiped out. So the flow is:
  1. GET the existing ruleset (tolerate 404 -> empty rules list)
  2. Drop any rule tagged with our description for THIS domain (dedupe)
  3. Append the new rule scoped by hostname expression
  4. PUT the merged ruleset back

Each rule uses a hostname-scoped expression:
  http.host eq "foo.com" or http.host eq "www.foo.com"
so multiple catches in one zone route correctly. The ``default_zone_id``
setting continues to work for multi-tenant zones; callers can still pass
``zone_id`` per-call to target a dedicated zone per domain if preferred.

Gated on ``Settings.cloudflare_api_token`` -- when the token is empty the
client runs in dry-run mode and returns a stub :class:`RedirectResult`
without hitting the network. That keeps CI / staging safe by default and
mirrors the same pattern used by :mod:`pacer.monetization.afternic`.

Scope for v1: zone is assumed to exist (operator points nameservers at
Cloudflare, dashboard or registrar creates the zone, we write the redirect
rule). Zone creation / DNS automation is a v2 concern -- we don't want to
accidentally spin up unpaid zones during a dry-run.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger

from pacer.config import get_settings

CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"
REDIRECT_PHASE = "http_request_dynamic_redirect"


@dataclass(frozen=True)
class RedirectResult:
    """Outcome of a single redirect-rule write."""

    provider: str  # always "cloudflare" for now
    domain: str
    zone_id: str
    target_url: str
    status: str  # "ok" | "dry_run" | "error"
    ruleset_id: str | None = None
    error: str | None = None


def _auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _rule_description(domain: str) -> str:
    """Stable tag used to dedupe this domain's rule on rerun."""
    return f"PACER auto-301 for {domain}"


def _hostname_expression(domain: str) -> str:
    """Match the naked domain and its www. subdomain at the CF edge."""
    return f'http.host eq "{domain}" or http.host eq "www.{domain}"'


def _build_redirect_rule(domain: str, target_url: str) -> dict[str, Any]:
    """The single rule body Cloudflare expects inside a ruleset's rules list."""
    return {
        "action": "redirect",
        "action_parameters": {
            "from_value": {
                "status_code": 301,
                "target_url": {"value": target_url},
                "preserve_query_string": True,
            }
        },
        "expression": _hostname_expression(domain),
        "description": _rule_description(domain),
        "enabled": True,
    }


def _build_redirect_payload(domain: str, target_url: str) -> dict[str, Any]:
    """Backward-compat wrapper -- payload for a ruleset containing just this rule.

    Retained so callers / tests that only care about the single-rule shape
    don't have to know about the GET-merge-PUT dance. For actual installs
    use :meth:`CloudflareRedirectClient.set_single_redirect` which merges
    into any existing ruleset first.
    """
    return {"rules": [_build_redirect_rule(domain, target_url)]}


def _merge_rules(
    existing: list[dict[str, Any]], domain: str, target_url: str
) -> list[dict[str, Any]]:
    """Drop any prior PACER rule for ``domain`` and append the fresh one.

    Preserves every other rule in the ruleset (including rules for other
    PACER-caught domains and rules the operator configured by hand).
    Dedupe key is the description string -- rules for other domains have a
    different description so they survive untouched.
    """
    dedupe_desc = _rule_description(domain)
    preserved = [r for r in existing if r.get("description") != dedupe_desc]
    preserved.append(_build_redirect_rule(domain, target_url))
    return preserved


class CloudflareRedirectClient:
    """Thin async httpx wrapper around the Cloudflare Rulesets API.

    Scope is narrow on purpose: one method to install a 301 redirect for
    an existing zone. Zone provisioning lives elsewhere.
    """

    def __init__(
        self,
        api_token: str,
        default_zone_id: str = "",
        base_url: str = CLOUDFLARE_API_BASE,
        timeout: float = 15.0,
    ) -> None:
        self._token = api_token
        self._default_zone_id = default_zone_id
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _entrypoint_url(self, zone_id: str) -> str:
        return (
            f"{self._base_url}/zones/{zone_id}/rulesets/phases/"
            f"{REDIRECT_PHASE}/entrypoint"
        )

    async def _fetch_existing_rules(
        self, client: httpx.AsyncClient, zone_id: str
    ) -> list[dict[str, Any]]:
        """GET the current entrypoint ruleset. Missing ruleset -> empty list.

        404 / "ruleset not found" is the normal first-time state for a zone
        that has never had a dynamic_redirect rule. Any other 4xx/5xx is
        treated as an error and propagated.
        """
        resp = await client.get(
            self._entrypoint_url(zone_id), headers=_auth_headers(self._token)
        )
        if resp.status_code == 404:
            return []
        if resp.status_code >= 400:
            resp.raise_for_status()
        result = (resp.json() or {}).get("result") or {}
        rules = result.get("rules") or []
        return list(rules)

    async def set_single_redirect(
        self,
        domain: str,
        target_url: str,
        zone_id: str | None = None,
    ) -> RedirectResult:
        """Install (or replace) a 301 rule for ``domain`` in the given zone.

        Preserves all other rules in the zone. If the zone already has a
        rule tagged for ``domain`` (same description), it's replaced; all
        other rules are left untouched.
        """
        zid = zone_id or self._default_zone_id
        if not zid:
            return RedirectResult(
                provider="cloudflare",
                domain=domain,
                zone_id="",
                target_url=target_url,
                status="error",
                error="no zone_id provided and no default_zone_id configured",
            )

        url = self._entrypoint_url(zid)

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            # 1. GET existing rules (tolerate 404)
            try:
                existing = await self._fetch_existing_rules(client, zid)
            except httpx.HTTPError as exc:
                logger.warning(
                    "cloudflare.redirect.get_error domain={} zone={} err={}",
                    domain,
                    zid,
                    exc,
                )
                return RedirectResult(
                    provider="cloudflare",
                    domain=domain,
                    zone_id=zid,
                    target_url=target_url,
                    status="error",
                    error=f"get failed: {exc}",
                )

            # 2. Merge ours in, dropping any prior rule for this domain
            merged = _merge_rules(existing, domain, target_url)
            payload = {"rules": merged}

            # 3. PUT the merged ruleset back
            try:
                resp = await client.put(
                    url, headers=_auth_headers(self._token), json=payload
                )
            except httpx.HTTPError as exc:
                logger.warning(
                    "cloudflare.redirect.http_error domain={} zone={} err={}",
                    domain,
                    zid,
                    exc,
                )
                return RedirectResult(
                    provider="cloudflare",
                    domain=domain,
                    zone_id=zid,
                    target_url=target_url,
                    status="error",
                    error=str(exc),
                )

        if resp.status_code >= 400:
            logger.warning(
                "cloudflare.redirect.api_error domain={} zone={} status={} body={}",
                domain,
                zid,
                resp.status_code,
                resp.text[:500],
            )
            return RedirectResult(
                provider="cloudflare",
                domain=domain,
                zone_id=zid,
                target_url=target_url,
                status="error",
                error=f"HTTP {resp.status_code}: {resp.text[:200]}",
            )

        data = resp.json()
        ruleset_id = (data.get("result") or {}).get("id")
        logger.info(
            "cloudflare.redirect.installed domain={} zone={} ruleset={} "
            "rules_in_zone={} -> {}",
            domain,
            zid,
            ruleset_id,
            len(merged),
            target_url,
        )
        return RedirectResult(
            provider="cloudflare",
            domain=domain,
            zone_id=zid,
            target_url=target_url,
            status="ok",
            ruleset_id=ruleset_id,
        )


# ─── Facade ──────────────────────────────────────────────────────────────


async def configure_cloudflare_redirect(
    domain: str, target_url: str, zone_id: str | None = None
) -> RedirectResult:
    """Configure a 301 on Cloudflare for ``domain`` -> ``target_url``.

    Reads settings each call (cheap; lru_cached). When the token is empty
    returns a dry-run :class:`RedirectResult` with ``status="dry_run"``.
    """
    settings = get_settings()
    token = settings.cloudflare_api_token.get_secret_value()
    zid = zone_id or settings.cloudflare_zone_id

    if not token:
        logger.info(
            "cloudflare.redirect.dry_run domain={} zone={} -> {} (no api token)",
            domain,
            zid,
            target_url,
        )
        return RedirectResult(
            provider="cloudflare",
            domain=domain,
            zone_id=zid,
            target_url=target_url,
            status="dry_run",
        )

    client = CloudflareRedirectClient(
        api_token=token, default_zone_id=settings.cloudflare_zone_id
    )
    return await client.set_single_redirect(domain, target_url, zone_id=zid)
