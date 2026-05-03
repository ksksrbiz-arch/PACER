"""Task configuration and execution with comprehensive error handling."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

import structlog

from pacer.monitoring import HealthCheck, MetricsCollector
from pacer.reliability import CircuitBreaker, RetryPolicy
from pacer.validation import Validator

logger = structlog.get_logger(__name__)


class TaskStatus(StrEnum):
    """Task execution status."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    RETRYING = "retrying"
    CIRCUIT_OPEN = "circuit_open"


@dataclass
class TaskResult:
    """Result of task execution."""

    task_id: str
    status: TaskStatus
    result: Any | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    attempts: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> float | None:
        """Calculate execution duration in seconds."""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    @property
    def is_success(self) -> bool:
        """Check if task completed successfully."""
        return self.status == TaskStatus.SUCCESS


@dataclass
class TaskConfig:
    """Configuration for automation tasks."""

    retry_policy: RetryPolicy | None = None
    enable_circuit_breaker: bool = True
    enable_health_check: bool = True
    enable_metrics: bool = True
    enable_validation: bool = True
    timeout_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AutomationTask:
    """
    Automated task with comprehensive error handling and reliability features.

    Features:
    - Automatic retry with configurable policy
    - Circuit breaker for fault tolerance
    - Health monitoring
    - Metrics collection
    - Input/output validation
    - Complete audit logging
    """

    def __init__(
        self,
        task_id: str,
        config: TaskConfig | None = None,
        func: Callable[..., Any] | None = None,
    ):
        """
        Initialize automation task.

        Args:
            task_id: Unique identifier for the task
            config: Task configuration
            func: Function to execute
        """
        self.task_id = task_id
        self.config = config or TaskConfig()
        self.func = func
        self._circuit_breaker: CircuitBreaker | None = None
        self._health_check: HealthCheck | None = None
        self._metrics: MetricsCollector | None = None
        self._validator: Validator | None = None

        self._setup_components()

    def _setup_components(self) -> None:
        """Initialize task components based on configuration."""
        if self.config.enable_circuit_breaker:
            self._circuit_breaker = CircuitBreaker(
                name=f"{self.task_id}_circuit",
                failure_threshold=5,
                recovery_timeout=60.0,
            )

        if self.config.enable_health_check:
            self._health_check = HealthCheck(name=self.task_id)

        if self.config.enable_metrics:
            self._metrics = MetricsCollector(namespace="pacer_tasks")

        if self.config.enable_validation:
            self._validator = Validator()

    def execute(self, *args: Any, **kwargs: Any) -> TaskResult:
        """
        Execute task with full error handling and reliability features.

        Args:
            *args: Positional arguments for task function
            **kwargs: Keyword arguments for task function

        Returns:
            TaskResult with execution status and result
        """
        result = TaskResult(
            task_id=self.task_id,
            status=TaskStatus.PENDING,
            started_at=datetime.utcnow(),
        )

        logger.info(
            "task_starting",
            task_id=self.task_id,
            config=self.config.metadata,
        )

        try:
            # Check circuit breaker
            if self._circuit_breaker and not self._circuit_breaker.can_execute():
                result.status = TaskStatus.CIRCUIT_OPEN
                result.error = "Circuit breaker is open"
                logger.warning("circuit_breaker_open", task_id=self.task_id)
                return result

            # Validate inputs
            if self._validator and self.config.enable_validation:
                self._validate_inputs(args, kwargs)

            # Execute with retry policy
            result.status = TaskStatus.RUNNING
            execution_result = self._execute_with_retry(args, kwargs, result)

            # Validate outputs
            if self._validator and self.config.enable_validation:
                self._validate_output(execution_result)

            result.result = execution_result
            result.status = TaskStatus.SUCCESS
            result.completed_at = datetime.utcnow()

            # Record success
            if self._circuit_breaker:
                self._circuit_breaker.record_success()

            if self._metrics:
                self._metrics.record_success(self.task_id, result.duration or 0)

            logger.info(
                "task_completed",
                task_id=self.task_id,
                duration=result.duration,
                attempts=result.attempts,
            )

        except Exception as e:
            result.status = TaskStatus.FAILED
            result.error = str(e)
            result.completed_at = datetime.utcnow()

            # Record failure
            if self._circuit_breaker:
                self._circuit_breaker.record_failure()

            if self._metrics:
                self._metrics.record_failure(self.task_id, str(e))

            logger.error(
                "task_failed",
                task_id=self.task_id,
                error=str(e),
                duration=result.duration,
                attempts=result.attempts,
            )

        return result

    def _execute_with_retry(self, args: tuple, kwargs: dict, result: TaskResult) -> Any:
        """Execute function with retry logic."""
        if not self.func:
            raise ValueError("No function provided for execution")

        retry_policy = self.config.retry_policy or RetryPolicy()
        attempts = 0
        last_error = None

        while attempts < retry_policy.max_attempts:
            attempts += 1
            result.attempts = attempts

            try:
                if attempts > 1:
                    result.status = TaskStatus.RETRYING
                    logger.info(
                        "task_retrying",
                        task_id=self.task_id,
                        attempt=attempts,
                        max_attempts=retry_policy.max_attempts,
                    )

                return self.func(*args, **kwargs)

            except Exception as e:
                last_error = e
                if attempts < retry_policy.max_attempts:
                    wait_time = retry_policy.calculate_wait_time(attempts)
                    logger.warning(
                        "task_attempt_failed",
                        task_id=self.task_id,
                        attempt=attempts,
                        error=str(e),
                        wait_time=wait_time,
                    )
                    import time

                    time.sleep(wait_time)
                else:
                    logger.error(
                        "task_all_attempts_failed",
                        task_id=self.task_id,
                        attempts=attempts,
                        error=str(e),
                    )

        if last_error:
            raise last_error
        raise RuntimeError("Task execution failed with no error recorded")

    def _validate_inputs(self, args: tuple, kwargs: dict) -> None:
        """Validate input arguments."""
        if not self._validator:
            return

        logger.debug("validating_inputs", task_id=self.task_id)
        # Add custom validation logic here
        pass

    def _validate_output(self, output: Any) -> None:
        """Validate output result."""
        if not self._validator:
            return

        logger.debug("validating_output", task_id=self.task_id)
        # Add custom validation logic here
        pass
