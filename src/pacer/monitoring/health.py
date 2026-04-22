"""Health check system for monitoring task status."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, Any
import structlog

logger = structlog.get_logger(__name__)


class HealthStatus(str, Enum):
    """Health check status values."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthCheck:
    """
    Health monitoring for automation tasks.

    Tracks task health based on success rate, latency, and error patterns.

    Attributes:
        name: Health check identifier
        check_interval: Seconds between health checks
        unhealthy_threshold: Failure rate to mark unhealthy (0.0-1.0)
        degraded_threshold: Failure rate to mark degraded (0.0-1.0)
    """

    name: str
    check_interval: float = 30.0
    unhealthy_threshold: float = 0.5
    degraded_threshold: float = 0.2
    status: HealthStatus = field(default=HealthStatus.UNKNOWN, init=False)
    last_check_time: Optional[datetime] = field(default=None, init=False)
    total_checks: int = field(default=0, init=False)
    failed_checks: int = field(default=0, init=False)
    metadata: Dict[str, Any] = field(default_factory=dict, init=False)

    def record_check(self, success: bool, metadata: Optional[Dict[str, Any]] = None) -> None:
        """
        Record health check result.

        Args:
            success: Whether check passed
            metadata: Additional check information
        """
        self.total_checks += 1
        if not success:
            self.failed_checks += 1

        self.last_check_time = datetime.utcnow()
        if metadata:
            self.metadata.update(metadata)

        self._update_status()

    def _update_status(self) -> None:
        """Update health status based on failure rate."""
        if self.total_checks == 0:
            self.status = HealthStatus.UNKNOWN
            return

        failure_rate = self.failed_checks / self.total_checks

        old_status = self.status

        if failure_rate >= self.unhealthy_threshold:
            self.status = HealthStatus.UNHEALTHY
        elif failure_rate >= self.degraded_threshold:
            self.status = HealthStatus.DEGRADED
        else:
            self.status = HealthStatus.HEALTHY

        if old_status != self.status:
            logger.info(
                "health_status_changed",
                name=self.name,
                old_status=old_status,
                new_status=self.status,
                failure_rate=failure_rate,
            )

    def is_healthy(self) -> bool:
        """Check if system is healthy."""
        return self.status == HealthStatus.HEALTHY

    def should_check(self) -> bool:
        """Check if health check is due."""
        if not self.last_check_time:
            return True

        elapsed = (datetime.utcnow() - self.last_check_time).total_seconds()
        return elapsed >= self.check_interval

    def get_status_report(self) -> Dict[str, Any]:
        """
        Get detailed health status report.

        Returns:
            Dictionary with health metrics
        """
        failure_rate = (
            self.failed_checks / self.total_checks if self.total_checks > 0 else 0.0
        )

        return {
            "name": self.name,
            "status": self.status,
            "total_checks": self.total_checks,
            "failed_checks": self.failed_checks,
            "failure_rate": failure_rate,
            "last_check_time": self.last_check_time.isoformat()
            if self.last_check_time
            else None,
            "metadata": self.metadata,
        }

    def reset(self) -> None:
        """Reset health check counters."""
        self.total_checks = 0
        self.failed_checks = 0
        self.status = HealthStatus.UNKNOWN
        self.metadata.clear()
        logger.info("health_check_reset", name=self.name)
