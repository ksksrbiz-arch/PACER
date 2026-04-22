"""Task executor with thread pool and async support."""

from concurrent.futures import ThreadPoolExecutor, Future
from typing import List, Optional, Dict, Any
import structlog

from .task import AutomationTask, TaskResult

logger = structlog.get_logger(__name__)


class TaskExecutor:
    """
    Execute multiple automation tasks with thread pool support.

    Features:
    - Parallel task execution
    - Resource management
    - Centralized monitoring
    """

    def __init__(self, max_workers: int = 4):
        """
        Initialize task executor.

        Args:
            max_workers: Maximum number of concurrent workers
        """
        self.max_workers = max_workers
        self._executor: Optional[ThreadPoolExecutor] = None
        self._active_tasks: Dict[str, Future] = {}

    def __enter__(self) -> "TaskExecutor":
        """Enter context manager."""
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit context manager and cleanup resources."""
        if self._executor:
            self._executor.shutdown(wait=True)
        self._executor = None
        self._active_tasks.clear()

    def submit(
        self, task: AutomationTask, *args: Any, **kwargs: Any
    ) -> Future[TaskResult]:
        """
        Submit task for execution.

        Args:
            task: AutomationTask to execute
            *args: Positional arguments for task
            **kwargs: Keyword arguments for task

        Returns:
            Future containing TaskResult
        """
        if not self._executor:
            raise RuntimeError("Executor not initialized. Use context manager.")

        logger.info("submitting_task", task_id=task.task_id)

        future = self._executor.submit(task.execute, *args, **kwargs)
        self._active_tasks[task.task_id] = future
        return future

    def execute_batch(
        self, tasks: List[AutomationTask], *args: Any, **kwargs: Any
    ) -> List[TaskResult]:
        """
        Execute multiple tasks in parallel.

        Args:
            tasks: List of AutomationTask instances
            *args: Positional arguments for all tasks
            **kwargs: Keyword arguments for all tasks

        Returns:
            List of TaskResult instances
        """
        if not self._executor:
            raise RuntimeError("Executor not initialized. Use context manager.")

        logger.info("executing_batch", task_count=len(tasks))

        futures = [self.submit(task, *args, **kwargs) for task in tasks]
        results = [future.result() for future in futures]

        logger.info(
            "batch_completed",
            task_count=len(tasks),
            success_count=sum(1 for r in results if r.is_success),
        )

        return results
