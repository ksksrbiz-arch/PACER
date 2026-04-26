"""Data validation with configurable rules."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class ValidationError(Exception):
    """Raised when validation fails."""

    def __init__(self, message: str, field: str | None = None):
        """
        Initialize validation error.

        Args:
            message: Error description
            field: Field that failed validation
        """
        self.field = field
        super().__init__(message)


@dataclass
class ValidationRule:
    """
    Single validation rule.

    Attributes:
        name: Rule identifier
        validator: Function that performs validation
        error_message: Message to show on failure
    """

    name: str
    validator: Callable[[Any], bool]
    error_message: str

    def validate(self, value: Any) -> None:
        """
        Validate value against rule.

        Args:
            value: Value to validate

        Raises:
            ValidationError: If validation fails
        """
        try:
            if not self.validator(value):
                raise ValidationError(self.error_message)
        except Exception as e:
            if isinstance(e, ValidationError):
                raise
            raise ValidationError(f"Validation error: {str(e)}") from e


class Validator:
    """
    Validation system with configurable rules.

    Provides common validation patterns and custom rule support.
    """

    def __init__(self):
        """Initialize validator."""
        self.rules: dict[str, list[ValidationRule]] = {}
        logger.debug("validator_initialized")

    def add_rule(self, field: str, rule: ValidationRule) -> None:
        """
        Add validation rule for field.

        Args:
            field: Field name to validate
            rule: ValidationRule to apply
        """
        if field not in self.rules:
            self.rules[field] = []
        self.rules[field].append(rule)
        logger.debug("validation_rule_added", field=field, rule=rule.name)

    def validate_field(self, field: str, value: Any) -> None:
        """
        Validate single field.

        Args:
            field: Field name
            value: Value to validate

        Raises:
            ValidationError: If validation fails
        """
        if field not in self.rules:
            return

        for rule in self.rules[field]:
            try:
                rule.validate(value)
            except ValidationError as e:
                e.field = field
                logger.warning(
                    "validation_failed",
                    field=field,
                    rule=rule.name,
                    error=str(e),
                )
                raise

    def validate_dict(self, data: dict[str, Any]) -> None:
        """
        Validate dictionary of fields.

        Args:
            data: Dictionary to validate

        Raises:
            ValidationError: If any field validation fails
        """
        for field, value in data.items():
            self.validate_field(field, value)

    @staticmethod
    def not_none(error_message: str = "Value cannot be None") -> ValidationRule:
        """Create rule that checks value is not None."""
        return ValidationRule(
            name="not_none",
            validator=lambda x: x is not None,
            error_message=error_message,
        )

    @staticmethod
    def not_empty(error_message: str = "Value cannot be empty") -> ValidationRule:
        """Create rule that checks value is not empty."""
        return ValidationRule(
            name="not_empty",
            validator=lambda x: bool(x),
            error_message=error_message,
        )

    @staticmethod
    def min_length(length: int, error_message: str | None = None) -> ValidationRule:
        """Create rule that checks minimum length."""
        msg = error_message or f"Value must be at least {length} characters"
        return ValidationRule(
            name="min_length",
            validator=lambda x: len(x) >= length if hasattr(x, "__len__") else False,
            error_message=msg,
        )

    @staticmethod
    def max_length(length: int, error_message: str | None = None) -> ValidationRule:
        """Create rule that checks maximum length."""
        msg = error_message or f"Value must be at most {length} characters"
        return ValidationRule(
            name="max_length",
            validator=lambda x: len(x) <= length if hasattr(x, "__len__") else False,
            error_message=msg,
        )

    @staticmethod
    def in_range(
        min_val: float, max_val: float, error_message: str | None = None
    ) -> ValidationRule:
        """Create rule that checks value is in range."""
        msg = error_message or f"Value must be between {min_val} and {max_val}"
        return ValidationRule(
            name="in_range",
            validator=lambda x: min_val <= x <= max_val,
            error_message=msg,
        )

    @staticmethod
    def matches_pattern(pattern: str, error_message: str | None = None) -> ValidationRule:
        """Create rule that checks value matches regex pattern."""
        import re

        msg = error_message or f"Value must match pattern: {pattern}"
        regex = re.compile(pattern)
        return ValidationRule(
            name="matches_pattern",
            validator=lambda x: bool(regex.match(str(x))),
            error_message=msg,
        )

    @staticmethod
    def is_type(expected_type: type, error_message: str | None = None) -> ValidationRule:
        """Create rule that checks value type."""
        msg = error_message or f"Value must be of type {expected_type.__name__}"
        return ValidationRule(
            name="is_type",
            validator=lambda x: isinstance(x, expected_type),
            error_message=msg,
        )
