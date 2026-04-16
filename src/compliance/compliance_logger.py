"""
Compliance logger — persists audit events to the compliance_logs Postgres table.

All events are tagged with LLC_ENTITY for:
  - Oregon DFR exemption opinion letter
  - Koinly / 8949 tax export
  - Canby business license renewal
"""

from loguru import logger

from src.config import Config


class ComplianceLogger:
    """
    Structured compliance logger for 1COMMERCE LLC.

    In a full deployment this writes to Postgres via SQLAlchemy async session.
    The log_compliance method in APIResilience also calls this indirectly.
    """

    async def log(self, event: str, details: dict, source: str = "PACER") -> None:
        """
        Persist a compliance event.

        Logs to the application logger immediately. Database persistence
        should be wired in by passing an async session from the caller.
        """
        entry = {
            "llc_entity": Config.LLC_ENTITY,
            "event": event,
            "source": source,
            "details": details,
        }
        logger.info(f"COMPLIANCE | {entry}")
        # To persist to Postgres, inject an AsyncSession and call:
        # session.add(ComplianceLog(**entry))
        # await session.commit()
