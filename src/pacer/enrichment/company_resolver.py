"""Resolve company name → canonical domain.

Cascade: Clearbit Autocomplete → Hunter domain-search → Apollo → Crunchbase.
Returns None if none of the providers yield a confident match.
"""
from __future__ import annotations

import re
from typing import Any

import tldextract
from loguru import logger

from pacer.config import get_settings
from pacer.utils.api_resilience import build_client, resilient_api

settings = get_settings()

_NAME_CLEAN_RE = re.compile(r"\b(inc|llc|corp|corporation|ltd|limited|co|company)\.?\b", re.I)


def _normalize(name: str) -> str:
    name = _NAME_CLEAN_RE.sub("", name).strip(" ,.;")
    return " ".join(name.split())


def _valid_domain(domain: str | None) -> str | None:
    if not domain:
        return None
    parsed = tldextract.extract(domain)
    if not parsed.domain or not parsed.suffix:
        return None
    return f"{parsed.domain}.{parsed.suffix}".lower()


@resilient_api(endpoint="clearbit.autocomplete")
async def _via_clearbit(name: str) -> str | None:
    if not settings.clearbit_api_key.get_secret_value():
        return None
    async with build_client(base_url="https://autocomplete.clearbit.com") as c:
        resp = await c.get("/v1/companies/suggest", params={"query": name})
        resp.raise_for_status()
        data = resp.json()
    if data:
        return _valid_domain(data[0].get("domain"))
    return None


@resilient_api(endpoint="hunter.domain_search")
async def _via_hunter(name: str) -> str | None:
    key = settings.hunter_api_key.get_secret_value()
    if not key:
        return None
    async with build_client(base_url="https://api.hunter.io") as c:
        resp = await c.get(
            "/v2/domain-search",
            params={"company": name, "api_key": key, "limit": 1},
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
    return _valid_domain(data.get("domain"))


@resilient_api(endpoint="apollo.organizations_search")
async def _via_apollo(name: str) -> str | None:
    key = settings.apollo_api_key.get_secret_value()
    if not key:
        return None
    async with build_client(
        base_url="https://api.apollo.io",
        headers={"X-Api-Key": key, "Content-Type": "application/json"},
    ) as c:
        resp = await c.post(
            "/v1/mixed_companies/search",
            json={"q_organization_name": name, "per_page": 1},
        )
        resp.raise_for_status()
        orgs = resp.json().get("organizations") or []
    if orgs:
        return _valid_domain(orgs[0].get("primary_domain"))
    return None


@resilient_api(endpoint="crunchbase.search")
async def _via_crunchbase(name: str) -> str | None:
    key = settings.crunchbase_api_key.get_secret_value()
    if not key:
        return None
    async with build_client(
        base_url="https://api.crunchbase.com",
        headers={"X-cb-user-key": key},
    ) as c:
        resp = await c.get(
            "/api/v4/searches/organizations",
            params={"query": name, "limit": 1},
        )
        resp.raise_for_status()
        data = resp.json().get("entities") or []
    if data:
        props = data[0].get("properties", {})
        return _valid_domain(props.get("website_url") or props.get("domain"))
    return None


async def resolve_domain(company_name: str) -> str | None:
    """Best-effort: cascade through providers until one returns a domain."""
    name = _normalize(company_name)
    if not name:
        return None

    for provider in (_via_clearbit, _via_hunter, _via_apollo, _via_crunchbase):
        try:
            domain = await provider(name)
        except Exception as exc:
            logger.debug("resolver_failed provider={} name={} err={}", provider.__name__, name, exc)
            continue
        if domain:
            logger.debug("resolver_hit provider={} name={} domain={}", provider.__name__, name, domain)
            return domain

    return None
