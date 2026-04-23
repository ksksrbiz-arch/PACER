"""Core automation framework with error handling and reliability."""

from .executor import TaskExecutor
from .task import AutomationTask, TaskConfig, TaskResult, TaskStatus

__all__ = [
    "AutomationTask",
    "TaskConfig",
    "TaskResult",
    "TaskStatus",
    "TaskExecutor",
]
