"""PACER PCL + CourtListener RECAP client.

PCL (Public Case Locator): authenticated search across all federal districts/bankruptcy.
RECAP archive (CourtListener): unlimited, no per-page PACER charges.

We prefer RECAP for docket fetches and fall back to PCL only when RECAP lacks the
specific docket entry we need — keeps PACER costs in the free-tier band.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from loguru import logger

from pacer.config import get_settings
from pacer.utils.api_resilience import build_client, resilient_api

settings = get_settings()


@dataclass(slots=True, frozen=True)
class BankruptcyFiling:
    case_number: str
    debtor_name: str
    filed: date
    court: str
    chapter: str
    docket_url: str
    recap_id: str | None = None
    source_payload: dict[str, Any] | None = None


class PacerClient:
    """Hybrid PACER PCL + RECAP client."""

    PCL_BASE = "https://pcl.uscourts.gov/pcl"
    RECAP_BASE = "https://www.courtlistener.com/api/rest/v4"

    def __init__(self) -> None:
        self._pcl: httpx.AsyncClient | None = None
        self._recap: httpx.AsyncClient | None = None
        self._pcl_token: str | None = None

    # ─── lifecycle ───────────────────────────────────────────────────
    async def __aenter__(self) -> "PacerClient":
        self._recap = build_client(
            base_url=self.RECAP_BASE,
            headers={
                "Authorization": f"Token {settings.courtlistener_api_token.get_secret_value()}",
                "Accept": "application/json",
            },
        )
        self._pcl = build_client(base_url=self.PCL_BASE)
        await self._pcl_login()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._recap:
            await self._recap.aclose()
        if self._pcl:
            await self._pcl.aclose()

    # ─── auth ────────────────────────────────────────────────────────
    @resilient_api(endpoint="pcl.login")
    async def _pcl_login(self) -> None:
        if not settings.pacer_username:
            logger.info("pcl_login_skipped — using RECAP only mode")
            return
        assert self._pcl is not None
        resp = await self._pcl.post(
            "/services/jaxrs/login",
            json={
                "loginId": settings.pacer_username,
                "password": settings.pacer_password.get_secret_value(),
                "clientCode": settings.pacer_client_code,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self._pcl_token = data.get("nextGenCSO") or data.get("loginToken")
        logger.info("pcl_login_ok")

    # ─── RECAP (free, preferred) ─────────────────────────────────────
    @resilient_api(endpoint="recap.search")
    async def search_recap_bankruptcies(
        self,
        *,
        since: date | None = None,
        chapters: tuple[str, ...] = ("7", "11"),
        query: str = "",
    ) -> list[BankruptcyFiling]:
        """Search RECAP for recent bankruptcy dockets.

        RECAP's search API supports the CourtListener search syntax:
        `court_id:bankr_*` narrows to bankruptcy courts.
        """
        assert self._recap is not None
        since = since or (datetime.now(UTC).date() - timedelta(days=1))
        chapter_q = " OR ".join(f"chapter:{c}" for c in chapters)
        params = {
            "type": "r",  # RECAP dockets
            "q": f"({chapter_q}) AND court_id:bankr_* {query}".strip(),
            "filed_after": since.isoformat(),
            "order_by": "dateFiled desc",
        }
        resp = await self._recap.get("/search/", params=params)
        resp.raise_for_status()
        payload = resp.json()
        results = payload.get("results", [])
        filings: list[BankruptcyFiling] = []
        for r in results:
            try:
                filings.append(
                    BankruptcyFiling(
                        case_number=r.get("docketNumber", ""),
                        debtor_name=r.get("caseName", ""),
                        filed=date.fromisoformat(r["dateFiled"][:10]),
                        court=r.get("court_id", ""),
                        chapter=str(r.get("chapter", "")),
                        docket_url=f"https://www.courtlistener.com{r.get('absolute_url', '')}",
                        recap_id=str(r.get("id", "")),
                        source_payload=r,
                    )
                )
            except Exception:
                logger.warning("recap_parse_failed record={}", r.get("id"))
                continue
        logger.info("recap_search found={} since={}", len(filings), since)
        return filings

    # ─── PCL (authoritative fallback) ────────────────────────────────
    @resilient_api(endpoint="pcl.search")
    async def pcl_find_case(self, case_number: str, court: str) -> dict[str, Any] | None:
        if not self._pcl_token:
            return None
        assert self._pcl is not None
        resp = await self._pcl.post(
            "/services/jaxrs/cases/find",
            headers={"X-NEXT-GEN-CSO": self._pcl_token},
            json={"caseNumberFull": case_number, "courtId": court},
        )
        resp.raise_for_status()
        return resp.json()
