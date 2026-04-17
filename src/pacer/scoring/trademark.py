"""USPTO trademark screener — detects potential UDRP / TM conflict risk
before a domain is queued for drop-catch.

Uses the USPTO Trademark Search System (TSDR / TESS public JSON endpoints).
If the API key is unset we degrade to a local-heuristic "unknown" verdict
rather than failing the pipeline. The router treats `unknown` the same as
`no conflict` but the compliance log gets a warning entry.

Design notes
------------
- Matching is brand-level: we strip the TLD, normalize punctuation, and
  compare against live/active USPTO records.
- Exact-match on a live standard-character mark is a hard stop
  (conflict=True).
- Fuzzy match + overlapping Nice class (category → class map) is a soft
  stop (conflict=True) — caller decides whether to override.
- Everything else is conflict=False.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger
from pydantic import SecretStr
from tenacity import retry, stop_after_attempt, wait_exponential

from pacer.config import get_settings

# Map our category slugs (from router._categorize) → USPTO Nice class ints
# (International Classification of Goods and Services). Overlap here is what
# escalates a fuzzy match to a conflict.
_CATEGORY_TO_NICE_CLASSES: dict[str, set[int]] = {
    "legal": {35, 36, 45},
    "finance": {35, 36},
    "tech": {9, 35, 38, 42},
    "real_estate": {36, 37},
    "healthcare": {5, 10, 44},
    "logistics": {35, 39},
    "manufacturing": {6, 7, 40},
    "retail": {35},
    "default": {35},  # advertising / business — catch-all
}

_USPTO_SEARCH_URL = "https://tsdrapi.uspto.gov/ts/cd/casestatus/search"
_USPTO_TIMEOUT = 12.0


@dataclass(frozen=True)
class TrademarkVerdict:
    conflict: bool
    reason: str  # "exact_match" | "fuzzy_class_overlap" | "clear" | "unknown"
    matches: list[dict[str, Any]]  # raw USPTO records (for compliance_log)


def _normalize_brand(domain: str) -> str:
    """Strip TLD, lowercase, drop non-alphanumerics."""
    brand = domain.split(".", 1)[0].lower()
    return re.sub(r"[^a-z0-9]", "", brand)


def _is_live(record: dict[str, Any]) -> bool:
    status = (record.get("markCurrentStatusCategory") or record.get("status") or "").lower()
    # USPTO "live" categories: registered, published, pending examination, etc.
    return status in {"live", "registered", "pending", "published"}


class USPTOTrademarkScreener:
    """Async screener hitting USPTO TSDR search.

    Usage:
        screener = USPTOTrademarkScreener()
        verdict = await screener.check("widget.com", category="tech")
        if verdict.conflict:
            candidate.status = Status.DISCARDED
    """

    def __init__(
        self,
        api_key: SecretStr | None = None,
        client: httpx.AsyncClient | None = None,
        enabled: bool | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key: SecretStr = api_key or settings.uspto_api_key
        self._client = client
        self._enabled = (
            enabled if enabled is not None else getattr(settings, "uspto_tmscreen_enabled", True)
        )

    async def check(self, domain: str, category: str = "default") -> TrademarkVerdict:
        if not self._enabled:
            return TrademarkVerdict(False, "disabled", [])

        brand = _normalize_brand(domain)
        if len(brand) < 3:
            return TrademarkVerdict(False, "too_short", [])

        try:
            records = await self._search(brand)
        except Exception as exc:
            logger.warning("uspto_tmscreen_failed domain={} err={}", domain, exc)
            return TrademarkVerdict(False, "unknown", [])

        if not records:
            return TrademarkVerdict(False, "clear", [])

        live = [r for r in records if _is_live(r)]

        # Exact match on a live mark → hard stop
        for r in live:
            mark = re.sub(r"[^a-z0-9]", "", (r.get("markIdentification") or "").lower())
            if mark == brand:
                return TrademarkVerdict(True, "exact_match", [r])

        # Fuzzy match + overlapping Nice class → soft stop
        our_classes = _CATEGORY_TO_NICE_CLASSES.get(category, _CATEGORY_TO_NICE_CLASSES["default"])
        for r in live:
            mark = re.sub(r"[^a-z0-9]", "", (r.get("markIdentification") or "").lower())
            if brand in mark or mark in brand:
                classes = {
                    int(c) for c in (r.get("internationalClassNumbers") or []) if str(c).isdigit()
                }
                if classes & our_classes:
                    return TrademarkVerdict(True, "fuzzy_class_overlap", [r])

        return TrademarkVerdict(False, "clear", live)

    async def is_conflict(self, domain: str, category: str = "default") -> bool:
        """Thin bool wrapper for router/pipeline use."""
        return (await self.check(domain, category)).conflict

    # ─── internals ───────────────────────────────────────────────────
    async def _client_ctx(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        headers = {"User-Agent": "1COMMERCE-LLC PACER trademark-screener"}
        if self._api_key.get_secret_value():
            headers["USPTO-API-KEY"] = self._api_key.get_secret_value()
        return httpx.AsyncClient(headers=headers, timeout=_USPTO_TIMEOUT)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    async def _search(self, brand: str) -> list[dict[str, Any]]:
        client = await self._client_ctx()
        owns_client = self._client is None
        try:
            resp = await client.get(
                _USPTO_SEARCH_URL,
                params={"searchText": brand, "rows": 25, "activeOnly": "true"},
            )
            resp.raise_for_status()
            data = resp.json()
        finally:
            if owns_client:
                await client.aclose()

        return data.get("results") or data.get("trademarks") or []
