"""
PACER PCL + CourtListener RECAP client with production-grade error handling.

Daily flow:
  1. PCL API → filter Chapter 7/11 + yesterday's date + tech keywords
  2. RECAP free supplement → richer docket keyword search
  3. Merge, deduplicate by debtor name
  4. Return list[DomainCandidate] for downstream enrichment → scoring → drop-catch
"""

from datetime import datetime, timedelta, timezone

import httpx
from loguru import logger

from src.config import Config
from src.models.domain import DomainCandidate
from src.utils.api_resilience import APIResilience


class PACERClient:
    PCL_BASE = "https://pcl.uscourts.gov/pcl-public-api/rest/cases/find"
    RECAP_BASE = "https://www.courtlistener.com/api/rest/v4/recap/"

    # Tech keywords used in PCL natureOfSuit filter
    TECH_KEYWORDS = "software OR saas OR technology OR internet OR platform OR subscription"

    @APIResilience.resilient_api_call(max_attempts=5)
    async def _call_pcl(self, params: dict) -> list[dict]:
        """Hit the PACER PCL REST API with auth and return raw case list."""
        async with httpx.AsyncClient(
            auth=(Config.PACER_USERNAME, Config.PACER_PASSWORD),
            timeout=30,
        ) as client:
            resp = await client.get(self.PCL_BASE, params=params)
            resp.raise_for_status()
            return resp.json().get("results", [])

    @APIResilience.resilient_api_call(max_attempts=5)
    async def _call_recap(self, query: str) -> list[dict]:
        """Hit the CourtListener RECAP API (free, no key required for basic use)."""
        headers = {}
        if Config.COURTLISTENER_API_KEY:
            headers["Authorization"] = f"Token {Config.COURTLISTENER_API_KEY}"

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                self.RECAP_BASE,
                params={"q": query, "format": "json"},
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json().get("results", [])

    async def fetch_yesterday_bankruptcies(self) -> list[DomainCandidate]:
        """
        Main entry point — returns de-duplicated DomainCandidate list for yesterday.

        Falls back gracefully: PCL → RECAP → empty list (pipeline never hard-fails).
        """
        yesterday = (datetime.now(tz=timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

        # Phase 1a: PCL API (official, filtered, authenticated)
        pcl_params = {
            "federalBankruptcyChapter": "7,11",
            "dateFiledFrom": yesterday,
            "dateFiledTo": yesterday,
            "natureOfSuit": self.TECH_KEYWORDS,
            "pageSize": 100,
        }
        try:
            pcl_cases = await self._call_pcl(pcl_params)
        except Exception as exc:
            logger.error(f"PCL API fully failed: {exc} — using RECAP only")
            pcl_cases = []
            await APIResilience.log_compliance(
                "pacer_pcl_failure", {"date": yesterday, "error": str(exc)}
            )

        # Phase 1b: RECAP free supplement (always run — richer keyword search)
        recap_query = (
            f"federalBankruptcyChapter:(7 OR 11) AND dateFiled:{yesterday} "
            f"AND (software OR saas OR platform)"
        )
        try:
            recap_cases = await self._call_recap(recap_query)
        except Exception as exc:
            logger.error(f"RECAP fallback also failed: {exc}")
            recap_cases = []
            await APIResilience.log_compliance(
                "recap_failure", {"date": yesterday, "error": str(exc)}
            )

        # Merge + deduplicate by debtor name (case-insensitive)
        pcl_case_ids = {id(c) for c in pcl_cases}
        all_cases = pcl_cases + recap_cases
        candidates: list[DomainCandidate] = []
        seen: set[str] = set()

        for case in all_cases:
            debtor = case.get("debtorName") or case.get("partyName") or ""
            debtor = debtor.strip()
            if not debtor or debtor.lower() in seen:
                continue
            seen.add(debtor.lower())
            candidates.append(
                DomainCandidate(
                    company_name=debtor,
                    case_id=case.get("caseNumber"),
                    filing_date=yesterday,
                    source="pacer_pcl" if id(case) in pcl_case_ids else "recap",
                )
            )

        logger.info(
            f"✅ PACER pipeline complete: {len(candidates)} candidates "
            f"| PCL: {len(pcl_cases)} | RECAP: {len(recap_cases)}"
        )
        await APIResilience.log_compliance(
            "pacer_daily_run",
            {
                "date": yesterday,
                "candidates": len(candidates),
                "pcl_count": len(pcl_cases),
                "recap_count": len(recap_cases),
                "entity": Config.LLC_ENTITY,
            },
        )
        return candidates

    async def fetch_by_date_range(self, date_from: str, date_to: str) -> list[DomainCandidate]:
        """Fetch bankruptcies for an arbitrary date range (useful for backfill)."""
        pcl_params = {
            "federalBankruptcyChapter": "7,11",
            "dateFiledFrom": date_from,
            "dateFiledTo": date_to,
            "natureOfSuit": self.TECH_KEYWORDS,
            "pageSize": 100,
        }
        try:
            pcl_cases = await self._call_pcl(pcl_params)
        except Exception as exc:
            logger.error(f"PCL range fetch failed: {exc}")
            pcl_cases = []

        candidates = []
        seen: set[str] = set()
        for case in pcl_cases:
            debtor = (case.get("debtorName") or case.get("partyName") or "").strip()
            if not debtor or debtor.lower() in seen:
                continue
            seen.add(debtor.lower())
            candidates.append(
                DomainCandidate(
                    company_name=debtor,
                    case_id=case.get("caseNumber"),
                    filing_date=case.get("dateFiled", date_from),
                    source="pacer_pcl",
                )
            )
        logger.info(f"Range fetch {date_from}→{date_to}: {len(candidates)} candidates")
        return candidates
