"""Audit logging for complete traceability."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class EventType(StrEnum):
    """Audit event types."""

    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_RETRIED = "task_retried"
    VALIDATION_FAILED = "validation_failed"
    CIRCUIT_BREAKER_OPENED = "circuit_breaker_opened"
    CIRCUIT_BREAKER_CLOSED = "circuit_breaker_closed"
    HEALTH_CHECK_FAILED = "health_check_failed"
    SYSTEM_ERROR = "system_error"


@dataclass
class AuditEvent:
    """
    Immutable audit event record.

    Attributes:
        event_type: Type of event
        task_id: Associated task identifier
        timestamp: When event occurred
        details: Event-specific information
        user: Optional user identifier
        metadata: Additional context
    """

    event_type: EventType
    task_id: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    details: dict[str, Any] = field(default_factory=dict)
    user: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary."""
        return {
            "event_type": self.event_type.value,
            "task_id": self.task_id,
            "timestamp": self.timestamp.isoformat(),
            "details": self.details,
            "user": self.user,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        """Convert event to JSON string."""
        return json.dumps(self.to_dict())


class AuditLogger:
    """
    Audit logger for complete system traceability.

    Maintains immutable audit trail of all system operations.
    """

    def __init__(self, log_file: str | None = None):
        """
        Initialize audit logger.

        Args:
            log_file: Optional file path for audit log persistence
        """
        self.log_file = log_file
        self.events: list[AuditEvent] = []
        logger.info("audit_logger_initialized", log_file=log_file)

    def log_event(self, event: AuditEvent) -> None:
        """
        Log audit event.

        Args:
            event: AuditEvent to log
        """
        self.events.append(event)

        # Log to structured logger
        logger.info(
            "audit_event",
            event_type=event.event_type.value,
            task_id=event.task_id,
            details=event.details,
        )

        # Persist to file if configured
        if self.log_file:
            self._persist_event(event)

    def _persist_event(self, event: AuditEvent) -> None:
        """Write event to audit log file."""
        try:
            with open(self.log_file, "a") as f:
                f.write(event.to_json() + "\n")
        except Exception as e:
            logger.error("audit_log_write_failed", error=str(e))

    def log_task_started(self, task_id: str, config: dict[str, Any] | None = None) -> None:
        """Log task start event."""
        event = AuditEvent(
            event_type=EventType.TASK_STARTED,
            task_id=task_id,
            details={"config": config or {}},
        )
        self.log_event(event)

    def log_task_completed(self, task_id: str, duration: float, attempts: int) -> None:
        """Log task completion event."""
        event = AuditEvent(
            event_type=EventType.TASK_COMPLETED,
            task_id=task_id,
            details={"duration": duration, "attempts": attempts},
        )
        self.log_event(event)

    def log_task_failed(self, task_id: str, error: str, attempts: int) -> None:
        """Log task failure event."""
        event = AuditEvent(
            event_type=EventType.TASK_FAILED,
            task_id=task_id,
            details={"error": error, "attempts": attempts},
        )
        self.log_event(event)

    def log_task_retried(self, task_id: str, attempt: int, wait_time: float) -> None:
        """Log task retry event."""
        event = AuditEvent(
            event_type=EventType.TASK_RETRIED,
            task_id=task_id,
            details={"attempt": attempt, "wait_time": wait_time},
        )
        self.log_event(event)

    def get_events(
        self,
        task_id: str | None = None,
        event_type: EventType | None = None,
        since: datetime | None = None,
    ) -> list[AuditEvent]:
        """
        Query audit events with filters.

        Args:
            task_id: Filter by task ID
            event_type: Filter by event type
            since: Filter by timestamp (events after this time)

        Returns:
            List of matching AuditEvent instances
        """
        results = self.events

        if task_id:
            results = [e for e in results if e.task_id == task_id]

        if event_type:
            results = [e for e in results if e.event_type == event_type]

        if since:
            results = [e for e in results if e.timestamp >= since]

        return results

    def get_event_count(self, event_type: EventType | None = None) -> int:
        """
        Get count of audit events.

        Args:
            event_type: Optional filter by event type

        Returns:
            Number of events
        """
        if event_type:
            return len([e for e in self.events if e.event_type == event_type])
        return len(self.events)

    def clear(self) -> None:
        """Clear all audit events (use with caution)."""
        self.events.clear()
        logger.warning("audit_log_cleared")
