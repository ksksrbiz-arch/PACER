"""Shared pipeline utilities."""
from __future__ import annotations

from loguru import logger
from sqlalchemy.dialects.postgresql import insert as pg_insert

from pacer.db import session_scope
from pacer.models.domain_candidate import DomainCandidate


async def upsert_candidates(candidates: list[DomainCandidate]) -> list[DomainCandidate]:
    """Insert or no-op on (domain) unique constraint. Returns persisted rows."""
    if not candidates:
        return []

    async with session_scope() as sess:
        values = [
            {
                "domain": c.domain,
                "company_name": c.company_name,
                "source": c.source,
                "source_record_id": c.source_record_id,
                "source_payload": c.source_payload,
                "status": c.status,
                "llc_entity": c.llc_entity or "1COMMERCE LLC",
            }
            for c in candidates
        ]
        stmt = pg_insert(DomainCandidate).values(values)
        stmt = stmt.on_conflict_do_nothing(index_elements=[DomainCandidate.domain])
        await sess.execute(stmt)

    logger.info("upserted candidates={}", len(candidates))
    return candidates
