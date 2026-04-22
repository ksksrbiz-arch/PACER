"""Test suite for audit logging system."""

import pytest
from datetime import datetime
from pacer.logging import AuditLogger, AuditEvent
from pacer.logging.audit import EventType


class TestAuditEvent:
    """Test cases for AuditEvent."""

    def test_event_creation(self):
        """Test creating audit event."""
        event = AuditEvent(
            event_type=EventType.TASK_STARTED,
            task_id="test_task",
            details={"config": {"retry": 3}}
        )

        assert event.event_type == EventType.TASK_STARTED
        assert event.task_id == "test_task"
        assert event.details["config"]["retry"] == 3

    def test_to_dict(self):
        """Test converting event to dictionary."""
        event = AuditEvent(
            event_type=EventType.TASK_COMPLETED,
            task_id="test_task"
        )

        event_dict = event.to_dict()

        assert event_dict["event_type"] == "task_completed"
        assert event_dict["task_id"] == "test_task"
        assert "timestamp" in event_dict

    def test_to_json(self):
        """Test converting event to JSON."""
        event = AuditEvent(
            event_type=EventType.TASK_FAILED,
            task_id="test_task"
        )

        json_str = event.to_json()

        assert isinstance(json_str, str)
        assert "task_failed" in json_str
        assert "test_task" in json_str


class TestAuditLogger:
    """Test cases for AuditLogger."""

    def test_initialization(self):
        """Test audit logger initialization."""
        logger = AuditLogger()
        assert logger.events == []

    def test_log_event(self):
        """Test logging audit event."""
        logger = AuditLogger()
        event = AuditEvent(
            event_type=EventType.TASK_STARTED,
            task_id="test_task"
        )

        logger.log_event(event)

        assert len(logger.events) == 1
        assert logger.events[0] == event

    def test_log_task_started(self):
        """Test logging task started event."""
        logger = AuditLogger()
        logger.log_task_started("task_1", config={"retry": 3})

        assert len(logger.events) == 1
        assert logger.events[0].event_type == EventType.TASK_STARTED
        assert logger.events[0].task_id == "task_1"

    def test_log_task_completed(self):
        """Test logging task completed event."""
        logger = AuditLogger()
        logger.log_task_completed("task_1", duration=5.0, attempts=2)

        assert len(logger.events) == 1
        event = logger.events[0]
        assert event.event_type == EventType.TASK_COMPLETED
        assert event.details["duration"] == 5.0
        assert event.details["attempts"] == 2

    def test_log_task_failed(self):
        """Test logging task failed event."""
        logger = AuditLogger()
        logger.log_task_failed("task_1", error="Connection timeout", attempts=3)

        assert len(logger.events) == 1
        event = logger.events[0]
        assert event.event_type == EventType.TASK_FAILED
        assert event.details["error"] == "Connection timeout"

    def test_log_task_retried(self):
        """Test logging task retry event."""
        logger = AuditLogger()
        logger.log_task_retried("task_1", attempt=2, wait_time=4.0)

        assert len(logger.events) == 1
        event = logger.events[0]
        assert event.event_type == EventType.TASK_RETRIED
        assert event.details["attempt"] == 2

    def test_get_events_by_task_id(self):
        """Test querying events by task ID."""
        logger = AuditLogger()
        logger.log_task_started("task_1")
        logger.log_task_started("task_2")
        logger.log_task_completed("task_1", duration=1.0, attempts=1)

        task_1_events = logger.get_events(task_id="task_1")

        assert len(task_1_events) == 2
        assert all(e.task_id == "task_1" for e in task_1_events)

    def test_get_events_by_type(self):
        """Test querying events by type."""
        logger = AuditLogger()
        logger.log_task_started("task_1")
        logger.log_task_completed("task_1", duration=1.0, attempts=1)
        logger.log_task_started("task_2")

        started_events = logger.get_events(event_type=EventType.TASK_STARTED)

        assert len(started_events) == 2
        assert all(e.event_type == EventType.TASK_STARTED for e in started_events)

    def test_get_events_since(self):
        """Test querying events by timestamp."""
        logger = AuditLogger()

        # Log first event
        logger.log_task_started("task_1")
        first_time = datetime.utcnow()

        # Log second event
        logger.log_task_started("task_2")

        # Query events since first_time
        recent_events = logger.get_events(since=first_time)

        assert len(recent_events) >= 1

    def test_get_event_count(self):
        """Test getting event count."""
        logger = AuditLogger()
        logger.log_task_started("task_1")
        logger.log_task_completed("task_1", duration=1.0, attempts=1)
        logger.log_task_started("task_2")

        total_count = logger.get_event_count()
        assert total_count == 3

        started_count = logger.get_event_count(event_type=EventType.TASK_STARTED)
        assert started_count == 2

    def test_clear(self):
        """Test clearing audit log."""
        logger = AuditLogger()
        logger.log_task_started("task_1")
        logger.log_task_started("task_2")

        logger.clear()

        assert len(logger.events) == 0
