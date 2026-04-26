"""Test suite for automation framework."""

from datetime import datetime

from pacer.automation import AutomationTask, TaskConfig, TaskStatus
from pacer.reliability import RetryPolicy


class TestTaskResult:
    """Test cases for TaskResult."""

    def test_duration_calculation(self):
        """Test duration calculation."""
        from pacer.automation.task import TaskResult

        result = TaskResult(
            task_id="test",
            status=TaskStatus.SUCCESS,
            started_at=datetime(2024, 1, 1, 12, 0, 0),
            completed_at=datetime(2024, 1, 1, 12, 0, 5),
        )

        assert result.duration == 5.0

    def test_is_success(self):
        """Test success status check."""
        from pacer.automation.task import TaskResult

        success_result = TaskResult(task_id="test", status=TaskStatus.SUCCESS)
        assert success_result.is_success is True

        failed_result = TaskResult(task_id="test", status=TaskStatus.FAILED)
        assert failed_result.is_success is False


class TestAutomationTask:
    """Test cases for AutomationTask."""

    def test_successful_execution(self):
        """Test successful task execution."""

        def sample_func():
            return "success"

        task = AutomationTask("test_task", func=sample_func)
        result = task.execute()

        assert result.status == TaskStatus.SUCCESS
        assert result.result == "success"
        assert result.attempts == 1

    def test_failed_execution(self):
        """Test failed task execution."""

        def failing_func():
            raise ValueError("Test error")

        config = TaskConfig(retry_policy=RetryPolicy(max_attempts=1))
        task = AutomationTask("test_task", config=config, func=failing_func)
        result = task.execute()

        assert result.status == TaskStatus.FAILED
        assert "Test error" in result.error
        assert result.attempts == 1

    def test_retry_on_failure(self):
        """Test retry mechanism on failure."""
        call_count = 0

        def unstable_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("Temporary error")
            return "success"

        config = TaskConfig(retry_policy=RetryPolicy(max_attempts=3, base_delay=0.01))
        task = AutomationTask("test_task", config=config, func=unstable_func)
        result = task.execute()

        assert result.status == TaskStatus.SUCCESS
        assert result.attempts == 3
        assert call_count == 3

    def test_all_retries_exhausted(self):
        """Test behavior when all retries are exhausted."""

        def always_failing():
            raise ValueError("Permanent error")

        config = TaskConfig(retry_policy=RetryPolicy(max_attempts=2, base_delay=0.01))
        task = AutomationTask("test_task", config=config, func=always_failing)
        result = task.execute()

        assert result.status == TaskStatus.FAILED
        assert result.attempts == 2
        assert "Permanent error" in result.error

    def test_circuit_breaker_prevents_execution(self):
        """Test circuit breaker blocks execution when open."""

        def failing_func():
            raise ValueError("Error")

        config = TaskConfig(retry_policy=RetryPolicy(max_attempts=1), enable_circuit_breaker=True)
        task = AutomationTask("test_task", config=config, func=failing_func)

        # Open the circuit by recording failures
        if task._circuit_breaker:
            for _ in range(5):
                task._circuit_breaker.record_failure()

        result = task.execute()
        assert result.status == TaskStatus.CIRCUIT_OPEN

    def test_task_with_arguments(self):
        """Test task execution with arguments."""

        def add_func(a, b):
            return a + b

        task = AutomationTask("test_task", func=add_func)
        result = task.execute(5, 3)

        assert result.status == TaskStatus.SUCCESS
        assert result.result == 8

    def test_task_with_kwargs(self):
        """Test task execution with keyword arguments."""

        def greet_func(name, greeting="Hello"):
            return f"{greeting}, {name}!"

        task = AutomationTask("test_task", func=greet_func)
        result = task.execute("World", greeting="Hi")

        assert result.status == TaskStatus.SUCCESS
        assert result.result == "Hi, World!"


class TestTaskExecutor:
    """Test cases for TaskExecutor."""

    def test_executor_context_manager(self):
        """Test executor as context manager."""
        from pacer.automation import TaskExecutor

        with TaskExecutor(max_workers=2) as executor:
            assert executor._executor is not None

        assert executor._executor is None

    def test_submit_task(self):
        """Test submitting single task."""
        from pacer.automation import TaskExecutor

        def sample_func():
            return "result"

        task = AutomationTask("test", func=sample_func)

        with TaskExecutor() as executor:
            future = executor.submit(task)
            result = future.result()

            assert result.status == TaskStatus.SUCCESS
            assert result.result == "result"

    def test_execute_batch(self):
        """Test batch execution of multiple tasks."""
        from pacer.automation import TaskExecutor

        def task_func(x):
            return x * 2

        tasks = [AutomationTask(f"task_{i}", func=lambda i=i: task_func(i)) for i in range(5)]

        with TaskExecutor(max_workers=3) as executor:
            results = executor.execute_batch(tasks)

            assert len(results) == 5
            assert all(r.status == TaskStatus.SUCCESS for r in results)
