"""Retry policy with configurable backoff strategies."""

from dataclasses import dataclass
from typing import Optional
import random


@dataclass
class RetryPolicy:
    """
    Configurable retry policy with exponential backoff.

    Attributes:
        max_attempts: Maximum number of retry attempts
        base_delay: Base delay in seconds before first retry
        backoff_factor: Multiplier for exponential backoff
        max_delay: Maximum delay between retries
        jitter: Add random jitter to prevent thundering herd
    """

    max_attempts: int = 3
    base_delay: float = 1.0
    backoff_factor: float = 2.0
    max_delay: float = 60.0
    jitter: bool = True

    def calculate_wait_time(self, attempt: int) -> float:
        """
        Calculate wait time for given attempt number.

        Args:
            attempt: Current attempt number (1-indexed)

        Returns:
            Wait time in seconds
        """
        if attempt <= 0:
            return 0.0

        # Exponential backoff: base_delay * (backoff_factor ^ (attempt - 1))
        wait = self.base_delay * (self.backoff_factor ** (attempt - 1))

        # Cap at max_delay
        wait = min(wait, self.max_delay)

        # Add jitter if enabled (±25% random variation)
        if self.jitter:
            jitter_range = wait * 0.25
            wait += random.uniform(-jitter_range, jitter_range)

        return max(0.0, wait)

    def should_retry(self, attempt: int) -> bool:
        """
        Check if should retry based on attempt number.

        Args:
            attempt: Current attempt number (1-indexed)

        Returns:
            True if should retry, False otherwise
        """
        return attempt < self.max_attempts
