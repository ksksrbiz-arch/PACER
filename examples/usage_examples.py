"""Example usage of PACER automation framework."""

from pacer.automation import AutomationTask, TaskConfig, TaskExecutor
from pacer.reliability import RetryPolicy
from pacer.logging import AuditLogger


def example_simple_task():
    """Example of a simple automated task."""

    def process_data(data: str) -> str:
        """Sample processing function."""
        return data.upper()

    # Create task with default configuration
    task = AutomationTask("simple_task", func=process_data)

    # Execute task
    result = task.execute("hello world")

    print(f"Status: {result.status}")
    print(f"Result: {result.result}")
    print(f"Duration: {result.duration}s")


def example_resilient_task():
    """Example of a task with retry and circuit breaker."""

    call_count = 0

    def unstable_operation():
        """Simulates an unstable operation that may fail."""
        nonlocal call_count
        call_count += 1

        if call_count < 3:
            raise ConnectionError("Temporary network issue")

        return "Operation successful"

    # Configure with retry policy and circuit breaker
    config = TaskConfig(
        retry_policy=RetryPolicy(
            max_attempts=5,
            base_delay=1.0,
            backoff_factor=2.0
        ),
        enable_circuit_breaker=True,
        enable_health_check=True,
        enable_metrics=True
    )

    task = AutomationTask("resilient_task", config=config, func=unstable_operation)

    # Execute with automatic retry
    result = task.execute()

    print(f"Status: {result.status}")
    print(f"Result: {result.result}")
    print(f"Attempts: {result.attempts}")
    print(f"Duration: {result.duration}s")


def example_batch_execution():
    """Example of executing multiple tasks in parallel."""

    def process_item(item_id: int) -> dict:
        """Process a single item."""
        return {
            "id": item_id,
            "processed": True,
            "value": item_id * 2
        }

    # Create multiple tasks
    tasks = [
        AutomationTask(
            f"task_{i}",
            func=lambda i=i: process_item(i)
        )
        for i in range(10)
    ]

    # Execute tasks in parallel
    with TaskExecutor(max_workers=4) as executor:
        results = executor.execute_batch(tasks)

    # Check results
    successful = sum(1 for r in results if r.is_success)
    print(f"Completed {successful}/{len(results)} tasks successfully")


def example_with_audit_logging():
    """Example with complete audit trail."""

    # Initialize audit logger
    audit_logger = AuditLogger(log_file="/tmp/audit.log")

    def important_operation(value: int) -> int:
        """An operation that needs audit trail."""
        return value * 2

    # Log task start
    audit_logger.log_task_started("critical_task", config={"retry": 3})

    # Create and execute task
    config = TaskConfig(
        retry_policy=RetryPolicy(max_attempts=3)
    )
    task = AutomationTask("critical_task", config=config, func=important_operation)

    result = task.execute(42)

    # Log completion or failure
    if result.is_success:
        audit_logger.log_task_completed(
            "critical_task",
            duration=result.duration or 0,
            attempts=result.attempts
        )
    else:
        audit_logger.log_task_failed(
            "critical_task",
            error=result.error or "Unknown error",
            attempts=result.attempts
        )

    print(f"Audit events: {audit_logger.get_event_count()}")
    print(f"Result: {result.result}")


if __name__ == "__main__":
    print("=== Simple Task Example ===")
    example_simple_task()

    print("\n=== Resilient Task Example ===")
    example_resilient_task()

    print("\n=== Batch Execution Example ===")
    example_batch_execution()

    print("\n=== Audit Logging Example ===")
    example_with_audit_logging()
