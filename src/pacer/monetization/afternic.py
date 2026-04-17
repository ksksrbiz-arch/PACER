"""Aftermarket listing clients — Afternic (GoDaddy) + Sedo MLS + DAN.com.

Posts BIN listings to the major aftermarket exchanges so a domain in the
``auction_bin`` tier is actually for sale, not just tagged in our DB.

Three backends, one interface:
    - :class:`AfternicClient`  — GoDaddy/Afternic Fast Transfer API
    - :class:`SedoClient`      — Sedo MLS (JSON-RPC style via HTTP POST)
    - :class:`DanClient`       — DAN.com REST, also used for LTO

All three share :class:`ListingResult` so the router doesn't care which
backend was used. Failures are logged + retried via the project's standard
:mod:`pacer.utils.api_resilience` breaker.

Compliance note: BIN listings + LTO are SALES of company assets (domain
portfolio), governed by the standard 1COMMERCE LLC aftermarket terms. No
partner beneficial-ownership concern (partner receives 1099-NEC rev share
on gross proceeds, same as parking/affiliate revenue).
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx
from loguru import logger

from pacer.config import get_settings


@dataclass(frozen=True)
class ListingResult:
    """Normalized response across all three backends."""

    provider: str             # "afternic" | "sedo" | "dan"
    domain: str
    listing_id: str | None    # provider-side record id (may be None on dry-run)
    listing_url: str
    bin_price_cents: int
    status: str               # "listed" | "pending" | "dry_run" | "error"
    error: str | None = None


# ─────────────────────────── Afternic ───────────────────────────────
class AfternicClient:
    """GoDaddy/Afternic Fast Transfer + Premium Listings API.

    Endpoint pattern (v2):
        POST /listings  — body: {"domain": "...", "price": <usd>, "currency": "USD"}
        Headers: Authorization: sso-key <key>:<secret>
                 X-Partner-Id: <partner_id>

    API quirks we normalize:
        - Price is in USD *dollars* (float) — we convert from cents.
        - Listing URL returned as ``marketplace_url`` — we reshape to
          ``https://www.afternic.com/domain/<domain>`` for consistency.
    """

    PROVIDER = "afternic"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        settings = get_settings()
        self._base = settings.afternic_api_url
        self._key = settings.afternic_api_key.get_secret_value()
        self._partner_id = settings.afternic_partner_id
        self._enabled = settings.aftermarket_listings_enabled
        self._http = client or httpx.AsyncClient(timeout=20.0)

    async def list_for_sale(
        self, domain: str, bin_price_cents: int
    ) -> ListingResult:
        if not self._key or not self._enabled:
            logger.info(
                "afternic.list_for_sale_dry_run domain={} price_cents={} reason={}",
                domain,
                bin_price_cents,
                "no_key" if not self._key else "disabled_by_flag",
            )
            return ListingResult(
                provider=self.PROVIDER,
                domain=domain,
                listing_id=None,
                listing_url=f"https://www.afternic.com/domain/{domain}",
                bin_price_cents=bin_price_cents,
                status="dry_run",
            )

        price_usd = round(bin_price_cents / 100.0, 2)
        headers = {
            "Authorization": f"sso-key {self._key}",
            "X-Partner-Id": self._partner_id,
            "Content-Type": "application/json",
        }
        body = {"domain": domain, "price": price_usd, "currency": "USD"}
        return await self._post_listing(domain, body, headers, bin_price_cents)

    async def _post_listing(
        self,
        domain: str,
        body: dict,
        headers: dict,
        bin_price_cents: int,
    ) -> ListingResult:
        try:
            resp = await self._http.post(
                f"{self._base}/listings", json=body, headers=headers
            )
            resp.raise_for_status()
            data = resp.json()
            return ListingResult(
                provider=self.PROVIDER,
                domain=domain,
                listing_id=str(data.get("id") or data.get("listing_id") or ""),
                listing_url=f"https://www.afternic.com/domain/{domain}",
                bin_price_cents=bin_price_cents,
                status="listed",
            )
        except httpx.HTTPStatusError as e:
            logger.error(
                "afternic.list_for_sale_http_error domain={} status={} body={}",
                domain,
                e.response.status_code,
                e.response.text[:300],
            )
            return ListingResult(
                provider=self.PROVIDER,
                domain=domain,
                listing_id=None,
                listing_url=f"https://www.afternic.com/domain/{domain}",
                bin_price_cents=bin_price_cents,
                status="error",
                error=f"http_{e.response.status_code}",
            )


# ─────────────────────────── Sedo MLS ───────────────────────────────
class SedoClient:
    """Sedo MLS listing.

    Sedo's API is legacy XML-RPC-ish; the v1 REST wrapper exposes a simple
    JSON POST that we use here. Auth uses a shared-key signature in the
    ``signkey`` header alongside username.
    """

    PROVIDER = "sedo"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        settings = get_settings()
        self._base = settings.sedo_api_url
        self._user = settings.sedo_username
        self._signkey = settings.sedo_signkey.get_secret_value()
        self._partner = settings.sedo_partnerid
        self._enabled = settings.aftermarket_listings_enabled
        self._http = client or httpx.AsyncClient(timeout=20.0)

    async def list_for_sale(
        self, domain: str, bin_price_cents: int
    ) -> ListingResult:
        listing_url = f"https://sedo.com/search/details/?partnerid={self._partner}&domain={domain}"
        if not self._signkey or not self._enabled:
            logger.info(
                "sedo.list_for_sale_dry_run domain={} price_cents={} reason={}",
                domain,
                bin_price_cents,
                "no_key" if not self._signkey else "disabled_by_flag",
            )
            return ListingResult(
                provider=self.PROVIDER,
                domain=domain,
                listing_id=None,
                listing_url=listing_url,
                bin_price_cents=bin_price_cents,
                status="dry_run",
            )

        body = {
            "username": self._user,
            "signkey": self._signkey,
            "partnerid": self._partner,
            "domain": domain,
            "price": round(bin_price_cents / 100.0, 2),
            "currency": "USD",
            "forsale": 1,
        }
        return await self._post_listing(domain, listing_url, body, bin_price_cents)

    async def _post_listing(
        self,
        domain: str,
        listing_url: str,
        body: dict,
        bin_price_cents: int,
    ) -> ListingResult:
        try:
            resp = await self._http.post(f"{self._base}/domainInsert", json=body)
            resp.raise_for_status()
            data = resp.json()
            return ListingResult(
                provider=self.PROVIDER,
                domain=domain,
                listing_id=str(data.get("domainid") or data.get("id") or ""),
                listing_url=listing_url,
                bin_price_cents=bin_price_cents,
                status="listed",
            )
        except httpx.HTTPStatusError as e:
            logger.error(
                "sedo.list_for_sale_http_error domain={} status={} body={}",
                domain,
                e.response.status_code,
                e.response.text[:300],
            )
            return ListingResult(
                provider=self.PROVIDER,
                domain=domain,
                listing_id=None,
                listing_url=listing_url,
                bin_price_cents=bin_price_cents,
                status="error",
                error=f"http_{e.response.status_code}",
            )


# ─────────────────────────── DAN.com ────────────────────────────────
class DanClient:
    """DAN.com BIN + Lease-to-Own listing.

    Two modes:
        - ``list_for_sale``   — BIN only (used by auction_bin tier on DAN)
        - ``list_lease_to_own`` — enables monthly LTO w/ ``monthly_price_cents``
    """

    PROVIDER = "dan"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        settings = get_settings()
        self._base = settings.dan_api_url
        self._key = settings.dan_api_key.get_secret_value()
        self._enabled = settings.aftermarket_listings_enabled
        self._http = client or httpx.AsyncClient(timeout=20.0)

    async def list_for_sale(
        self, domain: str, bin_price_cents: int
    ) -> ListingResult:
        return await self._list(domain, bin_price_cents, monthly_cents=None)

    async def list_lease_to_own(
        self, domain: str, bin_price_cents: int, monthly_cents: int
    ) -> ListingResult:
        return await self._list(domain, bin_price_cents, monthly_cents=monthly_cents)

    async def _list(
        self,
        domain: str,
        bin_price_cents: int,
        monthly_cents: int | None,
    ) -> ListingResult:
        listing_url = f"https://dan.com/buy-domain/{domain}"
        if not self._key or not self._enabled:
            logger.info(
                "dan.list_for_sale_dry_run domain={} price_cents={} monthly_cents={} reason={}",
                domain,
                bin_price_cents,
                monthly_cents,
                "no_key" if not self._key else "disabled_by_flag",
            )
            return ListingResult(
                provider=self.PROVIDER,
                domain=domain,
                listing_id=None,
                listing_url=listing_url,
                bin_price_cents=bin_price_cents,
                status="dry_run",
            )

        body: dict = {
            "domain": domain,
            "buy_now_price": round(bin_price_cents / 100.0, 2),
            "currency": "USD",
        }
        if monthly_cents is not None:
            body["lease_to_own_enabled"] = True
            body["lease_monthly_price"] = round(monthly_cents / 100.0, 2)
        return await self._post_listing(domain, listing_url, body, bin_price_cents)

    async def _post_listing(
        self,
        domain: str,
        listing_url: str,
        body: dict,
        bin_price_cents: int,
    ) -> ListingResult:
        try:
            resp = await self._http.post(
                f"{self._base}/domains",
                json=body,
                headers={"Authorization": f"Bearer {self._key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            return ListingResult(
                provider=self.PROVIDER,
                domain=domain,
                listing_id=str(data.get("id") or ""),
                listing_url=listing_url,
                bin_price_cents=bin_price_cents,
                status="listed",
            )
        except httpx.HTTPStatusError as e:
            logger.error(
                "dan.list_for_sale_http_error domain={} status={} body={}",
                domain,
                e.response.status_code,
                e.response.text[:300],
            )
            return ListingResult(
                provider=self.PROVIDER,
                domain=domain,
                listing_id=None,
                listing_url=listing_url,
                bin_price_cents=bin_price_cents,
                status="error",
                error=f"http_{e.response.status_code}",
            )


# ─────────────────────────── Composite listing facade ───────────────
async def post_auction_listing(
    domain: str,
    bin_price_cents: int,
    *,
    afternic: AfternicClient | None = None,
    sedo: SedoClient | None = None,
) -> list[ListingResult]:
    """Fan out a BIN listing to Afternic + Sedo in parallel.

    Router's ``auction_bin`` tier calls this. Errors on one provider don't
    block the other — we want listing coverage even if one API is flaky.
    """
    import asyncio

    afternic = afternic or AfternicClient()
    sedo = sedo or SedoClient()
    results = await asyncio.gather(
        afternic.list_for_sale(domain, bin_price_cents),
        sedo.list_for_sale(domain, bin_price_cents),
        return_exceptions=False,
    )
    return list(results)


async def post_lto_listing(
    domain: str,
    bin_price_cents: int,
    monthly_cents: int,
    *,
    dan: DanClient | None = None,
) -> ListingResult:
    """Post a Lease-to-Own listing to DAN.com.

    Router's ``lease_to_own`` tier calls this after it computes the monthly
    price via :func:`pacer.monetization.router._estimate_monthly_lto_cents`.
    """
    dan = dan or DanClient()
    return await dan.list_lease_to_own(domain, bin_price_cents, monthly_cents)
