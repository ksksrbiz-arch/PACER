"""Cloudflare auto-301 redirect client.

Automates the final hop of the redirect pipeline: once
:class:`MonetizationRouter` assigns a ``redirect_target`` to a caught
domain, this client installs a Single Redirect rule at the Cloudflare
edge so traffic hitting that domain lands on the target URL with a 301.

Cloudflare model (Rulesets API, modern replacement for Page Rules):
  PUT /zones/{zone_id}/rulesets/phases/http_request_dynamic_redirect/entrypoint
  body: {"rules": [{
    "action": "redirect",
    "action_parameters": {"from_value": {
      "status_code": 301,
      "target_url": {"value": "https://dest/path"},
      "preserve_query_string": true,
    }},
    "expression": "true",
    "description": "PACER auto-301 for <domain>",
  }]}

Gated on ``Settings.cloudflare_api_token`` — when the token is empty the
client runs in dry-run mode and returns a stub :class:`RedirectResult`
without hitting the network. That keeps CI / staging safe by default and
mirrors the same pattern used by :mod:`pacer.monetization.afternic`.

Scope for v1: zone is assumed to exist (operator points nameservers at
Cloudflare, dashboard or registrar creates the zone, we write the redirect
rule). Zone creation / DNS automation is a v2 concern — we don't want to
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


def _build_redirect_payload(domain: str, target_url: str) -> dict[str, Any]:
    """The rule body Cloudflare expects at the dynamic_redirect entrypoint."""
    return {
        "rules": [
            {
                "action": "redirect",
                "action_parameters": {
                    "from_value": {
                        "status_code": 301,
                        "target_url": {"value": target_url},
                        "preserve_query_string": True,
                    }
                },
                "expression": "true",
                "description": f"PACER auto-301 for {domain}",
                "enabled": True,
            }
        ]
    }


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

    async def set_single_redirect(
        self,
        domain: str,
        target_url: str,
        zone_id: str | None = None,
    ) -> RedirectResult:
        """Install (or overwrite) a 301 rule at the zone's redirect phase.

        PUT semantics replace the entire entrypoint ruleset, which is the
        documented way to manage a single-tenant zone rule set — if you
        need multi-rule zones, call this from a higher-level orchestrator
        that assembles all rules first.
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

        url = f"{self._base_url}/zones/{zid}/rulesets/phases/" f"{REDIRECT_PHASE}/entrypoint"
        payload = _build_redirect_payload(domain, target_url)

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.put(url, headers=_auth_headers(self._token), json=payload)
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
            "cloudflare.redirect.installed domain={} zone={} ruleset={} -> {}",
            domain,
            zid,
            ruleset_id,
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
    """Configure a 301 on Cloudflare for ``domain`` → ``target_url``.

    Reads settings each call (cheap; lru_cached). When the token is empty
    returns a dry-run :class:`RedirectResult` with ``status="dry_run"``.
    """
    settings = get_settings()
    token = settings.cloudflare_api_token.get_secret_value()
    zid = zone_id or settings.cloudflare_zone_id

    if not settings.cloudflare_api_token.get_secret_value():
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

    client = CloudflareRedirectClient(api_token=token, default_zone_id=settings.cloudflare_zone_id)
    return await client.set_single_redirect(domain, target_url, zone_id=zid)
