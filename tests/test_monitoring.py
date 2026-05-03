"""Test suite for monitoring components."""

from pacer.monitoring import HealthCheck, HealthStatus, MetricsCollector


class TestHealthCheck:
    """Test cases for HealthCheck."""

    def test_initial_state(self):
        """Test initial health check state."""
        health = HealthCheck("test_service")
        assert health.status == HealthStatus.UNKNOWN
        assert health.total_checks == 0
        assert health.failed_checks == 0

    def test_record_successful_check(self):
        """Test recording successful health check."""
        health = HealthCheck("test_service")
        health.record_check(success=True)

        assert health.total_checks == 1
        assert health.failed_checks == 0
        assert health.status == HealthStatus.HEALTHY

    def test_record_failed_check(self):
        """Test recording failed health check."""
        health = HealthCheck("test_service", unhealthy_threshold=0.5)

        # Record 3 failures out of 4 checks (75% failure rate)
        health.record_check(success=True)
        health.record_check(success=False)
        health.record_check(success=False)
        health.record_check(success=False)

        assert health.total_checks == 4
        assert health.failed_checks == 3
        assert health.status == HealthStatus.UNHEALTHY

    def test_degraded_status(self):
        """Test degraded status between healthy and unhealthy."""
        health = HealthCheck("test_service", degraded_threshold=0.2, unhealthy_threshold=0.5)

        # 3 failures out of 10 checks = 30% (degraded)
        for i in range(10):
            health.record_check(success=(i % 3 != 0))

        assert health.status == HealthStatus.DEGRADED

    def test_is_healthy(self):
        """Test is_healthy method."""
        health = HealthCheck("test_service")
        health.record_check(success=True)

        assert health.is_healthy() is True

    def test_should_check(self):
        """Test should_check timing logic."""
        health = HealthCheck("test_service", check_interval=1.0)

        # Should check initially
        assert health.should_check() is True

        # Record a check
        health.record_check(success=True)

        # Should not check immediately after
        assert health.should_check() is False

    def test_get_status_report(self):
        """Test status report generation."""
        health = HealthCheck("test_service")
        health.record_check(success=True)
        health.record_check(success=False)

        report = health.get_status_report()

        assert report["name"] == "test_service"
        assert report["status"] == health.status
        assert report["total_checks"] == 2
        assert report["failed_checks"] == 1
        assert report["failure_rate"] == 0.5

    def test_reset(self):
        """Test resetting health check."""
        health = HealthCheck("test_service")
        health.record_check(success=False)
        health.record_check(success=False)

        health.reset()

        assert health.total_checks == 0
        assert health.failed_checks == 0
        assert health.status == HealthStatus.UNKNOWN


class TestMetricsCollector:
    """Test cases for MetricsCollector."""

    def test_initialization(self):
        """Test metrics collector initialization."""
        metrics = MetricsCollector(namespace="test")
        assert metrics.namespace == "test"
        assert metrics.registry is not None

    def test_record_success(self):
        """Test recording successful task execution."""
        metrics = MetricsCollector()

        # Should not raise exception
        metrics.record_success("task_1", duration=1.5, attempts=1)

    def test_record_failure(self):
        """Test recording failed task execution."""
        metrics = MetricsCollector()

        # Should not raise exception
        metrics.record_failure("task_1", "ValueError")

    def test_active_tasks_counter(self):
        """Test active tasks counter."""
        metrics = MetricsCollector()

        metrics.increment_active_tasks()
        metrics.increment_active_tasks()
        metrics.decrement_active_tasks()

        # Should complete without errors

    def test_circuit_breaker_state(self):
        """Test circuit breaker state tracking."""
        metrics = MetricsCollector()

        metrics.update_circuit_breaker_state("test_circuit", "closed")
        metrics.update_circuit_breaker_state("test_circuit", "open")
        metrics.update_circuit_breaker_state("test_circuit", "half_open")

    def test_get_registry(self):
        """Test getting Prometheus registry."""
        metrics = MetricsCollector()
        registry = metrics.get_registry()

        assert registry is not None
