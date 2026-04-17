"""Parallel multi-registrar backorder orchestrator.

Strategy: place backorders at ALL configured registrars simultaneously. Each
registrar has a different catch algorithm — the more parallel bids, the higher
the effective catch rate. Whichever one wins, we track via WHOIS polling.
"""

from __future__ import annotations

import asyncio

from loguru import logger

from pacer.compliance.audit import record_event
from pacer.dropcatch import dropcatch_com, dynadot, godaddy, namejet
from pacer.models.domain_candidate import DomainCandidate, Status

_REGISTRARS = (
    ("dynadot", dynadot.place_backorder),
    ("dropcatch", dropcatch_com.place_backorder),
    ("namejet", namejet.place_backorder),
    ("godaddy", godaddy.place_backorder),
)


async def submit_backorders(candidate: DomainCandidate) -> DomainCandidate:
    """Fan out to all registrars; survive individual failures."""
    tasks = {name: fn(candidate.domain) for name, fn in _REGISTRARS}
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    successes: list[str] = []
    for (name, _), result in zip(tasks.items(), results, strict=True):
        if isinstance(result, Exception):
            logger.warning(
                "backorder_failed registrar={} domain={} err={}", name, candidate.domain, result
            )
            continue
        if isinstance(result, dict) and result.get("ok"):
            successes.append(name)

    await record_event(
        event_type="dropcatch_submitted",
        endpoint="dropcatch.orchestrator",
        domain=candidate.domain,
        message=f"registrars={','.join(successes) or 'none'}",
        payload={"registrars": successes},
    )

    if successes:
        candidate.status = Status.QUEUED_DROPCATCH
    else:
        logger.error("no_registrar_accepted domain={}", candidate.domain)
        candidate.status = Status.FAILED

    return candidate
