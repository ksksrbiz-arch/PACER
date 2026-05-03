"""Test suite for reliability components."""

import time

from pacer.reliability import CircuitBreaker, CircuitState, RetryPolicy


class TestRetryPolicy:
    """Test cases for RetryPolicy."""

    def test_default_policy(self):
        """Test default retry policy configuration."""
        policy = RetryPolicy()
        assert policy.max_attempts == 3
        assert policy.base_delay == 1.0
        assert policy.backoff_factor == 2.0

    def test_should_retry(self):
        """Test retry decision logic."""
        policy = RetryPolicy(max_attempts=3)
        assert policy.should_retry(1) is True
        assert policy.should_retry(2) is True
        assert policy.should_retry(3) is False
        assert policy.should_retry(4) is False

    def test_calculate_wait_time_exponential(self):
        """Test exponential backoff calculation."""
        policy = RetryPolicy(base_delay=1.0, backoff_factor=2.0, jitter=False)

        # First retry: 1.0 * 2^0 = 1.0
        wait1 = policy.calculate_wait_time(1)
        assert wait1 == 1.0

        # Second retry: 1.0 * 2^1 = 2.0
        wait2 = policy.calculate_wait_time(2)
        assert wait2 == 2.0

        # Third retry: 1.0 * 2^2 = 4.0
        wait3 = policy.calculate_wait_time(3)
        assert wait3 == 4.0

    def test_calculate_wait_time_with_max_delay(self):
        """Test wait time capping at max_delay."""
        policy = RetryPolicy(base_delay=10.0, backoff_factor=10.0, max_delay=50.0, jitter=False)

        wait = policy.calculate_wait_time(5)
        assert wait == 50.0  # Should be capped at max_delay

    def test_calculate_wait_time_with_jitter(self):
        """Test jitter adds randomness to wait time."""
        policy = RetryPolicy(base_delay=10.0, jitter=True)

        wait1 = policy.calculate_wait_time(1)
        wait2 = policy.calculate_wait_time(1)

        # With jitter, values should vary (probabilistic test)
        # But should be in reasonable range (10.0 ± 25%)
        assert 7.5 <= wait1 <= 12.5
        assert 7.5 <= wait2 <= 12.5


class TestCircuitBreaker:
    """Test cases for CircuitBreaker."""

    def test_initial_state(self):
        """Test circuit breaker starts in CLOSED state."""
        cb = CircuitBreaker("test", failure_threshold=3)
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_open_on_threshold(self):
        """Test circuit opens after threshold failures."""
        cb = CircuitBreaker("test", failure_threshold=3)

        # Record failures up to threshold
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()

        # Should open on third failure
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

    def test_success_resets_failure_count(self):
        """Test success resets failure counter in CLOSED state."""
        cb = CircuitBreaker("test", failure_threshold=3)

        cb.record_failure()
        cb.record_failure()
        assert cb.failure_count == 2

        cb.record_success()
        assert cb.failure_count == 0

    def test_half_open_after_timeout(self):
        """Test circuit transitions to HALF_OPEN after recovery timeout."""
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.1)

        # Open the circuit
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout
        time.sleep(0.15)

        # Should allow execution in HALF_OPEN
        assert cb.can_execute() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_to_closed_on_success(self):
        """Test circuit closes from HALF_OPEN after successful attempts."""
        cb = CircuitBreaker("test", failure_threshold=2, success_threshold=2, recovery_timeout=0.1)

        # Open the circuit
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)
        cb.can_execute()  # Transition to HALF_OPEN

        # Record successes
        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()

        # Should close after success_threshold
        assert cb.state == CircuitState.CLOSED

    def test_half_open_to_open_on_failure(self):
        """Test circuit reopens from HALF_OPEN on failure."""
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.1)

        # Open the circuit
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)
        cb.can_execute()  # Transition to HALF_OPEN

        # Failure in HALF_OPEN should reopen
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_reset(self):
        """Test manual reset of circuit breaker."""
        cb = CircuitBreaker("test", failure_threshold=2)

        # Open the circuit
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Reset
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0
        assert cb.can_execute() is True
