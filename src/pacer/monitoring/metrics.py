"""Metrics collection using Prometheus client."""

import structlog
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

logger = structlog.get_logger(__name__)


class MetricsCollector:
    """
    Collect and expose metrics for monitoring.

    Uses Prometheus client library for metrics collection.

    Attributes:
        namespace: Metric namespace prefix
    """

    def __init__(self, namespace: str = "pacer", registry: CollectorRegistry | None = None):
        """
        Initialize metrics collector.

        Args:
            namespace: Prefix for all metrics
            registry: Custom Prometheus registry (optional)
        """
        self.namespace = namespace
        self.registry = registry or CollectorRegistry()

        # Task execution metrics
        self.task_total = Counter(
            f"{namespace}_tasks_total",
            "Total number of tasks executed",
            ["task_id", "status"],
            registry=self.registry,
        )

        self.task_duration = Histogram(
            f"{namespace}_task_duration_seconds",
            "Task execution duration in seconds",
            ["task_id"],
            registry=self.registry,
        )

        self.task_attempts = Histogram(
            f"{namespace}_task_attempts",
            "Number of attempts per task",
            ["task_id"],
            registry=self.registry,
        )

        self.active_tasks = Gauge(
            f"{namespace}_active_tasks",
            "Number of currently active tasks",
            registry=self.registry,
        )

        # Error metrics
        self.errors_total = Counter(
            f"{namespace}_errors_total",
            "Total number of errors",
            ["task_id", "error_type"],
            registry=self.registry,
        )

        # Circuit breaker metrics
        self.circuit_breaker_state = Gauge(
            f"{namespace}_circuit_breaker_state",
            "Circuit breaker state (0=closed, 1=half_open, 2=open)",
            ["circuit_name"],
            registry=self.registry,
        )

        logger.info("metrics_collector_initialized", namespace=namespace)

    def record_success(self, task_id: str, duration: float, attempts: int = 1) -> None:
        """
        Record successful task execution.

        Args:
            task_id: Task identifier
            duration: Execution duration in seconds
            attempts: Number of attempts needed
        """
        self.task_total.labels(task_id=task_id, status="success").inc()
        self.task_duration.labels(task_id=task_id).observe(duration)
        self.task_attempts.labels(task_id=task_id).observe(attempts)

    def record_failure(self, task_id: str, error_type: str) -> None:
        """
        Record task failure.

        Args:
            task_id: Task identifier
            error_type: Type of error that occurred
        """
        self.task_total.labels(task_id=task_id, status="failure").inc()
        self.errors_total.labels(task_id=task_id, error_type=error_type).inc()

    def increment_active_tasks(self) -> None:
        """Increment active task counter."""
        self.active_tasks.inc()

    def decrement_active_tasks(self) -> None:
        """Decrement active task counter."""
        self.active_tasks.dec()

    def update_circuit_breaker_state(self, circuit_name: str, state: str) -> None:
        """
        Update circuit breaker state metric.

        Args:
            circuit_name: Circuit breaker identifier
            state: Current state (closed, half_open, open)
        """
        state_value = {"closed": 0, "half_open": 1, "open": 2}.get(state, -1)
        self.circuit_breaker_state.labels(circuit_name=circuit_name).set(state_value)

    def get_registry(self) -> CollectorRegistry:
        """Get Prometheus registry for exposition."""
        return self.registry
