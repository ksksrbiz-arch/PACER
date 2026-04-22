"""Core automation framework with error handling and reliability."""

from .task import AutomationTask, TaskConfig, TaskResult, TaskStatus
from .executor import TaskExecutor

__all__ = [
    "AutomationTask",
    "TaskConfig",
    "TaskResult",
    "TaskStatus",
    "TaskExecutor",
]
